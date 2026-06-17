import aiosqlite
import json
from datetime import datetime
from typing import Optional, List, Dict
from config import DB_PATH
from enum import Enum

class OrderStatus(str, Enum):
    CREATED = "created"
    PAID = "paid"
    PARTS_ORDERED = "parts_ordered"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

STATUS_RU = {
    OrderStatus.CREATED: "Создан",
    OrderStatus.PAID: "Оплачен",
    OrderStatus.PARTS_ORDERED: "Запчасти заказаны",
    OrderStatus.IN_PROGRESS: "В работе",
    OrderStatus.READY: "Готов к выдаче",
    OrderStatus.COMPLETED: "Завершён",
    OrderStatus.CANCELLED: "Отменён"
}

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                model TEXT,
                services TEXT,
                location TEXT,
                meeting_time TEXT,
                part_name TEXT,
                part_price INTEGER,
                part_type TEXT,
                total_price INTEGER,
                status TEXT DEFAULT 'created',
                created_at TEXT,
                updated_at TEXT,
                tracking_photo TEXT,
                payment_screenshot TEXT
            )
        """)
        await db.commit()

async def save_order(data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO orders 
            (order_id, user_id, model, services, location, meeting_time, part_name, 
             part_price, part_type, total_price, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('order_id'),
            data.get('user_id'),
            data.get('model'),
            json.dumps(data.get('selected_services', [])),
            data.get('location'),
            data.get('time'),
            data.get('part_name'),
            data.get('part_price'),
            data.get('part_type'),
            data.get('total_price'),
            data.get('status', 'created'),
            data.get('created_at', datetime.now().isoformat()),
            datetime.now().isoformat()
        ))
        await db.commit()

async def update_order_status(order_id: str, status: OrderStatus):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE orders SET status=?, updated_at=? WHERE order_id=?",
            (status.value, datetime.now().isoformat(), order_id)
        )
        await db.commit()

async def get_order(order_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
        row = await cursor.fetchone()
        if row:
            keys = [col[0] for col in cursor.description]
            order = dict(zip(keys, row))
            if order.get('services'):
                order['services'] = json.loads(order['services'])
            return order
    return None

async def get_user_orders(user_id: int) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT order_id, model, status, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(zip([col[0] for col in cursor.description], row)) for row in rows]