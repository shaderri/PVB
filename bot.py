import asyncio
import aiohttp
import logging
import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError
from flask import Flask, jsonify, request as flask_request
import pytz
from dotenv import load_dotenv

load_dotenv()

# Настройки бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
REQUIRED_CHANNEL = "@PlantsVsBrain"  # Обязательная подписка

# Supabase API
SUPABASE_URL = "https://vextbzatpprnksyutbcp.supabase.co/rest/v1/game_stock"
SUPABASE_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZleHRiemF0cHBybmtzeXV0YmNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTM4NjYzMTIsImV4cCI6MjA2OTQ0MjMxMn0.apcPdBL5o-t5jK68d9_r9C7m-8H81NQbTXK0EW0o800"

SEEDS_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.seeds&active=eq.true&order=created_at.desc"
GEAR_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.gear&active=eq.true&order=created_at.desc"
WEATHER_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.weather&active=eq.true&order=created_at.desc"

# Интервал проверки
CHECK_INTERVAL_MINUTES = 5
CHECK_DELAY_SECONDS = 15

# Cooldown для команд (10 секунд)
COMMAND_COOLDOWN = 10

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Погода
WEATHER_DATA = {
    "gold": {"emoji": "🌟", "name": "Золотая"},
    "diamond": {"emoji": "💎", "name": "Алмазная"},
    "frozen": {"emoji": "❄️", "name": "Ледяная"},
    "neon": {"emoji": "🌈", "name": "Неоновая"},
    "rainbow": {"emoji": "🌈", "name": "Радужная"},
    "upsidedown": {"emoji": "🙃", "name": "Перевёрнутая"},
    "underworld": {"emoji": "🔥", "name": "Подземная"},
    "magma": {"emoji": "🌋", "name": "Магма"},
    "galaxy": {"emoji": "🌌", "name": "Галактика"}
}

# Предметы
ITEMS_DATA = {
    "Cactus": {"emoji": "🌵", "price": "$200", "category": "seed"},
    "Strawberry": {"emoji": "🍓", "price": "$1,250", "category": "seed"},
    "Pumpkin": {"emoji": "🎃", "price": "$5,000", "category": "seed"},
    "Sunflower": {"emoji": "🌻", "price": "$25,000", "category": "seed"},
    "Dragon Fruit": {"emoji": "🐉", "price": "$100k", "category": "seed"},
    "Eggplant": {"emoji": "🍆", "price": "$250k", "category": "seed"},
    "Watermelon": {"emoji": "🍉", "price": "$1m", "category": "seed"},
    "Grape": {"emoji": "🍇", "price": "$2.5m", "category": "seed"},
    "Cocotank": {"emoji": "🥥", "price": "$5m", "category": "seed"},
    "Carnivorous Plant": {"emoji": "🪴", "price": "$25m", "category": "seed"},
    "Mr Carrot": {"emoji": "🥕", "price": "$50m", "category": "seed"},
    "Tomatrio": {"emoji": "🍅", "price": "$125m", "category": "seed"},
    "Shroombino": {"emoji": "🍄", "price": "$200m", "category": "seed"},
    "Mango": {"emoji": "🥭", "price": "$367m", "category": "seed"},
    "Bat": {"emoji": "🏏", "price": "Free", "category": "gear"},
    "Water Bucket": {"emoji": "🪣", "price": "$7,500", "category": "gear"},
    "Frost Grenade": {"emoji": "❄️", "price": "$12,500", "category": "gear"},
    "Banana Gun": {"emoji": "🍌", "price": "$25,000", "category": "gear"},
    "Frost Blower": {"emoji": "🌬️", "price": "$125,000", "category": "gear"},
    "Lucky Potion": {"emoji": "🍀", "price": "TBD", "category": "gear"},
    "Speed Potion": {"emoji": "⚡", "price": "TBD", "category": "gear"},
    "Carrot Launcher": {"emoji": "🥕", "price": "$500,000", "category": "gear"}
}

# Редкие предметы для канала
NOTIFICATION_ITEMS = ["Mr Carrot", "Tomatrio", "Shroombino", "Mango"]

last_stock_state: Dict[str, int] = {}
last_notification_time: Dict[str, datetime] = {}
NOTIFICATION_COOLDOWN = 300

# Cooldown для пользователей {user_id: {command: last_time}}
user_cooldowns: Dict[int, Dict[str, datetime]] = {}

# БД для автостоков
DB_FILE = "autostocks.db"
telegram_app: Optional[Application] = None


def init_database():
    """Инициализация SQLite БД для автостоков"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS autostocks (
            user_id INTEGER,
            item_name TEXT,
            PRIMARY KEY (user_id, item_name)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_user_id ON autostocks(user_id)
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")


def load_user_autostocks(user_id: int) -> Set[str]:
    """Загрузка автостоков конкретного пользователя"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT item_name FROM autostocks WHERE user_id = ?', (user_id,))
    items = {row[0] for row in cursor.fetchall()}
    conn.close()
    return items


def save_user_autostock(user_id: int, item_name: str):
    """Добавление предмета в автостоки"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO autostocks (user_id, item_name) VALUES (?, ?)', (user_id, item_name))
    conn.commit()
    conn.close()


def remove_user_autostock(user_id: int, item_name: str):
    """Удаление предмета из автостоков"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM autostocks WHERE user_id = ? AND item_name = ?', (user_id, item_name))
    conn.commit()
    conn.close()


def get_all_users_with_item(item_name: str) -> List[int]:
    """Получить всех пользователей, отслеживающих предмет"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT user_id FROM autostocks WHERE item_name = ?', (item_name,))
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


def check_command_cooldown(user_id: int, command: str) -> bool:
    """Проверка cooldown команды для пользователя"""
    if user_id not in user_cooldowns:
        user_cooldowns[user_id] = {}
    
    if command in user_cooldowns[user_id]:
        last_time = user_cooldowns[user_id][command]
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        if (now - last_time).total_seconds() < COMMAND_COOLDOWN:
            return False
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    user_cooldowns[user_id][command] = datetime.now(moscow_tz)
    return True


async def check_subscription(user_id: int, bot: Bot) -> bool:
    """Проверка подписки на обязательный канал"""
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError:
        return False


def get_next_check_time() -> datetime:
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    current_minute = now.minute
    next_minute = ((current_minute // CHECK_INTERVAL_MINUTES) + 1) * CHECK_INTERVAL_MINUTES
    
    if next_minute >= 60:
        next_check = now.replace(minute=0, second=CHECK_DELAY_SECONDS, microsecond=0) + timedelta(hours=1)
    else:
        next_check = now.replace(minute=next_minute, second=CHECK_DELAY_SECONDS, microsecond=0)
    
    if next_check <= now:
        next_check += timedelta(minutes=CHECK_INTERVAL_MINUTES)
    
    return next_check


def calculate_sleep_time() -> float:
    next_check = get_next_check_time()
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    sleep_seconds = (next_check - now).total_seconds()
    return max(sleep_seconds, 0)


class StockTracker:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_running = False

    async def init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_supabase_api(self, url: str) -> Optional[List[Dict]]:
        try:
            await self.init_session()
            headers = {
                "apikey": SUPABASE_API_KEY,
                "Authorization": f"Bearer {SUPABASE_API_KEY}"
            }
            
            async with self.session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            logger.error(f"❌ Ошибка API: {e}")
            return None

    async def fetch_stock(self) -> Optional[Dict]:
        try:
            seeds_data, gear_data = await asyncio.gather(
                self.fetch_supabase_api(SEEDS_API_URL),
                self.fetch_supabase_api(GEAR_API_URL)
            )
            
            if seeds_data is None and gear_data is None:
                return None
            
            combined_data = []
            if seeds_data:
                combined_data.extend(seeds_data)
            if gear_data:
                combined_data.extend(gear_data)
            
            return {"data": combined_data}
        except Exception as e:
            logger.error(f"❌ Ошибка fetch_stock: {e}")
            return None

    async def fetch_weather(self) -> Optional[Dict]:
        try:
            weather_data = await self.fetch_supabase_api(WEATHER_API_URL)
            if weather_data and len(weather_data) > 0:
                return weather_data[0]
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка fetch_weather: {e}")
            return None

    def format_weather_message(self, weather_data: Optional[Dict]) -> str:
        if not weather_data:
            return "🌤️ *ПОГОДА В ИГРЕ*\n\n_Сейчас обычная погода_"
        
        weather_id = weather_data.get('item_id', '')
        ends_at_str = weather_data.get('ends_at', '')
        
        weather_info = WEATHER_DATA.get(weather_id, {"emoji": "🌤️", "name": "Неизвестная"})
        emoji = weather_info['emoji']
        name = weather_info['name']
        
        try:
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(moscow_tz)
            
            if ends_at_str:
                ends_at = datetime.fromisoformat(ends_at_str.replace('Z', '+00:00'))
                ends_at_msk = ends_at.astimezone(moscow_tz)
                
                if ends_at_msk > current_time:
                    time_left = ends_at_msk - current_time
                    minutes_left = int(time_left.total_seconds() / 60)
                    ends_time = ends_at_msk.strftime("%H:%M")
                    
                    return (
                        f"🌤️ *ПОГОДА В ИГРЕ*\n\n"
                        f"{emoji} *{name} погода*\n\n"
                        f"⏰ Закончится: {ends_time} МСК\n"
                        f"⏳ Осталось: ~{minutes_left} мин"
                    )
                else:
                    return "🌤️ *ПОГОДА В ИГРЕ*\n\n_Сейчас обычная погода_"
            else:
                return (
                    f"🌤️ *ПОГОДА В ИГРЕ*\n\n"
                    f"{emoji} *{name} погода*\n\n"
                    f"_Время окончания неизвестно_"
                )
        except Exception as e:
            logger.error(f"Ошибка форматирования погоды: {e}")
            return f"🌤️ *ПОГОДА В ИГРЕ*\n\n{emoji} *{name} погода*"

    def format_stock_message(self, stock_data: Dict) -> str:
        if not stock_data or 'data' not in stock_data:
            return "❌ *Не удалось получить данные о стоке*"

        seeds = []
        gear = []

        for item in stock_data['data']:
            display_name = item.get('display_name', '')
            multiplier = item.get('multiplier', 0)
            item_type = item.get('type', '')

            if not display_name or multiplier == 0:
                continue

            item_info = ITEMS_DATA.get(display_name, {"emoji": "📦", "price": "Unknown"})
            formatted_item = f"{item_info['emoji']} *{display_name}*: x{multiplier} ({item_info['price']})"

            if item_type == 'seeds':
                seeds.append(formatted_item)
            elif item_type == 'gear':
                gear.append(formatted_item)

        message = "📊 *ТЕКУЩИЙ СТОК*\n\n"
        message += "🌱 *СЕМЕНА:*\n" + ("\n".join(seeds) if seeds else "_Пусто_") + "\n\n"
        message += "⚔️ *СНАРЯЖЕНИЕ:*\n" + ("\n".join(gear) if gear else "_Пусто_") + "\n\n"

        try:
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(moscow_tz).strftime("%H:%M:%S")
        except:
            current_time = datetime.utcnow().strftime("%H:%M:%S")
        
        message += f"🕒 _Обновлено: {current_time} МСК_"
        return message

    def can_send_notification(self, item_name: str) -> bool:
        if item_name not in last_notification_time:
            return True
        
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        last_time = last_notification_time[item_name]
        return (now - last_time).total_seconds() >= NOTIFICATION_COOLDOWN

    async def check_for_notifications(self, stock_data: Dict, bot: Bot, channel_id: str):
        global last_stock_state
        if not stock_data or 'data' not in stock_data or not channel_id:
            return

        current_stock = {}
        for item in stock_data['data']:
            display_name = item.get('display_name', '')
            multiplier = item.get('multiplier', 0)
            if display_name and multiplier > 0:
                current_stock[display_name] = multiplier

        for item_name in NOTIFICATION_ITEMS:
            current_count = current_stock.get(item_name, 0)
            previous_count = last_stock_state.get(item_name, 0)
            
            should_notify = False
            if current_count > 0 and (previous_count == 0 or current_count > previous_count):
                should_notify = True
            
            if should_notify and self.can_send_notification(item_name):
                await self.send_notification(bot, channel_id, item_name, current_count)

        last_stock_state = current_stock.copy()

    async def check_user_autostocks(self, stock_data: Dict, bot: Bot):
        """Оптимизированная проверка автостоков"""
        if not stock_data or 'data' not in stock_data:
            return

        current_stock = {}
        for item in stock_data['data']:
            display_name = item.get('display_name', '')
            multiplier = item.get('multiplier', 0)
            if display_name and multiplier > 0:
                current_stock[display_name] = multiplier

        # Получаем пользователей только для предметов в стоке
        tasks = []
        for item_name in current_stock.keys():
            users = get_all_users_with_item(item_name)
            for user_id in users:
                tasks.append(self.send_autostock_notification(bot, user_id, item_name, current_stock[item_name]))
        
        # Отправляем все уведомления параллельно
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_notification(self, bot: Bot, channel_id: str, item_name: str, count: int):
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(moscow_tz).strftime("%H:%M:%S")

            message = (
                f"🚨 *РЕДКИЙ ПРЕДМЕТ В СТОКЕ!* 🚨\n\n"
                f"{item_info['emoji']} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {item_info['price']}\n"
                f"🕒 {current_time} МСК"
            )

            await bot.send_message(chat_id=channel_id, text=message, parse_mode=ParseMode.MARKDOWN)
            last_notification_time[item_name] = datetime.now(moscow_tz)
            logger.info(f"✅ Уведомление: {item_name} x{count}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")

    async def send_autostock_notification(self, bot: Bot, user_id: int, item_name: str, count: int):
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(moscow_tz).strftime("%H:%M:%S")

            message = (
                f"🔔 *АВТОСТОК - {item_name}!*\n\n"
                f"{item_info['emoji']} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {item_info['price']}\n"
                f"🕒 {current_time} МСК"
            )

            await bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            # Пользователь заблокировал бота - ничего не логируем
            pass


tracker = StockTracker()


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    user_id = update.effective_user.id
    
    # Проверка cooldown
    if not check_command_cooldown(user_id, 'stock'):
        return
    
    # Проверка подписки только в ЛС
    if update.effective_chat.type == ChatType.PRIVATE:
        if not await check_subscription(user_id, context.bot):
            keyboard = [[InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")]]
            await update.effective_message.reply_text(
                f"⚠️ Для использования бота подпишитесь на {REQUIRED_CHANNEL}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    
    stock_data = await tracker.fetch_stock()
    message = tracker.format_stock_message(stock_data)
    await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    user_id = update.effective_user.id
    
    if not check_command_cooldown(user_id, 'weather'):
        return
    
    if update.effective_chat.type == ChatType.PRIVATE:
        if not await check_subscription(user_id, context.bot):
            keyboard = [[InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")]]
            await update.effective_message.reply_text(
                f"⚠️ Для использования бота подпишитесь на {REQUIRED_CHANNEL}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    
    weather_data = await tracker.fetch_weather()
    message = tracker.format_weather_message(weather_data)
    await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def autostock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    # Только в ЛС
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    
    user_id = update.effective_user.id
    
    if not check_command_cooldown(user_id, 'autostock'):
        await update.effective_message.reply_text("⏳ Подождите 10 секунд перед следующим запросом")
        return
    
    # Проверка подписки
    if not await check_subscription(user_id, context.bot):
        keyboard = [[InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")]]
        await update.effective_message.reply_text(
            f"⚠️ Для использования автостоков подпишитесь на {REQUIRED_CHANNEL}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    keyboard = [
        [InlineKeyboardButton("🌱 Семена", callback_data="as_seeds")],
        [InlineKeyboardButton("⚔️ Снаряжение", callback_data="as_gear")],
        [InlineKeyboardButton("📋 Мои автостоки", callback_data="as_list")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        "🔔 *УПРАВЛЕНИЕ АВТОСТОКАМИ*\n\n"
        "Выберите категорию предметов.\n"
        "⏰ Проверка: каждые 5 минут в :15 секунд"
    )
    
    await update.effective_message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


async def autostock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "as_seeds":
        user_items = load_user_autostocks(user_id)
        keyboard = []
        for item_name, item_info in ITEMS_DATA.items():
            if item_info['category'] == 'seed':
                is_tracking = item_name in user_items
                status = "✅" if is_tracking else "➕"
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name}",
                    callback_data=f"t_seed_{item_name}"
                )])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🌱 *СЕМЕНА*\n\nВыберите предметы:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "as_gear":
        user_items = load_user_autostocks(user_id)
        keyboard = []
        for item_name, item_info in ITEMS_DATA.items():
            if item_info['category'] == 'gear':
                is_tracking = item_name in user_items
                status = "✅" if is_tracking else "➕"
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name}",
                    callback_data=f"t_gear_{item_name}"
                )])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("⚔️ *СНАРЯЖЕНИЕ*\n\nВыберите предметы:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "as_list":
        user_items = load_user_autostocks(user_id)
        if not user_items:
            message = "📋 *МОИ АВТОСТОКИ*\n\n_Нет отслеживаемых предметов_"
        else:
            items_list = []
            for item_name in user_items:
                item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
                items_list.append(f"{item_info['emoji']} *{item_name}* ({item_info['price']})")
            message = f"📋 *МОИ АВТОСТОКИ*\n\n" + "\n".join(items_list)
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="as_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "as_back":
        keyboard = [
            [InlineKeyboardButton("🌱 Семена", callback_data="as_seeds")],
            [InlineKeyboardButton("⚔️ Снаряжение", callback_data="as_gear")],
            [InlineKeyboardButton("📋 Мои автостоки", callback_data="as_list")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = "🔔 *УПРАВЛЕНИЕ АВТОСТОКАМИ*\n\nВыберите категорию."
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data.startswith("t_seed_") or data.startswith("t_gear_"):
        # Определяем категорию и имя предмета
        if data.startswith("t_seed_"):
            item_name = data.replace("t_seed_", "")
            category = "seed"
        else:
            item_name = data.replace("t_gear_", "")
            category = "gear"
        
        user_items = load_user_autostocks(user_id)
        
        if item_name in user_items:
            remove_user_autostock(user_id, item_name)
        else:
            save_user_autostock(user_id, item_name)
        
        # Обновляем клавиатуру
        user_items = load_user_autostocks(user_id)
        keyboard = []
        for name, info in ITEMS_DATA.items():
            if info['category'] == category:
                is_tracking = name in user_items
                status = "✅" if is_tracking else "➕"
                callback_prefix = "t_seed_" if category == "seed" else "t_gear_"
                keyboard.append([InlineKeyboardButton(
                    f"{status} {info['emoji']} {name}",
                    callback_data=f"{callback_prefix}{name}"
                )])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        except:
            pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    
    channel_info = f"📢 Канал: {CHANNEL_ID}" if CHANNEL_ID else ""
    welcome_message = (
        "👋 *Plants vs Brainrots Stock Tracker!*\n\n"
        "📊 /stock - Текущий сток\n"
        "🌤️ /weather - Погода в игре\n"
        "🔔 /autostock - Автостоки\n"
        "❓ /help - Справка\n\n"
        f"{channel_info}\n"
        "📦 *Редкие предметы:*\n"
        "• 🥕 Mr Carrot\n"
        "• 🍅 Tomatrio\n"
        "• 🍄 Shroombino\n"
        "• 🥭 Mango"
    )
    await update.effective_message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    
    help_message = (
        "📚 *КОМАНДЫ:*\n\n"
        "/start - Информация\n"
        "/stock - Текущий сток\n"
        "/weather - Погода в игре\n"
        "/autostock - Настроить автостоки (только в ЛС)\n"
        "/help - Справка\n\n"
        "⏰ Проверка каждые 5 минут в :15 секунд\n"
        f"📢 Обязательная подписка: {REQUIRED_CHANNEL}"
    )
    await update.effective_message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN)


async def periodic_stock_check(application: Application):
    if tracker.is_running:
        return
    
    tracker.is_running = True
    moscow_tz = pytz.timezone('Europe/Moscow')
    
    logger.info("🚀 Периодическая проверка запущена")
    
    initial_sleep = calculate_sleep_time()
    await asyncio.sleep(initial_sleep)

    while tracker.is_running:
        try:
            now = datetime.now(moscow_tz)
            logger.info(f"🔍 Проверка - {now.strftime('%H:%M:%S')}")
            
            stock_data = await tracker.fetch_stock()
            
            if stock_data:
                if CHANNEL_ID:
                    await tracker.check_for_notifications(stock_data, application.bot, CHANNEL_ID)
                await tracker.check_user_autostocks(stock_data, application.bot)
            
            sleep_time = calculate_sleep_time()
            await asyncio.sleep(sleep_time)
        except Exception as e:
            logger.error(f"❌ Ошибка проверки: {e}")
            await asyncio.sleep(calculate_sleep_time())


async def post_init(application: Application):
    init_database()
    asyncio.create_task(periodic_stock_check(application))


# Flask
flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET", "HEAD"])
@flask_app.route("/ping", methods=["GET", "HEAD"])
def ping():
    if flask_request.method == "HEAD":
        return "", 200
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    next_check = get_next_check_time()
    
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "moscow_time": now.strftime("%H:%M:%S"),
        "next_check": next_check.strftime("%H:%M:%S"),
        "bot": "PVB Stock Tracker",
        "is_running": tracker.is_running
    }), 200


@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "running": tracker.is_running}), 200


def main():
    logger.info("="*60)
    logger.info("🌱 Plants vs Brainrots Stock Tracker Bot")
    logger.info("="*60)

    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("stock", stock_command))
    telegram_app.add_handler(CommandHandler("weather", weather_command))
    telegram_app.add_handler(CommandHandler("autostock", autostock_command))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CallbackQueryHandler(autostock_callback))

    telegram_app.post_init = post_init

    async def shutdown_callback(app: Application):
        logger.info("🛑 Остановка бота")
        tracker.is_running = False
        try:
            await tracker.close_session()
        except Exception as e:
            logger.error(f"❌ Ошибка закрытия: {e}")

    telegram_app.post_shutdown = shutdown_callback

    logger.info("🔄 Режим: Polling")
    
    # Flask в отдельном потоке
    import threading
    
    def run_flask_server():
        port = int(os.getenv("PORT", "5000"))
        logger.info(f"🚀 Flask запущен на порту {port}")
        import logging as flask_logging
        flask_log = flask_logging.getLogger('werkzeug')
        flask_log.setLevel(flask_logging.ERROR)
        flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    logger.info("🚀 Бот запущен!")
    logger.info("="*60)
    telegram_app.run_polling(allowed_updates=None, drop_pending_updates=True)


if __name__ == "__main__":
    main()