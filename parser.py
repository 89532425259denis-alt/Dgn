import aiohttp
import re
from bs4 import BeautifulSoup
from config import DEEPSEEK_API_KEY

async def parse_ozon(part_name: str, model: str):
    try:
        query = f"{model} {part_name}".replace(" ", "+")
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://www.ozon.ru/search/?text={query}", headers=headers, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    items = soup.find_all("div", class_="tile-hover-target")[:2]
                    result = []
                    for item in items:
                        try:
                            title = item.find("span", class_="tsBody500Medium")
                            price = item.find("span", class_="tsHeadline500Medium")
                            if price:
                                p = int(re.sub(r"\D", "", price.text))
                                result.append({
                                    "title": title.text if title else part_name,
                                    "price": p,
                                    "url": "https://ozon.ru"
                                })
                        except:
                            continue
                    if result:
                        return result
    except:
        pass
    
    # Fallback
    return [
        {"title": f"Аналог {part_name} для {model}", "price": 850, "url": "https://ozon.ru"},
        {"title": f"Качественный аналог", "price": 1050, "url": "https://ozon.ru"}
    ]

async def parse_original(part_name: str, model: str):
    return [
        {"title": f"Оригинал {part_name}", "price": 2400, "url": "https://dns-shop.ru"},
        {"title": f"Оригинальная запчасть", "price": 2800, "url": "https://citilink.ru"}
    ]

async def detect_phone(photo_ids):
    # Здесь можно подключить DeepSeek Vision, пока заглушка
    return {
        "brand": "Apple",
        "model": "iPhone 13",
        "year": 2021,
        "confidence": 88,
        "is_original": True,
        "is_chinese_copy": False
    }