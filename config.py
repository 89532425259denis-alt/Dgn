import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
PAYMENT_PHONE = os.getenv("PAYMENT_PHONE", "89532425259")
PAYMENT_BANK = os.getenv("PAYMENT_BANK", "Т-Банк")
DB_PATH = os.getenv("DB_PATH", "repair_bot.db")

if not TOKEN or not ADMIN_ID:
    raise ValueError("BOT_TOKEN и ADMIN_ID должны быть заданы в .env файле")