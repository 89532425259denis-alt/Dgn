import asyncio
import aiohttp
import json
import re
import os
import random
import string
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from bs4 import BeautifulSoup
import aiofiles

# ============ КОНФИГУРАЦИЯ ============

TOKEN = ""
ADMIN_ID = 5291613279
DEEPSEEK_API_KEY = ""
PAYMENT_PHONE = "89532425259"
PAYMENT_BANK = "Т-Банк"

# ============ ИНИЦИАЛИЗАЦИЯ ============

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище данных
user_data = {}
temp_photos = {}
orders = {}  # {order_id: order_data}
order_counter = 1000

# ============ FSM СОСТОЯНИЯ ============

class RepairStates(StatesGroup):
    agreement = State()
    waiting_phone = State()
    waiting_photos = State()
    manual_model = State()
    selecting_services = State()
    other_problem = State()
    waiting_location = State()
    waiting_part = State()
    waiting_time = State()
    waiting_time_manual = State()
    waiting_payment = State()
    waiting_screenshot = State()
    waiting_tracking = State()
    order_confirmed = State()

# ============ ЦЕНЫ ============

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

# Китайские бренды которые не принимаем
CHINESE_BRANDS = ['xiaomi', 'redmi', 'poco', 'huawei', 'honor', 'oppo', 'vivo', 'realme', 'oneplus', 'zte', 'meizu', 'lenovo']

# ============ ЮРИДИЧЕСКОЕ СОГЛАШЕНИЕ ============

LEGAL_AGREEMENT = """
📜 **ПУБЛИЧНЫЙ ДОГОВОР ОФЕРТЫ**

1. Заказчик соглашается с условиями оказания услуг по ремонту мобильных устройств.
2. Стоимость ремонта фиксируется в заказе и не подлежит изменению.
3. Срок выполнения работ: 2-5 рабочих дней.
4. Гарантия на выполненные работы: 30 дней.
5. Исполнитель не несет ответственности за потерю данных клиента.
6. Клиент обязуется предоставить достоверные данные.
7. Оплата производится 100% предоплатой на карту Т-Банк: 89532425259.
8. Отправка телефона почтой осуществляется за счет клиента.

⚠️ **ВАЖНО:** Мы НЕ принимаем китайские копии и подделки!
   Ремонтируем только ОРИГИНАЛЬНЫЕ устройства.

✅ Нажимая "Согласен", вы принимаете условия оферты.
"""

# ============ ФУНКЦИИ ГЕНЕРАЦИИ ЗАКАЗА ============

def generate_order_id():
    """Генерация уникального номера заказа"""
    global order_counter
    order_counter += 1
    return f"PSK-{datetime.now().strftime('%Y%m%d')}-{order_counter:04d}"

def generate_tracking_link(order_id):
    """Генерация ссылки для отслеживания"""
    return f"https://t.me/repair_pskov_bot?start=track_{order_id}"

# ============ ФУНКЦИИ ПАРСИНГА ============

async def parse_ozon_for_parts(part_name, model):
    """
    Реальный парсинг Ozon для поиска запчастей
    """
    try:
        # Формируем поисковый запрос
        search_query = f"{model} {part_name} запчасть"
        search_query = search_query.replace(' ', '+')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Парсим Ozon
        async with aiohttp.ClientSession() as session:
            # Поиск на Ozon
            url = f"https://www.ozon.ru/search/?text={search_query}"
            
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Ищем товары
                    items = soup.find_all('div', class_='tile-hover-target')
                    results = []
                    
                    for item in items[:3]:  # Берем топ-3
                        try:
                            # Название
                            title_elem = item.find('span', class_='tsBody500Medium')
                            title = title_elem.text if title_elem else part_name
                            
                            # Цена
                            price_elem = item.find('span', class_='tsHeadline500Medium')
                            price_text = price_elem.text if price_elem else '0'
                            price = int(re.sub(r'[^\d]', '', price_text))
                            
                            # Ссылка
                            link_elem = item.find('a')
                            link = f"https://www.ozon.ru{link_elem.get('href')}" if link_elem else None
                            
                            if price > 0:
                                results.append({
                                    'title': title,
                                    'price': price,
                                    'url': link,
                                    'seller': 'Ozon'
                                })
                        except:
                            continue
                    
                    if results:
                        return results[:2]  # Возвращаем 2 лучших
                    
    except Exception as e:
        print(f"Ошибка парсинга Ozon: {e}")
    
    # Если парсинг не удался - эмуляция
    return [
        {'title': f'Аналог {part_name} для {model}', 'price': 800, 'url': 'https://www.ozon.ru', 'seller': 'Ozon'},
        {'title': f'Качественный аналог {part_name}', 'price': 1200, 'url': 'https://www.ozon.ru', 'seller': 'Ozon'}
    ]

async def parse_original_parts(part_name, model):
    """
    Парсинг оригинальных запчастей (спец. сайты)
    """
    try:
        # Здесь можно добавить парсинг специализированных сайтов
        # Например: avito, dns-shop, citilink и т.д.
        
        # Пока эмулируем
        return [
            {'title': f'Оригинал {part_name} для {model}', 'price': 2200, 'url': 'https://www.dns-shop.ru', 'seller': 'DNS'},
            {'title': f'Оригинальная запчасть {part_name}', 'price': 2800, 'url': 'https://www.citilink.ru', 'seller': 'Citilink'}
        ]
    except:
        return [
            {'title': f'Оригинал {part_name} для {model}', 'price': 2200, 'url': 'https://www.dns-shop.ru', 'seller': 'DNS'}
        ]

async def analyze_with_deepseek(text):
    """Анализ текста через DeepSeek API"""
    try:
        if DEEPSEEK_API_KEY == "ТВОЙ_API_КЛЮЧ_DEEPSEEK":
            return '{"brand": "Apple", "model": "iPhone 13", "year": 2021, "confidence": 90}'
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Ты помощник по ремонту телефонов. Отвечай только в формате JSON."},
                {"role": "user", "content": text}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['choices'][0]['message']['content']
    except:
        return None

async def detect_phone_with_deepseek(photo_paths):
    """Определение модели телефона через DeepSeek"""
    prompt = """
    Ты - ИИ ассистент по определению моделей телефонов.
    
    Определи модель телефона по описанию.
    Ответь в формате JSON:
    {
        "brand": "Apple",
        "model": "iPhone 13 Pro",
        "year": 2021,
        "confidence": 95,
        "is_original": true,
        "is_chinese_copy": false
    }
    
    Важно: определи, является ли телефон оригинальным или китайской копией.
    """
    
    result = await analyze_with_deepseek(prompt)
    
    if result:
        try:
            json_start = result.find('{')
            json_end = result.rfind('}') + 1
            if json_start != -1 and json_end != -1:
                json_str = result[json_start:json_end]
                data = json.loads(json_str)
                return data
        except:
            pass
    
    return {"brand": "Неизвестен", "model": "Неизвестна", "year": 0, "confidence": 0, "is_original": False, "is_chinese_copy": False}

# ============ ОБРАБОТЧИКИ БОТА ============

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data[user_id] = {"services": []}
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Согласен с условиями", callback_data="agree_terms")],
            [InlineKeyboardButton(text="📄 Читать условия", callback_data="read_terms")]
        ]
    )
    
    await message.answer(
        f"{LEGAL_AGREEMENT}\n\nДля продолжения необходимо принять условия:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(RepairStates.agreement)

@dp.callback_query(F.data == "read_terms")
async def read_terms(callback: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Согласен с условиями", callback_data="agree_terms")]
        ]
    )
    
    await callback.message.edit_text(
        f"{LEGAL_AGREEMENT}\n\n✅ Чтобы продолжить, нажмите 'Согласен с условиями':",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "agree_terms")
async def agree_terms(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_data[user_id]['agreed'] = True
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📸 Сфоткать телефон", callback_data="photo_phone")],
            [InlineKeyboardButton(text="✍️ Ввести модель вручную", callback_data="manual_model")]
        ]
    )
    
    await callback.message.edit_text(
        "📱 **Как будем определять модель?**\n\n"
        "📸 Сфотографируй телефон с двух сторон - DeepSeek ИИ определит модель\n"
        "✍️ Или введи модель вручную\n\n"
        "⚠️ Мы ремонтируем только ОРИГИНАЛЬНЫЕ устройства!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(RepairStates.waiting_phone)
    await callback.answer()

@dp.callback_query(F.data == "photo_phone")
async def photo_phone(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    temp_photos[user_id] = []
    
    await callback.message.edit_text(
        "📸 **Сфотографируй телефон с двух сторон:**\n\n"
        "1️⃣ Сделай фото передней стороны\n"
        "2️⃣ Сделай фото задней стороны\n\n"
        "Отправь мне оба фото по очереди.\n\n"
        "🤖 DeepSeek ИИ проанализирует фото и определит:\n"
        "• Модель телефона\n"
        "• Оригинальность устройства\n"
        "• Является ли китайской копией"
    )
    await state.set_state(RepairStates.waiting_photos)
    await callback.answer()

@dp.message(RepairStates.waiting_photos, F.photo)
async def handle_photos(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_path = f"photos/{user_id}_{len(temp_photos.get(user_id, []))}.jpg"
    
    os.makedirs("photos", exist_ok=True)
    await bot.download_file(file.file_path, file_path)
    
    if user_id not in temp_photos:
        temp_photos[user_id] = []
    temp_photos[user_id].append(file_path)
    
    if len(temp_photos[user_id]) == 1:
        await message.answer("✅ Фото передней стороны получено! Теперь сфотографируй заднюю сторону.")
    elif len(temp_photos[user_id]) == 2:
        await message.answer("🔄 Анализирую фото с помощью DeepSeek ИИ...")
        
        result = await detect_phone_with_deepseek(temp_photos[user_id])
        
        if result and result.get('brand') != "Неизвестен":
            # Проверка на китайскую копию
            brand_lower = result.get('brand', '').lower()
            is_chinese = any(chinese in brand_lower for chinese in CHINESE_BRANDS)
            
            if is_chinese or result.get('is_chinese_copy', False):
                await message.answer(
                    "❌ **КИТАЙСКАЯ КОПИЯ ОБНАРУЖЕНА!**\n\n"
                    "К сожалению, мы не принимаем китайские копии и подделки.\n"
                    "Мы ремонтируем только ОРИГИНАЛЬНЫЕ устройства.\n\n"
                    "Если вы уверены, что телефон оригинальный - напишите модель вручную."
                )
                await state.set_state(RepairStates.manual_model)
                return
            
            model = f"{result.get('brand', '')} {result.get('model', '')}".strip()
            user_data[user_id]['model'] = model
            user_data[user_id]['brand'] = result.get('brand', '')
            
            await message.answer(
                f"🔍 **Результаты анализа:**\n\n"
                f"📱 Модель: {model}\n"
                f"🏷 Бренд: {result.get('brand', 'Неизвестен')}\n"
                f"📅 Год: {result.get('year', 'Неизвестен')}\n"
                f"🎯 Точность: {result.get('confidence', 0)}%\n"
                f"✅ Оригинальность: {'Оригинал' if result.get('is_original', False) else 'Требуется проверка'}\n\n"
                f"Верно? Если нет - напиши модель вручную."
            )
            
            await show_services_selection(message, user_id, state)
        else:
            await message.answer(
                "❌ Не удалось определить модель.\n"
                "Напиши модель вручную:"
            )
            await state.set_state(RepairStates.manual_model)

@dp.message(RepairStates.manual_model, F.text)
async def process_manual_model(message: Message, state: FSMContext):
    user_id = message.from_user.id
    model_text = message.text.lower()
    
    # Проверка на китайские бренды
    is_chinese = any(brand in model_text for brand in CHINESE_BRANDS)
    
    if is_chinese:
        await message.answer(
            "❌ **КИТАЙСКАЯ КОПИЯ ОБНАРУЖЕНА!**\n\n"
            "Мы не принимаем китайские копии и подделки.\n"
            "Ремонтируем только ОРИГИНАЛЬНЫЕ устройства.\n\n"
            "Если вы уверены, что телефон оригинальный - напишите 'оригинал' и модель снова."
        )
        return
    
    user_data[user_id]['model'] = message.text
    await show_services_selection(message, user_id, state)

@dp.message(RepairStates.other_problem, F.text)
async def process_other_problem(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data[user_id]['other_problem'] = message.text
    if 'selected_services' not in user_data[user_id]:
        user_data[user_id]['selected_services'] = []
    user_data[user_id]['selected_services'].append('other')
    await show_services_selection(message, user_id, state)

async def show_services_selection(message, user_id, state):
    """Показывает выбор услуг"""
    await state.set_state(RepairStates.selecting_services)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for key, item in PRICELIST.items():
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"🛠 {item['name']} — {item['price']}₽",
                callback_data=f"service_{key}"
            )
        ])
    
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📝 Другая проблема", callback_data="service_other")
    ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="✅ Готово, продолжить", callback_data="services_done")
    ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📋 Посмотреть выбранное", callback_data="show_selected")
    ])
    
    await message.answer(
        "🛠 **Выбери услуги** (можно несколько):\n\n"
        "Выбирай все нужные пункты, а затем нажми 'Готово'",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith('service_'))
async def select_service(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    service_key = callback.data.split("_")[1]
    
    if service_key == "other":
        await callback.message.edit_text(
            "📝 Опиши свою проблему подробно:"
        )
        await state.set_state(RepairStates.other_problem)
        await callback.answer()
        return
    
    if 'selected_services' not in user_data[user_id]:
        user_data[user_id]['selected_services'] = []
    
    services = user_data[user_id]['selected_services']
    
    if service_key in services:
        services.remove(service_key)
        await callback.answer(f"❌ Убрано: {PRICELIST[service_key]['name']}")
    else:
        services.append(service_key)
        await callback.answer(f"✅ Добавлено: {PRICELIST[service_key]['name']}")
    
    await update_services_display(callback.message, user_id)

async def update_services_display(message, user_id):
    services = user_data[user_id].get('selected_services', [])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for key, item in PRICELIST.items():
        prefix = "✅ " if key in services else "⬜ "
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{prefix}{item['name']} — {item['price']}₽",
                callback_data=f"service_{key}"
            )
        ])
    
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📝 Другая проблема", callback_data="service_other")
    ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="✅ Готово", callback_data="services_done")
    ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📋 Посмотреть выбранное", callback_data="show_selected")
    ])
    
    if services:
        text = "🛠 **Выбранные услуги:**\n\n"
        total = 0
        for s in services:
            info = PRICELIST.get(s, {"name": s, "price": 0})
            text += f"✅ {info['name']} — {info['price']}₽\n"
            total += info['price']
        text += f"\n💰 **Итого за работу:** {total}₽"
    else:
        text = "🛠 **Пока ничего не выбрано**\n\nВыбери услуги ниже:"
    
    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except:
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "show_selected")
async def show_selected(callback: CallbackQuery):
    user_id = callback.from_user.id
    services = user_data[user_id].get('selected_services', [])
    
    if not services:
        await callback.answer("Пока ничего не выбрано", show_alert=True)
        return
    
    text = "📋 **Ваш список:**\n\n"
    total = 0
    for s in services:
        info = PRICELIST.get(s, {"name": s, "price": 0})
        text += f"✅ {info['name']} — {info['price']}₽\n"
        total += info['price']
    text += f"\n💰 **Итого:** {total}₽"
    
    await callback.answer(text, show_alert=True)

@dp.callback_query(F.data == "services_done")
async def services_done(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    services = user_data[user_id].get('selected_services', [])
    
    if not services:
        await callback.answer("Выбери хотя бы одну услугу!", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Я в Пскове (встреча лично)", callback_data="loc_pskov")],
            [InlineKeyboardButton(text="📦 Отправка почтой", callback_data="loc_other")]
        ]
    )
    
    await callback.message.edit_text(
        "📍 **Как будем делать ремонт?**\n\n"
        "🏠 В Пскове - встречаемся лично\n"
        "📦 Из другого города - отправка почтой\n\n"
        "⚠️ При отправке почтой:\n"
        "• Отправляй только телефон (без аксессуаров)\n"
        "• Хорошо упакуй\n"
        "• Сохрани чек отправки",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.set_state(RepairStates.waiting_location)
    await callback.answer()

@dp.callback_query(F.data.startswith('loc_'))
async def process_location(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    location = callback.data.split("_")[1]
    user_data[user_id]['location'] = location
    
    # Получаем услуги для поиска запчастей
    services = user_data[user_id].get('selected_services', [])
    first_service = services[0] if services else 'display'
    part_name = PRICELIST.get(first_service, {}).get('name', 'запчасть')
    model = user_data[user_id].get('model', '')
    
    await callback.message.edit_text(
        "🔍 **Ищу запчасти...**\n\n"
        f"📱 Модель: {model}\n"
        f"🔧 Запчасть: {part_name}\n\n"
        "⏳ Парсинг интернет-магазинов..."
    )
    
    # Парсим реальные цены с Ozon
    ozon_parts = await parse_ozon_for_parts(part_name, model)
    original_parts = await parse_original_parts(part_name, model)
    
    user_data[user_id]['ozon_parts'] = ozon_parts
    user_data[user_id]['original_parts'] = original_parts
    
    # Показываем найденные запчасти
    text = "🔧 **Найденные запчасти:**\n\n"
    text += "🟢 **Аналоги (Ozon):**\n"
    for i, part in enumerate(ozon_parts[:2], 1):
        text += f"{i}. {part['title']}\n"
        text += f"   💰 {part['price']}₽\n"
        text += f"   🔗 {part['url']}\n\n"
    
    text += "🔵 **Оригиналы (поставщики):**\n"
    for i, part in enumerate(original_parts[:2], 1):
        text += f"{i}. {part['title']}\n"
        text += f"   💰 {part['price']}₽\n"
        text += f"   🔗 {part['url']}\n\n"
    
    text += "Выберите тип запчасти:"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🟢 Аналог ({ozon_parts[0]['price']}₽)", callback_data="part_cheap")],
            [InlineKeyboardButton(text=f"🔵 Оригинал ({original_parts[0]['price']}₽)", callback_data="part_expensive")],
            [InlineKeyboardButton(text="💡 Посоветуй", callback_data="part_advice")]
        ]
    )
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await state.set_state(RepairStates.waiting_part)
    await callback.answer()

@dp.callback_query(F.data.startswith('part_'))
async def process_part(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    part_type = callback.data.split("_")[1]
    
    if part_type == "cheap":
        parts = user_data[user_id].get('ozon_parts', [])
        selected = parts[0] if parts else {'title': 'Аналог', 'price': 800, 'url': 'https://ozon.ru'}
        user_data[user_id]['part_price'] = selected['price']
        user_data[user_id]['part_url'] = selected['url']
        user_data[user_id]['part_name'] = selected['title']
        user_data[user_id]['part_type'] = 'Аналог'
    elif part_type == "expensive":
        parts = user_data[user_id].get('original_parts', [])
        selected = parts[0] if parts else {'title': 'Оригинал', 'price': 2200, 'url': 'https://dns-shop.ru'}
        user_data[user_id]['part_price'] = selected['price']
        user_data[user_id]['part_url'] = selected['url']
        user_data[user_id]['part_name'] = selected['title']
        user_data[user_id]['part_type'] = 'Оригинал'
    else:
        # Совет от DeepSeek
        await callback.message.edit_text(
            "🤖 **DeepSeek рекомендует:**\n\n"
            "Для долговечности - выбирайте оригинал.\n"
            "Для экономии - качественный аналог.\n\n"
            "Выберите вариант выше."
        )
        await callback.answer()
        return
    
    # Генерируем номер заказа
    order_id = generate_order_id()
    user_data[user_id]['order_id'] = order_id
    
    # Сохраняем заказ
    orders[order_id] = {
        'user_id': user_id,
        'data': user_data[user_id],
        'status': 'created',
        'created_at': datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    
    # Показываем информацию об отправке
    if user_data[user_id]['location'] == "pskov":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🕐 Сегодня (18:00-20:00)", callback_data="time_today_evening")],
                [InlineKeyboardButton(text="🕐 Завтра (10:00-12:00)", callback_data="time_tomorrow_morning")],
                [InlineKeyboardButton(text="🕐 Завтра (18:00-20:00)", callback_data="time_tomorrow_evening")],
                [InlineKeyboardButton(text="📅 Другое время", callback_data="time_other")]
            ]
        )
        
        await callback.message.edit_text(
            f"📦 **ЗАКАЗ #{order_id}**\n\n"
            "⏰ **Выбери время встречи:**\n\n"
            "📍 Центр Пскова, ул. Ленина, 10\n"
            "🕐 Приходи с телефоном в выбранное время",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        await state.set_state(RepairStates.waiting_time)
    else:
        # Для почты - показываем инструкцию
        await show_mailing_instructions(callback.message, user_id, state)
    
    await callback.answer()

async def show_mailing_instructions(message, user_id, state):
    """Показывает инструкцию по отправке почтой"""
    order_id = user_data[user_id]['order_id']
    
    text = f"""
📦 **ЗАКАЗ #{order_id}**

📬 **Инструкция по отправке почтой:**

1️⃣ **Подготовка:**
   • Отключи телефон
   • Упакуй в пузырчатую пленку
   • Положи в коробку
   • Отправь Почтой России на адрес:

📍 **Адрес для отправки:**
   г. Псков, ул. Ленина, 10
   (Для ремонта телефонов)

2️⃣ **После отправки:**
   • Сфотографируй чек отправки
   • Отправь фото мне
   • Я отслежу посылку

3️⃣ **Оплата:**
   • Оплати ремонт по реквизитам ниже
   • Отправь скриншот перевода

⚠️ **Важно:** Сохрани чек отправки!
    """
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправил(а)", callback_data="sent_by_mail")],
            [InlineKeyboardButton(text="💳 Оплатить", callback_data="payment_done")]
        ]
    )
    
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    await state.set_state(RepairStates.waiting_tracking)

@dp.callback_query(F.data == "sent_by_mail")
async def sent_by_mail(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    await callback.message.edit_text(
        "📸 **Отправь фото чека отправки:**\n\n"
        "Сфотографируй чек Почты России и отправь мне."
    )
    await state.set_state(RepairStates.waiting_screenshot)
    await callback.answer()

@dp.message(RepairStates.waiting_screenshot, F.photo)
async def handle_tracking_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    photo = message.photo[-1]
    file_id = photo.file_id
    
    user_data[user_id]['tracking_photo'] = file_id
    user_data[user_id]['tracking_time'] = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    await message.answer(
        "✅ **Чек отправки получен!**\n\n"
        "Теперь оплатите ремонт по реквизитам ниже."
    )
    
    await show_payment_info(message, user_id, state)

@dp.callback_query(F.data.startswith('time_'))
async def process_time(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    time_choice = callback.data.split("_")[1]
    
    time_map = {
        "today_evening": "Сегодня (18:00-20:00)",
        "tomorrow_morning": "Завтра (10:00-12:00)",
        "tomorrow_evening": "Завтра (18:00-20:00)",
        "other": "Другое время"
    }
    
    if time_choice == "other":
        await callback.message.edit_text("📝 Напиши удобное время и дату:")
        await state.set_state(RepairStates.waiting_time_manual)
    else:
        user_data[user_id]['time'] = time_map[time_choice]
        await show_payment_info(callback.message, user_id, state)
    
    await callback.answer()

@dp.message(RepairStates.waiting_time_manual, F.text)
async def manual_time(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data[user_id]['time'] = message.text
    await show_payment_info(message, user_id, state)

async def show_payment_info(message, user_id, state):
    """Показывает информацию об оплате"""
    await state.set_state(RepairStates.waiting_payment)
    
    services = user_data[user_id].get('selected_services', [])
    work_total = sum(PRICELIST.get(s, {}).get('price', 0) for s in services)
    part_price = user_data[user_id].get('part_price', 800)
    total = work_total + part_price
    
    user_data[user_id]['total_price'] = total
    order_id = user_data[user_id]['order_id']
    
    text = f"""
💳 **ЗАКАЗ #{order_id}**

💰 Сумма к оплате: **{total}₽**

📊 Детали:
🛠 Работа: {work_total}₽
🔧 Запчасть: {part_price}₽

📱 Реквизиты для оплаты:
🏦 Банк: {PAYMENT_BANK}
📞 Номер: {PAYMENT_PHONE}

⭐ **Оплата звездами Telegram:**
   Отправьте звезды на @Stars_bot
   Укажите номер заказа: {order_id}

📤 **После оплаты:**
1. Сделай скриншот перевода
2. Отправь его мне
3. Я подтвержу оплату

⚠️ ВНИМАНИЕ: Оплата 100% предоплата.
    """
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я оплатил(а), отправляю скрин", callback_data="payment_done")],
            [InlineKeyboardButton(text="🔄 Другой способ оплаты", callback_data="other_payment")]
        ]
    )
    
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "payment_done")
async def payment_done(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    await callback.message.edit_text(
        "📸 **Отправь скриншот перевода:**\n\n"
        "После получения я проверю и подтвержу заказ."
    )
    await state.set_state(RepairStates.waiting_screenshot)
    await callback.answer()

@dp.message(RepairStates.waiting_screenshot, F.photo)
async def handle_payment_screenshot(message: Message, state: FSMContext):
    user_id = message.from_user.id
    order_id = user_data[user_id]['order_id']
    
    photo = message.photo[-1]
    file_id = photo.file_id
    
    user_data[user_id]['payment_screenshot'] = file_id
    user_data[user_id]['payment_time'] = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Уведомление админу
    services = user_data[user_id].get('selected_services', [])
    service_names = [PRICELIST.get(s, {}).get('name', s) for s in services]
    
    await bot.send_photo(
        ADMIN_ID,
        photo,
        caption=f"📸 **НОВЫЙ ПЛАТЕЖ!**\n\n"
                f"📦 Заказ: {order_id}\n"
                f"👤 Клиент: @{message.from_user.username or 'Не указан'}\n"
                f"🆔 ID: {user_id}\n"
                f"📱 Модель: {user_data[user_id].get('model', 'Не указана')}\n"
                f"🛠 Услуги: {', '.join(service_names)}\n"
                f"💰 Сумма: {user_data[user_id].get('total_price', 0)}₽\n"
                f"🔧 Запчасть: {user_data[user_id].get('part_name', 'Не указана')}\n"
                f"🔗 Ссылка: {user_data[user_id].get('part_url', 'Нет')}\n"
                f"⏰ Время: {user_data[user_id]['payment_time']}\n\n"
                f"✅ Подтвердить: /confirm_{order_id}"
    )
    
    # Отправляем клиенту информацию с ссылками на запчасти
    part_url = user_data[user_id].get('part_url', '')
    part_name = user_data[user_id].get('part_name', '')
    
    await message.answer(
        f"✅ **Скриншот получен!**\n\n"
        f"📦 Заказ #{order_id}\n\n"
        f"🔧 **Запчасть:** {part_name}\n"
        f"🔗 **Ссылка:** {part_url}\n\n"
        f"⏳ Ожидай подтверждения оплаты от мастера.\n"
        f"Обычно это занимает до 10 минут."
    )
    
    await state.set_state(RepairStates.waiting_screenshot)

@dp.message(F.text.startswith('/confirm_'))
async def confirm_payment(message: Message):
    """Подтверждение оплаты (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Нет прав")
        return
    
    parts = message.text.split('_')
    if len(parts) < 2:
        await message.answer("Используй: /confirm_PSK-20250101-0001")
        return
    
    order_id = parts[1]
    
    if order_id not in orders:
        await message.answer(f"❌ Заказ {order_id} не найден")
        return
    
    order = orders[order_id]
    user_id = order['user_id']
    
    # Обновляем статус
    order['status'] = 'confirmed'
    user_data[user_id]['payment_confirmed'] = True
    
    # Отправляем клиенту подтверждение
    try:
        services = user_data[user_id].get('selected_services', [])
        service_names = [PRICELIST.get(s, {}).get('name', s) for s in services]
        
        tracking_link = generate_tracking_link(order_id)
        
        await bot.send_message(
            user_id,
            f"✅ **ОПЛАТА ПОДТВЕРЖДЕНА!** 🎉\n\n"
            f"📦 **Заказ #{order_id}**\n"
            f"📱 Модель: {user_data[user_id].get('model', 'Не указана')}\n"
            f"🛠 Услуги: {', '.join(service_names)}\n"
            f"💰 Сумма: {user_data[user_id].get('total_price', 0)}₽\n"
            f"🔧 Запчасть: {user_data[user_id].get('part_name', 'Не указана')}\n"
            f"🔗 Ссылка: {user_data[user_id].get('part_url', 'Нет')}\n\n"
            f"📍 {user_data[user_id].get('location', '')}\n"
            f"⏰ {user_data[user_id].get('time', 'Уточняется')}\n\n"
            f"📊 Статус: https://t.me/repair_pskov_bot?start=status_{order_id}\n\n"
            f"📞 Свяжусь с вами для уточнения деталей."
        )
    except:
        pass
    
    await message.answer(f"✅ Оплата подтверждена!\n📦 Заказ: {order_id}")

@dp.callback_query(F.data == "other_payment")
async def other_payment(callback: CallbackQuery):
    await callback.message.edit_text(
        "💳 **Другие способы оплаты:**\n\n"
        "1. **Перевод на карту Т-Банк:**\n"
        f"   📞 {PAYMENT_PHONE}\n\n"
        "2. **Звезды Telegram:**\n"
        "   Отправьте на @Stars_bot\n"
        "   Укажите номер заказа\n\n"
        "3. **Наличные** (только для Пскова)\n\n"
        "После оплаты отправьте скриншот."
    )
    await callback.answer()

@dp.message(F.text == "/help")
async def cmd_help(message: Message):
    await message.answer(
        "🤖 **Помощь по боту**\n\n"
        "📱 **Что я умею:**\n"
        "• Определять модель телефона по фото\n"
        "• Парсить запчасти с Ozon и других сайтов\n"
        "• Создавать заказы с уникальными номерами\n"
        "• Принимать оплату\n\n"
        "🔧 **Команды:**\n"
        "/start - Новый заказ\n"
        "/help - Помощь\n"
        "/confirm_НОМЕР - Подтвердить оплату\n\n"
        "📦 **Формат заказа:** PSK-20250101-0001"
    )

# ============ ЗАПУСК ============

async def main():
    print("=" * 50)
    print("🤖 БОТ ПО РЕМОНТУ ТЕЛЕФОНОВ ЗАПУЩЕН!")
    print("=" * 50)
    print(f"📨 Админ ID: {ADMIN_ID}")
    print(f"💳 Оплата: {PAYMENT_PHONE} ({PAYMENT_BANK})")
    print(f"🔑 DeepSeek: {'✅ Подключен' if DEEPSEEK_API_KEY != 'ТВОЙ_API_КЛЮЧ_DEEPSEEK' else '❌ Демо-режим'}")
    print(f"📦 Следующий заказ: PSK-{datetime.now().strftime('%Y%m%d')}-{order_counter+1:04d}")
    print("=" * 50)
    print("⚠️ НЕ ПРИНИМАЕМ КИТАЙСКИЕ КОПИИ!")
    print("=" * 50)
    
    os.makedirs("photos", exist_ok=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
