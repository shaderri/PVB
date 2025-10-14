import asyncio
import aiohttp
import logging
import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError
from flask import Flask, jsonify, request as flask_request
import pytz
from dotenv import load_dotenv

load_dotenv()

# Настройки бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
REQUIRED_CHANNEL = "@PlantsVsBrain"

# Admin ID
ADMIN_ID = 7177110883

# Supabase API
SUPABASE_URL_BASE = os.getenv("SUPABASE_URL", "https://vgneaaqqqmdpkmeepvdp.supabase.co/rest/v1")
SUPABASE_API_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZnbmVhYXFxcW1kcGttZWVwdmRwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTk1OTE1NjEsImV4cCI6MjA3NTE2NzU2MX0.uw7YbMCsAAk_PrOAa6lnc8Rwub9jGGkn6dtlLfJMB5w")

AUTOSTOCKS_URL = f"{SUPABASE_URL_BASE}/user_autostocks"
USERS_URL = f"{SUPABASE_URL_BASE}/bot_users"

SUPABASE_URL = "https://vextbzatpprnksyutbcp.supabase.co/rest/v1/game_stock"
SUPABASE_API_KEY_STOCK = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZleHRiemF0cHBybmtzeXV0YmNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTM4NjYzMTIsImV4cCI6MjA2OTQ0MjMxMn0.apcPdBL5o-t5jK68d9_r9C7m-8H81NQbTXK0EW0o800"

SEEDS_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.seeds&active=eq.true&order=created_at.desc"
GEAR_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.gear&active=eq.true&order=created_at.desc"
WEATHER_API_URL = f"{SUPABASE_URL}?select=*&game=eq.plantsvsbrainrots&type=eq.weather&active=eq.true&order=created_at.desc"

CHECK_INTERVAL_MINUTES = 5
CHECK_DELAY_SECONDS = 10
COMMAND_COOLDOWN = 15
STOCK_CACHE_SECONDS = 30

BROADCAST_MESSAGE = 1

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

logger.info(f"🔗 Supabase (автостоки): {SUPABASE_URL_BASE}")
logger.info(f"🔗 API стока: {SUPABASE_URL}")

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
    "King Limone": {"emoji": "🍋", "price": "$450m", "category": "seed"},
    "Water Bucket": {"emoji": "🪣", "price": "$7,500", "category": "gear"},
    "Frost Grenade": {"emoji": "❄️", "price": "$12,500", "category": "gear"},
    "Banana Gun": {"emoji": "🍌", "price": "$25,000", "category": "gear"},
    "Frost Blower": {"emoji": "🌬️", "price": "$125,000", "category": "gear"},
    "Carrot Launcher": {"emoji": "🥕", "price": "$500,000", "category": "gear"}
}

NOTIFICATION_ITEMS = ["Mr Carrot", "Tomatrio", "Shroombino", "Mango", "King Limone"]

last_stock_state: Dict[str, int] = {}
last_notification_time: Dict[str, datetime] = {}
NOTIFICATION_COOLDOWN = 300

user_cooldowns: Dict[int, Dict[str, datetime]] = {}

stock_cache: Optional[Dict] = None
stock_cache_time: Optional[datetime] = None

telegram_app: Optional[Application] = None

NAME_TO_ID: Dict[str, str] = {}
ID_TO_NAME: Dict[str, str] = {}

# ОПТИМИЗАЦИЯ: Кэш с ограничением размера и автоочисткой
user_autostocks_cache: Dict[int, Set[str]] = {}
user_autostocks_time: Dict[int, datetime] = {}
AUTOSTOCK_CACHE_TTL = 120  # 120 сек вместо 60
MAX_CACHE_SIZE = 10000  # Максимум 10k пользователей в кэше

SEED_ITEMS_LIST = [(name, info) for name, info in ITEMS_DATA.items() if info['category'] == 'seed']
GEAR_ITEMS_LIST = [(name, info) for name, info in ITEMS_DATA.items() if info['category'] == 'gear']

last_keyboard_cache: Dict[tuple, str] = {}


def build_item_id_mappings():
    global NAME_TO_ID, ID_TO_NAME
    NAME_TO_ID.clear()
    ID_TO_NAME.clear()
    
    for item_name in ITEMS_DATA.keys():
        hash_obj = hashlib.sha1(item_name.encode('utf-8'))
        hash_hex = hash_obj.hexdigest()[:8]
        category = ITEMS_DATA[item_name]['category']
        safe_id = f"t_{category}_{hash_hex}"
        
        NAME_TO_ID[item_name] = safe_id
        ID_TO_NAME[safe_id] = item_name
        
    logger.info(f"✅ Построены маппинги: {len(NAME_TO_ID)} предметов")


def get_moscow_time() -> datetime:
    return datetime.now(pytz.timezone('Europe/Moscow'))


def check_command_cooldown(user_id: int, command: str) -> tuple[bool, Optional[int]]:
    if user_id not in user_cooldowns:
        user_cooldowns[user_id] = {}
    
    if command in user_cooldowns[user_id]:
        last_time = user_cooldowns[user_id][command]
        now = get_moscow_time()
        elapsed = (now - last_time).total_seconds()
        
        if elapsed < COMMAND_COOLDOWN:
            seconds_left = int(COMMAND_COOLDOWN - elapsed)
            return False, seconds_left
    
    user_cooldowns[user_id][command] = get_moscow_time()
    return True, None


async def check_subscription(user_id: int, bot: Bot) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError:
        return False


def get_next_check_time() -> datetime:
    now = get_moscow_time()
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
    now = get_moscow_time()
    sleep_seconds = (next_check - now).total_seconds()
    return max(sleep_seconds, 0)


def _cleanup_cache():
    """ОПТИМИЗАЦИЯ: Очистить старые записи из кэша"""
    global user_autostocks_cache, user_autostocks_time
    
    if len(user_autostocks_cache) > MAX_CACHE_SIZE:
        now = get_moscow_time()
        # Удаляем записи старше 5 минут
        to_delete = []
        for user_id, cache_time in user_autostocks_time.items():
            if (now - cache_time).total_seconds() > 300:
                to_delete.append(user_id)
        
        for user_id in to_delete:
            user_autostocks_cache.pop(user_id, None)
            user_autostocks_time.pop(user_id, None)
        
        logger.info(f"♻️ Очищено {len(to_delete)} записей из кэша (осталось {len(user_autostocks_cache)})")


class SupabaseDB:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "apikey": SUPABASE_API_KEY,
            "Authorization": f"Bearer {SUPABASE_API_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
    
    async def init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def save_user(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None):
        try:
            await self.init_session()
            data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_seen": get_moscow_time().isoformat()
            }
            
            headers = self.headers.copy()
            headers["Prefer"] = "resolution=merge-duplicates"
            
            async with self.session.post(USERS_URL, json=data, headers=headers, timeout=5) as response:
                return response.status in [200, 201]
        except Exception as e:
            logger.error(f"Ошибка сохранения пользователя: {e}")
            return False
    
    async def get_all_users(self) -> List[int]:
        try:
            await self.init_session()
            params = {"select": "user_id"}
            
            async with self.session.get(USERS_URL, headers=self.headers, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return [item['user_id'] for item in data]
                return []
        except Exception as e:
            logger.error(f"Ошибка получения пользователей: {e}")
            return []
    
    async def load_user_autostocks(self, user_id: int, use_cache: bool = True) -> Set[str]:
        if use_cache and user_id in user_autostocks_cache:
            cache_time = user_autostocks_time.get(user_id)
            if cache_time:
                now = get_moscow_time()
                if (now - cache_time).total_seconds() < AUTOSTOCK_CACHE_TTL:
                    return user_autostocks_cache[user_id].copy()
        
        try:
            await self.init_session()
            params = {"user_id": f"eq.{user_id}", "select": "item_name"}
            
            async with self.session.get(AUTOSTOCKS_URL, headers=self.headers, params=params, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    items_set = {item['item_name'] for item in data}
                    
                    user_autostocks_cache[user_id] = items_set
                    user_autostocks_time[user_id] = get_moscow_time()
                    
                    return items_set
                return set()
        except Exception as e:
            logger.error(f"Ошибка загрузки автостоков: {e}")
            return set()
    
    async def save_user_autostock(self, user_id: int, item_name: str) -> bool:
        if user_id not in user_autostocks_cache:
            user_autostocks_cache[user_id] = set()
        user_autostocks_cache[user_id].add(item_name)
        user_autostocks_time[user_id] = get_moscow_time()
        
        try:
            await self.init_session()
            data = {"user_id": user_id, "item_name": item_name}
            
            async with self.session.post(AUTOSTOCKS_URL, json=data, headers=self.headers, timeout=5) as response:
                logger.info(f"💾 Добавлен автосток '{item_name}' для {user_id}: {response.status}")
                return response.status in [200, 201]
        except Exception as e:
            logger.error(f"Ошибка сохранения автостока: {e}")
            return False
    
    async def remove_user_autostock(self, user_id: int, item_name: str) -> bool:
        if user_id in user_autostocks_cache:
            user_autostocks_cache[user_id].discard(item_name)
            user_autostocks_time[user_id] = get_moscow_time()
        
        try:
            await self.init_session()
            params = {"user_id": f"eq.{user_id}", "item_name": f"eq.{item_name}"}
            
            async with self.session.delete(AUTOSTOCKS_URL, headers=self.headers, params=params, timeout=5) as response:
                logger.info(f"🗑️ Удален автосток '{item_name}' для {user_id}: {response.status}")
                return response.status in [200, 204]
        except Exception as e:
            logger.error(f"Ошибка удаления автостока: {e}")
            return False
    
    async def get_users_tracking_item(self, item_name: str) -> List[int]:
        try:
            await self.init_session()
            params = {"item_name": f"eq.{item_name}", "select": "user_id"}
            
            async with self.session.get(AUTOSTOCKS_URL, headers=self.headers, params=params, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    return [item['user_id'] for item in data]
                return []
        except Exception as e:
            logger.error(f"Ошибка получения пользователей: {e}")
            return []


class StockTracker:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_running = False
        self.db = SupabaseDB()

    async def init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
        await self.db.close_session()

    async def fetch_supabase_api(self, url: str) -> Optional[List[Dict]]:
        try:
            await self.init_session()
            headers = {
                "apikey": SUPABASE_API_KEY_STOCK,
                "Authorization": f"Bearer {SUPABASE_API_KEY_STOCK}"
            }
            
            async with self.session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                return None
        except Exception as e:
            logger.error(f"❌ Ошибка API стока: {e}")
            return None

    async def fetch_stock(self, use_cache: bool = True) -> Optional[Dict]:
        global stock_cache, stock_cache_time
        
        if use_cache and stock_cache and stock_cache_time:
            now = get_moscow_time()
            if (now - stock_cache_time).total_seconds() < STOCK_CACHE_SECONDS:
                return stock_cache
        
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
            
            result = {"data": combined_data}
            
            stock_cache = result
            stock_cache_time = get_moscow_time()
            
            return result
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
            current_time = get_moscow_time()
            
            if ends_at_str:
                ends_at = datetime.fromisoformat(ends_at_str.replace('Z', '+00:00'))
                ends_at_msk = ends_at.astimezone(pytz.timezone('Europe/Moscow'))
                
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

        current_time = get_moscow_time().strftime("%H:%M:%S")
        message += f"🕒 _Обновлено: {current_time} МСК_"
        return message

    def can_send_notification(self, item_name: str) -> bool:
        if item_name not in last_notification_time:
            return True
        
        now = get_moscow_time()
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
        """ОПТИМИЗАЦИЯ: Отправляем уведомления с задержкой, а не все сразу"""
        if not stock_data or 'data' not in stock_data:
            return

        current_stock = {}
        for item in stock_data['data']:
            display_name = item.get('display_name', '')
            multiplier = item.get('multiplier', 0)
            if display_name and multiplier > 0:
                current_stock[display_name] = multiplier

        # ОПТИМИЗАЦИЯ: Отправляем по одному с задержкой, не параллельно
        for item_name, count in current_stock.items():
            try:
                users = await self.db.get_users_tracking_item(item_name)
                for user_id in users:
                    try:
                        await self.send_autostock_notification(bot, user_id, item_name, count)
                        await asyncio.sleep(0.02)  # Минимальная задержка между сообщениями
                    except:
                        pass
            except:
                pass

    async def send_notification(self, bot: Bot, channel_id: str, item_name: str, count: int):
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
            current_time = get_moscow_time().strftime("%H:%M:%S")

            message = (
                f"🚨 *РЕДКИЙ ПРЕДМЕТ В СТОКЕ!* 🚨\n\n"
                f"{item_info['emoji']} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {item_info['price']}\n"
                f"🕒 {current_time} МСК"
            )

            await bot.send_message(chat_id=channel_id, text=message, parse_mode=ParseMode.MARKDOWN)
            last_notification_time[item_name] = get_moscow_time()
            logger.info(f"✅ Уведомление: {item_name} x{count}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")

    async def send_autostock_notification(self, bot: Bot, user_id: int, item_name: str, count: int):
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
            current_time = get_moscow_time().strftime("%H:%M:%S")

            message = (
                f"🔔 *АВТОСТОК - {item_name}!*\n\n"
                f"{item_info['emoji']} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {item_info['price']}\n"
                f"🕒 {current_time} МСК"
            )

            await bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.MARKDOWN)
        except:
            pass


tracker = StockTracker()


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    user_id = update.effective_user.id
    
    await tracker.db.save_user(
        user_id, 
        update.effective_user.username, 
        update.effective_user.first_name
    )
    
    can_execute, seconds_left = check_command_cooldown(user_id, 'stock')
    if not can_execute:
        await update.effective_message.reply_text(
            f"⏳ Подождите {seconds_left} сек. перед следующим запросом"
        )
        return
    
    if update.effective_chat.type == ChatType.PRIVATE:
        if not await check_subscription(user_id, context.bot):
            keyboard = [[InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")]]
            await update.effective_message.reply_text(
                f"⚠️ Для использования бота подпишитесь на {REQUIRED_CHANNEL}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    
    stock_data = await tracker.fetch_stock(use_cache=True)
    message = tracker.format_stock_message(stock_data)
    await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    user_id = update.effective_user.id
    
    await tracker.db.save_user(
        user_id, 
        update.effective_user.username, 
        update.effective_user.first_name
    )
    
    can_execute, seconds_left = check_command_cooldown(user_id, 'weather')
    if not can_execute:
        await update.effective_message.reply_text(
            f"⏳ Подождите {seconds_left} сек. перед следующим запросом"
        )
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
    
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    
    user_id = update.effective_user.id
    
    await tracker.db.save_user(
        user_id, 
        update.effective_user.username, 
        update.effective_user.first_name
    )
    
    can_execute, seconds_left = check_command_cooldown(user_id, 'autostock')
    if not can_execute:
        await update.effective_message.reply_text(
            f"⏳ Подождите {seconds_left} сек. перед следующим запросом"
        )
        return
    
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
        "⏰ Проверка: каждые 5 минут в :10 секунд"
    )
    
    await update.effective_message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


def _keyboard_to_str(keyboard: InlineKeyboardMarkup) -> str:
    try:
        buttons_data = []
        for row in keyboard.inline_keyboard:
            row_data = []
            for btn in row:
                row_data.append(f"{btn.text}:{btn.callback_data}")
            buttons_data.append("|".join(row_data))
        return "||".join(buttons_data)
    except:
        return ""


async def autostock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    try:
        if data == "as_seeds":
            user_items = await tracker.db.load_user_autostocks(user_id, use_cache=True)
            keyboard = []
            for item_name, item_info in SEED_ITEMS_LIST:
                is_tracking = item_name in user_items
                status = "✅" if is_tracking else "➕"
                safe_callback = NAME_TO_ID.get(item_name, "invalid")
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name}",
                    callback_data=safe_callback
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if keyboard and len(keyboard) > 0:
                await query.edit_message_text("🌱 *СЕМЕНА*\n\nВыберите предметы:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        
        elif data == "as_gear":
            user_items = await tracker.db.load_user_autostocks(user_id, use_cache=True)
            keyboard = []
            for item_name, item_info in GEAR_ITEMS_LIST:
                is_tracking = item_name in user_items
                status = "✅" if is_tracking else "➕"
                safe_callback = NAME_TO_ID.get(item_name, "invalid")
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name}",
                    callback_data=safe_callback
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if keyboard and len(keyboard) > 0:
                await query.edit_message_text("⚔️ *СНАРЯЖЕНИЕ*\n\nВыберите предметы:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        
        elif data == "as_list":
            user_items = await tracker.db.load_user_autostocks(user_id, use_cache=True)
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
        
        elif data.startswith("t_"):
            item_name = ID_TO_NAME.get(data)
            if not item_name:
                await query.answer("❌ Ошибка: предмет не найден", show_alert=True)
                return
            
            category = ITEMS_DATA.get(item_name, {}).get('category', 'seed')
            user_items = await tracker.db.load_user_autostocks(user_id, use_cache=True)
            
            if item_name in user_items:
                user_items.discard(item_name)
                asyncio.create_task(tracker.db.remove_user_autostock(user_id, item_name))
            else:
                user_items.add(item_name)
                asyncio.create_task(tracker.db.save_user_autostock(user_id, item_name))
            
            items_list = SEED_ITEMS_LIST if category == 'seed' else GEAR_ITEMS_LIST
            keyboard = []
            for name, info in items_list:
                is_tracking = name in user_items
                status = "✅" if is_tracking else "➕"
                safe_callback = NAME_TO_ID.get(name, "invalid")
                keyboard.append([InlineKeyboardButton(
                    f"{status} {info['emoji']} {name}",
                    callback_data=safe_callback
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            cache_key = (user_id, category)
            new_keyboard_str = _keyboard_to_str(reply_markup)
            old_keyboard_str = last_keyboard_cache.get(cache_key, "")
            
            if new_keyboard_str == old_keyboard_str:
                return
            
            last_keyboard_cache[cache_key] = new_keyboard_str
            
            try:
                await query.edit_message_reply_markup(reply_markup=reply_markup)
            except TelegramError:
                try:
                    category_text = "🌱 *СЕМЕНА*" if category == "seed" else "⚔️ *СНАРЯЖЕНИЕ*"
                    await query.edit_message_text(
                        f"{category_text}\n\nВыберите предметы:",
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
    
    except Exception as e:
        logger.error(f"❌ Ошибка в autostock_callback: {e}", exc_info=True)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return
    
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("❌ Рассылка доступна только в ЛС")
        return
    
    await update.effective_message.reply_text(
        "📢 *РАССЫЛКА ВСЕМ ПОЛЬЗОВАТЕЛЯМ*\n\n"
        "Отправьте текст сообщения для рассылки.\n"
        "Поддерживается Markdown форматирование.\n\n"
        "Для отмены введите /cancel",
        parse_mode=ParseMode.MARKDOWN
    )
    
    return BROADCAST_MESSAGE


async def broadcast_message_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message or not update.message:
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ConversationHandler.END
    
    message_text = update.message.text or ""
    message_html = update.message.text_html or message_text
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, отправить", callback_data="bc_confirm"),
            InlineKeyboardButton("❌ Отменить", callback_data="bc_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    context.user_data['broadcast_text'] = message_text
    context.user_data['broadcast_html'] = message_html
    context.user_data['broadcast_entities'] = update.message.entities or []
    
    await update.effective_message.reply_text(
        f"📝 *ПРЕДПРОСМОТР СООБЩЕНИЯ:*\n\n{message_text}\n\n"
        f"Отправить это сообщение всем пользователям?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    return ConversationHandler.END


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return
    
    data = query.data
    
    if data == "bc_cancel":
        await query.edit_message_text("❌ Рассылка отменена")
        return
    
    if data == "bc_confirm":
        broadcast_text = context.user_data.get('broadcast_text')
        broadcast_html = context.user_data.get('broadcast_html')
        broadcast_entities = context.user_data.get('broadcast_entities', [])
        
        if not broadcast_text:
            await query.edit_message_text("❌ Ошибка: текст сообщения не найден")
            return
        
        await query.edit_message_text("📤 Начинаю рассылку...")
        
        users = await tracker.db.get_all_users()
        
        if not users:
            await query.message.reply_text("❌ Пользователи не найдены")
            return
        
        sent = 0
        failed = 0
        
        for user_id_to_send in users:
            try:
                if broadcast_entities:
                    await context.bot.send_message(
                        chat_id=user_id_to_send,
                        text=broadcast_html,
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user_id_to_send,
                        text=broadcast_text,
                        parse_mode=ParseMode.MARKDOWN
                    )
                sent += 1
                await asyncio.sleep(0.1)  # 100ms задержка между сообщениями
            except Exception as e:
                failed += 1
        
        report = (
            f"✅ *РАССЫЛКА ЗАВЕРШЕНА*\n\n"
            f"📊 Статистика:\n"
            f"• Отправлено: {sent}\n"
            f"• Ошибок: {failed}\n"
            f"• Всего пользователей: {len(users)}"
        )
        
        await query.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Рассылка завершена: {sent} успешно, {failed} ошибок")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return ConversationHandler.END
    
    await update.effective_message.reply_text("❌ Операция отменена")
    return ConversationHandler.END


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_user:
        return
    
    await tracker.db.save_user(
        update.effective_user.id, 
        update.effective_user.username, 
        update.effective_user.first_name
    )
    
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
        "⏰ Проверка каждые 5 минут в :10 секунд\n"
        f"📢 Обязательная подписка: {REQUIRED_CHANNEL}"
    )
    await update.effective_message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN)


async def periodic_stock_check(application: Application):
    """ОПТИМИЗАЦИЯ: Улучшенная периодическая проверка с правильной остановкой"""
    if tracker.is_running:
        return
    
    tracker.is_running = True
    logger.info("🚀 Периодическая проверка запущена")
    
    try:
        initial_sleep = calculate_sleep_time()
        await asyncio.sleep(initial_sleep)

        while tracker.is_running:
            try:
                now = get_moscow_time()
                logger.info(f"🔍 Проверка - {now.strftime('%H:%M:%S')}")
                
                # Очистка кэша каждые 10 проверок
                if int(now.timestamp()) % 100 == 0:
                    _cleanup_cache()
                
                stock_data = await tracker.fetch_stock(use_cache=False)
                
                if stock_data:
                    if CHANNEL_ID:
                        await tracker.check_for_notifications(stock_data, application.bot, CHANNEL_ID)
                    await tracker.check_user_autostocks(stock_data, application.bot)
                
                sleep_time = calculate_sleep_time()
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Ошибка проверки: {e}")
                await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        tracker.is_running = False
        logger.info("🛑 Периодическая проверка остановлена")


async def post_init(application: Application):
    asyncio.create_task(periodic_stock_check(application))


flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET", "HEAD"])
@flask_app.route("/ping", methods=["GET", "HEAD"])
def ping():
    if flask_request.method == "HEAD":
        return "", 200
    
    now = get_moscow_time()
    next_check = get_next_check_time()
    
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "moscow_time": now.strftime("%H:%M:%S"),
        "next_check": next_check.strftime("%H:%M:%S"),
        "bot": "PVB Stock Tracker",
        "is_running": tracker.is_running,
        "cache_size": len(user_autostocks_cache)
    }), 200


@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "running": tracker.is_running}), 200


def main():
    logger.info("="*60)
    logger.info("🌱 Plants vs Brainrots Stock Tracker Bot")
    logger.info("="*60)

    build_item_id_mappings()

    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("stock", stock_command))
    telegram_app.add_handler(CommandHandler("weather", weather_command))
    telegram_app.add_handler(CommandHandler("autostock", autostock_command))
    telegram_app.add_handler(CommandHandler("help", help_command))
    
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_command)],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_message_received)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )
    telegram_app.add_handler(broadcast_handler)
    
    telegram_app.add_handler(CallbackQueryHandler(autostock_callback, pattern="^as_|^t_"))
    telegram_app.add_handler(CallbackQueryHandler(broadcast_callback, pattern="^bc_"))

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
    logger.info(f"👤 Admin ID: {ADMIN_ID}")
    logger.info("="*60)
    telegram_app.run_polling(allowed_updates=None, drop_pending_updates=True)


if __name__ == "__main__":
    main()