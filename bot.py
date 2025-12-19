import asyncio
import logging
import os
import hashlib
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set, Tuple
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError
import pytz
from dotenv import load_dotenv
import discord
import aiohttp
from flask import Flask, jsonify, request as flask_request
import threading

load_dotenv()

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
REQUIRED_CHANNELS = ["@PlantsVsBrain", "@linkRobloxNews"]
ADMIN_ID = 7177110883

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://vgneaaqqqmdpkmeepvdp.supabase.co")
SUPABASE_API_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZnbmVhYXFxcW1kcGttZWVwdmRwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTk1OTE1NjEsImV4cCI6MjA3NTE2NzU2MX0.uw7YbMCsAAk_PrOAa6lnc8Rwub9jGGkn6dtlLfJMB5w")

AUTOSTOCKS_URL = f"{SUPABASE_URL}/rest/v1/user_autostocks"
USERS_URL = f"{SUPABASE_URL}/rest/v1/bot_users"

# Discord канал стоков
DISCORD_STOCK_CHANNEL_ID = 1407975317682917457

STOCK_CACHE_SECONDS = 15
USER_NOTIFICATION_COOLDOWN = 120
AUTOSTOCK_CACHE_TTL = 120
SUBSCRIPTION_CACHE_TTL = 180

if not BOT_TOKEN or not DISCORD_TOKEN:
    raise ValueError("BOT_TOKEN и DISCORD_TOKEN обязательны!")

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

# ========== ДАННЫЕ ПРЕДМЕТОВ ==========
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
    "King Limone": {"emoji": "🍋", "price": "$670m", "category": "seed"},
    "Starfruit": {"emoji": "⭐", "price": "$750m", "category": "seed"},
    "Brussel Sprouts": {"emoji": "🥬", "price": "$900m", "category": "seed"},
    "Water Bucket": {"emoji": "🪣", "price": "$7,500", "category": "gear"},
    "Frost Grenade": {"emoji": "❄️", "price": "$12,500", "category": "gear"},
    "Banana Gun": {"emoji": "🍌", "price": "$25,000", "category": "gear"},
    "Frost Blower": {"emoji": "🌬️", "price": "$125,000", "category": "gear"},
    "Carrot Launcher": {"emoji": "🥕", "price": "$500,000", "category": "gear"}
}

NOTIFICATION_ITEMS = ["Tomatrio", "Shroombino", "Mango", "King Limone", "Starfruit", "Brussel Sprouts"]

SEED_ITEMS_LIST = [(name, info) for name, info in ITEMS_DATA.items() if info['category'] == 'seed']
GEAR_ITEMS_LIST = [(name, info) for name, info in ITEMS_DATA.items() if info['category'] == 'gear']

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
stock_cache: Optional[Dict] = None
stock_cache_time: Optional[datetime] = None
user_autostocks_cache: Dict[int, Set[str]] = {}
user_autostocks_time: Dict[int, datetime] = {}
subscription_cache: Dict[int, Tuple[bool, datetime]] = {}
user_sent_notifications: Dict[int, Dict[str, datetime]] = {}
item_last_seen: Dict[str, datetime] = {}
last_stock_state: Dict[str, int] = {}

NAME_TO_ID: Dict[str, str] = {}
ID_TO_NAME: Dict[str, str] = {}

telegram_app: Optional[Application] = None
discord_client: Optional[discord.Client] = None
http_session: Optional[aiohttp.ClientSession] = None

# ========== УТИЛИТЫ ==========
def get_moscow_time() -> datetime:
    return datetime.now(pytz.timezone('Europe/Moscow'))

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
    
    logger.info(f"✅ Маппинг: {len(NAME_TO_ID)} предметов")

async def check_subscription(user_id: int, bot: Bot, use_cache: bool = True) -> Tuple[bool, List[str]]:
    if use_cache and user_id in subscription_cache:
        is_subscribed, cache_time = subscription_cache[user_id]
        now = get_moscow_time()
        if (now - cache_time).total_seconds() < SUBSCRIPTION_CACHE_TTL:
            return (is_subscribed, [])
    
    not_subscribed = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                not_subscribed.append(channel)
        except TelegramError:
            not_subscribed.append(channel)
    
    is_subscribed = len(not_subscribed) == 0
    subscription_cache[user_id] = (is_subscribed, get_moscow_time())
    
    return (is_subscribed, not_subscribed)

def get_subscription_keyboard(not_subscribed: List[str] = None) -> InlineKeyboardMarkup:
    if not_subscribed is None:
        not_subscribed = REQUIRED_CHANNELS
    
    keyboard = []
    for channel in not_subscribed:
        channel_name = channel.replace("@", "")
        keyboard.append([InlineKeyboardButton(f"📢 {channel}", url=f"https://t.me/{channel_name}")])
    
    keyboard.append([InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")])
    return InlineKeyboardMarkup(keyboard)

# ========== БАЗА ДАННЫХ ==========
class SupabaseDB:
    def __init__(self):
        self.headers = {
            "apikey": SUPABASE_API_KEY,
            "Authorization": f"Bearer {SUPABASE_API_KEY}",
            "Content-Type": "application/json"
        }
    
    async def get_session(self) -> aiohttp.ClientSession:
        global http_session
        if http_session is None or http_session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return http_session
    
    async def save_user(self, user_id: int, username: str = None, first_name: str = None):
        try:
            session = await self.get_session()
            data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_seen": get_moscow_time().isoformat()
            }
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates"}
            async with session.post(USERS_URL, json=data, headers=headers, timeout=5) as response:
                return response.status in [200, 201]
        except Exception as e:
            logger.error(f"❌ save_user: {e}")
            return False
    
    async def get_all_users(self) -> List[int]:
        all_users = []
        offset = 0
        limit = 1000
        
        try:
            session = await self.get_session()
            while True:
                params = {"select": "user_id", "limit": limit, "offset": offset, "order": "user_id.asc"}
                async with session.get(USERS_URL, headers=self.headers, params=params, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if not data:
                            break
                        all_users.extend([item['user_id'] for item in data])
                        if len(data) < limit:
                            break
                        offset += limit
                        await asyncio.sleep(0.1)
                    else:
                        break
            return all_users
        except Exception as e:
            logger.error(f"❌ get_all_users: {e}")
            return all_users
    
    async def delete_user(self, user_id: int) -> bool:
        try:
            session = await self.get_session()
            params = {"user_id": f"eq.{user_id}"}
            async with session.delete(USERS_URL, headers=self.headers, params=params, timeout=5) as response:
                return response.status in [200, 204]
        except Exception as e:
            logger.error(f"❌ delete_user: {e}")
            return False
    
    async def delete_user_autostocks(self, user_id: int) -> bool:
        try:
            session = await self.get_session()
            params = {"user_id": f"eq.{user_id}"}
            async with session.delete(AUTOSTOCKS_URL, headers=self.headers, params=params, timeout=5) as response:
                return response.status in [200, 204]
        except Exception as e:
            logger.error(f"❌ delete_autostocks: {e}")
            return False
    
    async def load_user_autostocks(self, user_id: int, use_cache: bool = True) -> Set[str]:
        if use_cache and user_id in user_autostocks_cache:
            cache_time = user_autostocks_time.get(user_id)
            if cache_time:
                now = get_moscow_time()
                if (now - cache_time).total_seconds() < AUTOSTOCK_CACHE_TTL:
                    return user_autostocks_cache[user_id].copy()
        
        try:
            session = await self.get_session()
            params = {"user_id": f"eq.{user_id}", "select": "item_name"}
            async with session.get(AUTOSTOCKS_URL, headers=self.headers, params=params, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    items_set = {item['item_name'] for item in data}
                    user_autostocks_cache[user_id] = items_set
                    user_autostocks_time[user_id] = get_moscow_time()
                    return items_set
                return set()
        except Exception as e:
            logger.error(f"❌ load_autostocks: {e}")
            return set()
    
    async def save_user_autostock(self, user_id: int, item_name: str) -> bool:
        if user_id not in user_autostocks_cache:
            user_autostocks_cache[user_id] = set()
        user_autostocks_cache[user_id].add(item_name)
        user_autostocks_time[user_id] = get_moscow_time()
        
        try:
            session = await self.get_session()
            data = {"user_id": user_id, "item_name": item_name}
            async with session.post(AUTOSTOCKS_URL, json=data, headers=self.headers, timeout=5) as response:
                return response.status in [200, 201]
        except Exception as e:
            logger.error(f"❌ save_autostock: {e}")
            return False
    
    async def remove_user_autostock(self, user_id: int, item_name: str) -> bool:
        if user_id in user_autostocks_cache:
            user_autostocks_cache[user_id].discard(item_name)
            user_autostocks_time[user_id] = get_moscow_time()
        
        try:
            session = await self.get_session()
            params = {"user_id": f"eq.{user_id}", "item_name": f"eq.{item_name}"}
            async with session.delete(AUTOSTOCKS_URL, headers=self.headers, params=params, timeout=5) as response:
                return response.status in [200, 204]
        except Exception as e:
            logger.error(f"❌ remove_autostock: {e}")
            return False
    
    async def get_users_tracking_item(self, item_name: str) -> List[int]:
        all_users = []
        offset = 0
        limit = 1000
        
        try:
            session = await self.get_session()
            while True:
                params = {
                    "item_name": f"eq.{item_name}",
                    "select": "user_id",
                    "limit": limit,
                    "offset": offset
                }
                async with session.get(AUTOSTOCKS_URL, headers=self.headers, params=params, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if not data:
                            break
                        all_users.extend([item['user_id'] for item in data])
                        if len(data) < limit:
                            break
                        offset += limit
                        await asyncio.sleep(0.05)
                    else:
                        break
            return all_users
        except Exception as e:
            logger.error(f"❌ get_users_tracking: {e}")
            return all_users

# ========== DISCORD ПАРСЕР ==========
class DiscordStockParser:
    def __init__(self):
        self.db = SupabaseDB()
        self.telegram_bot: Optional[Bot] = None
    
    def parse_stock_message(self, content: str, embeds: List[discord.Embed]) -> Dict:
        """Парсит сообщения от Stock Notifier через embed fields"""
        result = {"seeds": [], "gear": []}
        
        if not embeds:
            logger.warning("⚠️ Нет embeds для парсинга")
            return result
        
        logger.info(f"🔍 Парсинг {len(embeds)} embeds")
        
        for embed in embeds:
            if not embed.fields:
                continue
            
            logger.info(f"📋 Обработка embed с {len(embed.fields)} полями")
            
            for field in embed.fields:
                # field.name = "<:Sunflower:1426493232933634080> Sunflower"
                # field.value = "+2 stock (<@&1408040455949647943>)"
                
                # Извлекаем название предмета из field.name
                # Убираем кастомные эмодзи формата <:Name:ID>
                name_clean = re.sub(r'<:[^:]+:\d+>\s*', '', field.name).strip()
                
                # Извлекаем количество из field.value
                value_match = re.search(r'\+(\d+)\s+stock', field.value, re.IGNORECASE)
                
                if not value_match:
                    continue
                
                quantity = int(value_match.group(1))
                
                # Нормализуем название
                item_name = self.normalize_item_name(name_clean)
                
                if item_name:
                    category = ITEMS_DATA[item_name]['category']
                    result[f"{category}s"].append((item_name, quantity))
                    logger.info(f"✅ Найден: {item_name} x{quantity} ({category})")
                else:
                    logger.warning(f"⚠️ Не распознан предмет: '{name_clean}' из field.name: '{field.name}'")
        
        logger.info(f"📊 Результат: {len(result['seeds'])} семян, {len(result['gear'])} снаряжения")
        return result
    
    def normalize_item_name(self, raw_name: str) -> Optional[str]:
        """Нормализует название предмета"""
        raw_name = raw_name.strip().lower()
        
        # Убираем лишние слова и символы
        raw_name = re.sub(r'\s*(seed|gun|launcher|grenade|bucket|blower)\s*', '', raw_name, flags=re.IGNORECASE)
        raw_name = raw_name.strip()
        
        # Прямое сопоставление
        for item_name in ITEMS_DATA.keys():
            if item_name.lower() == raw_name:
                return item_name
        
        # Маппинг вариаций
        name_map = {
            'dragon': 'Dragon Fruit',
            'dragon fruit': 'Dragon Fruit',
            'coco': 'Cocotank',
            'cocotank': 'Cocotank',
            'carnivorous': 'Carnivorous Plant',
            'carnivorous plant': 'Carnivorous Plant',
            'mr carrot': 'Mr Carrot',
            'carrot': 'Mr Carrot',
            'tomatrio': 'Tomatrio',
            'tomato': 'Tomatrio',
            'shroombino': 'Shroombino',
            'mushroom': 'Shroombino',
            'mango': 'Mango',
            'limone': 'King Limone',
            'king limone': 'King Limone',
            'king lemon': 'King Limone',
            'lemon': 'King Limone',
            'starfruit': 'Starfruit',
            'star': 'Starfruit',
            'brussel sprouts': 'Brussel Sprouts',
            'brussel': 'Brussel Sprouts',
            'sprouts': 'Brussel Sprouts',
            'water': 'Water Bucket',
            'water bucket': 'Water Bucket',
            'bucket': 'Water Bucket',
            'frost': 'Frost Grenade',
            'frost grenade': 'Frost Grenade',
            'banana': 'Banana Gun',
            'banana gun': 'Banana Gun',
            'frost blower': 'Frost Blower',
            'blower': 'Frost Blower',
            'carrot launcher': 'Carrot Launcher',
            'launcher': 'Carrot Launcher',
            'sunflower': 'Sunflower',
            'pumpkin': 'Pumpkin',
            'strawberry': 'Strawberry',
            'cactus': 'Cactus',
            'eggplant': 'Eggplant',
            'watermelon': 'Watermelon',
            'grape': 'Grape'
        }
        
        return name_map.get(raw_name)
    
    def format_stock_message(self, stock_data: Dict) -> str:
        if not stock_data:
            return "❌ *Не удалось получить данные*"
        
        message = "📊 *ТЕКУЩИЙ СТОК*\n\n"
        
        # Семена
        seeds = stock_data.get('seeds', [])
        message += "🌱 *СЕМЕНА:*\n"
        if seeds:
            for item_name, quantity in seeds:
                item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "?"})
                message += f"{item_info['emoji']} *{item_name}*: x{quantity} ({item_info['price']})\n"
        else:
            message += "_Пусто_\n"
        
        # Снаряжение
        gear = stock_data.get('gear', [])
        if gear:
            message += "\n⚔️ *СНАРЯЖЕНИЕ:*\n"
            for item_name, quantity in gear:
                item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "?"})
                message += f"{item_info['emoji']} *{item_name}*: x{quantity} ({item_info['price']})\n"
        
        current_time = get_moscow_time().strftime("%H:%M:%S")
        message += f"\n🕒 _Обновлено: {current_time} МСК_"
        return message
    
    def should_notify_item(self, item_name: str) -> bool:
        """Проверяет, можно ли отправлять уведомления для предмета (глобальный кулдаун)"""
        if item_name not in item_last_seen:
            return True
        now = get_moscow_time()
        last_time = item_last_seen[item_name]
        return (now - last_time).total_seconds() >= 90
    
    def can_send_to_user(self, user_id: int, item_name: str) -> bool:
        if user_id not in user_sent_notifications:
            return True
        if item_name not in user_sent_notifications[user_id]:
            return True
        now = get_moscow_time()
        last_time = user_sent_notifications[user_id][item_name]
        return (now - last_time).total_seconds() >= USER_NOTIFICATION_COOLDOWN
    
    async def send_autostock_notification(self, bot: Bot, user_id: int, item_name: str, count: int) -> bool:
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "?"})
            current_time = get_moscow_time().strftime("%H:%M:%S")
            
            message = (
                f"🔔 *АВТОСТОК - {item_name}!*\n\n"
                f"{item_info['emoji']} *{item_name}*\n"
                f"📦 Количество: *x{count}*\n"
                f"💰 Цена: {item_info['price']}\n"
                f"🕒 {current_time} МСК"
            )
            
            await bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.MARKDOWN)
            
            if user_id not in user_sent_notifications:
                user_sent_notifications[user_id] = {}
            user_sent_notifications[user_id][item_name] = get_moscow_time()
            
            return True
        except TelegramError as e:
            error_msg = str(e).lower()
            if "forbidden" in error_msg or "blocked" in error_msg or "bot was blocked" in error_msg or "user is deactivated" in error_msg:
                logger.info(f"🚫 Пользователь {user_id} заблокировал бота или удалил аккаунт")
                asyncio.create_task(self.cleanup_blocked_user(user_id))
                return False
            else:
                logger.warning(f"⚠️ Ошибка отправки {user_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при отправке {user_id}: {e}")
            return False
    
    async def cleanup_blocked_user(self, user_id: int):
        try:
            await self.db.delete_user_autostocks(user_id)
            await self.db.delete_user(user_id)
            
            user_autostocks_cache.pop(user_id, None)
            user_autostocks_time.pop(user_id, None)
            subscription_cache.pop(user_id, None)
            user_sent_notifications.pop(user_id, None)
            
            logger.info(f"✅ Очищен {user_id}")
        except Exception as e:
            logger.error(f"❌ Очистка {user_id}: {e}")
    
    async def check_user_autostocks(self, stock_data: Dict, bot: Bot):
        """Проверяет автостоки и отправляет уведомления пользователям"""
        if not stock_data:
            logger.warning("❌ stock_data пустой")
            return
        
        logger.info(f"🔍 Начало проверки автостоков. Данные: {stock_data}")
        
        current_stock = {}
        for stock_type in ['seeds', 'gear']:
            for item_name, quantity in stock_data.get(stock_type, []):
                if quantity > 0:
                    current_stock[item_name] = quantity
        
        if not current_stock:
            logger.warning("📭 Нет предметов в стоке для уведомлений")
            return
        
        logger.info(f"📦 Предметы в текущем стоке: {current_stock}")
        
        # Параллельная загрузка пользователей для всех предметов
        item_names = list(current_stock.keys())
        logger.info(f"🔎 Загружаем пользователей для предметов: {item_names}")
        
        user_tasks = [self.db.get_users_tracking_item(item_name) for item_name in item_names]
        users_results = await asyncio.gather(*user_tasks, return_exceptions=True)
        
        item_users_map = {}
        for item_name, result in zip(item_names, users_results):
            if isinstance(result, Exception):
                logger.error(f"❌ Ошибка загрузки пользователей для {item_name}: {result}")
                continue
            if result:
                item_users_map[item_name] = result
                logger.info(f"👥 {item_name}: {len(result)} пользователей отслеживают → {result}")
            else:
                logger.info(f"📭 {item_name}: нет пользователей")
        
        if not item_users_map:
            logger.warning("📭 Нет пользователей для уведомлений")
            return
        
        # Отправка уведомлений по каждому предмету
        for item_name, count in current_stock.items():
            logger.info(f"🔔 Обработка предмета: {item_name} (x{count})")
            
            # Проверяем глобальный кулдаун для предмета
            if not self.should_notify_item(item_name):
                last_time = item_last_seen.get(item_name)
                if last_time:
                    elapsed = (get_moscow_time() - last_time).total_seconds()
                    logger.warning(f"⏸️ {item_name}: глобальный кулдаун активен (прошло {elapsed:.0f}s из 90s)")
                continue
            
            users = item_users_map.get(item_name, [])
            if not users:
                logger.info(f"📭 {item_name}: нет пользователей для уведомления")
                continue
            
            logger.info(f"🚀 Отправка уведомлений для {item_name} → {len(users)} пользователям: {users}")
            item_last_seen[item_name] = get_moscow_time()
            
            sent = 0
            skipped = 0
            errors = 0
            
            # Отправка небольшими пакетами для избежания rate limits
            batch_size = 25
            for i in range(0, len(users), batch_size):
                batch = users[i:i + batch_size]
                send_tasks = []
                
                for user_id in batch:
                    # Проверяем персональный кулдаун пользователя
                    if not self.can_send_to_user(user_id, item_name):
                        last_notif = user_sent_notifications.get(user_id, {}).get(item_name)
                        if last_notif:
                            elapsed = (get_moscow_time() - last_notif).total_seconds()
                            logger.debug(f"⏸️ {item_name} → user {user_id}: персональный кулдаун (прошло {elapsed:.0f}s из 120s)")
                        skipped += 1
                        continue
                    
                    logger.info(f"✉️ Отправка {item_name} → user {user_id}")
                    send_tasks.append(self.send_autostock_notification(bot, user_id, item_name, count))
                
                if send_tasks:
                    results = await asyncio.gather(*send_tasks, return_exceptions=True)
                    for idx, result in enumerate(results):
                        if result is True:
                            sent += 1
                            logger.info(f"✅ Успешно отправлено user {batch[idx]}")
                        elif isinstance(result, Exception):
                            errors += 1
                            logger.error(f"❌ Ошибка отправки user {batch[idx]}: {result}")
                        else:
                            logger.warning(f"⚠️ Неожиданный результат для user {batch[idx]}: {result}")
                    
                    # Небольшая задержка между пакетами
                    if i + batch_size < len(users):
                        await asyncio.sleep(0.1)
            
            logger.info(f"📊 {item_name} итоги: ✅ отправлено {sent}, ⏸️ пропущено {skipped}, ❌ ошибок {errors}")
            
            # Задержка между разными предметами
            await asyncio.sleep(0.05)
        
        logger.info("✅ Проверка автостоков завершена")

parser = DiscordStockParser()

# ========== DISCORD CLIENT ==========
class PVBDiscordClient(discord.Client):
    def __init__(self):
        super().__init__()
        self.stock_channel = None
    
    async def on_ready(self):
        logger.info(f'✅ Discord подключен: {self.user}')
        self.stock_channel = self.get_channel(DISCORD_STOCK_CHANNEL_ID)
        if self.stock_channel:
            logger.info(f"✅ Канал стоков найден: {self.stock_channel.name}")
        else:
            logger.error("❌ Канал стоков не найден!")
    
    async def on_message(self, message: discord.Message):
        """Реакция на новые сообщения в канале стоков"""
        if message.channel.id != DISCORD_STOCK_CHANNEL_ID:
            return
        
        if not message.author.bot:
            return
        
        # Игнорируем StickyBot
        if 'StickyBot' in str(message.author.name):
            return
        
        # Проверяем embeds (там находятся данные)
        if not message.embeds:
            return
        
        # Проверяем title embed на наличие "restock"
        has_restock = any('restock' in (embed.title or '').lower() for embed in message.embeds)
        if not has_restock:
            return
        
        logger.info(f"📨 ===== НОВОЕ RESTOCK СООБЩЕНИЕ =====")
        logger.info(f"От: {message.author.name}")
        logger.info(f"Время: {get_moscow_time().strftime('%H:%M:%S')}")
        
        try:
            # Парсим сообщение
            stock_data = parser.parse_stock_message(message.content, message.embeds)
            
            if not stock_data['seeds'] and not stock_data['gear']:
                logger.warning("⚠️ Не удалось распарсить стоки")
                return
            
            # Обновляем кэш
            global stock_cache, stock_cache_time
            stock_cache = stock_data
            stock_cache_time = get_moscow_time()
            
            logger.info(f"✅ Стоки обновлены в кэше: {len(stock_data['seeds'])} семян, {len(stock_data['gear'])} снаряжения")
            logger.info(f"📦 Детали стоков: {stock_data}")
            
            # ВАЖНО: Отправляем автосток уведомления СРАЗУ
            if parser.telegram_bot:
                logger.info("🚀 Запуск отправки уведомлений...")
                await parser.check_user_autostocks(stock_data, parser.telegram_bot)
            else:
                logger.error("❌ Telegram bot не инициализирован!")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения: {e}", exc_info=True)
    
    async def fetch_latest_stock(self) -> Dict:
        """Получение последних стоков из истории"""
        global stock_cache, stock_cache_time
        
        now = get_moscow_time()
        if stock_cache and stock_cache_time:
            if (now - stock_cache_time).total_seconds() < STOCK_CACHE_SECONDS:
                logger.debug("📦 Возврат из кэша")
                return stock_cache
        
        if not self.stock_channel:
            logger.error("❌ Канал стоков недоступен")
            return {"seeds": [], "gear": []}
        
        try:
            logger.info("🔍 Поиск последнего stock сообщения в истории...")
            
            async for msg in self.stock_channel.history(limit=10):
                if not msg.author.bot or 'StickyBot' in str(msg.author.name):
                    continue
                
                if not msg.embeds:
                    continue
                
                # Проверяем title embed на "restock"
                has_restock = any('restock' in (embed.title or '').lower() for embed in msg.embeds)
                
                if has_restock:
                    logger.info(f"✅ Найдено stock сообщение от {msg.author.name}")
                    stock_data = parser.parse_stock_message(msg.content, msg.embeds)
                    
                    if stock_data['seeds'] or stock_data['gear']:
                        stock_cache = stock_data
                        stock_cache_time = now
                        logger.info(f"📦 Загружено: {len(stock_data['seeds'])} семян, {len(stock_data['gear'])} снаряжения")
                        return stock_data
            
            logger.warning("⚠️ Stock сообщения не найдены в истории")
            return {"seeds": [], "gear": []}
        except Exception as e:
            logger.error(f"❌ fetch_latest_stock: {e}", exc_info=True)
            return {"seeds": [], "gear": []}

# ========== КОМАНДЫ ==========
async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    is_subscribed, not_subscribed = await check_subscription(user_id, context.bot, use_cache=False)
    
    if is_subscribed:
        await query.edit_message_text(
            "✅ *ПОДПИСКА ПОДТВЕРЖДЕНА!*\n\n"
            "📊 /stock - Текущий сток\n"
            "🔔 /autostock - Автостоки\n",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        channels_text = "\n".join([f"• {ch}" for ch in not_subscribed])
        await query.edit_message_text(
            f"❌ *ПОДПИСКА НЕ НАЙДЕНА*\n\n"
            f"Подпишитесь:\n{channels_text}",
            reply_markup=get_subscription_keyboard(not_subscribed),
            parse_mode=ParseMode.MARKDOWN
        )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_user:
        return
    
    user = update.effective_user
    asyncio.create_task(parser.db.save_user(user.id, user.username, user.first_name))
    
    welcome_message = (
        "👋 *Plants vs Brainrots Stock Tracker!*\n\n"
        "📊 /stock - Текущий сток\n"
        "🔔 /autostock - Автостоки\n"
        "❓ /help - Справка\n\n"
        f"📢 {REQUIRED_CHANNELS[0]}\n"
        f"📢 {REQUIRED_CHANNELS[1]}"
    )
    await update.effective_message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    user_id = update.effective_user.id
    asyncio.create_task(parser.db.save_user(user_id, update.effective_user.username, update.effective_user.first_name))
    
    if update.effective_chat.type == ChatType.PRIVATE:
        is_subscribed, not_subscribed = await check_subscription(user_id, context.bot)
        if not is_subscribed:
            channels_text = "\n".join([f"• {ch}" for ch in not_subscribed])
            await update.effective_message.reply_text(
                f"⚠️ *Подпишитесь на каналы*\n\n{channels_text}",
                reply_markup=get_subscription_keyboard(not_subscribed),
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    if not discord_client or not discord_client.is_ready():
        await update.effective_message.reply_text("⚠️ *Discord загружается...*", parse_mode=ParseMode.MARKDOWN)
        return
    
    stock_data = await discord_client.fetch_latest_stock()
    message = parser.format_stock_message(stock_data)
    await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def autostock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    
    user_id = update.effective_user.id
    asyncio.create_task(parser.db.save_user(user_id, update.effective_user.username, update.effective_user.first_name))
    
    is_subscribed, not_subscribed = await check_subscription(user_id, context.bot)
    if not is_subscribed:
        channels_text = "\n".join([f"• {ch}" for ch in not_subscribed])
        await update.effective_message.reply_text(
            f"⚠️ *Подпишитесь на каналы*\n\n{channels_text}",
            reply_markup=get_subscription_keyboard(not_subscribed),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("🌱 Семена", callback_data="as_seeds")],
        [InlineKeyboardButton("📋 Мои автостоки", callback_data="as_list")],
    ]
    
    message = (
        "🔔 *УПРАВЛЕНИЕ АВТОСТОКАМИ*\n\n"
        "Выберите категорию.\n\n"
        "💡 Вы получите мгновенное уведомление при появлении предмета в стоке!"
    )
    
    await update.effective_message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    
    if update.effective_user.id != ADMIN_ID:
        return
    
    stats = (
        f"📊 *СТАТИСТИКА*\n\n"
        f"*Кэши:*\n"
        f"• Автостоки: {len(user_autostocks_cache)}\n"
        f"• Подписки: {len(subscription_cache)}\n"
        f"• Уведомления: {len(user_sent_notifications)}\n"
        f"• Предметы: {len(item_last_seen)}\n\n"
        f"*Discord:* {'✅' if discord_client and discord_client.is_ready() else '❌'}\n"
        f"*Telegram:* ✅"
    )
    
    await update.effective_message.reply_text(stats, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_user:
        return
    
    help_text = (
        "📚 *СПРАВКА*\n\n"
        "📊 /stock - Текущий сток\n"
        "🔔 /autostock - Управление автостоками\n"
        "❓ /help - Эта справка\n\n"
        "*Автостоки:*\n"
        "Добавьте предметы в автостоки, и вы получите уведомление, "
        "как только они появятся в стоке!\n\n"
        "*Обновление:*\n"
        "Стоки обновляются автоматически при каждом новом сообщении в Discord."
    )
    
    await update.effective_message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def autostock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    try:
        if data == "as_seeds":
            user_items = await parser.db.load_user_autostocks(user_id, use_cache=True)
            keyboard = []
            for item_name, item_info in SEED_ITEMS_LIST:
                is_tracking = item_name in user_items
                status = "✅" if is_tracking else "➕"
                safe_callback = NAME_TO_ID.get(item_name, "invalid")
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name} - {item_info['price']}",
                    callback_data=safe_callback
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
            
            await query.edit_message_text(
                "🌱 *СЕМЕНА*\n\nНажмите чтобы добавить/убрать:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif data == "as_gear":
            user_items = await parser.db.load_user_autostocks(user_id, use_cache=True)
            keyboard = []
            for item_name, item_info in GEAR_ITEMS_LIST:
                is_tracking = item_name in user_items
                status = "✅" if is_tracking else "➕"
                safe_callback = NAME_TO_ID.get(item_name, "invalid")
                keyboard.append([InlineKeyboardButton(
                    f"{status} {item_info['emoji']} {item_name} - {item_info['price']}",
                    callback_data=safe_callback
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
            
            await query.edit_message_text(
                "⚔️ *СНАРЯЖЕНИЕ*\n\nНажмите чтобы добавить/убрать:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif data == "as_list":
            user_items = await parser.db.load_user_autostocks(user_id, use_cache=True)
            if not user_items:
                message = "📋 *МОИ АВТОСТОКИ*\n\n_Нет отслеживаемых предметов_"
            else:
                items_list = []
                for item_name in sorted(user_items):
                    item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "?"})
                    items_list.append(f"{item_info['emoji']} *{item_name}* ({item_info['price']})")
                message = f"📋 *МОИ АВТОСТОКИ* ({len(user_items)})\n\n" + "\n".join(items_list)
            
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="as_back")]]
            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        
        elif data == "as_back":
            keyboard = [
                [InlineKeyboardButton("🌱 Семена", callback_data="as_seeds")],
                [InlineKeyboardButton("📋 Мои автостоки", callback_data="as_list")],
            ]
            message = "🔔 *УПРАВЛЕНИЕ АВТОСТОКАМИ*\n\nВыберите категорию."
            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        
        elif data.startswith("t_"):
            item_name = ID_TO_NAME.get(data)
            if not item_name:
                await query.answer("❌ Ошибка", show_alert=True)
                return
            
            category = ITEMS_DATA.get(item_name, {}).get('category', 'seed')
            user_items = await parser.db.load_user_autostocks(user_id, use_cache=True)
            
            if item_name in user_items:
                user_items.discard(item_name)
                asyncio.create_task(parser.db.remove_user_autostock(user_id, item_name))
                await query.answer(f"❌ {item_name} убран", show_alert=False)
            else:
                user_items.add(item_name)
                asyncio.create_task(parser.db.save_user_autostock(user_id, item_name))
                await query.answer(f"✅ {item_name} добавлен", show_alert=False)
            
            items_list = SEED_ITEMS_LIST if category == 'seed' else GEAR_ITEMS_LIST
            keyboard = []
            for name, info in items_list:
                is_tracking = name in user_items
                status = "✅" if is_tracking else "➕"
                safe_callback = NAME_TO_ID.get(name, "invalid")
                keyboard.append([InlineKeyboardButton(
                    f"{status} {info['emoji']} {name} - {info['price']}",
                    callback_data=safe_callback
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_back")])
            
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            except TelegramError:
                pass
    
    except Exception as e:
        logger.error(f"❌ Callback: {e}")

# ========== FLASK ==========
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET", "HEAD"])
@flask_app.route("/ping", methods=["GET", "HEAD"])
def ping():
    if flask_request.method == "HEAD":
        return "", 200
    
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "moscow_time": get_moscow_time().strftime("%H:%M:%S"),
        "bot": "PVB Stock Tracker v3.2 FIXED",
        "discord": discord_client.is_ready() if discord_client else False,
        "cache_size": len(user_autostocks_cache)
    }), 200

@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "discord": discord_client.is_ready() if discord_client else False
    }), 200

# ========== ИНИЦИАЛИЗАЦИЯ ==========
async def post_init(application: Application):
    parser.telegram_bot = application.bot
    logger.info("✅ Telegram bot инициализирован")

# ========== MAIN ==========
def main():
    logger.info("="*60)
    logger.info("🌱 PVB Stock Tracker Bot v3.2 - FIXED PARSER")
    logger.info("="*60)
    
    build_item_id_mappings()
    
    global discord_client, telegram_app
    
    discord_client = PVBDiscordClient()
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("stock", stock_command))
    telegram_app.add_handler(CommandHandler("autostock", autostock_command))
    telegram_app.add_handler(CommandHandler("stats", stats_command))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="^check_subscription$"))
    telegram_app.add_handler(CallbackQueryHandler(autostock_callback, pattern="^as_|^t_"))
    
    telegram_app.post_init = post_init
    
    async def shutdown_callback(app: Application):
        logger.info("🛑 Остановка бота...")
        if discord_client:
            await discord_client.close()
        if http_session and not http_session.closed:
            await http_session.close()
    
    telegram_app.post_shutdown = shutdown_callback
    
    async def run_both():
        discord_task = asyncio.create_task(discord_client.start(DISCORD_TOKEN))
        
        while not discord_client.is_ready():
            await asyncio.sleep(0.5)
        
        logger.info("✅ Discord клиент готов к работе")
        
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(allowed_updates=None, drop_pending_updates=True)
        
        logger.info("🚀 БОТ УСПЕШНО ЗАПУЩЕН!")
        logger.info(f"👤 Admin ID: {ADMIN_ID}")
        logger.info(f"📢 Обязательные каналы: {', '.join(REQUIRED_CHANNELS)}")
        logger.info(f"📡 Discord канал стоков: {DISCORD_STOCK_CHANNEL_ID}")
        logger.info("="*60)
        
        try:
            await discord_task
        except KeyboardInterrupt:
            pass
        finally:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
    
    def run_flask_server():
        port = int(os.getenv("PORT", "5000"))
        logger.info(f"🚀 Flask сервер запущен на порту {port}")
        import logging as flask_logging
        flask_log = flask_logging.getLogger('werkzeug')
        flask_log.setLevel(flask_logging.ERROR)
        flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    try:
        asyncio.run(run_both())
    except KeyboardInterrupt:
        logger.info("⚠️ Получен сигнал остановки")

if __name__ == "__main__":
    main()