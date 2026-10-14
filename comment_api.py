import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from aiohttp import web

_comment_lock = asyncio.Lock()

async def api_comments(request, *, core):
    u = await core._current_user(request)
    if not u:
        return web.json_response({'error': 'unauthorized'}, status=401)
    if not u.get('is_approved'):
        return web.json_response({'error': 'approval_required'}, status=403)
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError()
        post_id = str(uuid.UUID(data.get('post_id', '')))
        parent_id = str(uuid.UUID(data['parent_id'])) if data.get('parent_id') else None
        if not isinstance(data.get('display_name'), str) or not isinstance(data.get('body'), str):
            raise ValueError()
        display_name = data['display_name'].strip()
        body = data['body'].strip()
        if not display_name or not body:
            return web.json_response({'error': 'missing'}, status=400)
        if len(display_name) > 60 or len(body) > 2000:
            return web.json_response({'error': 'too_long'}, status=400)
    except (ValueError, TypeError, AttributeError, KeyError):
        return web.json_response({'error': 'invalid_comment'}, status=400)
    async with _comment_lock:
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
        statuses = await core.query(lambda: core.db.table('comment_statuses').select('id').eq('code', 'published').limit(1))
        if not statuses:
            return web.json_response({'error': 'status_missing'}, status=500)
        rows = await core.query(lambda: core.db.table('comments').insert({
            'post_id': post_id, 'parent_id': parent_id,
            'display_name': display_name, 'body': body,
            'status': 'published', 'status_id': statuses[0]['id'],
            'author_telegram_id': u['telegram_id'],
        }))
        return web.json_response({'ok': True, 'id': rows[0]['id'] if rows else None})
