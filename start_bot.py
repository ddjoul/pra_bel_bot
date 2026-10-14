import asyncio
import io
import re
import uuid
import warnings
from datetime import datetime, timezone, timedelta
from PIL import Image
from aiohttp import web
from aiogram import F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
import bot as core

CATEGORIES = {'poem': 'верш', 'translation': 'пераклад', 'news': 'навіна', 'culture': 'артыкул пра культуру', 'language': 'артыкул пра мову', 'idea': 'ідэя'}
ARTICLES = {'news', 'culture', 'language', 'idea'}
old_prompt = core.prompt
old_comments = core.api_comments
old_hash_check = core._check_tg_hash
comment_locks = {}
Image.MAX_IMAGE_PIXELS = 20000000

async def allowed_articles(uid):
    users = await core.query(lambda: core.db.table('telegram_users').select('is_approved,can_post_articles,is_admin').eq('telegram_id', uid).limit(1))
    return bool(users and any(users[0].get(k) for k in ('is_approved', 'can_post_articles', 'is_admin')))

async def send_text(chat, value):
    for i in range(0, len(value or ''), 1800):
        await core.bot.send_message(chat, value[i:i+1800])

async def prompt(chat, d):
    phase, p = d['phase'], d['payload']
    if phase == 'kind':
        options = [('верш', 'kind_poem'), ('пераклад', 'kind_translation')]
        if await allowed_articles(d['telegram_id']):
            options += [(CATEGORIES[k], 'kind_' + k) for k in ('news', 'culture', 'language', 'idea')]
        options.append(('скасаваць', 'cancel'))
        await core.bot.send_message(chat, 'што хочаш даслаць?', reply_markup=core.keyboard(d, options))
        return
    if phase == 'tags':
        await core.bot.send_message(chat, 'дашлі да 12 хэштэгаў праз прабел: #каханне #мова. Да 40 знакаў у кожным; літары, лічбы і падкрэсліванне.', reply_markup=core.keyboard(d, [('без хэштэгаў', 'skip_tags'), ('скасаваць', 'cancel')]))
        return
    if phase == 'body' and p.get('category') in ARTICLES:
        await core.bot.send_message(chat, 'дашлі тэкст, потым выяву, потым наступны тэкст — у патрэбным парадку. Выявы: JPEG, PNG або WebP, да 10 МБ. Подпіс да выявы стане подпісам пад ёй. Чарнавіковыя выявы захоўваюцца ў публічным сховішчы: не дасылай прыватныя дакументы.', reply_markup=core.keyboard(d, [('гатова', 'done'), ('скасаваць', 'cancel')]))
        return
    if phase == 'review':
        category = p.get('category')
        text = 'правер перад адпраўкай:\nтып: ' + CATEGORIES.get(category, category or '') + '\nназва: ' + p.get('title', '***') + '\nмова: ' + core.lang_name(p.get('language_code'))
        if category == 'translation':
            text += '\nаўтар арыгіналу: ' + p.get('original_author', '') + '\nмова арыгіналу: ' + core.lang_name(p.get('original_language'))
        text += '\nаўтарства: ' + ('ананімна' if p.get('anonymous') else 'з маім аўтарствам')
        text += '\nхэштэгі: ' + (' '.join('#' + t for t in p.get('tags', [])) or 'няма')
        await send_text(chat, text)
        if category in ARTICLES and p.get('content'):
            for block in p['content']:
                if block['type'] == 'text':
                    await send_text(chat, block['text'])
                elif block['type'] == 'image':
                    url = core.db.storage.from_('photos').get_public_url(block['path'])
                    await core.bot.send_photo(chat, url, caption=block.get('alt') or None)
        else:
            await send_text(chat, p.get('body', ''))
        await core.bot.send_message(chat, 'адправіць на публікацыю / праверку?', reply_markup=core.keyboard(d, [('адправіць', 'send'), ('скасаваць', 'cancel')]))
        return
    await old_prompt(chat, d)

core.prompt = prompt

async def answer_callback(c):
    try:
        await c.answer()
    except TelegramBadRequest as exc:
        if 'query is too old' not in str(exc).lower() and 'query id is invalid' not in str(exc).lower():
            raise

async def extra_callback(c):
    if not c.message or c.message.chat.type != 'private':
        raise SkipHandler
    token, sep, action = (c.data or '').partition(':')
    if not sep or action not in {'done', 'skip_tags'} | {'kind_' + k for k in ARTICLES}:
        raise SkipHandler
    async with core.locks[c.from_user.id]:
        d = await core.get_draft(c.from_user.id)
        if not d or token != d['id'][:8]:
            await answer_callback(c)
            await core.bot.send_message(c.from_user.id, 'кнопка ўжо неактуальная. /start')
            return
        p = d['payload']
        if action.startswith('kind_') and d['phase'] == 'kind':
            await answer_callback(c)
            if not await allowed_articles(c.from_user.id):
                await core.bot.send_message(c.from_user.id, 'артыкулы пакуль недаступныя для гэтага акаўнта.')
                return
            p['category'] = action.removeprefix('kind_')
            p['content'] = []
            d['phase'] = 'language'
        elif action == 'done' and d['phase'] == 'body':
            await answer_callback(c)
            if not p.get('body', '').strip():
                await core.bot.send_message(c.from_user.id, 'спачатку дашлі тэкст.')
                return
            d['phase'] = 'tags'
        elif action == 'skip_tags' and d['phase'] == 'tags':
            await answer_callback(c)
            p['tags'] = []
            d['phase'] = 'attribution'
        else:
            raise SkipHandler
        await core.save(d)
        await prompt(c.from_user.id, d)

async def extra_message(m):
    if m.chat.type != 'private' or not m.from_user or (m.text or '').startswith('/') or m.text in ('новы твор', 'мой ID', 'скасаваць'):
        raise SkipHandler
    async with core.locks[m.from_user.id]:
        d = await core.get_draft(m.from_user.id)
        if not d:
            raise SkipHandler
        p = d['payload']
        if d['phase'] == 'tags':
            if not m.text:
                await m.answer('хэштэгі трэба даслаць тэкстам.')
                return
            tags = list(dict.fromkeys(t.lstrip('#').lower() for t in m.text.split()))
            if not 1 <= len(tags) <= 12 or any(not re.fullmatch(r'\w{1,40}', t) for t in tags):
                await m.answer('да 12 хэштэгаў; кожны да 40 знакаў, без прабелаў і знакаў пунктуацыі.')
                return
            p['tags'] = tags
            d['phase'] = 'attribution'
            await core.save(d)
            await prompt(m.chat.id, d)
            return
        if d['phase'] != 'body' or p.get('category') not in ARTICLES:
            raise SkipHandler
        blocks = p.setdefault('content', [])
        if len(blocks) >= 60:
            await m.answer('максімум 60 блокаў. Націсні «гатова».')
            return
        if m.text:
            value = (p.get('body', '') + '\n\n' if p.get('body') else '') + m.text
            if len(value) > 60000:
                await m.answer('максімум 60000 знакаў; частка не дададзена.')
                return
            blocks.append({'type': 'text', 'text': m.text})
            p['body'] = value
        elif m.photo or m.document:
            if sum(b.get('type') == 'image' for b in blocks) >= 20:
                await m.answer('максімум 20 выяў.')
                return
            media = m.photo[-1] if m.photo else m.document
            if not media.file_size or media.file_size > 10 * 1024 * 1024:
                await m.answer('выява павінна быць да 10 МБ.')
                return
            caption = m.caption or ''
            if len(caption) > 300:
                await m.answer('подпіс да выявы — да 300 знакаў.')
                return
            raw = io.BytesIO()
            await core.bot.download(media, destination=raw)
            def normalize():
                raw.seek(0)
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(raw) as image:
                        if image.format not in ('JPEG', 'PNG', 'WEBP') or getattr(image, 'is_animated', False):
                            raise ValueError('unsupported image')
                        image.load()
                        output = io.BytesIO()
                        image.convert('RGB').save(output, format='JPEG', quality=90)
                        data = output.getvalue()
                        if len(data) > 10 * 1024 * 1024:
                            raise ValueError('image too large')
                        return data
            try:
                image_data = await asyncio.to_thread(normalize)
            except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                await m.answer('не ўдалося прачытаць выяву. Дашлі звычайны JPEG, PNG або WebP.')
                return
            path = str(m.from_user.id) + '/' + d['id'] + '/' + str(uuid.uuid4()) + '.jpg'
            await asyncio.to_thread(lambda: core.db.storage.from_('photos').upload(path, image_data, {'content-type': 'image/jpeg', 'upsert': 'false'}))
            blocks.append({'type': 'image', 'path': path, 'alt': caption})
        else:
            await m.answer('дашлі тэкст або выяву.')
            return
        await core.save(d)
        await m.answer('блок захаваны. Дашлі наступны або націсні «гатова».', reply_markup=core.keyboard(d, [('гатова', 'done'), ('скасаваць', 'cancel')]))

core.dp.message.register(extra_message, F.chat.type == 'private')
core.dp.message.handlers.insert(0, core.dp.message.handlers.pop())
core.dp.callback_query.register(extra_callback, F.data)
core.dp.callback_query.handlers.insert(0, core.dp.callback_query.handlers.pop())

def fresh_hash(data):
    try:
        age = datetime.now(timezone.utc).timestamp() - int(data['auth_date'])
    except (KeyError, TypeError, ValueError):
        return False
    return -60 <= age <= 86400 and old_hash_check(data)

core._check_tg_hash = fresh_hash

async def approved_comments(request):
    u = await core._current_user(request)
    if not u:
        return web.json_response({'error': 'unauthorized'}, status=401)
    if not u.get('is_approved'):
        return web.json_response({'error': 'approval_required'}, status=403)
    async with comment_locks.setdefault(u['telegram_id'], asyncio.Lock()):
        try:
            data = await request.json()
            post_id = str(uuid.UUID(data.get('post_id', '')))
            parent_id = str(uuid.UUID(data['parent_id'])) if data.get('parent_id') else None
            if not isinstance(data.get('display_name'), str) or not isinstance(data.get('body'), str):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, KeyError):
            return web.json_response({'error': 'invalid_comment'}, status=400)
        posts = await core.query(lambda: core.db.table('posts').select('id').eq('id', post_id).eq('status', 'published').limit(1))
        if not posts:
            return web.json_response({'error': 'post_not_found'}, status=404)
        if parent_id:
            parents = await core.query(lambda: core.db.table('comments').select('id,parent_id').eq('id', parent_id).eq('post_id', post_id).eq('status', 'published').limit(1))
            if not parents or parents[0].get('parent_id'):
                return web.json_response({'error': 'invalid_parent'}, status=400)
        since = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
        recent = await core.query(lambda: core.db.table('comments').select('id').eq('author_telegram_id', u['telegram_id']).gte('created_at', since).limit(1))
        if recent:
            return web.json_response({'error': 'wait_before_next_comment'}, status=429)
        return await old_comments(request)

core.api_comments = approved_comments

if __name__ == '__main__':
    core.main()
