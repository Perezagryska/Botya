import asyncio
import time
import sqlite3
import re
import random
import string
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, ChatPermissions
from aiogram.client.default import DefaultBotProperties

# --- НАСТРОЙКИ ---
API_TOKEN = '7473076554:AAFW6FFt9NTo-MK18xCQNgZN96ZaCP5Jbis'

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect('bot_data.db')
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
cursor.execute('CREATE TABLE IF NOT EXISTS warn_stats (user_id INTEGER PRIMARY KEY, mute_count INTEGER DEFAULT 0)')
cursor.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
cursor.execute('CREATE TABLE IF NOT EXISTS links (chat_id INTEGER PRIMARY KEY, url TEXT)')
conn.commit()

# --- СОСТОЯНИЯ ---
class AdminStates(StatesGroup):
    adding_admin = State()
    setting_ban_period = State()
    linking_url = State()
    waiting_code = State()

is_cleaning = {} 
user_messages = {}
pending_links = {} # Временное хранилище для кодов подтверждения

# --- ФУНКЦИИ ПРОВЕРКИ ---
async def get_group_owner(chat_id: int):
    try:
        admins_list = await bot.get_chat_administrators(chat_id)
        for admin in admins_list:
            if admin.status == "creator":
                return admin.user.id
    except: return None
    return None

async def is_user_admin(chat_id: int, user_id: int):
    owner_id = await get_group_owner(chat_id)
    cursor.execute('SELECT user_id FROM admins')
    db_admins = {row[0] for row in cursor.fetchall()}
    if user_id == owner_id or user_id in db_admins:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except: return False

# --- ОБРАБОТЧИК СТАРТА И ПРИВЕТСТВИЯ ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer("👋 Привет! Это бот по имени <b>Ботя</b>. Сделан как помощник админам, напиши меню или /menu")

@dp.message(F.new_chat_members)
async def greeter(msg: types.Message):
    # Проверка режима бана новеньких
    cursor.execute('SELECT value FROM settings WHERE key = "ban_period_end"')
    res = cursor.fetchone()
    if res and time.time() < float(res[0]):
        for u in msg.new_chat_members:
            try: await bot.ban_chat_member(msg.chat.id, u.id)
            except: pass
        return
    for u in msg.new_chat_members:
        await msg.answer(f"👋 Приветствую новый пользователь, {u.full_name}, меня зовут <b>Ботя</b>. Чтобы пользоваться мной пиши Меню или /menu")

# --- КОМАНДА РАЗМУТА ---
@dp.message(F.text.regexp(r"(?i)^Ботя\s+размут"))
async def un_mute_handler(msg: types.Message):
    if not await is_user_admin(msg.chat.id, msg.from_user.id): return
    
    target_id = None
    if msg.reply_to_message:
        target_id = msg.reply_to_message.from_user.id
    else:
        parts = msg.text.split()
        if len(parts) > 2:
            raw_id = parts[2].replace("@", "")
            if raw_id.isdigit(): target_id = int(raw_id)
            else: await msg.answer("Укажите корректный ID или ответьте на сообщение."); return

    if target_id:
        try:
            await bot.restrict_chat_member(
                msg.chat.id, target_id, 
                permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True, can_send_photos=True, can_send_videos=True),
                until_date=0
            )
            await msg.answer(f"Пользователь {target_id} размучен.")
        except: await msg.answer("Не удалось размутить пользователя.")

# --- ЧИСТКА ---
@dp.message(F.text.regexp(r"(?i)Ботя,?\s+Чистка"))
async def start_cleaning(msg: types.Message):
    if not await is_user_admin(msg.chat.id, msg.from_user.id): return
    is_cleaning[msg.chat.id] = True
    await msg.answer("<b>Внимание чистка, всем приказано молчать!</b>")
    await bot.set_chat_permissions(msg.chat.id, ChatPermissions(can_send_messages=False))

@dp.message(F.text.regexp(r"(?i)Ботя,?\s+закончилась"))
async def stop_cleaning(msg: types.Message):
    if not await is_user_admin(msg.chat.id, msg.from_user.id): return
    is_cleaning[msg.chat.id] = False
    await msg.answer("<b>Чистка закончилась, теперь всем разрешено писать.</b>")
    await bot.set_chat_permissions(msg.chat.id, ChatPermissions(can_send_messages=True, can_send_photos=True, can_send_videos=True, can_send_other_messages=True, can_add_web_page_previews=True))

# --- ОСНОВНОЙ ОБРАБОТЧИК ---
@dp.message()
async def main_handler(msg: types.Message, state: FSMContext):
    uid, cid = msg.from_user.id, msg.chat.id
    
    # Проверка кодового слова для привязки ссылки
    if msg.text in pending_links:
        owner_id = await get_group_owner(cid)
        if uid == owner_id:
            data = pending_links.pop(msg.text)
            cursor.execute('INSERT OR REPLACE INTO links (chat_id, url) VALUES (?, ?)', (cid, data['url']))
            conn.commit()
            await msg.answer(f"Чат успешно привязан! Ссылка: {data['url']}")
            return

    if is_cleaning.get(cid) and not await is_user_admin(cid, uid):
        try: await msg.delete()
        except: pass
        return

    # Быстрый Антиспам
    if not await is_user_admin(cid, uid):
        if not (msg.media_group_id or msg.forward_date):
            now = time.time()
            user_messages.setdefault(uid, [])
            user_messages[uid] = [t for t in user_messages[uid] if now - t < 2] # Интервал 2 сек
            user_messages[uid].append(now)
            
            if len(user_messages[uid]) >= 4: # Реагирует быстрее (4 сообщения)
                try:
                    await bot.restrict_chat_member(cid, uid, permissions=ChatPermissions(can_send_messages=False), until_date=int(time.time() + 900))
                    await msg.answer(f"Пользователь {msg.from_user.first_name} замучен за быстрый спам.")
                    user_messages[uid] = []
                except: pass

    # Команды меню
    if msg.text:
        text_lower = msg.text.lower()
        owner_id = await get_group_owner(cid)
        
        if (msg.text == "/SM" or msg.text.startswith("/SM@")) and uid == owner_id:
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="Добавить админа", callback_data="add_adm"))
            kb.row(InlineKeyboardButton(text="Забанить срок", callback_data="set_ban_period"))
            kb.row(InlineKeyboardButton(text="Добавить связку", callback_data="add_link"))
            await msg.answer("Меню владельца группы:", reply_markup=kb.as_markup())
        
        elif text_lower in ["меню", "/menu"] or text_lower.startswith("/menu@"):
            kb = InlineKeyboardBuilder()
            cursor.execute('SELECT url FROM links WHERE chat_id = ?', (cid,))
            link_data = cursor.fetchone()
            if link_data:
                kb.row(InlineKeyboardButton(text="Связанные ссылки", url=link_data[0]))
            kb.row(InlineKeyboardButton(text="Позвать админа", callback_data="call_adm"))
            kb.row(InlineKeyboardButton(text="Версия Боти", callback_data="Version"))
            await msg.answer("Меню Боти:", reply_markup=kb.as_markup())

# --- CALLBACKS ---
@dp.callback_query(F.data == "Version")
async def show_version(call: types.CallbackQuery):
    await call.message.answer("Сейчас стоит версия 1.6V")
    await call.answer()

@dp.callback_query(F.data == "call_adm")
async def call_admin_btn(call: types.CallbackQuery):
    owner_id = await get_group_owner(call.message.chat.id)
    await call.message.answer(f"Владелец <a href='tg://user?id={owner_id}'>группы</a>, вас зовут!")
    await call.answer()

@dp.callback_query(F.data == "add_link")
async def add_link_init(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Пришлите ссылку, которую хотите привязать:")
    await state.set_state(AdminStates.linking_url)
    await call.answer()

@dp.message(AdminStates.linking_url)
async def process_link(msg: types.Message, state: FSMContext):
    code = "BOTA_" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    pending_links[code] = {'url': msg.text, 'owner': msg.from_user.id}
    await msg.answer(f"Ссылка получена. Теперь напиши в чате, куда нужно привязать ссылку, это кодовое слово:\n<code>{code}</code>")
    await state.clear()

# (Остальные хендлеры для админов и бана срока остаются как в 1.1V)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
