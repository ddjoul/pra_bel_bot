import asyncio
import io
import logging
import re
import uuid
import warnings
from urllib.parse import urlparse
from PIL import Image
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import start_bot
from start_bot import core

edit_lock = asyncio.Lock()
LIMITS = {'name': 100, 'nickname': 40, 'bio': 5000, 'quote': 2000, 'telegram_url': 500, 'instagram_url': 500, 'tiktok_url': 500}
HOSTS = {'telegram_url': ('t.me', 'telegram.me'), 'instagram_url': ('instagram.com',), 'tiktok_url': ('tiktok.com',)}

async def user(request):
    u = await core._current_user(request)
    if not u:
        raise web.HTTPUnauthorized(text='unauthorized')
    if not u.get('is_approved'):
        raise web.HTTPForbidden(text='approval_required')
    return u

async def owned(request, author_id=None):
    u = await user(request)
    rows = await core.query(lambda: core.db.rpc('web_owned_authors', {'p_token': u['token']}))
    if author_id is None:
        return u, rows
    a = next((a for a in rows if a['id'] == author_id), None)
    if not a:
        raise web.HTTPForbidden(text='author_not_owned')
    return u, a

async def list_authors(request):
    u, rows = await owned(request)
    return web.json_response({'telegram_id': u['telegram_id'], 'authors': rows})

async def update_author(request):
    aid = str(uuid.UUID(request.match_info['id']))
    async with edit_lock:
        _, current = await owned(request, aid)
        data = await request.json()
        if isinstance(data, dict) and set(data) == {'remove_avatar'} and data['remove_avatar'] is True:
            def remove():
                q = core.db.table('authors').update({'avatar_path': None}).eq('id', aid)
                previous = current.get('avatar_path')
                return q.is_('avatar_path', 'null') if previous is None else q.eq('avatar_path', previous)
            rows = await core.query(remove)
            if not rows:
                raise web.HTTPConflict(text='avatar_changed_refresh')
            return web.json_response({'author': rows[0], 'avatar_path': None})
        if not isinstance(data, dict) or not data or set(data) - set(LIMITS):
            raise web.HTTPBadRequest(text='invalid_fields')
        patch = {}
        for field, value in data.items():
            if not isinstance(value, str):
                raise web.HTTPBadRequest(text='text_required')
            value = value.strip()
            if len(value) > LIMITS[field]:
                raise web.HTTPBadRequest(text='field_too_long')
            if field == 'name' and (not value or value.casefold() == 'ананім'):
                raise web.HTTPBadRequest(text='invalid_name')
            if field == 'nickname':
                value = value.lower()
                if value and (value == 'anon' or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,39}', value)):
                    raise web.HTTPBadRequest(text='invalid_nickname')
                if value:
                    matches = await core.query(lambda: core.db.table('authors').select('id').eq('nickname', value).neq('id', aid).limit(1))
                    if matches:
                        raise web.HTTPConflict(text='nickname_taken')
            if field in HOSTS and value:
                parsed = urlparse(value)
                host = (parsed.hostname or '').lower()
                if parsed.scheme != 'https' or parsed.username or parsed.password or not any(host == h or host.endswith('.' + h) for h in HOSTS[field]):
                    raise web.HTTPBadRequest(text='invalid_social_url')
            patch[field] = value or None
        rows = await core.query(lambda: core.db.table('authors').update(patch).eq('id', aid))
        return web.json_response({'author': rows[0] if rows else None})

async def avatar(request):
    aid = str(uuid.UUID(request.match_info['id']))
    u, _ = await owned(request, aid)
    reader = await request.multipart()
    part = await reader.next()
    if not part or part.name != 'file':
        raise web.HTTPBadRequest(text='file_required')
    raw = bytearray()
    while True:
        chunk = await part.read_chunk()
        if not chunk:
            break
        raw.extend(chunk)
        if len(raw) > 5 * 1024 * 1024:
            raise web.HTTPRequestEntityTooLarge(max_size=5 * 1024 * 1024, actual_size=len(raw))
    def normalize():
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as img:
                if img.format not in ('JPEG', 'PNG', 'WEBP') or getattr(img, 'is_animated', False):
                    raise ValueError('invalid_image')
                img.load()
                img = img.convert('RGB')
                img.thumbnail((1200, 1200))
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=90)
                return output.getvalue()
    try:
        image = await asyncio.to_thread(normalize)
    except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise web.HTTPBadRequest(text='invalid_image')
    await owned(request, aid)
    path = str(u['telegram_id']) + '/' + aid + '/' + str(uuid.uuid4()) + '.jpg'
    await asyncio.to_thread(lambda: core.db.storage.from_('avatars').upload(path, image, {'content-type': 'image/jpeg', 'upsert': 'false'}))
    url = core.db.storage.from_('avatars').get_public_url(path)
    rows = await core.query(lambda: core.db.table('authors').update({'avatar_path': url}).eq('id', aid))
    return web.json_response({'author': rows[0] if rows else None, 'avatar_path': url})

async def rating(request):
    pid = str(uuid.UUID(request.match_info['id']))
    if request.method == 'POST':
        u = await user(request)
        data = await request.json()
        value = data.get('rating') if isinstance(data, dict) else None
        if type(value) is not int or not 1 <= value <= 5:
            raise web.HTTPBadRequest(text='invalid_rating')
        result = await core.query(lambda: core.db.rpc('web_rate_poem', {'p_token': u['token'], 'p_post_id': pid, 'p_rating': value}))
        return web.json_response(result)
    result = await core.query(lambda: core.db.rpc('poem_rating_summary', {'p_post_id': pid}))
    value = result[0] if result else {'average_rating': None, 'votes': 0}
    u = await core._current_user(request)
    value['my_rating'] = None
    if u and u.get('is_approved'):
        rows = await core.query(lambda: core.db.table('poem_ratings').select('rating').eq('post_id', pid).eq('telegram_id', u['telegram_id']).limit(1))
        value['my_rating'] = rows[0]['rating'] if rows else None
    return web.json_response(value)

@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status < 400:
            raise
        return web.json_response({'error': exc.text}, status=exc.status)
    except (ValueError, TypeError):
        return web.json_response({'error': 'invalid_request'}, status=400)
    except Exception as exc:
        logging.warning('Account API failure: %s', type(exc).__name__)
        return web.json_response({'error': 'server_error'}, status=500)

def main():
    logging.basicConfig(level=logging.WARNING)
    app = web.Application(middlewares=[core.cors_mw, errors], client_max_size=6 * 1024 * 1024)
    app.router.add_get('/', core.health)
    app.router.add_get('/health', core.health)
    app.router.add_post('/webhook/comment', core.comment_webhook)
    app.router.add_get('/api/tg/callback', core.tg_callback)
    app.router.add_get('/api/me', core.api_me)
    app.router.add_post('/api/logout', core.api_logout)
    app.router.add_post('/api/comments', core.api_comments)
    app.router.add_get('/api/authors', list_authors)
    app.router.add_post('/api/authors/{id}', update_author)
    app.router.add_post('/api/authors/{id}/avatar', avatar)
    app.router.add_get('/api/ratings/{id}', rating)
    app.router.add_post('/api/ratings/{id}', rating)
    SimpleRequestHandler(dispatcher=core.dp, bot=core.bot, secret_token=core.WEBHOOK_SECRET, handle_in_background=False).register(app, path=core.WEBHOOK_PATH)
    core.dp.startup.register(core.on_startup)
    setup_application(app, core.dp, bot=core.bot)
    web.run_app(app, host='0.0.0.0', port=int(core.os.getenv('PORT', '10000')), access_log=None)

if __name__ == '__main__':
    main()
