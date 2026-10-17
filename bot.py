import asyncio
import json
import logging
import os
from bs4 import BeautifulSoup
import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BOT_TOKEN = os.getenv("8649041001:AAGuNYq4SfF_JCJWjWlEHpJULjq6NUkwQ-Y")
CHAT_ID = os.getenv("6504410659")
TARGET_QUEUE = "6.2"
URL = "https://off.energy.mk.ua/"
STATE_FILE = "queue_6_2_state.json"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📅 Посмотреть график")]],
        resize_keyboard=True,
    )


def get_schedule() -> list[str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }
    response = requests.get(URL, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    schedule = []

    for element in soup.find_all(["tr", "div", "li"]):
        text = element.get_text(" ", strip=True)
        if TARGET_QUEUE in text:
            words = text.split()
            hours = [
                w
                for w in words
                if ":" in w and ("-" in w or "–" in w or len(w) == 5)
            ]
            if hours:
                schedule.extend(hours)

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
        changes.append("🔴 **Добавлено:** " + ", ".join(added))
    if removed:
        changes.append("🟢 **Отменено:** " + ", ".join(removed))

    return "\n".join(changes)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    msg = (
        f"Привет! Бот отслеживает график отключений для очереди {TARGET_QUEUE}.\n\n"
        "• Уведомление придёт автоматически при изменении графика.\n"
        "• Нажмите кнопку ниже, чтобы проверить текущий график."
    )
    await message.answer(msg, reply_markup=get_main_keyboard())


@dp.message(F.text == "📅 Посмотреть график")
async def show_schedule(message: types.Message):
    try:
        current = get_schedule()
        status_text = (
            ", ".join(current) if current else "Отключений не запланировано"
        )
        msg = (
            f"💡 **Текущий график (очередь {TARGET_QUEUE}):**\n\n"
            f"⏰ {status_text}\n\n"
            f"🔗 [Открыть сайт]({URL})"
        )
    except Exception as e:
        msg = f"❌ Ошибка при получении графика: {e}"

    await message.answer(
        msg,
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=get_main_keyboard(),
    )


async def auto_check_loop():
    while True:
        try:
            current = get_schedule()
            saved = load_saved_schedule()

            if current != saved:
                if saved:
                    diff = compare_schedules(saved, current)
                    status_text = (
                        ", ".join(current) if current else "Отключений нет"
                    )
                    msg = (
                        f"⚡ **Изменение графика (очередь {TARGET_QUEUE})!**\n\n"
                        f"{diff}\n\n"
                        f"📋 **Новый график:** {status_text}\n"
                        f"🔗 [Открыть сайт]({URL})"
                    )
                    await bot.send_message(
                        CHAT_ID,
                        msg,
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                        reply_markup=get_main_keyboard(),
                    )

                save_schedule(current)
                logging.info(f"График обновлен: {current}")

        except Exception as e:
            logging.error(f"Ошибка проверки: {e}")

        await asyncio.sleep(300)


async def main():
    asyncio.create_task(auto_check_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
