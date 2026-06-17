import asyncio
import os
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import TOKEN, ADMIN_ID, PAYMENT_PHONE, PAYMENT_BANK
from database import init_db, save_order, update_order_status, get_order, OrderStatus, STATUS_RU
import parser  # отдельный файл с парсингом

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_data = {}
temp_photos = {}

class RepairStates(StatesGroup):
    agreement = State()
    waiting_phone = State()
    waiting_photos = State()
    manual_model = State()
    selecting_services = State()
    waiting_location = State()
    waiting_part = State()
    waiting_time = State()
    waiting_payment = State()
    waiting_payment_screenshot = State()

PRICELIST = {
    "display": {"name": "Замена дисплея", "price": 500},
    "battery": {"name": "Замена аккумулятора", "price": 400},
    "speaker": {"name": "Замена динамика", "price": 250},
    "camera": {"name": "Замена камеры", "price": 300},
    "back_panel": {"name": "Замена задней панели", "price": 250},
    "buttons": {"name": "Замена кнопок", "price": 250},
    "fingerprint": {"name": "Замена сканера", "price": 270},
    "charging": {"name": "Замена разъема зарядки", "price": 300},
    "microphone": {"name": "Замена микрофона", "price": 250}
}

CHINESE_BRANDS = ['xiaomi', 'redmi', 'poco', 'huawei', 'honor', 'oppo', 'vivo', 'realme']

def generate_order_id():
    import random
    return f"PSK-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}"

def get_total(data):
    work = sum(PRICELIST.get(s, {}).get('price', 0) for s in data.get('selected_services', []))
    return work + data.get('part_price', 800)

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    user_data[message.from_user.id] = {"selected_services": []}
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Согласен", callback_data="agree")]])
    await message.answer("📜 Публичный договор оферты...\n\nНажмите 'Согласен'", reply_markup=kb)
    await state.set_state(RepairStates.agreement)

@dp.callback_query(F.data == "agree")
async def agree(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 По фото", callback_data="photo")],
        [InlineKeyboardButton(text="✍️ Вручную", callback_data="manual")]
    ])
    await callback.message.edit_text("📱 Как определить модель?", reply_markup=kb)
    await state.set_state(RepairStates.waiting_phone)

@dp.callback_query(F.data == "photo")
async def photo_mode(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📸 Отправьте 2 фото телефона")
    await state.set_state(RepairStates.waiting_photos)

@dp.message(RepairStates.waiting_photos, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in temp_photos:
        temp_photos[uid] = []
    temp_photos[uid].append(message.photo[-1].file_id)
    
    if len(temp_photos[uid]) == 2:
        await message.answer("🔄 Анализирую...")
        result = await parser.detect_phone(temp_photos[uid])
        if result.get("is_chinese_copy"):
            await message.answer("❌ Китайская копия. Введите модель вручную.")
            await state.set_state(RepairStates.manual_model)
        else:
            user_data[uid]["model"] = f"{result.get('brand')} {result.get('model')}"
            await show_services(message, uid, state)
    else:
        await message.answer("✅ Первое фото получено. Отправьте второе.")

@dp.message(RepairStates.manual_model, F.text)
async def manual_model(message: Message, state: FSMContext):
    uid = message.from_user.id
    if any(b in message.text.lower() for b in CHINESE_BRANDS):
        await message.answer("❌ Китайские бренды не принимаем.")
        return
    user_data[uid]["model"] = message.text
    await show_services(message, uid, state)

async def show_services(message, uid, state):
    await state.set_state(RepairStates.selecting_services)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for k, v in PRICELIST.items():
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{v['name']} — {v['price']}₽", callback_data=f"svc_{k}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="✅ Готово", callback_data="services_done")])
    await message.answer("🛠 Выберите услуги:", reply_markup=kb)

@dp.callback_query(F.data.startswith("svc_"))
async def select_service(callback: CallbackQuery):
    uid = callback.from_user.id
    key = callback.data.split("_")[1]
    if "selected_services" not in user_data[uid]:
        user_data[uid]["selected_services"] = []
    if key in user_data[uid]["selected_services"]:
        user_data[uid]["selected_services"].remove(key)
    else:
        user_data[uid]["selected_services"].append(key)
    await callback.answer("Добавлено" if key in user_data[uid]["selected_services"] else "Убрано")

@dp.callback_query(F.data == "services_done")
async def services_done(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not user_data[uid].get("selected_services"):
        await callback.answer("Выберите услуги!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В Пскове", callback_data="loc_pskov")],
        [InlineKeyboardButton(text="📦 Почтой", callback_data="loc_mail")]
    ])
    await callback.message.edit_text("📍 Способ ремонта?", reply_markup=kb)
    await state.set_state(RepairStates.waiting_location)

@dp.callback_query(F.data.startswith("loc_"))
async def choose_location(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    user_data[uid]["location"] = callback.data.split("_")[1]
    await callback.message.edit_text("🔍 Ищу запчасти...")
    
    model = user_data[uid].get("model", "iPhone")
    part = PRICELIST[user_data[uid]["selected_services"][0]]["name"]
    
    ozon = await parser.parse_ozon(part, model)
    orig = await parser.parse_original(part, model)
    
    user_data[uid]["ozon"] = ozon
    user_data[uid]["original"] = orig
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟢 Аналог {ozon[0]['price']}₽", callback_data="part_cheap")],
        [InlineKeyboardButton(text=f"🔵 Оригинал {orig[0]['price']}₽", callback_data="part_exp")]
    ])
    await callback.message.edit_text("🔧 Выберите запчасть:", reply_markup=kb)
    await state.set_state(RepairStates.waiting_part)

@dp.callback_query(F.data.startswith("part_"))
async def choose_part(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    choice = callback.data.split("_")[1]
    
    if choice == "cheap":
        p = user_data[uid]["ozon"][0]
        user_data[uid]["part_type"] = "Аналог"
    else:
        p = user_data[uid]["original"][0]
        user_data[uid]["part_type"] = "Оригинал"
    
    user_data[uid]["part_name"] = p["title"]
    user_data[uid]["part_price"] = p["price"]
    user_data[uid]["part_url"] = p.get("url", "")
    
    order_id = generate_order_id()
    user_data[uid]["order_id"] = order_id
    
    order_data = {
        "order_id": order_id,
        "user_id": uid,
        "model": user_data[uid].get("model"),
        "selected_services": user_data[uid].get("selected_services"),
        "location": user_data[uid].get("location"),
        "part_name": user_data[uid]["part_name"],
        "part_price": user_data[uid]["part_price"],
        "part_type": user_data[uid]["part_type"],
        "total_price": get_total(user_data[uid]),
        "status": OrderStatus.CREATED.value
    }
    await save_order(order_data)
    
    if user_data[uid]["location"] == "pskov":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🕐 Сегодня 18:00", callback_data="time_today")],
            [InlineKeyboardButton(text="🕐 Завтра 10:00", callback_data="time_tomorrow")]
        ])
        await callback.message.edit_text(f"📦 Заказ #{order_id}\nВыберите время:", reply_markup=kb)
        await state.set_state(RepairStates.waiting_time)
    else:
        await callback.message.edit_text(f"📦 Заказ #{order_id}\nОтправьте фото чека после отправки.")
        await state.set_state(RepairStates.waiting_payment)

@dp.callback_query(F.data.startswith("time_"))
async def choose_time(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    user_data[uid]["time"] = "Сегодня 18:00" if "today" in callback.data else "Завтра 10:00"
    await show_payment(callback.message, uid, state)

async def show_payment(message, uid, state):
    total = get_total(user_data[uid])
    order_id = user_data[uid]["order_id"]
    text = f"""💳 **ЗАКАЗ #{order_id}**

Сумма: **{total}₽**

{PAYMENT_BANK}: {PAYMENT_PHONE}

После оплаты отправьте скриншот."""
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Я оплатил", callback_data="pay_done")]])
    await message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await state.set_state(RepairStates.waiting_payment)

@dp.callback_query(F.data == "pay_done")
async def pay_done(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📸 Отправьте скриншот оплаты")
    await state.set_state(RepairStates.waiting_payment_screenshot)

@dp.message(RepairStates.waiting_payment_screenshot, F.photo)
async def receive_payment(message: Message, state: FSMContext):
    uid = message.from_user.id
    order_id = user_data[uid]["order_id"]
    
    await update_order_status(order_id, OrderStatus.PAID)
    
    await message.answer(f"✅ Оплата получена! Заказ #{order_id}")
    
    # Уведомление админу
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id,
        caption=f"💰 Платёж по заказу {order_id}\nСумма: {get_total(user_data[uid])}₽")

@dp.message(F.text.startswith("/status_"))
async def change_status(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, order_id, status = message.text.split("_", 2)
        new_status = OrderStatus(status)
        await update_order_status(order_id, new_status)
        
        order = await get_order(order_id)
        if order:
            await bot.send_message(order["user_id"], 
                f"📢 Статус заказа #{order_id} изменён: {STATUS_RU[new_status]}")
        await message.answer("✅ Статус обновлён")
    except:
        await message.answer("Ошибка формата")

@dp.message(F.text.startswith("/track_"))
async def track(message: Message):
    order_id = message.text.split("_")[1]
    order = await get_order(order_id)
    if not order:
        await message.answer("Заказ не найден")
        return
    status = OrderStatus(order["status"])
    await message.answer(f"""📦 #{order_id}
📱 {order["model"]}
💰 {order["total_price"]}₽
Статус: {STATUS_RU.get(status, status)}""")

async def main():
    await init_db()
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())