import asyncio
import aiohttp
import logging
import os
import threading
from datetime import datetime
from typing import Dict, Optional
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from flask import Flask, jsonify
import pytz

# Настройки бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # Формат: @channel_username или -100xxxxx для приватных каналов
API_URL = "https://plantsvsbrainrotsstocktracker.com/api/stock?since=1759075506296"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))  # По умолчанию 30 секунд

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
    "Cocotank": {"emoji": "🥥", "price": "$5m", "category": "seed"},
    "Carnivorous Plant": {"emoji": "🪴", "price": "$25m", "category": "seed"},
    "Mr Carrot": {"emoji": "🥕", "price": "$50m", "category": "seed"},
    "Tomatrio": {"emoji": "🍅", "price": "$125m", "category": "seed"},
    "Shroombino": {"emoji": "🍄", "price": "$200m", "category": "seed"},

    # Снаряжение
    "Bat": {"emoji": "🏏", "price": "Free", "category": "gear"},
    "Water Bucket": {"emoji": "🪣", "price": "$7,500", "category": "gear"},
    "Frost Grenade": {"emoji": "❄️", "price": "$12,500", "category": "gear"},
    "Frost grenade": {"emoji": "❄️", "price": "$12,500", "category": "gear"},
    "Banana Gun": {"emoji": "🍌", "price": "$25,000", "category": "gear"},
    "Frost Blower": {"emoji": "🌬️", "price": "$125,000", "category": "gear"},
    "Lucky Potion": {"emoji": "🍀", "price": "TBD", "category": "gear"},
    "Speed Potion": {"emoji": "⚡", "price": "TBD", "category": "gear"},
    "Carrot Launcher": {"emoji": "🥕", "price": "$500,000", "category": "gear"}
}

# Предметы для уведомлений
NOTIFICATION_ITEMS = ["Mr Carrot", "Tomatrio", "Shroombino"]

# Хранение последнего состояния стока
last_stock_state = {}

class StockTracker:
    def __init__(self):
        self.session = None
        self.bot = None

    async def init_session(self):
        """Инициализация aiohttp сессии"""
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Закрытие aiohttp сессии"""
        if self.session:
            await self.session.close()

    async def fetch_stock(self) -> Optional[Dict]:
        """Получение данных о стоке"""
        try:
            await self.init_session()
            async with self.session.get(API_URL, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"API вернул статус {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Ошибка при получении стока: {e}")
            return None

    def format_stock_message(self, stock_data: Dict) -> str:
        """Форматирование сообщения о стоке"""
        if not stock_data or 'data' not in stock_data:
            return "❌ *Не удалось получить данные о стоке*"

        seeds = []
        gear = []

        for item in stock_data['data']:
            name = item.get('name', '')
            stock_count = item.get('stock', 0)
            available = item.get('available', False)
            category = item.get('category', '')

            if not available or stock_count == 0:
                continue

            item_info = ITEMS_DATA.get(name, {"emoji": "📦", "price": "Unknown"})
            emoji = item_info['emoji']
            price = item_info['price']

            formatted_item = f"{emoji} *{name}*: x{stock_count} ({price})"

            if category == 'SEEDS':
                seeds.append(formatted_item)
            elif category == 'GEAR':
                gear.append(formatted_item)

        # Формирование сообщения
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

        # Добавляем время обновления
        moscow_tz = pytz.timezone('Europe/Moscow')
        current_time = datetime.now(moscow_tz).strftime("%H:%M:%S")
        message += f"🕒 _Обновлено: {current_time} МСК_"

        return message

    async def check_for_notifications(self, stock_data: Dict, bot: Bot, channel_id: str):
        """Проверка на наличие редких предметов и отправка уведомлений"""
        global last_stock_state

        if not stock_data or 'data' not in stock_data or not channel_id:
            return

        current_stock = {}
        for item in stock_data['data']:
            name = item.get('name', '')
            stock_count = item.get('stock', 0)
            available = item.get('available', False)

            if available and stock_count > 0:
                current_stock[name] = stock_count

        # Проверяем редкие предметы
        for item_name in NOTIFICATION_ITEMS:
            if item_name in current_stock:
                # Если предмет появился в стоке или его количество увеличилось
                if item_name not in last_stock_state or current_stock[item_name] > last_stock_state.get(item_name, 0):
                    await self.send_notification(bot, channel_id, item_name, current_stock[item_name])

        # Обновляем последнее состояние
        last_stock_state = current_stock

    async def send_notification(self, bot: Bot, channel_id: str, item_name: str, count: int):
        """Отправка уведомления в канал"""
        try:
            item_info = ITEMS_DATA.get(item_name, {"emoji": "📦", "price": "Unknown"})
            emoji = item_info['emoji']

            moscow_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(moscow_tz).strftime("%H:%M")

            channel_mention = channel_id if channel_id and channel_id.startswith('@') else channel_id

            message = (
                f"{emoji} *{item_name}: x{count} в стоке!*\n"
                f"🕒 {current_time} МСК\n\n"
                f"{channel_mention or ''}"
            )

            await bot.send_message(
                chat_id=channel_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"Отправлено уведомление о {item_name} в {channel_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления: {e}")

# Экземпляр трекера
tracker = StockTracker()

async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stock"""
    await update.message.reply_text("⏳ *Загрузка данных о стоке...*", parse_mode=ParseMode.MARKDOWN)

    stock_data = await tracker.fetch_stock()
    message = tracker.format_stock_message(stock_data)

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    channel_info = f"🔔 Канал для уведомлений: {CHANNEL_ID}\n" if CHANNEL_ID else "🔕 Канал для уведомлений не настроен\n"

    welcome_message = (
        "👋 *Добро пожаловать в Plants vs Brainrots Stock Tracker!*\n\n"
        "📊 Используйте команду /stock чтобы посмотреть текущий сток\n\n"
        f"{channel_info}"
        "📦 Бот отслеживает редкие предметы:\n"
        "• 🥕 Mr Carrot ($50m)\n"
        "• 🍅 Tomatrio ($125m)\n"
        "• 🍄 Shroombino ($200m)\n\n"
        f"_Бот проверяет сток каждые {CHECK_INTERVAL} секунд_"
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_message = (
        "📚 *Доступные команды:*\n\n"
        "/start - Информация о боте\n"
        "/stock - Показать текущий сток\n"
        "/help - Это сообщение\n\n"
        "💡 *Подсказка:* Бот автоматически проверяет сток и отправляет уведомления о редких предметах!"
    )
    await update.message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN)

async def periodic_stock_check(application: Application):
    """Периодическая проверка стока"""
    bot = application.bot

    logger.info(f"Запущена периодическая проверка стока (интервал: {CHECK_INTERVAL} сек)")
    if CHANNEL_ID:
        logger.info(f"Уведомления будут отправляться в: {CHANNEL_ID}")
    else:
        logger.info("Канал для уведомлений не настроен")

    while True:
        try:
            stock_data = await tracker.fetch_stock()
            if stock_data and CHANNEL_ID:
                # Исправленный порядок аргументов
                await tracker.check_for_notifications(stock_data, bot, CHANNEL_ID)

            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Ошибка в периодической проверке: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

async def post_init(application: Application):
    """Запуск периодической проверки после инициализации"""
    asyncio.create_task(periodic_stock_check(application))

# --- Flask часть (для пингера / keep-alive) ---
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
@flask_app.route("/ping", methods=["GET"])
def ping():
    # Возвращаем простой ответ — пингер будет считать инстанс живым
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}), 200

def run_flask():
    port = int(os.getenv("PORT", "5000"))
    # реком: в продакшене нужно использовать gunicorn/uvicorn; для Render простого Flask.run бывает достаточно
    logger.info(f"Запуск Flask на 0.0.0.0:{port}")
    flask_app.run(host="0.0.0.0", port=port, threaded=True)

def main():
    """Основная функция запуска бота и Flask"""
    logger.info("Запуск бота...")

    # Запуск Flask в отдельном потоке, чтобы внешний пингер мог дергать /ping
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info(f"Интервал проверки: {CHECK_INTERVAL} секунд")

    # Создание приложения Telegram
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавление обработчиков команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stock", stock_command))
    application.add_handler(CommandHandler("help", help_command))

    # Запуск периодической проверки после инициализации
    application.post_init = post_init

    # Запуск бота (блокирующий вызов)
    logger.info("Бот успешно запущен! Нажмите Ctrl+C для остановки.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
