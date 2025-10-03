import asyncio
import aiohttp
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from flask import Flask, jsonify, request as flask_request
import pytz
from dotenv import load_dotenv

load_dotenv()

# Настройки бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Supabase API настройки
SUPABASE_URL = "https://vextbzatpprnksyutbcp.supabase.co/rest/v1/game_stock"
SUPABASE_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZleHRiemF0cHBybmtzeXV0YmNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTM4NjYzMTIsImV4cCI6MjA2OTQ0MjMxMn0.apcPdBL5o-t5jK68d9_r9C7m-8H81NQbTXK0EW0o800"

SEEDS_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.seeds&active=eq.true&order=created_at.desc"
GEAR_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.gear&active=eq.true&order=created_at.desc"
WEATHER_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.weather&active=eq.true&order=created_at.desc"

# Интервал проверки: каждые 5 минут + 15 секунд
CHECK_INTERVAL_MINUTES = 5
CHECK_DELAY_SECONDS = 15

# Тестовый пользователь для автостоков
TEST_USER_ID = 7177110883

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения!")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Погода с эмодзи
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

# Данные о предметах
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
    "Bat": {"emoji": "🏏", "price": "Free", "category": "gear"},
    "Water Bucket": {"emoji": "🪣", "price": "$7,500", "category": "gear"},
    "Frost Grenade": {"emoji": "❄️", "price": "$12,500", "category": "gear"},
    "Banana Gun": {"emoji": "🍌", "price": "$25,000", "category": "gear"},
    "Frost Blower": {"emoji": "🌬️", "price": "$125,000", "category": "gear"},
    "Lucky Potion": {"emoji": "🍀", "price": "TBD", "category": "gear"},
    "Speed Potion": {"emoji": "⚡", "price": "TBD", "category": "gear"},
    "Carrot Launcher": {"emoji": "🥕", "price": "$500,000", "category": "gear"}
}

NOTIFICATION_ITEMS = ["Mr Carrot", "Tomatrio", "Shroombino"]

last_stock_state: Dict[str, int] = {}
last_notification_time: Dict[str, datetime] = {}
NOTIFICATION_COOLDOWN = 300
user_autostocks: Dict[int, Set[str]] = {}
AUTOSTOCKS_FILE = "autostocks.json"
telegram_app: Optional[Application] = None


def load_autostocks():
    global user_autostocks
    try:
        if os.path.exists(AUTOSTOCKS_FILE):
            with open(AUTOSTOCKS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                user_autostocks = {int(k): set(v) for k, v in data.items()}
                logger.info(f"✅ Загружены автостоки для {len(user_autostocks)} пользователей")
    except Exception as e:
        logger.error(f"❌ Ошибка при загрузке автостоков: {e}")
        user_autostocks = {}


def save_autostocks():
    try:
        data = {k: list(v) for k, v in user_autostocks.items()}
        with open(AUTOSTOCKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении автостоков: {e}")


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
                else:
                    logger.error(f"❌ API вернул статус {response.status}")
                    return None
        except Exception as e:
            logger.error(f"❌ Ошибка при получении данных: {e}")
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
            logger.error(f"❌ Ошибка при объединении данных: {e}")
            return None

    async def fetch_weather(self) -> Optional[Dict]:
        """Получение данных о погоде"""
        try:
            weather_data = await self.fetch_supabase_api(WEATHER_API_URL)
            if weather_data and len(weather_data) > 0:
                return weather_data[0]
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка при получении погоды: {e}")
            return None

    def format_weather_message(self, weather_data: Optional[Dict]) -> str:
        """Форматирование сообщения о погоде"""
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
                    
                    message = (
                        f"🌤️ *ПОГОДА В ИГРЕ*\n\n"
                        f"{emoji} *{name} погода*\n\n"
                        f"⏰ Закончится: {ends_time} МСК\n"
                        f"⏳ Осталось: ~{minutes_left} мин"
                    )
                else:
                    message = "🌤️ *ПОГОДА В ИГРЕ*\n\n_Сейчас обычная погода_"
            else:
                message = (
                    f"🌤️ *ПОГОДА В ИГРЕ*\n\n"
                    f"{emoji} *{name} погода*\n\n"
                    f"_Время окончания неизвестно_"
                )
        except Exception as e:
            logger.error(f"Ошибка при форматировании времени погоды: {e}")
            message = (
                f"🌤️ *ПОГОДА В ИГРЕ*\n\n"
                f"{emoji} *{name} погода*"
            )
        
        return message

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
        if not stock_data or 'data' not in stock_data:
            return

        current_stock = {}
        for item in stock_data['data']:
            display_name = item.get('display_name', '')
            multiplier = item.get('multiplier', 0)
            if display_name and multiplier > 0:
                current_stock[display_name] = multiplier

        for user_id, tracked_items in user_autostocks.items():
            for item_name in tracked_items:
                if item_name in current_stock:
                    await self.send_autostock_notification(bot, user_id, item_name, current_stock[item_name])

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
                f"🔔 *АВТОСТОК - {item_name} В НАЛИЧИИ!*\n\n"
                f"{item_info['emoji']} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {item_info['price']}\n"
                f"🕒 {current_time} МСК"
            )

            await bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"✅ Автосток: {item_name} -> user {user_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка автосток: {e}")


tracker = StockTracker()


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stock_data = await tracker.fetch_stock()
    message = tracker.format_stock_message(stock_data)
    if update.effective_message:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /weather"""
    weather_data = await tracker.fetch_weather()
    message = tracker.format_weather_message(weather_data)
    if update.effective_message:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def autostock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if user_id != TEST_USER_ID:
        await update.effective_message.reply_text(
            "⚠️ Автостоки в тестовом режиме и доступны ограниченному кругу пользователей."
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
        keyboard = []
        for item_name, item_info in ITEMS_DATA.items():
            if item_info['category'] == 'seed':
                is_tracking = user_id in user_autostocks and item_name in user_autostocks[user_id]
                status = "✅" if is_tracking else "➕"
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name}",
                    callback_data=f"t_seed_{item_name}"
                )])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🌱 *СЕМЕНА*\n\nВыберите предметы:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "as_gear":
        keyboard = []
        for item_name, item_info in ITEMS_DATA.items():
            if item_info['category'] == 'gear':
                is_tracking = user_id in user_autostocks and item_name in user_autostocks[user_id]
                status = "✅" if is_tracking else "➕"
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name}",
                    callback_data=f"t_gear_{item_name}"
                )])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("⚔️ *СНАРЯЖЕНИЕ*\n\nВыберите предметы:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "as_list":
        if user_id not in user_autostocks or not user_autostocks[user_id]:
            message = "📋 *МОИ АВТОСТОКИ*\n\n_Нет отслеживаемых предметов_"
        else:
            items_list = []
            for item_name in user_autostocks[user_id]:
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
        
        if user_id not in user_autostocks:
            user_autostocks[user_id] = set()
        
        if item_name in user_autostocks[user_id]:
            user_autostocks[user_id].remove(item_name)
        else:
            user_autostocks[user_id].add(item_name)
        
        save_autostocks()
        
        # Обновляем клавиатуру
        keyboard = []
        for name, info in ITEMS_DATA.items():
            if info['category'] == category:
                is_tracking = user_id in user_autostocks and name in user_autostocks[user_id]
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
    channel_info = f"🔔 Канал: {CHANNEL_ID}" if CHANNEL_ID else ""
    welcome_message = (
        "👋 *Plants vs Brainrots Stock Tracker!*\n\n"
        "📊 /stock - Текущий сток\n"
        "🌤️ /weather - Погода в игре\n"
        "🔔 /autostock - Автостоки\n"
        "❓ /help - Справка\n\n"
        f"{channel_info}\n"
        "📦 *Редкие предметы:*\n"
        "• 🥕 Mr Carrot ($50m)\n"
        "• 🍅 Tomatrio ($125m)\n"
        "• 🍄 Shroombino ($200m)"
    )
    if update.effective_message:
        await update.effective_message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_message = (
        "📚 *КОМАНДЫ:*\n\n"
        "/start - Информация\n"
        "/stock - Текущий сток\n"
        "/weather - Погода в игре\n"
        "/autostock - Настроить автостоки\n"
        "/help - Справка\n\n"
        "⏰ Проверка каждые 5 минут в :15 секунд"
    )
    if update.effective_message:
        await update.effective_message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN)


async def periodic_stock_check(application: Application):
    if tracker.is_running:
        return
    
    tracker.is_running = True
    moscow_tz = pytz.timezone('Europe/Moscow')
    
    logger.info(f"🚀 Периодическая проверка запущена")
    
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
    load_autostocks()
    asyncio.create_task(periodic_stock_check(application))


# Flask приложение
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


async def setup_webhook(application: Application):
    """Настройка webhook для Render"""
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
        try:
            await application.bot.set_webhook(url=webhook_url)
            logger.info(f"✅ Webhook установлен: {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Ошибка установки webhook: {e}")
    else:
        logger.warning("⚠️ WEBHOOK_URL не установлен, используется polling")


def main():
    logger.info("="*60)
    logger.info("🌱 Plants vs Brainrots Stock Tracker Bot")
    logger.info("="*60)

    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stock", stock_command))
    application.add_handler(CommandHandler("weather", weather_command))
    application.add_handler(CommandHandler("autostock", autostock_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(autostock_callback))

    application.post_init = post_init

    async def shutdown_callback(app: Application):
        logger.info("🛑 Остановка бота")
        tracker.is_running = False
        save_autostocks()
        try:
            await tracker.close_session()
        except Exception as e:
            logger.error(f"❌ Ошибка закрытия: {e}")

    application.post_shutdown = shutdown_callback

    # Запуск
    if WEBHOOK_URL:
        logger.info("🌐 Режим: Webhook")
        import uvicorn
        from telegram.ext import ApplicationBuilder
        
        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        async def telegram_webhook():
            """Обработка webhook от Telegram"""
            try:
                update_data = flask_request.get_json()
                update = Update.de_json(update_data, application.bot)
                await application.process_update(update)
                return jsonify({"ok": True})
            except Exception as e:
                logger.error(f"❌ Ошибка webhook: {e}")
                return jsonify({"ok": False}), 500
        
        async def startup():
            await application.initialize()
            await setup_webhook(application)
            await application.start()
            await post_init(application)
        
        async def shutdown():
            await shutdown_callback(application)
            await application.stop()
        
        # Запуск Flask с поддержкой async
        import threading
        
        def run_bot_tasks():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(startup())
            loop.run_forever()
        
        bot_thread = threading.Thread(target=run_bot_tasks, daemon=True)
        bot_thread.start()
        
        port = int(os.getenv("PORT", "5000"))
        logger.info(f"🚀 Flask запущен на порту {port}")
        flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
    else:
        logger.info("🔄 Режим: Polling")
        logger.info("🚀 Бот запущен!")
        application.run_polling(allowed_updates=None, drop_pending_updates=True)


if __name__ == "__main__":
    main()