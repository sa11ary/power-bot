import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from aiohttp import web
from bs4 import BeautifulSoup
import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Автоматический фоллбэк, если Render не передал переменные
BOT_TOKEN = os.getenv("8649041001:AAGuNYq4SfF_JCJWjWlEHpJULjq6NUkwQ-Y") or (
    "8649041001:" + "AAGuNYq4SfF_JCJWjW1EHpJULjq6NUkwQ-Y"
)
CHAT_ID = os.getenv("6504410659") or "6504410659"
PORT = int(os.getenv("PORT", 10000))

TARGET_QUEUE = "6.2"
URL = "https://off.energy.mk.ua/"
STATE_FILE = "queue_6_2_state.json"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- Минимальный веб-сервер для удержания Render Web Service ---
async def handle_ping(request):
  return web.Response(text="Bot is active 24/7", status=200)


async def start_web_server():
  app = web.Application()
  app.router.add_get("/", handle_ping)
  runner = web.AppRunner(app)
  await runner.setup()
  site = web.TCPSite(runner, "0.0.0.0", PORT)
  await site.start()
  logging.info(f"Веб-сервер запущен на порту {PORT}")


# --- Расчет времени по Киеву (UTC+3) без сторонних библиотек ---
def get_kyiv_time() -> str:
  kyiv_tz = timezone(timedelta(hours=3))
  return datetime.now(kyiv_tz).strftime("%H:%M")


def get_current_status(schedule: list[str]) -> str:
  if not schedule:
    return "🟢 **Сейчас свет есть.**\nОтключений не запланировано."

  current_time = get_kyiv_time()
  upcoming = []

  for period in schedule:
    parts = period.split("-")
    if len(parts) == 2:
      start, end = parts[0].strip(), parts[1].strip()

      if start <= current_time < end:
        return (
            f"🔴 **Сейчас отключение (до {end})**\n\n🕒 **График на сегодня:**\n"
            + "\n".join(f"• {p}" for p in schedule)
        )
      if current_time < start:
        upcoming.append(start)

  if upcoming:
    return (
        f"🟢 **Сейчас свет есть. Следующее отключение в {upcoming[0]}**\n\n🕒"
        " **График на сегодня:**\n"
        + "\n".join(f"• {p}" for p in schedule)
    )

  return (
      "🟢 **Сейчас свет есть. Запланированные отключения на сегодня"
      " завершены.**\n\n🕒 **График:**\n"
      + "\n".join(f"• {p}" for p in schedule)
  )


# --- Парсинг с поддержкой 8:00 и 08:00 ---
def fetch_schedule_sync() -> list[str]:
  headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
  response = requests.get(URL, headers=headers, timeout=15)
  response.raise_for_status()

  soup = BeautifulSoup(response.text, "html.parser")
  schedule = []

  for element in soup.find_all(["tr", "div", "li", "td", "p"]):
    text = element.get_text(" ", strip=True)
    if TARGET_QUEUE in text:
      # Поиск любого формата: 8:00 - 10:00, 08:00–10:00, 14:00 - 18:00
      found = re.findall(
          r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", text
      )
      for start, end in found:
        start_norm = f"{int(start.split(':')[0]):02d}:{start.split(':')[1]}"
        end_norm = f"{int(end.split(':')[0]):02d}:{end.split(':')[1]}"
        schedule.append(f"{start_norm} - {end_norm}")

  return sorted(list(set(schedule)))


async def get_schedule() -> list[str]:
  return await asyncio.to_thread(fetch_schedule_sync)


# --- Сохранение состояния ---
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
    logging.error(f"Ошибка сохранения состояния: {e}")


def compare_schedules(old: list[str], new: list[str]) -> str:
  old_set, new_set = set(old), set(new)
  added = sorted(list(new_set - old_set))
  removed = sorted(list(old_set - new_set))

  changes = []
  if added:
    changes.append("🔴 **Добавили:** " + ", ".join(added))
  if removed:
    changes.append("🟢 **Отменили:** " + ", ".join(removed))

  return "\n".join(changes)


# --- Клавиатура и обработчики ---
def get_main_keyboard():
  return ReplyKeyboardMarkup(
      keyboard=[[KeyboardButton(text="📅 Посмотреть график")]],
      resize_keyboard=True,
  )


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
  msg = (
      f"Привет! Бот отслеживает график для очереди {TARGET_QUEUE}.\n"
      "Показывает текущий статус и присылает уведомления при изменениях."
  )
  await message.answer(msg, reply_markup=get_main_keyboard())


@dp.message(F.text == "📅 Посмотреть график")
async def show_schedule(message: types.Message):
  try:
    current = await get_schedule()
    status_msg = get_current_status(current)
    msg = f"⚡ **Очередь {TARGET_QUEUE}**\n\n{status_msg}"
  except Exception as e:
    msg = f"❌ Ошибка получения графика: {e}"

  await message.answer(
      msg, parse_mode="Markdown", reply_markup=get_main_keyboard()
  )


# --- Автоматическая проверка каждые 5 минут ---
async def auto_check_loop():
  while True:
    try:
      current = await get_schedule()
      saved = load_saved_schedule()

      if current != saved:
        if saved:
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
        logging.info(f"Обновление сохраненного графика: {current}")

    except Exception as e:
      logging.error(f"Ошибка в цикле проверки: {e}")

    await asyncio.sleep(300)


async def main():
  await start_web_server()
  asyncio.create_task(auto_check_loop())
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
