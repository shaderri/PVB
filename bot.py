import asyncio
import aiohttp
import logging
import os
import threading
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from flask import Flask, jsonify, request
import pytz
from dotenv import load_dotenv

load_dotenv()

# Настройки бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Supabase API настройки
SUPABASE_URL = "https://vextbzatpprnksyutbcp.supabase.co/rest/v1/game_stock"
SUPABASE_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZleHRiemF0cHBybmtzeXV0YmNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTM4NjYzMTIsImV4cCI6MjA2OTQ0MjMxMn0.apcPdBL5o-t5jK68d9_r9C7m-8H81NQbTXK0EW0o800"

SEEDS_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.seeds&active=eq.true&order=created_at.desc"
GEAR_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.gear&active=eq.true&order=created_at.desc"

# Интервал проверки: каждые 5 минут + 15 секунд
CHECK_INTERVAL_MINUTES = 5
CHECK_DELAY_SECONDS = 15

# Тестовый пользователь для автостоков
TEST_USER_ID = 7177110883

# Проверка наличия обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения!")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Данные о предметах
ITEMS_DATA = {
    # Семена
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

    # Снаряжение
    "Bat": {"emoji": "🏏", "price": "Free", "category": "gear"},
    "Water Bucket": {"emoji": "🪣", "price": "$7,500", "category": "gear"},
    "Frost Grenade": {"emoji": "❄️", "price": "$12,500", "category": "gear"},
    "Banana Gun": {"emoji": "🍌", "price": "$25,000", "category": "gear"},
    "Frost Blower": {"emoji": "🌬️", "price": "$125,000", "category": "gear"},
    "Lucky Potion": {"emoji": "🍀", "price": "TBD", "category": "gear"},
    "Speed Potion": {"emoji": "⚡", "price": "TBD", "category": "gear"},
    "Carrot Launcher": {"emoji": "🥕", "price": "$500,000", "category": "gear"}
}

# Предметы для уведомлений в канал
NOTIFICATION_ITEMS = ["Mr Carrot", "Tomatrio", "Shroombino"]

# Хранение последнего состояния стока
last_stock_state: Dict[str, int] = {}

# Хранение времени последнего уведомления для каждого предмета
last_notification_time: Dict[str, datetime] = {}

# Минимальный интервал между уведомлениями об одном предмете (в секундах)
NOTIFICATION_COOLDOWN = 300  # 5 минут

# Хранение автостоков пользователей: {user_id: set(item_names)}
user_autostocks: Dict[int, Set[str]] = {}

# Файл для сохранения автостоков
AUTOSTOCKS_FILE = "autostocks.json"


def load_autostocks():
    """Загрузка автостоков из файла"""
    global user_autostocks
    try:
        if os.path.exists(AUTOSTOCKS_FILE):
            with open(AUTOSTOCKS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Преобразуем ключи обратно в int и значения в set
                user_autostocks = {int(k): set(v) for k, v in data.items()}
                logger.info(f"✅ Загружены автостоки для {len(user_autostocks)} пользователей")
    except Exception as e:
        logger.error(f"❌ Ошибка при загрузке автостоков: {e}")
        user_autostocks = {}


def save_autostocks():
    """Сохранение автостоков в файл"""
    try:
        # Преобразуем set в list для JSON
        data = {k: list(v) for k, v in user_autostocks.items()}
        with open(AUTOSTOCKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Автостоки сохранены")
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении автостоков: {e}")


def get_next_check_time() -> datetime:
    """Вычисляет следующее время проверки (каждые 5 минут + 15 секунд)"""
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
    """Вычисляет время до следующей проверки в секундах"""
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
        """Инициализация aiohttp сессии"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Закрытие aiohttp сессии"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_supabase_api(self, url: str) -> Optional[List[Dict]]:
        """Получение данных из Supabase API"""
        try:
            await self.init_session()
            headers = {
                "apikey": SUPABASE_API_KEY,
                "Authorization": f"Bearer {SUPABASE_API_KEY}"
            }
            
            async with self.session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"❌ API вернул статус {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.error("❌ Timeout при запросе к API")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка при получении данных: {e}")
            return None

    async def fetch_stock(self) -> Optional[Dict]:
        """Получение данных о стоке из обоих API (seeds + gear)"""
        try:
            seeds_task = self.fetch_supabase_api(SEEDS_API_URL)
            gear_task = self.fetch_supabase_api(GEAR_API_URL)
            
            seeds_data, gear_data = await asyncio.gather(seeds_task, gear_task)
            
            if seeds_data is None and gear_data is None:
                logger.error("❌ Не удалось получить данные ни из одного API")
                return None
            
            combined_data = []
            
            if seeds_data:
                combined_data.extend(seeds_data)
                logger.info(f"✅ Получено семян: {len(seeds_data)}")
            
            if gear_data:
                combined_data.extend(gear_data)
                logger.info(f"✅ Получено снаряжения: {len(gear_data)}")
            
            logger.info(f"✅ Всего предметов в стоке: {len(combined_data)}")
            
            return {"data": combined_data}
            
        except Exception as e:
            logger.error(f"❌ Ошибка при объединении данных: {e}")
            return None

    def format_stock_message(self, stock_data: Dict) -> str:
        """Форматирование сообщения о стоке"""
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
            emoji = item_info['emoji']
            price = item_info['price']

            formatted_item = f"{emoji} *{display_name}*: x{multiplier} ({price})"

            if item_type == 'seeds':
                seeds.append(formatted_item)
            elif item_type == 'gear':
                gear.append(formatted_item)

        message = "📊 *ТЕКУЩИЙ СТОК*\n\n"
        
        if seeds:
            message += "🌱 *СЕМЕНА:*\n"
            message += "\n".join(seeds) + "\n\n"
        else:
            message += "🌱 *СЕМЕНА:* _Пусто_\n\n"

        if gear:
            message += "⚔️ *СНАРЯЖЕНИЕ:*\n"
            message += "\n".join(gear) + "\n\n"
        else:
            message += "⚔️ *СНАРЯЖЕНИЕ:* _Пусто_\n\n"

        try:
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(moscow_tz).strftime("%H:%M:%S")
        except Exception:
            current_time = datetime.utcnow().strftime("%H:%M:%S")
        
        message += f"🕒 _Обновлено: {current_time} МСК_"

        return message

    def can_send_notification(self, item_name: str) -> bool:
        """Проверяет, можно ли отправить уведомление (cooldown)"""
        if item_name not in last_notification_time:
            return True
        
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        last_time = last_notification_time[item_name]
        
        if (now - last_time).total_seconds() >= NOTIFICATION_COOLDOWN:
            return True
        
        return False

    async def check_for_notifications(self, stock_data: Dict, bot: Bot, channel_id: str):
        """Проверка на наличие редких предметов и отправка уведомлений в канал"""
        global last_stock_state

        if not stock_data or 'data' not in stock_data or not channel_id:
            return

        current_stock = {}
        
        for item in stock_data['data']:
            display_name = item.get('display_name', '')
            multiplier = item.get('multiplier', 0)

            if display_name and multiplier > 0:
                current_stock[display_name] = multiplier

        logger.info(f"📦 Текущий сток редких предметов: {current_stock}")
        logger.info(f"📝 Предыдущий сток: {last_stock_state}")

        for item_name in NOTIFICATION_ITEMS:
            current_count = current_stock.get(item_name, 0)
            previous_count = last_stock_state.get(item_name, 0)
            
            should_notify = False
            
            if current_count > 0:
                if previous_count == 0:
                    should_notify = True
                    logger.info(f"🆕 {item_name} появился в стоке! (0 -> {current_count})")
                elif current_count > previous_count:
                    should_notify = True
                    logger.info(f"📈 {item_name} увеличился! ({previous_count} -> {current_count})")
            
            if should_notify and self.can_send_notification(item_name):
                await self.send_notification(bot, channel_id, item_name, current_count)
            elif should_notify and not self.can_send_notification(item_name):
                logger.info(f"⏳ {item_name}: уведомление отложено (cooldown)")

        last_stock_state = current_stock.copy()

    async def check_user_autostocks(self, stock_data: Dict, bot: Bot):
        """Проверка автостоков пользователей"""
        if not stock_data or 'data' not in stock_data:
            return

        current_stock = {}
        for item in stock_data['data']:
            display_name = item.get('display_name', '')
            multiplier = item.get('multiplier', 0)
            if display_name and multiplier > 0:
                current_stock[display_name] = multiplier

        # Проверяем автостоки каждого пользователя
        for user_id, tracked_items in user_autostocks.items():
            for item_name in tracked_items:
                if item_name in current_stock:
                    await self.send_autostock_notification(bot, user_id, item_name, current_stock[item_name])

    async def send_notification(self, bot: Bot, channel_id: str, item_name: str, count: int):
        """Отправка уведомления в канал"""
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
            emoji = item_info['emoji']
            price = item_info['price']

            try:
                moscow_tz = pytz.timezone('Europe/Moscow')
                current_time = datetime.now(moscow_tz).strftime("%H:%M:%S")
            except Exception:
                current_time = datetime.utcnow().strftime("%H:%M:%S")

            message = (
                f"🚨 *РЕДКИЙ ПРЕДМЕТ В СТОКЕ!* 🚨\n\n"
                f"{emoji} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {price}\n"
                f"🕒 {current_time} МСК"
            )

            await bot.send_message(
                chat_id=channel_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            
            moscow_tz = pytz.timezone('Europe/Moscow')
            last_notification_time[item_name] = datetime.now(moscow_tz)
            
            logger.info(f"✅ Уведомление отправлено: {item_name} x{count} -> {channel_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке уведомления: {e}")

    async def send_autostock_notification(self, bot: Bot, user_id: int, item_name: str, count: int):
        """Отправка уведомления пользователю об автостоке"""
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
            emoji = item_info['emoji']
            price = item_info['price']

            try:
                moscow_tz = pytz.timezone('Europe/Moscow')
                current_time = datetime.now(moscow_tz).strftime("%H:%M:%S")
            except Exception:
                current_time = datetime.utcnow().strftime("%H:%M:%S")

            message = (
                f"🔔 *АВТОСТОК - {item_name} В НАЛИЧИИ!*\n\n"
                f"{emoji} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {price}\n"
                f"🕒 {current_time} МСК"
            )

            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"✅ Автосток уведомление отправлено: {item_name} x{count} -> user {user_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке автосток уведомления: {e}")


# Экземпляр трекера
tracker = StockTracker()


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stock"""
    stock_data = await tracker.fetch_stock()
    message = tracker.format_stock_message(stock_data)

    if update.effective_message:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def autostock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /autostock - управление автостоками"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Проверка доступа (только тестовый пользователь)
    if user_id != TEST_USER_ID:
        await update.effective_message.reply_text(
            "⚠️ Автостоки находятся в тестовом режиме и доступны только ограниченному кругу пользователей."
        )
        return

    # Создаем клавиатуру с категориями
    keyboard = [
        [InlineKeyboardButton("🌱 Семена", callback_data="autostock_seeds")],
        [InlineKeyboardButton("⚔️ Снаряжение", callback_data="autostock_gear")],
        [InlineKeyboardButton("📋 Мои автостоки", callback_data="autostock_list")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        "🔔 *УПРАВЛЕНИЕ АВТОСТОКАМИ*\n\n"
        "Выберите категорию предметов для отслеживания.\n"
        "Вы будете получать уведомления каждый раз, когда выбранный предмет появляется в стоке.\n\n"
        "⏰ Проверка: каждые 5 минут в :15 секунд"
    )
    
    await update.effective_message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def autostock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback кнопок для автостоков"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "autostock_seeds":
        # Показываем список семян
        keyboard = []
        for item_name, item_info in ITEMS_DATA.items():
            if item_info['category'] == 'seed':
                emoji = item_info['emoji']
                is_tracking = user_id in user_autostocks and item_name in user_autostocks[user_id]
                status = "✅" if is_tracking else "➕"
                keyboard.append([InlineKeyboardButton(
                    f"{status} {emoji} {item_name}",
                    callback_data=f"toggle_{item_name}"
                )])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="autostock_back")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🌱 *СЕМЕНА*\n\nВыберите предметы для отслеживания:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "autostock_gear":
        # Показываем список снаряжения
        keyboard = []
        for item_name, item_info in ITEMS_DATA.items():
            if item_info['category'] == 'gear':
                emoji = item_info['emoji']
                is_tracking = user_id in user_autostocks and item_name in user_autostocks[user_id]
                status = "✅" if is_tracking else "➕"
                keyboard.append([InlineKeyboardButton(
                    f"{status} {emoji} {item_name}",
                    callback_data=f"toggle_{item_name}"
                )])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="autostock_back")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "⚔️ *СНАРЯЖЕНИЕ*\n\nВыберите предметы для отслеживания:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "autostock_list":
        # Показываем список отслеживаемых предметов
        if user_id not in user_autostocks or not user_autostocks[user_id]:
            message = "📋 *МОИ АВТОСТОКИ*\n\n_Вы пока не отслеживаете ни один предмет_"
        else:
            tracked = user_autostocks[user_id]
            items_list = []
            for item_name in tracked:
                item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
                emoji = item_info['emoji']
                price = item_info['price']
                items_list.append(f"{emoji} *{item_name}* ({price})")
            
            message = f"📋 *МОИ АВТОСТОКИ*\n\n" + "\n".join(items_list)
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="autostock_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "autostock_back":
        # Возврат в главное меню
        keyboard = [
            [InlineKeyboardButton("🌱 Семена", callback_data="autostock_seeds")],
            [InlineKeyboardButton("⚔️ Снаряжение", callback_data="autostock_gear")],
            [InlineKeyboardButton("📋 Мои автостоки", callback_data="autostock_list")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = (
            "🔔 *УПРАВЛЕНИЕ АВТОСТОКАМИ*\n\n"
            "Выберите категорию предметов для отслеживания."
        )
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    
    elif data.startswith("toggle_"):
        # Переключение отслеживания предмета
        item_name = data.replace("toggle_", "")
        
        if user_id not in user_autostocks:
            user_autostocks[user_id] = set()
        
        if item_name in user_autostocks[user_id]:
            user_autostocks[user_id].remove(item_name)
            action = "удален из"
        else:
            user_autostocks[user_id].add(item_name)
            action = "добавлен в"
        
        save_autostocks()
        
        item_info = ITEMS_DATA.get(item_name, {"emoji": "📦"})
        emoji = item_info['emoji']
        
        await query.answer(f"{emoji} {item_name} {action} автостоки!")
        
        # Обновляем клавиатуру
        category = item_info.get('category', '')
        if category == 'seed':
            await autostock_callback(update, context)  # Перезагружаем список семян
        else:
            await autostock_callback(update, context)  # Перезагружаем список снаряжения


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    channel_info = f"🔔 Канал для уведомлений: {CHANNEL_ID}" if CHANNEL_ID else "🔕 Канал для уведомлений не настроен"

    welcome_message = (
        "👋 *Добро пожаловать в Plants vs Brainrots Stock Tracker!*\n\n"
        "📊 /stock - Посмотреть текущий сток\n"
        "🔔 /autostock - Управление автостоками\n"
        "❓ /help - Справка\n\n"
        f"{channel_info}\n\n"
        "📦 *Бот отслеживает редкие предметы:*\n"
        "• 🥕 Mr Carrot ($50m)\n"
        "• 🍅 Tomatrio ($125m)\n"
        "• 🍄 Shroombino ($200m)\n\n"
        f"⏱️ _Проверка каждые {CHECK_INTERVAL_MINUTES} минут + {CHECK_DELAY_SECONDS} секунд_"
    )
    if update.effective_message:
        await update.effective_message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_message = (
        "📚 *ДОСТУПНЫЕ КОМАНДЫ:*\n\n"
        "/start - Информация о боте\n"
        "/stock - Показать текущий сток\n"
        "/autostock - Настроить автостоки\n"
        "/help - Это сообщение\n\n"
        "💡 *ЧТО ТАКОЕ АВТОСТОКИ?*\n"
        "Автостоки позволяют отслеживать нужные вам предметы. "
        "Когда выбранный предмет появляется в стоке, вы получите личное уведомление.\n\n"
        "⏰ Проверка выполняется каждые 5 минут в :15 секунд (13:05:15, 13:10:15, и т.д.)"
    )
    if update.effective_message:
        await update.effective_message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN)


async def periodic_stock_check(application: Application):
    """Периодическая проверка стока с синхронизацией по времени"""
    bot = application.bot

    # Предотвращение запуска нескольких экземпляров
    if tracker.is_running:
        logger.warning("⚠️ Периодическая проверка уже запущена, пропускаем")
        return
    
    tracker.is_running = True
    moscow_tz = pytz.timezone('Europe/Moscow')
    
    logger.info(f"🚀 Запущена периодическая проверка стока")
    logger.info(f"⏱️ Интервал: каждые {CHECK_INTERVAL_MINUTES} минут + {CHECK_DELAY_SECONDS} секунд")
    logger.info(f"📝 Примеры времени проверки: 13:05:15, 13:10:15, 13:15:15")
    
    if CHANNEL_ID:
        logger.info(f"📢 Уведомления будут отправляться в: {CHANNEL_ID}")
    else:
        logger.warning("⚠️ Канал для уведомлений не настроен")

    # Ждем до следующего правильного времени перед первой проверкой
    initial_sleep = calculate_sleep_time()
    next_check = get_next_check_time()
    logger.info(f"⏳ Ожидание до первой проверки: {initial_sleep:.1f} сек (следующая проверка: {next_check.strftime('%H:%M:%S')})")
    await asyncio.sleep(initial_sleep)

    while tracker.is_running:
        try:
            now = datetime.now(moscow_tz)
            logger.info(f"\n{'='*50}")
            logger.info(f"🔍 ПРОВЕРКА СТОКА - {now.strftime('%H:%M:%S')} МСК")
            logger.info(f"{'='*50}")
            
            stock_data = await tracker.fetch_stock()
            
            if stock_data:
                # Проверяем уведомления в канал
                if CHANNEL_ID:
                    await tracker.check_for_notifications(stock_data, bot, CHANNEL_ID)
                
                # Проверяем автостоки пользователей
                await tracker.check_user_autostocks(stock_data, bot)
            
            # Вычисляем время до следующей проверки
            sleep_time = calculate_sleep_time()
            next_check = get_next_check_time()
            
            logger.info(f"✅ Проверка завершена")
            logger.info(f"⏳ Следующая проверка: {next_check.strftime('%H:%M:%S')} (через {sleep_time:.1f} сек)")
            logger.info(f"{'='*50}\n")
            
            await asyncio.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"❌ Ошибка в периодической проверке: {e}", exc_info=True)
            await asyncio.sleep(calculate_sleep_time())


async def post_init(application: Application):
    """Запуск периодической проверки после инициализации"""
    # Загружаем автостоки при старте
    load_autostocks()
    
    # Создаём задачу только один раз
    asyncio.create_task(periodic_stock_check(application))


# --- Flask часть (для пингера / keep-alive) ---
flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET", "HEAD"])
@flask_app.route("/ping", methods=["GET", "HEAD"])
def ping():
    """Эндпоинт для пингера Render (поддержка HEAD запросов)"""
    if request.method == "HEAD":
        return "", 200
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    next_check = get_next_check_time()
    
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "moscow_time": now.strftime("%H:%M:%S"),
        "next_check": next_check.strftime("%H:%M:%S"),
        "bot": "Plants vs Brainrots Stock Tracker",
        "is_running": tracker.is_running
    }), 200


@flask_app.route("/health", methods=["GET"])
def health():
    """Healthcheck эндпоинт"""
    return jsonify({"status": "healthy", "is_running": tracker.is_running}), 200


def run_flask():
    """Запуск Flask сервера"""
    port = int(os.getenv("PORT", "5000"))
    logger.info(f"🌐 Запуск Flask сервера на 0.0.0.0:{port}")
    # Отключаем вывод логов Flask для чистоты
    import logging as flask_logging
    flask_log = flask_logging.getLogger('werkzeug')
    flask_log.setLevel(flask_logging.ERROR)
    
    flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


def main():
    """Основная функция запуска бота и Flask"""
    logger.info("="*60)
    logger.info("🌱 Plants vs Brainrots Stock Tracker Bot")
    logger.info("="*60)

    # Запуск Flask в отдельном потоке для пингера Render
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask сервер запущен в фоновом потоке")

    # Создание приложения Telegram
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавление обработчиков команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stock", stock_command))
    application.add_handler(CommandHandler("autostock", autostock_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Добавление обработчика callback кнопок
    application.add_handler(CallbackQueryHandler(autostock_callback))

    # Запуск периодической проверки после инициализации
    application.post_init = post_init

    # Регистрация graceful shutdown для закрытия aiohttp сессии
    async def shutdown_callback(app: Application):
        logger.info("🛑 Завершение работы: остановка периодической проверки")
        tracker.is_running = False
        
        logger.info("🛑 Сохранение автостоков")
        save_autostocks()
        
        logger.info("🛑 Закрытие aiohttp сессии")
        try:
            await tracker.close_session()
        except Exception as e:
            logger.exception(f"❌ Ошибка при закрытии aiohttp сессии: {e}")

    application.post_shutdown = shutdown_callback

    # Запуск бота (блокирующий вызов)
    logger.info("🚀 Бот успешно запущен!")
    logger.info("="*60)
    application.run_polling(allowed_updates=None, drop_pending_updates=True)


if __name__ == "__main__":
    main()