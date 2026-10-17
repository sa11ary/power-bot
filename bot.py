import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup
import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Токен и Chat ID (с подстраховкой, если Render очистит переменные)
BOT_TOKEN = os.getenv("8649041001:AAGuNYq4SfF_JCJWjWlEHpJULjq6NUkwQ-Y") or (
    "8649041001:" + "AAGuNYq4SfF_JCJWjW1EHpJULjq6NUkwQ-Y"
)
CHAT_ID = os.getenv("6504410659") or "6504410659"

TARGET_QUEUE = "6.2"
URL = "https://off.energy.mk.ua/"
STATE_FILE = "queue_6_2_state.json"

logging.basicConfig(level=logging.INFO)


# --- Мгновенный веб-сервер для прохождения проверки Render (Health Check) ---
class HealthHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.send_header("Content-type", "text/plain")
    self.end_headers()
    self.wfile.write(b"OK")

  def log_message(self, format, *args):
    return


def run_health_server():
  port = int(os.getenv("PORT", 10000))
  server = HTTPServer(("0.0.0.0", port), HealthHandler)
  logging.info(f"Health check сервер успешно запущен на порту {port}")
  server.serve_forever()


# Запускаем веб-сервер в фоновом потоке до старта бота
threading.Thread(target=run_health_server, daemon=True).start()

# --- Инициализация бота ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- Расчет времени (Киев UTC+3 / EEST) ---
def get_kyiv_time() -> str:
  kyiv_tz = timezone(timedelta(hours=3))
  return datetime.now(kyiv_tz).strftime("%H:%M")


def get_current_status(schedule: list[str]) -> str:
  if not schedule:
    return "🟢 **Сейчас свет есть. Отключений не запланировано.**"

  current_time = get_kyiv_time()
  upcoming = []

  for period in schedule:
    parts = period.split("-")
    if len(parts) == 2:
      start, end = parts[0].strip(), parts[1].strip()

      if start <= current_time < end:
        return (
            f"🔴 **Сейчас отключение (до {end})**\n\n🕒 Весь график:\n"
            + "\n".join(schedule)
        )
      if current_time < start:
        upcoming.append(start)

  if upcoming:
    return (
        f"🟢 **Сейчас свет есть. Следующее отключение в {upcoming[0]}**\n\n🕒 Весь"
        " график:\n"
        + "\n".join(schedule)
    )

  return (
      "🟢 **Сейчас свет есть. Запланированные отключения на сегодня"
      " завершены.**\n\n🕒 Прошедший график:\n"
      + "\n".join(schedule)
  )


# --- Парсинг сайта Николаевоблэнерго ---
def fetch_schedule_sync() -> list[str]:
  headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
  response = requests.get(URL, headers=headers, timeout=15)
  response.raise_for_status()

  soup = BeautifulSoup(response.text, "html.parser")
  schedule = []

  for element in soup.find_all(["tr", "div", "li"]):
    text = element.get_text(" ", strip=True)
    if TARGET_QUEUE in text:
      times = re.findall(r"\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2}", text)
      for t in times:
        clean_t = t.replace("–", "-").replace(" ", "")
        parts = clean_t.split("-")
        if len(parts) == 2:
          schedule.append(f"{parts[0]} - {parts[1]}")

  return sorted(list(set(schedule)))


async def get_schedule() -> list[str]:
  return await asyncio.to_thread(fetch_schedule_sync)


# --- Работа с сохраненным состоянием ---
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
    logging.error(f"Ошибка сохранения файла: {e}")


def compare_schedules(old: list[str], new: list[str]) -> str:
  old_set, new_set = set(old), set(new)
  added = sorted(list(new_set - old_set))
  removed = sorted(list(old_set - new_set))

  changes = []
  if added:
    changes.append("🔴 **Добавили отключения:** " + ", ".join(added))
  if removed:
    changes.append("🟢 **Отменили отключения:** " + ", ".join(removed))

  return "\n".join(changes)


# --- Хэндлеры Telegram ---
def get_main_keyboard():
  return ReplyKeyboardMarkup(
      keyboard=[[KeyboardButton(text="📅 Посмотреть график")]],
      resize_keyboard=True,
  )


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
  msg = (
      f"Привет! Бот отслеживает очередь {TARGET_QUEUE}.\n"
      "Показывает онлайн-статус и сразу присылает сообщения при изменениях"
      " графика."
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


# --- Цикл автопроверки каждые 5 минут ---
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
        logging.info(f"График обновлен: {current}")

    except Exception as e:
      logging.error(f"Ошибка проверки графика: {e}")

    await asyncio.sleep(300)


async def main():
  asyncio.create_task(auto_check_loop())
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
