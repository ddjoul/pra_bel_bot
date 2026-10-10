import asyncio
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parent / '.env', encoding='utf-8-sig')
missing = [k for k in ('BOT_TOKEN','SUPABASE_URL','SUPABASE_SECRET_KEY') if not os.getenv(k,'').strip()]
if missing:
    raise SystemExit('Запоўні .env побач з bot.py. Адсутнічаюць: ' + ', '.join(missing))
bot = Bot(os.environ['BOT_TOKEN'].strip())
db = create_client(os.environ['SUPABASE_URL'].strip(), os.environ['SUPABASE_SECRET_KEY'].strip())
dp = Dispatcher()
db_lock = asyncio.Lock()
locks = defaultdict(asyncio.Lock)
languages = []
main_menu = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text='новы твор'),KeyboardButton(text='мой ID')],
    [KeyboardButton(text='скасаваць')]], resize_keyboard=True, is_persistent=True,
    one_time_keyboard=False, input_field_placeholder='напішы паведамленне або абяры дзеянне')

async def query(build):
    async with db_lock:
        return await asyncio.to_thread(lambda: build().execute().data)

def now():
    return datetime.now(timezone.utc).isoformat()

async def register(user):
    rows = await query(lambda: db.table('telegram_users').select('*').eq('telegram_id',user.id))
    if rows:
        await query(lambda: db.table('telegram_users').update({'username':user.username}).eq('telegram_id',user.id))
    else:
        await query(lambda: db.table('telegram_users').insert({'telegram_id':user.id,'username':user.username}))

async def get_draft(uid):
    rows = await query(lambda: db.table('telegram_drafts').select('*').eq('telegram_id',uid))
    return rows[0] if rows else None

async def new_draft(uid):
    d = {'telegram_id':uid,'id':str(uuid.uuid4()),'phase':'kind','payload':{},'result':None,'updated_at':now()}
    await query(lambda: db.table('telegram_drafts').upsert(d,on_conflict='telegram_id'))
    return d

async def save(d):
    await query(lambda: db.table('telegram_drafts').update({'phase':d['phase'],'payload':d['payload'],'updated_at':now()}).eq('telegram_id',d['telegram_id']).eq('id',d['id']))

def keyboard(d,options):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t,callback_data=f"{d['id'][:8]}:{a}")] for t,a in options])

def lang_name(code):
    return next((x['name'] for x in languages if x['code']==code),code)

async def review_or_name(d):
    if d['payload'].get('anonymous'):
        d['phase']='review'
        return
    rows=await query(lambda: db.table('telegram_users').select('author_id,display_name').eq('telegram_id',d['telegram_id']))
    u=rows[0]
    d['phase']='review' if u['author_id'] or u['display_name'] else 'name'

async def prompt(chat,d):
    phase,p=d['phase'],d['payload']
    options=[]
    if phase=='kind':
        text='што хочаш даслаць?'
        options=[('верш','kind_poem'),('пераклад','kind_translation')]
    elif phase in ('language','original_language'):
        text=('абяры мову арыгіналу:' if phase=='original_language' else
              'на якую мову зроблены пераклад?' if p['category']=='translation' else 'абяры мову верша:')
        options=[(x['name'],'lang_'+x['code']) for x in languages]
    elif phase=='original_author':
        text='напішы імя аўтара арыгіналу або націсні «невядомы аўтар».'
        options=[('невядомы аўтар','unknown_author')]
    elif phase=='title':
        text='напішы назву твора або націсні «без назвы».'
        options=[('без назвы','untitled')]
    elif phase=='body':
        text='дашлі тэкст перакладу.' if p['category']=='translation' else 'дашлі тэкст верша.'
        text+='\nможна некалькімі паведамленнямі. захоўвай радкі і строфы.\nпасля націсні «гатова».'
        options=[('гатова','done')]
    elif phase=='attribution':
        text='як апублікаваць твор?'
        options=[('з маім аўтарствам','attribution_named'),('застацца ананімным','attribution_anonymous')]
    elif phase=='name':
        text=('як падпісаць твой твор? напішы імя або псеўданім (да 100 знакаў).'
              '\nкалі ў цябе ўжо ёсць профіль на сайце, папрасі адміністратара прывязаць яго праз /id перад адпраўкай.')
    elif phase=='review':
        text=('правер перад адпраўкай:\n\nтып: '+('верш' if p['category']=='poem' else 'пераклад')
              +'\nмова: '+lang_name(p['language_code'])+'\nназва: '+p['title'])
        if p['category']=='translation':
            text+='\nмова арыгіналу: '+lang_name(p['original_language'])+'\nаўтар арыгіналу: '+p['original_author']
        text+='\nаўтарства: '+('ананімна' if p.get('anonymous') else 'з маім аўтарствам')
        text+='\n\nтэкст ніжэй. для змянення пачні нанова праз /new.'
        await bot.send_message(chat,text)
        for i in range(0,len(p['body']),1800):
            await bot.send_message(chat,p['body'][i:i+1800])
        await bot.send_message(chat,'адправіць твор?',reply_markup=keyboard(d,[('адправіць','send'),('скасаваць','cancel')]))
        return
    elif phase=='sent':
        text='твор апублікаваны.' if d['result']['status']=='published' else 'твор адпраўлены на праверку.'
        text+='\nID: '+d['result']['post_id']+'\nновы твор: /new'
    else:
        text='адпраўка скасавана. новы твор: /new'
    if phase not in ('sent','cancelled'):
        options.append(('скасаваць','cancel'))
    await bot.send_message(chat,text,reply_markup=keyboard(d,options) if options else main_menu)

@dp.message(CommandStart())
async def start(m:Message):
    if m.chat.type!='private':
        return
    async with locks[m.from_user.id]:
        await register(m.from_user)
        await m.answer('вітаем у пра.бел. меню — унізе чата.',reply_markup=main_menu)
        d=await get_draft(m.from_user.id)
        await prompt(m.chat.id,d or await new_draft(m.from_user.id))

@dp.message(Command('new'))
@dp.message(F.text=='новы твор')
async def new(m:Message):
    if m.chat.type!='private':
        return
    async with locks[m.from_user.id]:
        await register(m.from_user)
        d=await get_draft(m.from_user.id)
        if d and d['phase'] not in ('sent','cancelled'):
            await m.answer('ёсць незавершаны твор. пачаць нанова і выдаліць гэты чарнавік?',reply_markup=keyboard(d,[('так, пачаць нанова','restart'),('працягнуць','resume')]))
            return
        await m.answer('пачынаем новы твор.',reply_markup=main_menu)
        await prompt(m.chat.id,await new_draft(m.from_user.id))

@dp.message(Command('id'))
@dp.message(F.text=='мой ID')
async def identity(m:Message):
    if m.chat.type!='private':
        return
    async with locks[m.from_user.id]:
        await register(m.from_user)
        await m.answer(f'твой Telegram ID: {m.from_user.id}\nдашлі яго адміністратару для прывязкі профілю.',reply_markup=main_menu)

@dp.message(Command('cancel'))
@dp.message(F.text=='скасаваць')
async def cancel(m:Message):
    if m.chat.type!='private':
        return
    async with locks[m.from_user.id]:
        d=await get_draft(m.from_user.id)
        if d and d['phase'] not in ('sent','cancelled'):
            d['phase']='cancelled'
            await save(d)
        await m.answer('скасавана. новы твор: /new',reply_markup=main_menu)

@dp.callback_query(F.data)
async def callback(c:CallbackQuery):
    await c.answer()
    if not c.message or c.message.chat.type!='private':
        return
    async with locks[c.from_user.id]:
        d=await get_draft(c.from_user.id)
        token,sep,action=c.data.partition(':')
        if not d or not sep or token!=d['id'][:8]:
            await bot.send_message(c.from_user.id,'гэта кнопка ўжо неактуальная. /start')
            return
        phase,p=d['phase'],d['payload']
        if action=='send' and phase in ('review','sent'):
            await query(lambda: db.rpc('telegram_submit',{'p_telegram_id':c.from_user.id,'p_draft_id':d['id']}))
            await prompt(c.from_user.id,await get_draft(c.from_user.id))
            return
        if phase in ('sent','cancelled'):
            await prompt(c.from_user.id,d)
            return
        if action=='restart':
            await bot.send_message(c.from_user.id,'пачынаем нанова.',reply_markup=main_menu)
            await prompt(c.from_user.id,await new_draft(c.from_user.id))
            return
        if action=='resume':
            await prompt(c.from_user.id,d)
            return
        if action=='cancel':
            d['phase']='cancelled'
        elif phase=='kind' and action in ('kind_poem','kind_translation'):
            p['category']=action.removeprefix('kind_')
            d['phase']='language' if p['category']=='poem' else 'original_language'
        elif phase in ('language','original_language') and action.startswith('lang_'):
            value=action.removeprefix('lang_')
            if value not in {x['code'] for x in languages}:
                return
            p['original_language' if phase=='original_language' else 'language_code']=value
            d['phase']=('language' if phase=='original_language' else 'original_author' if p['category']=='translation' else 'title')
        elif phase=='original_author' and action=='unknown_author':
            p['original_author']='невядомы аўтар'
            d['phase']='title'
        elif phase=='title' and action=='untitled':
            p['title']='***'
            d['phase']='body'
        elif phase=='body' and action=='done':
            if not p.get('body','').strip():
                await bot.send_message(c.from_user.id,'спачатку дашлі тэкст.')
                return
            d['phase']='attribution'
        elif phase=='attribution' and action in ('attribution_named','attribution_anonymous'):
            p['anonymous']=action=='attribution_anonymous'
            await review_or_name(d)
        else:
            await bot.send_message(c.from_user.id,'гэта кнопка ўжо неактуальная. /start')
            return
        await save(d)
        await prompt(c.from_user.id,d)

@dp.message()
async def text(m:Message):
    if m.chat.type!='private':
        return
    async with locks[m.from_user.id]:
        d=await get_draft(m.from_user.id)
        if not d:
            await m.answer('пачні з /start',reply_markup=main_menu)
            return
        if not m.text or not m.text.strip():
            await m.answer('дашлі тэкставае паведамленне.')
            return
        if m.text.startswith('/'):
            await m.answer('каманды: /start, /new, /id, /cancel',reply_markup=main_menu)
            return
        phase,p=d['phase'],d['payload']
        if phase in ('title','original_author','name'):
            value=m.text.strip()
            limit=100 if phase=='name' else 200
            if len(value)>limit:
                await m.answer(f'максімум {limit} знакаў.')
                return
            if phase=='name':
                await query(lambda: db.table('telegram_users').update({'display_name':value}).eq('telegram_id',m.from_user.id))
                d['phase']='review'
            else:
                p[phase]=value
                d['phase']='body' if phase=='title' else 'title'
        elif phase=='body':
            old=p.get('body','')
            value=(old+'\n\n' if old else '')+m.text
            if len(value)>60000:
                await m.answer('максімум 60000 знакаў на твор. гэта частка не дададзена.')
                return
            p['body']=value
            await save(d)
            await m.answer('частка захавана. дашлі наступную або націсні «гатова».',reply_markup=keyboard(d,[('гатова','done'),('скасаваць','cancel')]))
            return
        else:
            await prompt(m.chat.id,d)
            return
        await save(d)
        await prompt(m.chat.id,d)

@dp.errors()
async def error(event):
    logging.error('Handler failed: %s',type(event.exception).__name__)
    u=event.update
    uid=u.message.from_user.id if u.message else u.callback_query.from_user.id if u.callback_query else None
    if uid:
        try:
            await bot.send_message(uid,'не ўдалося выканаць дзеянне. паспрабуй яшчэ раз або /start. калі памылка паўтараецца, звярніся да адміністратара.')
        except Exception:
            pass
    return True

async def main():
    global languages
    logging.basicConfig(level=logging.WARNING)
    languages=await query(lambda: db.table('languages').select('id,code,name').order('sort_order'))
    if not languages:
        raise RuntimeError('No languages configured')
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot,handle_as_tasks=False,allowed_updates=['message','callback_query'])

if __name__=='__main__':
    asyncio.run(main())