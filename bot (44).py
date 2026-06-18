import asyncio
import aiohttp
import json
import re
import os
import base64
import math
import time
from html import escape
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from bs4 import BeautifulSoup


# ==========================================================
#                    ЗАГРУЗКА .env
# ==========================================================

def load_dotenv(path: str = ".env") -> None:
    """Минимальная загрузка .env без внешней зависимости python-dotenv."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()


# ==========================================================
#                       КОНФИГУРАЦИЯ
# ==========================================================

def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "да", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).replace(",", "."))
    except ValueError:
        return default


TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN", "")
ADMIN_ID = env_int("ADMIN_ID", 5291613279)

# DeepSeek может нормализовать текст модели, но стандартный DeepSeek Chat API не видит фото.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Если нужно распознавание фото телефона — укажите OpenAI-compatible vision endpoint.
# Например: VISION_API_URL=https://api.openai.com/v1/chat/completions
VISION_API_URL = os.getenv("VISION_API_URL", "")
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o-mini")

PAYMENT_PHONE = os.getenv("PAYMENT_PHONE", "89532425259")
PAYMENT_BANK = os.getenv("PAYMENT_BANK", "Т-Банк")
ENABLE_STARS_PAYMENT = env_bool("ENABLE_STARS_PAYMENT", True)
# ВАЖНО: курс Stars задайте сами. По умолчанию 1 звезда = 1 ₽ для удобства теста.
STAR_RUB_RATE = max(env_float("STAR_RUB_RATE", 1.0), 0.01)

SHOP_NAME = os.getenv("SHOP_NAME", "Ремонт телефонов Псков")
SHOP_CITY = os.getenv("SHOP_CITY", "Псков")
SHOP_ADDRESS = os.getenv("SHOP_ADDRESS", "г. Псков, ул. Ленина, 10")
SHOP_POSTCODE = os.getenv("SHOP_POSTCODE", "180000")
SHOP_PHONE = os.getenv("SHOP_PHONE", PAYMENT_PHONE)

# Посылка: телефон в упаковке. Поправьте под свои требования.
PACKAGE_WEIGHT_GRAMS = env_int("PACKAGE_WEIGHT_GRAMS", 600)
PACKAGE_LENGTH_CM = env_int("PACKAGE_LENGTH_CM", 20)
PACKAGE_WIDTH_CM = env_int("PACKAGE_WIDTH_CM", 12)
PACKAGE_HEIGHT_CM = env_int("PACKAGE_HEIGHT_CM", 8)

# СДЭК API v2
CDEK_CLIENT_ID = os.getenv("CDEK_CLIENT_ID", "")
CDEK_CLIENT_SECRET = os.getenv("CDEK_CLIENT_SECRET", "")
CDEK_TEST_MODE = env_bool("CDEK_TEST_MODE", False)
CDEK_BASE_URL = "https://api.edu.cdek.ru/v2" if CDEK_TEST_MODE else "https://api.cdek.ru/v2"
CDEK_FROM_CITY_CODE = os.getenv("CDEK_FROM_CITY_CODE", "")  # код города отправителя в СДЭК
CDEK_DEFAULT_TARIFF_CODE = env_int("CDEK_DEFAULT_TARIFF_CODE", 136)

# Почта России / Отправка API
RUSSIAN_POST_TOKEN = os.getenv("RUSSIAN_POST_TOKEN", "")
RUSSIAN_POST_LOGIN = os.getenv("RUSSIAN_POST_LOGIN", "")
RUSSIAN_POST_PASSWORD = os.getenv("RUSSIAN_POST_PASSWORD", "")
# Можно указать уже готовый Basic-токен из кабинета Почты России.
RUSSIAN_POST_BASIC_AUTH = os.getenv("RUSSIAN_POST_BASIC_AUTH", "")
RUSSIAN_POST_BASE_URL = os.getenv("RUSSIAN_POST_BASE_URL", "https://otpravka-api.pochta.ru")

ORDERS_FILE = Path(os.getenv("ORDERS_FILE", "orders.json"))
PHOTOS_DIR = Path(os.getenv("PHOTOS_DIR", "photos"))


# ==========================================================
#                    ИНИЦИАЛИЗАЦИЯ БОТА
# ==========================================================

bot: Optional[Bot] = None
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_data: dict[int, dict[str, Any]] = {}
temp_photos: dict[int, list[str]] = {}
orders: dict[str, dict[str, Any]] = {}
order_counter = env_int("ORDER_COUNTER_START", 1000)


# ==========================================================
#                         FSM
# ==========================================================

class RepairStates(StatesGroup):
    agreement = State()
    waiting_contact = State()
    waiting_phone = State()
    waiting_photos = State()
    manual_model = State()
    selecting_services = State()
    other_problem = State()
    waiting_delivery_name = State()
    waiting_delivery_address = State()
    waiting_part = State()
    waiting_pickup_time = State()
    waiting_time_manual = State()
    waiting_payment = State()
    waiting_payment_screenshot = State()
    waiting_tracking_number = State()
    order_confirmed = State()


# ==========================================================
#                         ЦЕНЫ
# ==========================================================

PRICELIST = {
    "display": {"name": "Замена дисплея", "price": 500},
    "battery": {"name": "Замена аккумулятора", "price": 400},
    "speaker": {"name": "Замена динамика", "price": 250},
    "camera": {"name": "Замена камеры", "price": 300},
    "back_panel": {"name": "Замена задней панели", "price": 250},
    "buttons": {"name": "Замена кнопок", "price": 250},
    "fingerprint": {"name": "Замена сканера", "price": 270},
    "charging": {"name": "Замена разъема зарядки", "price": 300},
    "microphone": {"name": "Замена микрофона", "price": 250},
}

COPY_KEYWORDS = [
    "копия",
    "реплика",
    "подделка",
    "clone",
    "copy",
    "fake",
    "не оригинал",
    "неоригинал",
]

# Если вы реально не принимаете конкретные бренды, задайте их в .env:
# UNSUPPORTED_BRANDS=xiaomi,redmi,poco,huawei,honor
UNSUPPORTED_BRANDS = [
    brand.strip().lower()
    for brand in os.getenv("UNSUPPORTED_BRANDS", "").split(",")
    if brand.strip()
]


# ==========================================================
#                         ТЕКСТЫ
# ==========================================================

LEGAL_AGREEMENT = f"""
📜 <b>ПУБЛИЧНЫЙ ДОГОВОР ОФЕРТЫ</b>

1. Заказчик соглашается с условиями оказания услуг по ремонту мобильных устройств.
2. Стоимость ремонта фиксируется в заказе после выбора услуги, запчасти и доставки.
3. Срок выполнения работ: 2–5 рабочих дней после получения устройства и запчасти.
4. Гарантия на выполненные работы: 30 дней.
5. Исполнитель не несет ответственности за потерю данных клиента.
6. Клиент обязуется предоставить достоверные данные для заказа и доставки.
7. Оплата производится предоплатой: переводом на {escape(PAYMENT_BANK)} {escape(PAYMENT_PHONE)} или доступным способом в Telegram.
8. Доставка доступна: личная встреча в Пскове, СДЭК, Почта России. Первичная отправка телефона в ремонт — за счет клиента, обратная доставка рассчитывается в заказе.

⚠️ <b>ВАЖНО:</b> Мы не принимаем подделки, реплики и китайские копии. Ремонтируем только оригинальные устройства.

✅ Нажимая «Согласен», вы принимаете условия оферты.
""".strip()


# ==========================================================
#                    ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================================

def get_bot() -> Bot:
    if bot is None:
        raise RuntimeError("Бот еще не инициализирован")
    return bot


def h(value: Any) -> str:
    return escape(str(value or ""))


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def ikb(rows: list[list[InlineKeyboardButton]], back: Optional[str] = None) -> InlineKeyboardMarkup:
    if back:
        rows.append([btn("⬅️ Назад", back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def safe_edit(message: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)


async def send_html(message: Message, text: str, reply_markup: Any = None) -> None:
    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)


def normalize_ru_phone(raw: str) -> Optional[str]:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return None


def is_rejected_model(text: str) -> tuple[bool, str]:
    lower = (text or "").lower()
    if any(word in lower for word in COPY_KEYWORDS):
        return True, "Похоже, указана копия/реплика. Мы принимаем только оригинальные устройства."
    for brand in UNSUPPORTED_BRANDS:
        if brand and brand in lower:
            return True, f"Бренд «{brand}» сейчас не принимается мастерской."
    return False, ""


def generate_order_id() -> str:
    global order_counter
    order_counter += 1
    return f"PSK-{datetime.now().strftime('%Y%m%d')}-{order_counter:04d}"


def generate_tracking_link(order_id: str) -> str:
    # Замените username, если у бота другое имя.
    bot_username = os.getenv("BOT_USERNAME", "repair_pskov_bot")
    return f"https://t.me/{bot_username}?start=status_{order_id}"


def work_total(user_id: int) -> int:
    services = user_data.get(user_id, {}).get("selected_services", [])
    return sum(PRICELIST.get(s, {}).get("price", 0) for s in services)


def delivery_total(user_id: int) -> int:
    return int(user_data.get(user_id, {}).get("delivery_cost", 0) or 0)


def order_total(user_id: int) -> int:
    return work_total(user_id) + int(user_data[user_id].get("part_price", 0) or 0) + delivery_total(user_id)


def service_names(user_id: int) -> list[str]:
    services = user_data.get(user_id, {}).get("selected_services", [])
    names = []
    for service_key in services:
        if service_key == "other":
            names.append(f"Другая проблема: {user_data[user_id].get('other_problem', '')}")
        else:
            names.append(PRICELIST.get(service_key, {}).get("name", service_key))
    return names


def delivery_name(method: str) -> str:
    return {
        "pickup": "Личная встреча в Пскове",
        "cdek": "СДЭК",
        "rupost": "Почта России",
    }.get(method, method or "Не выбрано")


def extract_post_index(text: str) -> Optional[str]:
    match = re.search(r"\b\d{6}\b", text or "")
    return match.group(0) if match else None


def parse_russian_post_address(raw: str) -> Optional[dict[str, str]]:
    """
    Ожидаемый формат:
    180000, Псковская область, Псков, ул. Ленина, 10, 1
    """
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if len(parts) < 5:
        return None
    index = extract_post_index(parts[0]) or extract_post_index(raw)
    if not index:
        return None

    return {
        "index": index,
        "region": parts[1] if len(parts) > 1 else "",
        "place": parts[2] if len(parts) > 2 else "",
        "street": parts[3] if len(parts) > 3 else "",
        "house": parts[4] if len(parts) > 4 else "",
        "room": parts[5] if len(parts) > 5 else "",
    }


def split_full_name(full_name: str) -> dict[str, str]:
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    return {
        "surname": parts[0] if len(parts) > 0 else "Клиент",
        "given_name": parts[1] if len(parts) > 1 else "Клиент",
        "middle_name": " ".join(parts[2:]) if len(parts) > 2 else "",
    }


def load_orders() -> None:
    global orders, order_counter
    if not ORDERS_FILE.exists():
        return
    try:
        payload = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        orders = payload.get("orders", {})
        order_counter = int(payload.get("order_counter", order_counter))
    except Exception as exc:
        print(f"Не удалось загрузить {ORDERS_FILE}: {exc}")


def save_orders() -> None:
    try:
        ORDERS_FILE.write_text(
            json.dumps({"order_counter": order_counter, "orders": orders}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Не удалось сохранить {ORDERS_FILE}: {exc}")


def ensure_user(user_id: int) -> dict[str, Any]:
    if user_id not in user_data:
        user_data[user_id] = {"selected_services": []}
    user_data[user_id].setdefault("selected_services", [])
    return user_data[user_id]


def create_or_update_order(user_id: int, status: str = "created") -> str:
    data = ensure_user(user_id)
    order_id = data.get("order_id")
    if not order_id:
        order_id = generate_order_id()
        data["order_id"] = order_id

    data["total_price"] = order_total(user_id)
    orders[order_id] = {
        "user_id": user_id,
        "data": data.copy(),
        "status": status,
        "created_at": orders.get(order_id, {}).get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_orders()
    return order_id


def update_order_snapshot(order_id: str, status: Optional[str] = None) -> None:
    order = orders.get(order_id)
    if not order:
        return
    uid = int(order["user_id"])
    if uid in user_data:
        order["data"] = user_data[uid].copy()
    if status:
        order["status"] = status
    order["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_orders()


def order_status_ru(status: str) -> str:
    return {
        "created": "Создан, ожидает оплату",
        "payment_pending": "Ожидает проверки оплаты",
        "paid": "Оплачен",
        "confirmed": "Оплата подтверждена",
        "in_delivery": "В доставке",
        "in_repair": "В ремонте",
        "done": "Готов",
        "cancelled": "Отменен",
    }.get(status, status)


# ==========================================================
#                  API: CDEK И ПОЧТА РОССИИ
# ==========================================================

class CDEKClient:
    def __init__(self) -> None:
        self.access_token: Optional[str] = None
        self.expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(CDEK_CLIENT_ID and CDEK_CLIENT_SECRET)

    async def token(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token
        if not self.configured:
            raise RuntimeError("CDEK_CLIENT_ID/CDEK_CLIENT_SECRET не заданы")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{CDEK_BASE_URL}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": CDEK_CLIENT_ID,
                    "client_secret": CDEK_CLIENT_SECRET,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"CDEK auth error {response.status}: {payload}")
                self.access_token = payload["access_token"]
                self.expires_at = time.time() + int(payload.get("expires_in", 3600))
                return self.access_token

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        access_token = await self.token()
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {access_token}"
        headers["Content-Type"] = "application/json"
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                f"{CDEK_BASE_URL}{path}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=40),
                **kwargs,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"CDEK error {response.status}: {payload}")
                return payload

    async def find_city_code(self, city: str) -> Optional[int]:
        if not city:
            return None
        payload = await self.request(
            "GET",
            "/location/cities",
            params={"country_codes": "RU", "city": city, "size": 1},
        )
        if isinstance(payload, list) and payload:
            return int(payload[0].get("code"))
        return None

    async def quote(self, city: str, address: str = "") -> dict[str, Any]:
        if not self.configured:
            return {
                "provider": "cdek",
                "title": "СДЭК (примерный расчет)",
                "cost": 450,
                "period": "2–5 дней",
                "note": "Для точного тарифа задайте CDEK_CLIENT_ID и CDEK_CLIENT_SECRET в .env",
            }

        to_code = await self.find_city_code(city)
        from_location: dict[str, Any] = {"city": SHOP_CITY, "address": SHOP_ADDRESS}
        if CDEK_FROM_CITY_CODE.isdigit():
            from_location = {"code": int(CDEK_FROM_CITY_CODE), "address": SHOP_ADDRESS}

        to_location: dict[str, Any] = {"city": city, "address": address or city}
        if to_code:
            to_location = {"code": to_code, "address": address or city}

        payload = {
            "type": 1,
            "from_location": from_location,
            "to_location": to_location,
            "packages": [
                {
                    "weight": PACKAGE_WEIGHT_GRAMS,
                    "length": PACKAGE_LENGTH_CM,
                    "width": PACKAGE_WIDTH_CM,
                    "height": PACKAGE_HEIGHT_CM,
                }
            ],
        }
        result = await self.request("POST", "/calculator/tarifflist", json=payload)
        tariffs = result.get("tariff_codes") or result.get("tariffs") or []
        if not tariffs:
            raise RuntimeError(f"СДЭК не вернул тарифы: {result}")

        best = min(tariffs, key=lambda item: float(item.get("delivery_sum", 10**9)))
        period_min = best.get("period_min")
        period_max = best.get("period_max")
        period = ""
        if period_min and period_max:
            period = f"{period_min}–{period_max} дней"
        elif period_min:
            period = f"от {period_min} дней"
        else:
            period = "срок уточняется"

        return {
            "provider": "cdek",
            "title": best.get("tariff_name") or "СДЭК",
            "tariff_code": best.get("tariff_code", CDEK_DEFAULT_TARIFF_CODE),
            "city_code": to_code,
            "cost": int(math.ceil(float(best.get("delivery_sum", 0)))),
            "period": period,
            "raw": best,
        }

    async def create_order(self, order_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"created": False, "note": "СДЭК API не настроен"}

        city = data.get("delivery_city", "")
        address = data.get("delivery_address", "")
        city_code = data.get("cdek_city_code") or await self.find_city_code(city)

        from_location: dict[str, Any] = {"city": SHOP_CITY, "address": SHOP_ADDRESS}
        if CDEK_FROM_CITY_CODE.isdigit():
            from_location = {"code": int(CDEK_FROM_CITY_CODE), "address": SHOP_ADDRESS}

        to_location: dict[str, Any] = {"city": city, "address": address}
        if city_code:
            to_location = {"code": int(city_code), "address": address}

        payload = {
            "type": 1,
            "number": order_id,
            "tariff_code": int(data.get("cdek_tariff_code") or CDEK_DEFAULT_TARIFF_CODE),
            "comment": f"Обратная отправка телефона после ремонта. Заказ {order_id}",
            "recipient": {
                "name": data.get("delivery_full_name") or data.get("client_name") or "Клиент",
                "phones": [{"number": data.get("contact_phone") or SHOP_PHONE}],
            },
            "sender": {
                "name": SHOP_NAME,
                "phones": [{"number": SHOP_PHONE}],
            },
            "from_location": from_location,
            "to_location": to_location,
            "packages": [
                {
                    "number": order_id,
                    "weight": PACKAGE_WEIGHT_GRAMS,
                    "length": PACKAGE_LENGTH_CM,
                    "width": PACKAGE_WIDTH_CM,
                    "height": PACKAGE_HEIGHT_CM,
                    "comment": "Телефон после ремонта",
                    "items": [
                        {
                            "name": "Ремонт телефона",
                            "ware_key": order_id,
                            "payment": {"value": 0},
                            "cost": int(data.get("total_price", 0) or 0),
                            "weight": PACKAGE_WEIGHT_GRAMS,
                            "amount": 1,
                        }
                    ],
                }
            ],
        }
        result = await self.request("POST", "/orders", json=payload)
        entity = result.get("entity") or {}
        return {
            "created": True,
            "uuid": entity.get("uuid"),
            "cdek_number": entity.get("cdek_number"),
            "raw": result,
        }


class RussianPostClient:
    @property
    def configured(self) -> bool:
        return bool(RUSSIAN_POST_TOKEN and (RUSSIAN_POST_BASIC_AUTH or (RUSSIAN_POST_LOGIN and RUSSIAN_POST_PASSWORD)))

    def headers(self) -> dict[str, str]:
        if not self.configured:
            raise RuntimeError("Почта России API не настроена")
        basic = RUSSIAN_POST_BASIC_AUTH
        if not basic:
            basic_raw = f"{RUSSIAN_POST_LOGIN}:{RUSSIAN_POST_PASSWORD}".encode("utf-8")
            basic = base64.b64encode(basic_raw).decode("ascii")
        return {
            "Authorization": f"AccessToken {RUSSIAN_POST_TOKEN}",
            "X-User-Authorization": f"Basic {basic}",
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json;charset=UTF-8",
        }

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                f"{RUSSIAN_POST_BASE_URL}{path}",
                headers=self.headers(),
                timeout=aiohttp.ClientTimeout(total=40),
                **kwargs,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"Russian Post error {response.status}: {payload}")
                return payload

    async def quote(self, index_to: str) -> dict[str, Any]:
        if not self.configured:
            return {
                "provider": "rupost",
                "title": "Почта России (примерный расчет)",
                "cost": 350,
                "period": "3–7 дней",
                "note": "Для точного тарифа задайте RUSSIAN_POST_TOKEN и авторизацию в .env",
            }

        payload = {
            "index-from": SHOP_POSTCODE,
            "index-to": index_to,
            "mail-category": "ORDINARY",
            "mail-type": "POSTAL_PARCEL",
            "mass": PACKAGE_WEIGHT_GRAMS,
            "fragile": True,
            "transport-type": "SURFACE",
        }
        result = await self.request("POST", "/1.0/tariff", json=payload)
        rate = result.get("total-rate") or result.get("totalRate") or result.get("rate") or 0
        cost = int(math.ceil(float(rate) / 100)) if rate else 0
        delivery_time = result.get("delivery-time") or result.get("deliveryTime") or {}
        min_days = delivery_time.get("min-days") or delivery_time.get("minDays")
        max_days = delivery_time.get("max-days") or delivery_time.get("maxDays")
        period = f"{min_days}–{max_days} дней" if min_days and max_days else "срок уточняется"
        return {
            "provider": "rupost",
            "title": "Почта России",
            "cost": cost,
            "period": period,
            "raw": result,
        }

    async def create_order(self, order_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"created": False, "note": "Почта России API не настроена"}

        address = data.get("post_address_struct") or parse_russian_post_address(data.get("delivery_address", ""))
        if not address:
            return {"created": False, "note": "Адрес Почты России не распознан. Заказ нужно создать вручную."}

        name = split_full_name(data.get("delivery_full_name", ""))
        payload = [
            {
                "address-type-to": "DEFAULT",
                "given-name": name["given_name"],
                "surname": name["surname"],
                "middle-name": name["middle_name"],
                "index-to": int(address["index"]),
                "region-to": address["region"],
                "place-to": address["place"],
                "street-to": address["street"],
                "house-to": address["house"],
                "room-to": address.get("room", ""),
                "mail-category": "ORDINARY",
                "mail-type": "POSTAL_PARCEL",
                "mass": PACKAGE_WEIGHT_GRAMS,
                "order-num": order_id,
                "tel-address": re.sub(r"\D", "", data.get("contact_phone", "")),
                "transport-type": "SURFACE",
            }
        ]
        result = await self.request("PUT", "/1.0/user/backlog", json=payload)
        first = result[0] if isinstance(result, list) and result else result
        return {
            "created": True,
            "barcode": first.get("barcode") if isinstance(first, dict) else None,
            "raw": result,
        }


cdek_client = CDEKClient()
russian_post_client = RussianPostClient()


# ==========================================================
#                      ПАРСИНГ ЗАПЧАСТЕЙ
# ==========================================================

async def parse_ozon_for_parts(part_name: str, model: str) -> list[dict[str, Any]]:
    """Поиск запчастей. Если Ozon не отдал HTML — возвращаем запасной вариант."""
    try:
        search_query = f"{model} {part_name} запчасть".replace(" ", "+")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
        }
        async with aiohttp.ClientSession() as session:
            url = f"https://www.ozon.ru/search/?text={search_query}"
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status != 200:
                    raise RuntimeError(f"Ozon status {response.status}")
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                items = soup.find_all("div", class_="tile-hover-target")
                results: list[dict[str, Any]] = []

                for item in items[:5]:
                    title_elem = item.find("span")
                    title = title_elem.text.strip() if title_elem else f"{part_name} для {model}"
                    price_text = item.get_text(" ")
                    prices = [int(x) for x in re.findall(r"(\d[\d\s]{2,})\s*₽", price_text.replace("\xa0", " "))]
                    price = min(prices) if prices else 0
                    link_elem = item.find("a")
                    link = "https://www.ozon.ru"
                    if link_elem and link_elem.get("href"):
                        href = link_elem.get("href")
                        link = href if href.startswith("http") else f"https://www.ozon.ru{href}"
                    if price > 0:
                        results.append({"title": title, "price": price, "url": link, "seller": "Ozon"})

                if results:
                    return results[:2]
    except Exception as exc:
        print(f"Ошибка парсинга Ozon: {exc}")

    return [
        {"title": f"Аналог {part_name} для {model}", "price": 800, "url": "https://www.ozon.ru", "seller": "Ozon"},
        {"title": f"Качественный аналог {part_name}", "price": 1200, "url": "https://www.ozon.ru", "seller": "Ozon"},
    ]


async def parse_original_parts(part_name: str, model: str) -> list[dict[str, Any]]:
    return [
        {"title": f"Оригинал {part_name} для {model}", "price": 2200, "url": "https://www.dns-shop.ru", "seller": "DNS"},
        {"title": f"Оригинальная запчасть {part_name}", "price": 2800, "url": "https://www.citilink.ru", "seller": "Citilink"},
    ]


# ==========================================================
#                     DEEPSEEK / VISION
# ==========================================================

async def analyze_with_deepseek(prompt: str) -> Optional[str]:
    if not DEEPSEEK_API_KEY:
        return None

    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Ты помощник по ремонту телефонов. Отвечай только валидным JSON без Markdown."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 300,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as response:
                result = await response.json(content_type=None)
                if response.status >= 400:
                    print(f"DeepSeek error {response.status}: {result}")
                    return None
                return result["choices"][0]["message"]["content"]
    except Exception as exc:
        print(f"Ошибка DeepSeek: {exc}")
        return None


def extract_json_object(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            return None
    return None


async def normalize_model_text(model_text: str) -> dict[str, Any]:
    """Нормализация ручного ввода модели. Без DeepSeek возвращает введенный текст."""
    fallback = {
        "brand": "",
        "model": model_text.strip(),
        "year": 0,
        "confidence": 70,
        "is_original": True,
        "is_chinese_copy": False,
    }
    prompt = f"""
Нормализуй модель телефона из текста клиента.
Текст: {model_text}

Верни JSON:
{{
  "brand": "Apple/Samsung/...",
  "model": "полная модель",
  "year": 2021,
  "confidence": 0-100,
  "is_original": true,
  "is_chinese_copy": false
}}
Если данных мало — confidence ниже, но не выдумывай точную модель.
""".strip()
    result = await analyze_with_deepseek(prompt)
    data = extract_json_object(result or "")
    if not data:
        return fallback
    data.setdefault("model", model_text.strip())
    data.setdefault("brand", "")
    data.setdefault("confidence", 70)
    return data


async def detect_phone_with_vision(photo_paths: list[str], caption: str = "") -> dict[str, Any]:
    """
    Стандартный DeepSeek Chat API не распознает изображения. Для фото используем
    OpenAI-compatible vision endpoint из VISION_API_URL/VISION_API_KEY.
    """
    if caption and len(caption.strip()) >= 3:
        return await normalize_model_text(caption.strip())

    if not (VISION_API_URL and VISION_API_KEY):
        return {
            "brand": "Неизвестен",
            "model": "Неизвестна",
            "year": 0,
            "confidence": 0,
            "is_original": False,
            "is_chinese_copy": False,
            "error": "Фото-распознавание не настроено. DeepSeek Chat API не принимает изображения.",
        }

    try:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Определи модель телефона по фото. Ответь только JSON: "
                    "{\"brand\":\"...\",\"model\":\"...\",\"year\":2021,"
                    "\"confidence\":90,\"is_original\":true,\"is_chinese_copy\":false}. "
                    "Если модель не видна — confidence 0."
                ),
            }
        ]
        for photo_path in photo_paths[:3]:
            encoded = base64.b64encode(Path(photo_path).read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )

        headers = {"Authorization": f"Bearer {VISION_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": 300,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                VISION_API_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                result = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"Vision API {response.status}: {result}")
                text = result["choices"][0]["message"]["content"]
                data = extract_json_object(text)
                if data:
                    return data
    except Exception as exc:
        print(f"Ошибка vision API: {exc}")

    return {
        "brand": "Неизвестен",
        "model": "Неизвестна",
        "year": 0,
        "confidence": 0,
        "is_original": False,
        "is_chinese_copy": False,
    }


# ==========================================================
#                      КЛАВИАТУРЫ / ЭКРАНЫ
# ==========================================================

def agreement_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [btn("✅ Согласен с условиями", "agree_terms")],
            [btn("📄 Читать условия", "read_terms")],
        ]
    )


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Поделиться телефоном", request_contact=True)],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Или введите номер вручную",
    )


def model_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [btn("📸 Сфотографировать телефон", "photo_phone")],
            [btn("✍️ Ввести модель вручную", "manual_model")],
        ],
        back="back_contact",
    )


def services_keyboard(user_id: int) -> InlineKeyboardMarkup:
    services = user_data[user_id].get("selected_services", [])
    rows: list[list[InlineKeyboardButton]] = []
    for key, item in PRICELIST.items():
        prefix = "✅" if key in services else "⬜"
        rows.append([btn(f"{prefix} {item['name']} — {item['price']}₽", f"service_{key}")])
    rows.append([btn("📝 Другая проблема", "service_other")])
    rows.append([btn("📋 Посмотреть выбранное", "show_selected")])
    rows.append([btn("✅ Готово, к доставке", "services_done")])
    return ikb(rows, back="back_model")


def delivery_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [btn("🏠 Я в Пскове — встреча лично", "delivery_pickup")],
            [btn("🚚 СДЭК", "delivery_cdek")],
            [btn("📮 Почта России", "delivery_rupost")],
        ],
        back="back_services",
    )


def pickup_time_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [btn("🕐 Сегодня 18:00–20:00", "pickup_time_today_evening")],
            [btn("🕐 Завтра 10:00–12:00", "pickup_time_tomorrow_morning")],
            [btn("🕐 Завтра 18:00–20:00", "pickup_time_tomorrow_evening")],
            [btn("📅 Другое время", "pickup_time_other")],
        ],
        back="back_part",
    )


def part_keyboard(user_id: int) -> InlineKeyboardMarkup:
    ozon_parts = user_data[user_id].get("ozon_parts", [])
    original_parts = user_data[user_id].get("original_parts", [])
    cheap_price = ozon_parts[0]["price"] if ozon_parts else 800
    original_price = original_parts[0]["price"] if original_parts else 2200
    return ikb(
        [
            [btn(f"🟢 Аналог ({cheap_price}₽)", "part_cheap")],
            [btn(f"🔵 Оригинал ({original_price}₽)", "part_expensive")],
            [btn("💡 Посоветуй", "part_advice")],
        ],
        back="back_delivery",
    )


def checkout_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if ENABLE_STARS_PAYMENT:
        rows.append([btn("⭐ Оплатить Telegram Stars", "pay_stars")])
    rows.append([btn("💳 Перевод на карту", "pay_bank")])
    if user_data[user_id].get("delivery_method") == "pickup":
        rows.append([btn("💵 Наличные при встрече", "pay_cash")])
    rows.append([btn("🔄 Другой способ оплаты", "other_payment")])
    return ikb(rows, back="back_checkout")


def tracking_after_payment_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [btn("📦 Я отправил телефон — ввести трек", "enter_tracking")],
            [btn("📊 Статус заказа", "show_order_status")],
        ]
    )


async def show_agreement(message: Message, state: FSMContext) -> None:
    await state.set_state(RepairStates.agreement)
    await send_html(
        message,
        f"{LEGAL_AGREEMENT}\n\nДля продолжения необходимо принять условия:",
        reply_markup=agreement_keyboard(),
    )


async def ask_contact(message: Message, state: FSMContext) -> None:
    await state.set_state(RepairStates.waiting_contact)
    await send_html(
        message,
        "📞 <b>Контактный телефон</b>\n\n"
        "Он нужен для оформления доставки СДЭК/Почтой России и связи по заказу.\n\n"
        "Нажмите кнопку «Поделиться телефоном» или введите номер вручную в формате +7...",
        reply_markup=contact_keyboard(),
    )


async def show_model_choice(message: Message, state: FSMContext, edit: bool = False) -> None:
    text = (
        "📱 <b>Как определим модель телефона?</b>\n\n"
        "📸 Можно отправить 2 фото телефона. Если vision API не настроен, бот попросит ввести модель вручную.\n"
        "✍️ Самый надежный вариант — ввести модель вручную, например: <b>iPhone 13 Pro</b>.\n\n"
        "⚠️ Подделки, реплики и копии не принимаются."
    )
    await state.set_state(RepairStates.waiting_phone)
    if edit:
        await safe_edit(message, text, model_keyboard())
    else:
        await send_html(message, text, reply_markup=model_keyboard())


async def show_services_selection(message: Message, user_id: int, state: FSMContext, edit: bool = False) -> None:
    await state.set_state(RepairStates.selecting_services)
    services = user_data[user_id].get("selected_services", [])
    if services:
        text = "🛠 <b>Выбранные услуги:</b>\n\n"
        for service_key in services:
            if service_key == "other":
                text += f"✅ Другая проблема: {h(user_data[user_id].get('other_problem', ''))}\n"
            else:
                info = PRICELIST.get(service_key, {"name": service_key, "price": 0})
                text += f"✅ {h(info['name'])} — {info['price']}₽\n"
        text += f"\n💰 <b>Итого за работу:</b> {work_total(user_id)}₽\n\n"
        text += "Можно добавить/убрать услуги кнопками ниже."
    else:
        text = "🛠 <b>Выберите услуги</b> (можно несколько):\n\nВыбирайте нужные пункты, затем нажмите «Готово, к доставке»."

    if edit:
        await safe_edit(message, text, services_keyboard(user_id))
    else:
        await send_html(message, text, reply_markup=services_keyboard(user_id))


async def show_delivery_selection(message: Message, state: FSMContext, edit: bool = False) -> None:
    text = (
        "📍 <b>Способ получения/доставки</b>\n\n"
        "Выберите, как будем работать с устройством:\n"
        "• личная встреча в Пскове;\n"
        "• СДЭК;\n"
        "• Почта России.\n\n"
        "Для СДЭК/Почты бот рассчитает доставку и сохранит данные для оформления заказа."
    )
    if edit:
        await safe_edit(message, text, delivery_keyboard())
    else:
        await send_html(message, text, reply_markup=delivery_keyboard())


async def show_part_selection(message: Message, user_id: int, state: FSMContext, edit: bool = False) -> None:
    await state.set_state(RepairStates.waiting_part)
    ozon_parts = user_data[user_id].get("ozon_parts", [])
    original_parts = user_data[user_id].get("original_parts", [])

    text = "🔧 <b>Найденные запчасти:</b>\n\n"
    text += "🟢 <b>Аналоги:</b>\n"
    for i, part in enumerate(ozon_parts[:2], 1):
        text += f"{i}. {h(part['title'])}\n   💰 {part['price']}₽\n   🔗 {h(part['url'])}\n\n"
    text += "🔵 <b>Оригиналы:</b>\n"
    for i, part in enumerate(original_parts[:2], 1):
        text += f"{i}. {h(part['title'])}\n   💰 {part['price']}₽\n   🔗 {h(part['url'])}\n\n"
    text += "Выберите тип запчасти:"

    if edit:
        await safe_edit(message, text, part_keyboard(user_id))
    else:
        await send_html(message, text, reply_markup=part_keyboard(user_id))


async def prepare_parts_and_show(message: Message, user_id: int, state: FSMContext, edit: bool = True) -> None:
    services = user_data[user_id].get("selected_services", [])
    first_service = services[0] if services else "display"
    part_name = PRICELIST.get(first_service, {}).get("name", "запчасть")
    model = user_data[user_id].get("model", "")

    loading_text = (
        "🔍 <b>Ищу запчасти...</b>\n\n"
        f"📱 Модель: {h(model)}\n"
        f"🔧 Запчасть: {h(part_name)}\n\n"
        "⏳ Проверяю варианты..."
    )
    if edit:
        await safe_edit(message, loading_text)
    else:
        await send_html(message, loading_text)

    ozon_parts = await parse_ozon_for_parts(part_name, model)
    original_parts = await parse_original_parts(part_name, model)
    user_data[user_id]["ozon_parts"] = ozon_parts
    user_data[user_id]["original_parts"] = original_parts

    await show_part_selection(message, user_id, state, edit=True)


async def show_checkout(message: Message, user_id: int, state: FSMContext, edit: bool = False) -> None:
    await state.set_state(RepairStates.waiting_payment)
    order_id = create_or_update_order(user_id, status="created")
    total = order_total(user_id)
    user_data[user_id]["total_price"] = total
    update_order_snapshot(order_id)

    services = "\n".join([f"• {h(name)}" for name in service_names(user_id)]) or "• Не указано"
    delivery_method = user_data[user_id].get("delivery_method", "")
    delivery_line = delivery_name(delivery_method)
    if delivery_method in {"cdek", "rupost"}:
        delivery_line += f" — {delivery_total(user_id)}₽, {h(user_data[user_id].get('delivery_period', 'срок уточняется'))}"

    text = f"""
🧾 <b>ЗАКАЗ #{h(order_id)}</b>

📱 <b>Модель:</b> {h(user_data[user_id].get('model', 'Не указана'))}
🛠 <b>Услуги:</b>
{services}

🔧 <b>Запчасть:</b> {h(user_data[user_id].get('part_name', 'Не выбрана'))}
🔩 <b>Тип:</b> {h(user_data[user_id].get('part_type', ''))}

🚚 <b>Доставка:</b> {h(delivery_line)}
{('⏰ <b>Встреча:</b> ' + h(user_data[user_id].get('time', '')) + chr(10)) if delivery_method == 'pickup' else ''}
💰 <b>Расчет:</b>
• Работа: {work_total(user_id)}₽
• Запчасть: {int(user_data[user_id].get('part_price', 0) or 0)}₽
• Доставка: {delivery_total(user_id)}₽

<b>Итого к оплате: {total}₽</b>

Выберите способ оплаты:
""".strip()

    if edit:
        await safe_edit(message, text, checkout_keyboard(user_id))
    else:
        await send_html(message, text, reply_markup=checkout_keyboard(user_id))


async def show_order_status_message(message: Message, order_id: str) -> None:
    order = orders.get(order_id)
    if not order:
        await send_html(message, f"❌ Заказ {h(order_id)} не найден.")
        return

    if message.from_user.id != ADMIN_ID and int(order["user_id"]) != message.from_user.id:
        await send_html(message, "❌ Этот заказ принадлежит другому пользователю.")
        return

    data = order.get("data", {})
    shipping_result = data.get("shipping_api_result") or {}
    tracking = data.get("client_tracking_number") or data.get("shipping_tracking") or "не указан"

    text = f"""
📊 <b>Статус заказа #{h(order_id)}</b>

Статус: <b>{h(order_status_ru(order.get('status', 'created')))}</b>
📱 Модель: {h(data.get('model', 'Не указана'))}
🚚 Доставка: {h(delivery_name(data.get('delivery_method', '')))}
📦 Трек клиента: {h(tracking)}
💰 Сумма: {h(data.get('total_price', 0))}₽

{('API доставки: ' + h(json.dumps(shipping_result, ensure_ascii=False)[:500])) if shipping_result else ''}
""".strip()
    await send_html(message, text)


# ==========================================================
#                         ХЕНДЛЕРЫ
# ==========================================================

@dp.message(F.text.startswith("/start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    args = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
    if args.startswith("status_") or args.startswith("track_"):
        order_id = args.split("_", 1)[1]
        await show_order_status_message(message, order_id)
        return

    user_id = message.from_user.id
    user_data[user_id] = {"selected_services": []}
    temp_photos.pop(user_id, None)
    await state.clear()
    await show_agreement(message, state)


@dp.callback_query(F.data == "read_terms")
async def read_terms(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RepairStates.agreement)
    await safe_edit(
        callback.message,
        f"{LEGAL_AGREEMENT}\n\n✅ Чтобы продолжить, нажмите «Согласен с условиями». ",
        ikb([[btn("✅ Согласен с условиями", "agree_terms")]], back="back_agreement"),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_agreement")
async def back_agreement(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RepairStates.agreement)
    await safe_edit(callback.message, f"{LEGAL_AGREEMENT}\n\nДля продолжения необходимо принять условия:", agreement_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "agree_terms")
async def agree_terms(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    ensure_user(user_id)["agreed"] = True
    # ReplyKeyboard нельзя поставить через edit_text, поэтому отправляем новое сообщение.
    try:
        await callback.message.delete()
    except Exception:
        pass
    await ask_contact(callback.message, state)
    await callback.answer()


@dp.message(RepairStates.waiting_contact, F.contact)
async def process_contact(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    contact = message.contact
    phone = normalize_ru_phone(contact.phone_number if contact else "")
    if not phone:
        await send_html(message, "❌ Не удалось распознать номер. Введите телефон вручную в формате +7...", reply_markup=contact_keyboard())
        return
    ensure_user(user_id)["contact_phone"] = phone
    await send_html(message, f"✅ Телефон сохранен: {h(phone)}", reply_markup=ReplyKeyboardRemove())
    await show_model_choice(message, state)


@dp.message(RepairStates.waiting_contact, F.text)
async def process_contact_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if (message.text or "").strip() == "⬅️ Назад":
        await send_html(message, "Возвращаюсь к условиям.", reply_markup=ReplyKeyboardRemove())
        await show_agreement(message, state)
        return

    phone = normalize_ru_phone(message.text or "")
    if not phone:
        await send_html(
            message,
            "❌ Не похоже на российский номер. Введите в формате +7XXXXXXXXXX или нажмите «Поделиться телефоном». ",
            reply_markup=contact_keyboard(),
        )
        return
    ensure_user(user_id)["contact_phone"] = phone
    await send_html(message, f"✅ Телефон сохранен: {h(phone)}", reply_markup=ReplyKeyboardRemove())
    await show_model_choice(message, state)


@dp.callback_query(F.data == "back_contact")
async def back_contact(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass
    await ask_contact(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data == "back_model")
async def back_model(callback: CallbackQuery, state: FSMContext) -> None:
    await show_model_choice(callback.message, state, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "photo_phone")
async def photo_phone(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    temp_photos[user_id] = []
    await state.set_state(RepairStates.waiting_photos)
    await safe_edit(
        callback.message,
        "📸 <b>Отправьте 2 фото телефона:</b>\n\n"
        "1️⃣ Передняя сторона\n"
        "2️⃣ Задняя сторона\n\n"
        "Можно добавить подпись с моделью, если знаете ее.\n\n"
        "⚠️ Если VISION_API_URL не настроен, фото не получится распознать автоматически — бот попросит ввести модель вручную.",
        ikb([[btn("✍️ Ввести модель вручную", "manual_model")]], back="back_model"),
    )
    await callback.answer()


@dp.message(RepairStates.waiting_photos, F.photo)
async def handle_photos(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    ensure_user(user_id)
    telegram_bot = get_bot()

    photo = message.photo[-1]
    file = await telegram_bot.get_file(photo.file_id)
    PHOTOS_DIR.mkdir(exist_ok=True)
    file_path = PHOTOS_DIR / f"{user_id}_{len(temp_photos.get(user_id, []))}_{photo.file_unique_id}.jpg"
    await telegram_bot.download_file(file.file_path, destination=file_path)

    temp_photos.setdefault(user_id, []).append(str(file_path))

    if len(temp_photos[user_id]) == 1:
        await send_html(
            message,
            "✅ Первое фото получено. Теперь отправьте фото задней стороны.",
            reply_markup=ikb([[btn("✍️ Ввести модель вручную", "manual_model")]], back="back_model"),
        )
        return

    await send_html(message, "🔄 Анализирую фото...")
    result = await detect_phone_with_vision(temp_photos[user_id], caption=message.caption or "")

    if result and result.get("brand") != "Неизвестен" and int(result.get("confidence", 0) or 0) > 0:
        model = f"{result.get('brand', '')} {result.get('model', '')}".strip()
        rejected, reason = is_rejected_model(model)
        if rejected or result.get("is_chinese_copy"):
            await send_html(
                message,
                "❌ <b>Устройство не принято.</b>\n\n"
                f"Причина: {h(reason or 'похоже на копию/реплику')}\n\n"
                "Если это ошибка — введите модель вручную и уточните, что устройство оригинальное.",
                reply_markup=ikb([[btn("✍️ Ввести модель вручную", "manual_model")]], back="back_model"),
            )
            await state.set_state(RepairStates.manual_model)
            return

        user_data[user_id]["model"] = model
        user_data[user_id]["brand"] = result.get("brand", "")
        user_data[user_id]["model_confidence"] = result.get("confidence", 0)

        await send_html(
            message,
            f"🔍 <b>Результат распознавания:</b>\n\n"
            f"📱 Модель: <b>{h(model)}</b>\n"
            f"🏷 Бренд: {h(result.get('brand', ''))}\n"
            f"📅 Год: {h(result.get('year', 'неизвестен'))}\n"
            f"🎯 Точность: {h(result.get('confidence', 0))}%\n\n"
            "Если модель верна — продолжайте. Если нет — введите вручную.",
            reply_markup=ikb(
                [
                    [btn("✅ Верно, выбрать ремонт", "model_confirm")],
                    [btn("✍️ Ввести модель вручную", "manual_model")],
                    [btn("🔁 Отправить фото заново", "photo_phone")],
                ],
                back="back_model",
            ),
        )
        await state.set_state(RepairStates.waiting_phone)
    else:
        error = result.get("error") if result else ""
        await send_html(
            message,
            "❌ Не удалось автоматически определить модель телефона.\n\n"
            f"{h(error)}\n\n"
            "Введите модель вручную, например: <b>iPhone 13 Pro</b> или <b>Samsung Galaxy S23</b>.",
            reply_markup=ikb([[btn("⬅️ К выбору способа", "back_model")]]),
        )
        await state.set_state(RepairStates.manual_model)


@dp.callback_query(F.data == "manual_model")
async def manual_model(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RepairStates.manual_model)
    await safe_edit(
        callback.message,
        "✍️ <b>Введите модель телефона вручную</b>\n\n"
        "Например: <b>iPhone 13 Pro</b>, <b>Samsung Galaxy S23</b>.\n"
        "Если устройство оригинальное — можно написать: «оригинал iPhone 13». ",
        ikb([], back="back_model"),
    )
    await callback.answer()


@dp.callback_query(F.data == "model_confirm")
async def model_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    if not user_data.get(user_id, {}).get("model"):
        await callback.answer("Сначала укажите модель", show_alert=True)
        return
    await show_services_selection(callback.message, user_id, state, edit=True)
    await callback.answer()


@dp.message(RepairStates.manual_model, F.text)
async def process_manual_model(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if text == "⬅️ Назад":
        await show_model_choice(message, state)
        return
    if len(text) < 2:
        await send_html(message, "Введите модель подробнее, например: <b>iPhone 13 Pro</b>.")
        return

    rejected, reason = is_rejected_model(text)
    if rejected:
        await send_html(
            message,
            "❌ <b>Устройство не принято.</b>\n\n"
            f"Причина: {h(reason)}\n\n"
            "Если это ошибка, напишите модель еще раз и уточните, что телефон оригинальный.",
            reply_markup=ikb([], back="back_model"),
        )
        return

    normalized = await normalize_model_text(text)
    model = f"{normalized.get('brand', '')} {normalized.get('model', text)}".strip()
    # Не дублируем бренд, если DeepSeek вернул model уже с брендом.
    brand = normalized.get("brand", "")
    if brand and model.lower().startswith(f"{brand} {brand}".lower()):
        model = model[len(brand) + 1 :]

    user_data.setdefault(user_id, {"selected_services": []})
    user_data[user_id]["model"] = model or text
    user_data[user_id]["brand"] = brand
    user_data[user_id]["model_confidence"] = normalized.get("confidence", 70)

    await send_html(message, f"✅ Модель сохранена: <b>{h(user_data[user_id]['model'])}</b>")
    await show_services_selection(message, user_id, state)


@dp.callback_query(F.data.startswith("service_"))
async def select_service(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    ensure_user(user_id)
    service_key = callback.data.replace("service_", "", 1)

    if service_key == "other":
        await state.set_state(RepairStates.other_problem)
        await safe_edit(callback.message, "📝 <b>Опишите проблему подробно:</b>", ikb([], back="back_services"))
        await callback.answer()
        return

    if service_key not in PRICELIST:
        await callback.answer("Неизвестная услуга", show_alert=True)
        return

    services = user_data[user_id].setdefault("selected_services", [])
    if service_key in services:
        services.remove(service_key)
        await callback.answer(f"Убрано: {PRICELIST[service_key]['name']}")
    else:
        services.append(service_key)
        await callback.answer(f"Добавлено: {PRICELIST[service_key]['name']}")

    await show_services_selection(callback.message, user_id, state, edit=True)


@dp.callback_query(F.data == "back_services")
async def back_services(callback: CallbackQuery, state: FSMContext) -> None:
    await show_services_selection(callback.message, callback.from_user.id, state, edit=True)
    await callback.answer()


@dp.message(RepairStates.other_problem, F.text)
async def process_other_problem(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if text == "⬅️ Назад":
        await show_services_selection(message, user_id, state)
        return
    if len(text) < 5:
        await send_html(message, "Опишите проблему чуть подробнее.")
        return
    user_data[user_id]["other_problem"] = text
    services = user_data[user_id].setdefault("selected_services", [])
    if "other" not in services:
        services.append("other")
    await show_services_selection(message, user_id, state)


@dp.callback_query(F.data == "show_selected")
async def show_selected(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    services = user_data.get(user_id, {}).get("selected_services", [])
    if not services:
        await callback.answer("Пока ничего не выбрано", show_alert=True)
        return

    lines = []
    for name in service_names(user_id):
        lines.append(f"✅ {name}")
    lines.append(f"\nИтого за работу: {work_total(user_id)}₽")
    await callback.answer("\n".join(lines), show_alert=True)


@dp.callback_query(F.data == "services_done")
async def services_done(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    services = user_data.get(user_id, {}).get("selected_services", [])
    if not services:
        await callback.answer("Выберите хотя бы одну услугу", show_alert=True)
        return
    await show_delivery_selection(callback.message, state, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "back_delivery")
async def back_delivery(callback: CallbackQuery, state: FSMContext) -> None:
    await show_delivery_selection(callback.message, state, edit=True)
    await callback.answer()


@dp.callback_query(F.data.startswith("delivery_"))
async def process_delivery(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    method = callback.data.replace("delivery_", "", 1)
    ensure_user(user_id)["delivery_method"] = method

    if method == "pickup":
        user_data[user_id]["delivery_cost"] = 0
        user_data[user_id]["delivery_period"] = "личная встреча"
        await prepare_parts_and_show(callback.message, user_id, state, edit=True)
        await callback.answer()
        return

    if method not in {"cdek", "rupost"}:
        await callback.answer("Неизвестный способ доставки", show_alert=True)
        return

    await state.set_state(RepairStates.waiting_delivery_name)
    await safe_edit(
        callback.message,
        f"🚚 <b>{h(delivery_name(method))}</b>\n\n"
        "Введите ФИО получателя для оформления обратной доставки.\n"
        "Например: <b>Иванов Иван Иванович</b>",
        ikb([], back="back_delivery"),
    )
    await callback.answer()


@dp.message(RepairStates.waiting_delivery_name, F.text)
async def process_delivery_name(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if len(text) < 3:
        await send_html(message, "Введите ФИО получателя полностью.", reply_markup=ikb([], back="back_delivery"))
        return

    user_data[user_id]["delivery_full_name"] = text
    method = user_data[user_id].get("delivery_method")
    await state.set_state(RepairStates.waiting_delivery_address)

    if method == "cdek":
        prompt = (
            "🚚 <b>СДЭК</b>\n\n"
            "Введите город и адрес/ПВЗ для обратной доставки.\n"
            "Формат: <b>город, адрес или код ПВЗ</b>\n"
            "Пример: <b>Москва, ПВЗ MSK123</b> или <b>Москва, ул. Тверская, 1</b>"
        )
    else:
        prompt = (
            "📮 <b>Почта России</b>\n\n"
            "Введите адрес в формате:\n"
            "<b>индекс, регион, город, улица, дом, квартира</b>\n\n"
            "Пример: <b>180000, Псковская область, Псков, ул. Ленина, 10, 1</b>"
        )
    await send_html(message, prompt, reply_markup=ikb([], back="back_delivery"))


@dp.message(RepairStates.waiting_delivery_address, F.text)
async def process_delivery_address(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    raw = (message.text or "").strip()
    method = user_data[user_id].get("delivery_method")

    if method == "cdek":
        parts = [p.strip() for p in raw.split(",", 1)]
        city = parts[0]
        address = parts[1] if len(parts) > 1 else raw
        if len(city) < 2:
            await send_html(message, "Введите город для СДЭК, например: <b>Москва, ПВЗ MSK123</b>.", reply_markup=ikb([], back="back_delivery"))
            return
        user_data[user_id]["delivery_city"] = city
        user_data[user_id]["delivery_address"] = address
        await send_html(message, "🔄 Рассчитываю доставку СДЭК...")
        try:
            quote = await cdek_client.quote(city, address)
        except Exception as exc:
            quote = {"provider": "cdek", "title": "СДЭК (примерный расчет)", "cost": 450, "period": "2–5 дней", "note": str(exc)}
        user_data[user_id]["delivery_cost"] = int(quote.get("cost", 0) or 0)
        user_data[user_id]["delivery_period"] = quote.get("period", "срок уточняется")
        user_data[user_id]["delivery_quote"] = quote
        if quote.get("tariff_code"):
            user_data[user_id]["cdek_tariff_code"] = quote.get("tariff_code")
        if quote.get("city_code"):
            user_data[user_id]["cdek_city_code"] = quote.get("city_code")

    elif method == "rupost":
        parsed = parse_russian_post_address(raw)
        if not parsed:
            await send_html(
                message,
                "❌ Не удалось распознать адрес. Нужен формат:\n"
                "<b>индекс, регион, город, улица, дом, квартира</b>\n"
                "Пример: <b>180000, Псковская область, Псков, ул. Ленина, 10, 1</b>",
                reply_markup=ikb([], back="back_delivery"),
            )
            return
        user_data[user_id]["delivery_address"] = raw
        user_data[user_id]["post_address_struct"] = parsed
        user_data[user_id]["delivery_city"] = parsed.get("place", "")
        await send_html(message, "🔄 Рассчитываю доставку Почтой России...")
        try:
            quote = await russian_post_client.quote(parsed["index"])
        except Exception as exc:
            quote = {"provider": "rupost", "title": "Почта России (примерный расчет)", "cost": 350, "period": "3–7 дней", "note": str(exc)}
        user_data[user_id]["delivery_cost"] = int(quote.get("cost", 0) or 0)
        user_data[user_id]["delivery_period"] = quote.get("period", "срок уточняется")
        user_data[user_id]["delivery_quote"] = quote
    else:
        await send_html(message, "Сначала выберите способ доставки.")
        await show_delivery_selection(message, state)
        return

    quote = user_data[user_id].get("delivery_quote", {})
    await send_html(
        message,
        f"✅ <b>Доставка рассчитана:</b>\n\n"
        f"Способ: {h(quote.get('title', delivery_name(method)))}\n"
        f"Стоимость: <b>{delivery_total(user_id)}₽</b>\n"
        f"Срок: {h(user_data[user_id].get('delivery_period'))}\n"
        f"{('Примечание: ' + h(quote.get('note'))) if quote.get('note') else ''}",
    )
    await prepare_parts_and_show(message, user_id, state, edit=False)


@dp.callback_query(F.data == "part_advice")
async def part_advice(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await safe_edit(
        callback.message,
        "💡 <b>Совет по выбору запчасти</b>\n\n"
        "• Если нужен максимально надежный результат — выбирайте оригинал.\n"
        "• Если важна цена — можно выбрать качественный аналог.\n\n"
        "Выберите вариант ниже:",
        part_keyboard(user_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("part_"))
async def process_part(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    part_type = callback.data.replace("part_", "", 1)

    if part_type == "cheap":
        parts = user_data[user_id].get("ozon_parts", [])
        selected = parts[0] if parts else {"title": "Аналог", "price": 800, "url": "https://ozon.ru"}
        user_data[user_id]["part_type"] = "Аналог"
    elif part_type == "expensive":
        parts = user_data[user_id].get("original_parts", [])
        selected = parts[0] if parts else {"title": "Оригинал", "price": 2200, "url": "https://dns-shop.ru"}
        user_data[user_id]["part_type"] = "Оригинал"
    else:
        await callback.answer("Неизвестный тип запчасти", show_alert=True)
        return

    user_data[user_id]["part_price"] = int(selected.get("price", 0) or 0)
    user_data[user_id]["part_url"] = selected.get("url", "")
    user_data[user_id]["part_name"] = selected.get("title", "")

    if user_data[user_id].get("delivery_method") == "pickup":
        await state.set_state(RepairStates.waiting_pickup_time)
        await safe_edit(
            callback.message,
            "⏰ <b>Выберите время встречи</b>\n\n"
            f"📍 Адрес: {h(SHOP_ADDRESS)}\n"
            "Приходите с телефоном в выбранное время.",
            pickup_time_keyboard(),
        )
    else:
        await show_checkout(callback.message, user_id, state, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "back_part")
async def back_part(callback: CallbackQuery, state: FSMContext) -> None:
    await show_part_selection(callback.message, callback.from_user.id, state, edit=True)
    await callback.answer()


@dp.callback_query(F.data.startswith("pickup_time_"))
async def process_pickup_time(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    choice = callback.data.replace("pickup_time_", "", 1)
    time_map = {
        "today_evening": "Сегодня 18:00–20:00",
        "tomorrow_morning": "Завтра 10:00–12:00",
        "tomorrow_evening": "Завтра 18:00–20:00",
    }

    if choice == "other":
        await state.set_state(RepairStates.waiting_time_manual)
        await safe_edit(callback.message, "📝 Напишите удобные дату и время встречи:", ikb([], back="back_part"))
        await callback.answer()
        return

    user_data[user_id]["time"] = time_map.get(choice, choice)
    await show_checkout(callback.message, user_id, state, edit=True)
    await callback.answer()


@dp.message(RepairStates.waiting_time_manual, F.text)
async def manual_time(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if len(text) < 3:
        await send_html(message, "Напишите дату и время подробнее.")
        return
    user_data[user_id]["time"] = text
    await show_checkout(message, user_id, state)


@dp.callback_query(F.data == "back_checkout")
async def back_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    if user_data.get(user_id, {}).get("delivery_method") == "pickup":
        await state.set_state(RepairStates.waiting_pickup_time)
        await safe_edit(callback.message, "⏰ <b>Выберите время встречи:</b>", pickup_time_keyboard())
    else:
        await show_part_selection(callback.message, user_id, state, edit=True)
    await callback.answer()


# ==========================================================
#                          ОПЛАТА
# ==========================================================

@dp.callback_query(F.data == "pay_bank")
async def pay_bank(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    order_id = create_or_update_order(user_id, status="created")
    total = order_total(user_id)

    text = f"""
💳 <b>Оплата заказа #{h(order_id)}</b>

Сумма к оплате: <b>{total}₽</b>

🏦 Банк: <b>{h(PAYMENT_BANK)}</b>
📞 Номер для перевода: <b>{h(PAYMENT_PHONE)}</b>

После оплаты отправьте скриншот перевода сюда в чат.
""".strip()
    await state.set_state(RepairStates.waiting_payment_screenshot)
    await safe_edit(callback.message, text, ikb([], back="back_checkout"))
    await callback.answer()


@dp.callback_query(F.data == "other_payment")
async def other_payment(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    rows: list[list[InlineKeyboardButton]] = []
    if ENABLE_STARS_PAYMENT:
        rows.append([btn("⭐ Telegram Stars", "pay_stars")])
    rows.append([btn("💳 Перевод на карту", "pay_bank")])
    if user_data.get(user_id, {}).get("delivery_method") == "pickup":
        rows.append([btn("💵 Наличные при встрече", "pay_cash")])

    await safe_edit(
        callback.message,
        "💳 <b>Способы оплаты</b>\n\n"
        f"1. Перевод на карту {h(PAYMENT_BANK)}: {h(PAYMENT_PHONE)}\n"
        "2. Telegram Stars — кнопка ниже.\n"
        "3. Наличные — только для личной встречи в Пскове.\n\n"
        "Выберите вариант:",
        ikb(rows, back="back_checkout"),
    )
    await callback.answer()


@dp.callback_query(F.data == "pay_cash")
async def pay_cash(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    if user_data.get(user_id, {}).get("delivery_method") != "pickup":
        await callback.answer("Наличные доступны только при личной встрече", show_alert=True)
        return
    order_id = create_or_update_order(user_id, status="confirmed")
    user_data[user_id]["payment_method"] = "Наличные при встрече"
    user_data[user_id]["payment_confirmed"] = False
    update_order_snapshot(order_id, status="confirmed")
    await finalize_order_after_payment(order_id, user_id, payment_confirmed=False)
    await state.set_state(RepairStates.order_confirmed)
    await callback.answer()


@dp.callback_query(F.data == "pay_stars")
async def pay_stars(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    if not ENABLE_STARS_PAYMENT:
        await callback.answer("Оплата Stars отключена", show_alert=True)
        return

    order_id = create_or_update_order(user_id, status="created")
    total_rub = order_total(user_id)
    stars_amount = max(1, int(math.ceil(total_rub / STAR_RUB_RATE)))
    user_data[user_id]["stars_amount"] = stars_amount
    update_order_snapshot(order_id)

    await get_bot().send_invoice(
        chat_id=callback.message.chat.id,
        title=f"Заказ {order_id}",
        description=f"Ремонт телефона. Сумма: {total_rub}₽",
        payload=f"order:{order_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"Заказ {order_id}", amount=stars_amount)],
    )
    await callback.message.answer(
        "⭐ Счет Telegram Stars отправлен выше. После успешной оплаты заказ подтвердится автоматически.",
        reply_markup=ikb([], back="back_checkout"),
    )
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    payload = pre_checkout_query.invoice_payload or ""
    order_id = payload.replace("order:", "", 1)
    if order_id not in orders:
        await pre_checkout_query.answer(ok=False, error_message="Заказ не найден. Создайте заказ заново.")
        return
    await pre_checkout_query.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message, state: FSMContext) -> None:
    payment = message.successful_payment
    payload = payment.invoice_payload or ""
    order_id = payload.replace("order:", "", 1)
    order = orders.get(order_id)
    if not order:
        await send_html(message, "✅ Оплата получена, но заказ не найден. Напишите администратору.")
        return

    user_id = int(order["user_id"])
    ensure_user(user_id).update(order.get("data", {}))
    user_data[user_id]["payment_method"] = "Telegram Stars"
    user_data[user_id]["payment_confirmed"] = True
    user_data[user_id]["telegram_payment_charge_id"] = payment.telegram_payment_charge_id
    user_data[user_id]["payment_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    update_order_snapshot(order_id, status="paid")

    await send_html(message, f"✅ Оплата Telegram Stars получена! Заказ #{h(order_id)} оформляется.")
    await finalize_order_after_payment(order_id, user_id, payment_confirmed=True)
    await state.set_state(RepairStates.order_confirmed)


@dp.message(RepairStates.waiting_payment_screenshot, F.photo)
async def handle_payment_screenshot(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    data = ensure_user(user_id)
    order_id = data.get("order_id") or create_or_update_order(user_id, status="created")
    photo = message.photo[-1]

    data["payment_screenshot"] = photo.file_id
    data["payment_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["payment_method"] = "Перевод на карту"
    update_order_snapshot(order_id, status="payment_pending")

    await notify_admin_about_payment(order_id, photo.file_id, message)
    await send_html(
        message,
        f"✅ Скриншот оплаты получен.\n\n"
        f"📦 Заказ #{h(order_id)} ожидает подтверждения мастера. Обычно это занимает до 10 минут.",
        reply_markup=ikb([[btn("📊 Статус заказа", "show_order_status")]], back="back_checkout"),
    )
    await state.set_state(RepairStates.order_confirmed)


@dp.message(RepairStates.waiting_payment_screenshot)
async def handle_payment_screenshot_wrong(message: Message) -> None:
    await send_html(message, "Пожалуйста, отправьте именно <b>фото/скриншот</b> перевода.")


async def notify_admin_about_payment(order_id: str, photo_file_id: str, message: Message) -> None:
    data = user_data.get(message.from_user.id, {})
    caption = build_admin_order_caption(order_id, data, header="📸 НОВЫЙ ПЛАТЕЖ НА ПРОВЕРКУ")
    caption += f"\n\n✅ Подтвердить: <code>/confirm_{h(order_id)}</code>"
    try:
        await get_bot().send_photo(ADMIN_ID, photo_file_id, caption=caption, parse_mode="HTML")
    except Exception as exc:
        print(f"Не удалось отправить админу платеж: {exc}")


@dp.message(F.text.startswith("/confirm_"))
async def confirm_payment(message: Message) -> None:
    if message.from_user.id != ADMIN_ID:
        await send_html(message, "❌ Нет прав.")
        return

    order_id = (message.text or "").replace("/confirm_", "", 1).strip()
    if order_id not in orders:
        await send_html(message, f"❌ Заказ {h(order_id)} не найден.")
        return

    user_id = int(orders[order_id]["user_id"])
    ensure_user(user_id).update(orders[order_id].get("data", {}))
    user_data[user_id]["payment_confirmed"] = True
    update_order_snapshot(order_id, status="confirmed")
    await finalize_order_after_payment(order_id, user_id, payment_confirmed=True)
    await send_html(message, f"✅ Оплата подтверждена. Заказ #{h(order_id)} обновлен.")


# ==========================================================
#              ФИНАЛИЗАЦИЯ, ДОСТАВКА, ТРЕКИНГ
# ==========================================================

def build_admin_order_caption(order_id: str, data: dict[str, Any], header: str = "🧾 НОВЫЙ ЗАКАЗ") -> str:
    services = data.get("selected_services", [])
    service_lines = []
    for service_key in services:
        if service_key == "other":
            service_lines.append(f"Другая проблема: {data.get('other_problem', '')}")
        else:
            service_lines.append(PRICELIST.get(service_key, {}).get("name", service_key))

    return f"""
<b>{h(header)}</b>

📦 Заказ: <b>{h(order_id)}</b>
👤 Клиент: <code>{h(data.get('user_id', ''))}</code>
📞 Телефон: {h(data.get('contact_phone', 'Не указан'))}
📱 Модель: {h(data.get('model', 'Не указана'))}
🛠 Услуги: {h(', '.join(service_lines) or 'Не указаны')}
🔧 Запчасть: {h(data.get('part_name', 'Не указана'))}
🔗 Ссылка: {h(data.get('part_url', 'Нет'))}
🚚 Доставка: {h(delivery_name(data.get('delivery_method', '')))}
📍 Адрес/ПВЗ: {h(data.get('delivery_address') or SHOP_ADDRESS)}
⏰ Время: {h(data.get('time', ''))}
💰 Сумма: <b>{h(data.get('total_price', 0))}₽</b>
""".strip()


async def finalize_order_after_payment(order_id: str, user_id: int, payment_confirmed: bool) -> None:
    data = ensure_user(user_id)
    data["user_id"] = user_id
    data["total_price"] = order_total(user_id)

    shipping_result: dict[str, Any] = {}
    method = data.get("delivery_method")

    if payment_confirmed and method == "cdek" and not data.get("shipping_api_result"):
        try:
            shipping_result = await cdek_client.create_order(order_id, data)
        except Exception as exc:
            shipping_result = {"created": False, "error": str(exc)}
        data["shipping_api_result"] = shipping_result
        if shipping_result.get("cdek_number"):
            data["shipping_tracking"] = shipping_result.get("cdek_number")

    if payment_confirmed and method == "rupost" and not data.get("shipping_api_result"):
        try:
            shipping_result = await russian_post_client.create_order(order_id, data)
        except Exception as exc:
            shipping_result = {"created": False, "error": str(exc)}
        data["shipping_api_result"] = shipping_result
        if shipping_result.get("barcode"):
            data["shipping_tracking"] = shipping_result.get("barcode")

    update_order_snapshot(order_id, status="confirmed" if payment_confirmed else "created")

    # Сообщение клиенту
    tracking_link = generate_tracking_link(order_id)
    if method == "pickup":
        client_text = f"""
✅ <b>Заказ #{h(order_id)} оформлен!</b>

Статус оплаты: {('подтверждена' if payment_confirmed else 'оплата наличными при встрече')}
📍 Встреча: {h(SHOP_ADDRESS)}
⏰ Время: {h(data.get('time', 'уточняется'))}
💰 Сумма: {h(data.get('total_price', 0))}₽

📊 Статус заказа: {h(tracking_link)}
""".strip()
    else:
        client_text = f"""
✅ <b>Заказ #{h(order_id)} оформлен!</b>

Статус оплаты: {('подтверждена' if payment_confirmed else 'ожидает подтверждения')}
🚚 Обратная доставка: {h(delivery_name(method))}
📍 Ваш адрес/ПВЗ: {h(data.get('delivery_address', ''))}
💰 Сумма: {h(data.get('total_price', 0))}₽

📦 <b>Теперь отправьте телефон в ремонт:</b>
Адрес мастерской: <b>{h(SHOP_ADDRESS)}</b>
Получатель/комментарий: <b>{h(SHOP_NAME)}, заказ {h(order_id)}</b>
Телефон: <b>{h(SHOP_PHONE)}</b>

После отправки нажмите кнопку ниже и введите трек-номер СДЭК/Почты.
📊 Статус заказа: {h(tracking_link)}
""".strip()

    try:
        await get_bot().send_message(
            user_id,
            client_text,
            reply_markup=tracking_after_payment_keyboard() if method in {"cdek", "rupost"} else ikb([[btn("📊 Статус заказа", "show_order_status")]]),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        print(f"Не удалось отправить клиенту финал заказа: {exc}")

    # Сообщение админу
    admin_caption = build_admin_order_caption(order_id, data, header="✅ ЗАКАЗ ОФОРМЛЕН")
    if shipping_result:
        admin_caption += f"\n\n🚚 API доставки: <code>{h(json.dumps(shipping_result, ensure_ascii=False)[:900])}</code>"
    try:
        await get_bot().send_message(ADMIN_ID, admin_caption, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        print(f"Не удалось отправить админу заказ: {exc}")


@dp.callback_query(F.data == "enter_tracking")
async def enter_tracking(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RepairStates.waiting_tracking_number)
    await callback.message.answer(
        "📦 Введите трек-номер отправления СДЭК/Почты России.\n\n"
        "Например: 1234567890 или 80081234567890",
        reply_markup=ikb([[btn("📊 Статус заказа", "show_order_status")]]),
    )
    await callback.answer()


@dp.message(RepairStates.waiting_tracking_number, F.text)
async def process_tracking_number(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    data = ensure_user(user_id)
    order_id = data.get("order_id")
    if not order_id:
        # Попробуем найти последний заказ пользователя.
        user_orders = [oid for oid, order in orders.items() if int(order.get("user_id", 0)) == user_id]
        order_id = user_orders[-1] if user_orders else None
    if not order_id:
        await send_html(message, "❌ Заказ не найден. Нажмите /start и оформите заказ заново.")
        return

    tracking = (message.text or "").strip()
    if len(tracking) < 5:
        await send_html(message, "Трек-номер слишком короткий. Введите корректный номер.")
        return

    data["client_tracking_number"] = tracking
    data["tracking_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    update_order_snapshot(order_id, status="in_delivery")

    await send_html(message, f"✅ Трек-номер сохранен: <b>{h(tracking)}</b>\nМастер получил уведомление.")
    try:
        await get_bot().send_message(
            ADMIN_ID,
            f"📦 Клиент отправил телефон\n\nЗаказ: <b>{h(order_id)}</b>\nТрек: <b>{h(tracking)}</b>\nID клиента: <code>{user_id}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        print(f"Не удалось отправить трек админу: {exc}")
    await state.set_state(RepairStates.order_confirmed)


@dp.callback_query(F.data == "show_order_status")
async def show_status_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    data = user_data.get(user_id, {})
    order_id = data.get("order_id")
    if not order_id:
        user_orders = [oid for oid, order in orders.items() if int(order.get("user_id", 0)) == user_id]
        order_id = user_orders[-1] if user_orders else None
    if not order_id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await show_order_status_message(callback.message, order_id)
    await callback.answer()


# ==========================================================
#                         HELP / ADMIN
# ==========================================================

@dp.message(F.text == "/help")
async def cmd_help(message: Message) -> None:
    await send_html(
        message,
        "🤖 <b>Помощь по боту</b>\n\n"
        "• /start — новый заказ\n"
        "• /help — помощь\n"
        "• /confirm_НОМЕР — подтвердить оплату (только админ)\n\n"
        "В заказе доступны: СДЭК, Почта России, Telegram Stars, перевод на карту, кнопки «Назад».\n\n"
        "Если фото телефона не распознаются — настройте VISION_API_URL/VISION_API_KEY или используйте ручной ввод модели.",
    )


@dp.message(F.text.startswith("/status_"))
async def cmd_status(message: Message) -> None:
    order_id = (message.text or "").replace("/status_", "", 1).strip()
    await show_order_status_message(message, order_id)


# ==========================================================
#                           ЗАПУСК
# ==========================================================

async def main() -> None:
    global bot
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN. Создайте .env или задайте переменную окружения BOT_TOKEN.")

    PHOTOS_DIR.mkdir(exist_ok=True)
    load_orders()
    bot = Bot(token=TOKEN)

    print("=" * 60)
    print("🤖 БОТ ПО РЕМОНТУ ТЕЛЕФОНОВ ЗАПУЩЕН")
    print("=" * 60)
    print(f"📨 Админ ID: {ADMIN_ID}")
    print(f"💳 Оплата: {PAYMENT_PHONE} ({PAYMENT_BANK})")
    print(f"⭐ Telegram Stars: {'включены' if ENABLE_STARS_PAYMENT else 'выключены'}, курс 1⭐ = {STAR_RUB_RATE}₽")
    print(f"🚚 СДЭК: {'настроен' if cdek_client.configured else 'демо/примерный расчет'}")
    print(f"📮 Почта России: {'настроена' if russian_post_client.configured else 'демо/примерный расчет'}")
    print(f"🤖 DeepSeek text: {'настроен' if DEEPSEEK_API_KEY else 'нет'}")
    print(f"👁 Vision API: {'настроен' if (VISION_API_URL and VISION_API_KEY) else 'нет'}")
    print(f"📦 Следующий заказ: PSK-{datetime.now().strftime('%Y%m%d')}-{order_counter + 1:04d}")
    print("=" * 60)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
