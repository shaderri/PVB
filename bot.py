import asyncio
import aiohttp
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Set
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from flask import Flask, jsonify
import pytz
from dotenv import load_dotenv

load_dotenv()

# Настройки бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # Формат: @channel_username или -100xxxxx
API_URL = os.getenv("API_URL", "https://plantsvsbrainrotsstocktracker.com/api/stock?since=1759075506296")

# НОВАЯ ЛОГИКА: проверка каждые 5 минут + 6 секунд
CHECK_INTERVAL_MINUTES = 5
CHECK_DELAY_SECONDS = 6  # Задержка после каждых 5 минут для обновления API

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
last_stock_state: Dict[str, int] = {}

# Хранение времени последнего уведомления для каждого предмета (защита от спама)
last_notification_time: Dict[str, datetime] = {}

# Минимальный интервал между уведомлениями об одном предмете (в секундах)
NOTIFICATION_COOLDOWN = 300  # 5 минут


def get_next_check_time() -> datetime:
    """Вычисляет следующее время проверки (каждые 5 минут + 6 секунд)"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    
    # Находим текущую минуту, кратную 5
    current_minute = now.minute
    next_minute = ((current_minute // CHECK_INTERVAL_MINUTES) + 1) * CHECK_INTERVAL_MINUTES
    
    if next_minute >= 60:
        # Переход на следующий час
        next_check = now.replace(minute=0, second=CHECK_DELAY_SECONDS, microsecond=0) + timedelta(hours=1)
    else:
        # Остаемся в текущем часе
        next_check = now.replace(minute=next_minute, second=CHECK_DELAY_SECONDS, microsecond=0)
    
    # Если следующее время уже прошло, добавляем еще 5 минут
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

    async def init_session(self):
        """Инициализация aiohttp сессии"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Закрытие aiohttp сессии"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_stock(self) -> Optional[Dict]:
        """Получение данных о стоке"""
        try:
            await self.init_session()
            async with self.session.get(API_URL, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"✅ Получены данные о стоке: {len(data.get('data', []))} предметов")
                    return data
                else:
                    logger.error(f"❌ API вернул статус {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.error("❌ Timeout при запросе к API")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка при получении стока: {e}")
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
        
        # Проверяем, прошло ли достаточно времени
        if (now - last_time).total_seconds() >= NOTIFICATION_COOLDOWN:
            return True
        
        return False

    async def check_for_notifications(self, stock_data: Dict, bot: Bot, channel_id: str):
        """Проверка на наличие редких предметов и отправка уведомлений"""
        global last_stock_state

        if not stock_data or 'data' not in stock_data or not channel_id:
            return

        current_stock = {}
        
        # Собираем текущие данные о стоке
        for item in stock_data['data']:
            name = item.get('name', '')
            stock_count = item.get('stock', 0)
            available = item.get('available', False)

            if available and stock_count > 0:
                current_stock[name] = stock_count

        logger.info(f"📦 Текущий сток редких предметов: {current_stock}")
        logger.info(f"📝 Предыдущий сток: {last_stock_state}")

        # Проверяем редкие предметы
        for item_name in NOTIFICATION_ITEMS:
            current_count = current_stock.get(item_name, 0)
            previous_count = last_stock_state.get(item_name, 0)
            
            # УСЛОВИЯ для отправки уведомления:
            # 1. Предмет появился в стоке (был 0, стал > 0)
            # 2. Количество предмета увеличилось
            # 3. Прошел cooldown с последнего уведомления
            
            should_notify = False
            
            if current_count > 0:
                if previous_count == 0:
                    # Предмет только что появился
                    should_notify = True
                    logger.info(f"🆕 {item_name} появился в стоке! (0 -> {current_count})")
                elif current_count > previous_count:
                    # Количество увеличилось
                    should_notify = True
                    logger.info(f"📈 {item_name} увеличился! ({previous_count} -> {current_count})")
            
            if should_notify and self.can_send_notification(item_name):
                await self.send_notification(bot, channel_id, item_name, current_count)
            elif should_notify and not self.can_send_notification(item_name):
                logger.info(f"⏳ {item_name}: уведомление отложено (cooldown)")

        # Обновляем последнее состояние
        last_stock_state = current_stock.copy()

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
            
            # Обновляем время последнего уведомления
            moscow_tz = pytz.timezone('Europe/Moscow')
            last_notification_time[item_name] = datetime.now(moscow_tz)
            
            logger.info(f"✅ Уведомление отправлено: {item_name} x{count} -> {channel_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке уведомления: {e}")


# Экземпляр трекера
tracker = StockTracker()


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stock"""
    if update.effective_message:
        await update.effective_message.reply_text("⏳ *Загрузка данных о стоке...*", parse_mode=ParseMode.MARKDOWN)

    stock_data = await tracker.fetch_stock()
    message = tracker.format_stock_message(stock_data)

    if update.effective_message:
        await update.effective_message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    channel_info = f"🔔 Канал для уведомлений: {CHANNEL_ID}" if CHANNEL_ID else "🔕 Канал для уведомлений не настроен"

    welcome_message = (
        "👋 *Добро пожаловать в Plants vs Brainrots Stock Tracker!*\n\n"
        "📊 Используйте команду /stock чтобы посмотреть текущий сток\n\n"
        f"{channel_info}\n\n"
        "📦 *Бот отслеживает редкие предметы:*\n"
        "• 🥕 Mr Carrot ($50m)\n"
        "• 🍅 Tomatrio ($125m)\n"
        "• 🍄 Shroombino ($200m)\n\n"
        f"⏱️ _Бот проверяет сток каждые {CHECK_INTERVAL_MINUTES} минут + {CHECK_DELAY_SECONDS} секунд_\n"
        f"_(например: 13:05:06, 13:10:06, 13:15:06)_"
    )
    if update.effective_message:
        await update.effective_message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_message = (
        "📚 *Доступные команды:*\n\n"
        "/start - Информация о боте\n"
        "/stock - Показать текущий сток\n"
        "/help - Это сообщение\n\n"
        "💡 *Подсказка:* Бот автоматически проверяет сток и отправляет уведомления о редких предметах!"
    )
    if update.effective_message:
        await update.effective_message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN)


async def periodic_stock_check(application: Application):
    """Периодическая проверка стока с синхронизацией по времени"""
    bot = application.bot

    moscow_tz = pytz.timezone('Europe/Moscow')
    
    logger.info(f"🚀 Запущена периодическая проверка стока")
    logger.info(f"⏱️ Интервал: каждые {CHECK_INTERVAL_MINUTES} минут + {CHECK_DELAY_SECONDS} секунд")
    logger.info(f"📝 Примеры времени проверки: 13:05:06, 13:10:06, 13:15:06")
    
    if CHANNEL_ID:
        logger.info(f"📢 Уведомления будут отправляться в: {CHANNEL_ID}")
    else:
        logger.warning("⚠️ Канал для уведомлений не настроен")

    # Ждем до следующего правильного времени перед первой проверкой
    initial_sleep = calculate_sleep_time()
    next_check = get_next_check_time()
    logger.info(f"⏳ Ожидание до первой проверки: {initial_sleep:.1f} сек (следующая проверка: {next_check.strftime('%H:%M:%S')})")
    await asyncio.sleep(initial_sleep)

    while True:
        try:
            now = datetime.now(moscow_tz)
            logger.info(f"\n{'='*50}")
            logger.info(f"🔍 ПРОВЕРКА СТОКА - {now.strftime('%H:%M:%S')} МСК")
            logger.info(f"{'='*50}")
            
            stock_data = await tracker.fetch_stock()
            
            if stock_data and CHANNEL_ID:
                await tracker.check_for_notifications(stock_data, bot, CHANNEL_ID)
            elif not CHANNEL_ID:
                logger.warning("⚠️ CHANNEL_ID не установлен, уведомления не отправляются")
            
            # Вычисляем время до следующей проверки
            sleep_time = calculate_sleep_time()
            next_check = get_next_check_time()
            
            logger.info(f"✅ Проверка завершена")
            logger.info(f"⏳ Следующая проверка: {next_check.strftime('%H:%M:%S')} (через {sleep_time:.1f} сек)")
            logger.info(f"{'='*50}\n")
            
            await asyncio.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"❌ Ошибка в периодической проверке: {e}", exc_info=True)
            # В случае ошибки ждем до следующего интервала
            await asyncio.sleep(calculate_sleep_time())


async def post_init(application: Application):
    """Запуск периодической проверки после инициализации"""
    asyncio.create_task(periodic_stock_check(application))


# --- Flask часть (для пингера / keep-alive) ---
flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET"])
@flask_app.route("/ping", methods=["GET"])
def ping():
    """Эндпоинт для пингера Render"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    next_check = get_next_check_time()
    
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "moscow_time": now.strftime("%H:%M:%S"),
        "next_check": next_check.strftime("%H:%M:%S"),
        "bot": "Plants vs Brainrots Stock Tracker"
    }), 200


@flask_app.route("/health", methods=["GET"])
def health():
    """Healthcheck эндпоинт"""
    return jsonify({"status": "healthy"}), 200


def run_flask():
    """Запуск Flask сервера"""
    port = int(os.getenv("PORT", "5000"))
    logger.info(f"🌐 Запуск Flask сервера на 0.0.0.0:{port}")
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
    application.add_handler(CommandHandler("help", help_command))

    # Запуск периодической проверки после инициализации
    application.post_init = post_init

    # Регистрация graceful shutdown для закрытия aiohttp сессии
    async def shutdown_callback(app: Application):
        logger.info("🛑 Завершение работы: закрытие aiohttp сессии")
        try:
            await tracker.close_session()
        except Exception as e:
            logger.exception(f"❌ Ошибка при закрытии aiohttp сессии: {e}")

    application.post_shutdown = shutdown_callback

    # Запуск бота (блокирующий вызов)
    logger.info("🚀 Бот успешно запущен!")
    logger.info("="*60)
    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()