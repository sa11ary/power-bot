import asyncio
import json
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BOT_TOKEN = os.getenv("8649041001:AAGuNYq4SfF_JCJWjWlEHpJULjq6NUkwQ-Y")
CHAT_ID = os.getenv("6504410659")
PORT = int(os.getenv("PORT", 8000))

TARGET_QUEUE = "6.2"
URL = "https://off.energy.mk.ua/"
STATE_FILE = "queue_6_2_state.json"

logging.basicConfig(level=logging.INFO)

if not BOT_TOKEN:
    logging.critical("ОШИБКА: Переменная BOT_TOKEN не найдена в Environment Variables!")

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


# --- Веб-сервер для поддержки активности Render ---
async def handle_ping(request):
    return web.Response(text="Bot is running 24/7", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {PORT}")


# --- Умный статус по времени ---
def get_current_status(schedule: list[str]) -> str:
    if not schedule:
        return "🟢 Свет есть. Отключений не запланировано."

    try:
        now = datetime.now(ZoneInfo("Europe/Kyiv"))
    except Exception:
        now = datetime.utcnow()

    current_time = now.strftime("%H:%M")
    upcoming = []

    for period in schedule:
        parts = period.split("-")
        if len(parts) == 2:
            start = parts[0].strip()
            end = parts[1].strip()

            if start <= current_time < end:
                return f"🔴 **Сейчас отключение (до {end})**\n\n🕒 Весь график:\n" + "\n".join(schedule)
            if current_time < start:
                upcoming.append(start)

    if upcoming:
        next_off = upcoming[0]
        return f"🟢 **Сейчас свет есть. Следующее отключение в {next_off}**\n\n🕒 Весь график:\n" + "\n".join(schedule)

    return "🟢 **Сейчас свет есть. Запланированные отключения на сегодня завершены.**\n\n🕒 Прошедший график:\n" + "\n".join(schedule)


# --- Асинхронный парсинг сайта ---
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📅 Посмотреть график")]],
        resize_keyboard=True,
    )

async def get_schedule() -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(URL, headers=headers) as response:
            response.raise_for_status()
            html = await response.text()

    soup = BeautifulSoup(html, "html.parser")
    schedule = []

    for element in soup.find_all(["tr", "div", "li"]):
        text = element.get_text(" ", strip=True)
        if TARGET_QUEUE in text:
            times = re.findall(r'\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2}', text)
            for t in times:
                clean_t = t.replace("–", "-").replace(" ", "")
                parts = clean_t.split("-")
                if len(parts) == 2:
                    schedule.append(f"{parts[0]} - {parts[1]}")

    return sorted(list(set(schedule)))


def load_saved_schedule() -> list[str]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_schedule(schedule: list[str]):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения: {e}")

def compare_schedules(old: list[str], new: list[str]) -> str:
    old_set, new_set = set(old), set(new)
    added = sorted(list(new_set - old_set))
    removed = sorted(list(old_set - new_set))

    changes = []
    if added:
        changes.append("🔴 Добавили отключения: " + ", ".join(added))
    if removed:
        changes.append("🟢 Отменили отключения: " + ", ".join(removed))

    return "\n".join(changes)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    msg = (
        f"Привет! Бот отслеживает очередь {TARGET_QUEUE}.\n"
        "Пишет статус света онлайн и автоматически присылает уведомления об изменениях."
    )
    await message.answer(msg, reply_markup=get_main_keyboard())

@dp.message(F.text == "📅 Посмотреть график")
async def show_schedule(message: types.Message):
    try:
        current = await get_schedule()
        status_msg = get_current_status(current)
        msg = f"⚡ **Очередь {TARGET_QUEUE}**\n\n{status_msg}"
    except Exception as e:
        msg = f"❌ Ошибка при получении графика: {e}"

    await message.answer(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def auto_check_loop():
    while True:
        try:
            current = await get_schedule()
            saved = load_saved_schedule()

            if current != saved:
                if saved and CHAT_ID:
                    diff = compare_schedules(saved, current)
                    status_msg = get_current_status(current)
                    
                    msg = (
                        f"⚠️ **ИЗМЕНЕНИЕ В ГРАФИКЕ (Очередь {TARGET_QUEUE})** ⚠️\n\n"
                        f"{diff}\n\n"
                        f"{status_msg}"
                    )
                    await bot.send_message(
                        CHAT_ID,
                        msg,
                        parse_mode="Markdown",
                        reply_markup=get_main_keyboard(),
                    )

                save_schedule(current)
                logging.info(f"График обновлен: {current}")

        except Exception as e:
            logging.error(f"Ошибка автоматической проверки: {e}")

        await asyncio.sleep(300)

async def main():
    if not bot:
        logging.critical("Бот не может быть запущен без BOT_TOKEN.")
        return

    await start_web_server()
    asyncio.create_task(auto_check_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
