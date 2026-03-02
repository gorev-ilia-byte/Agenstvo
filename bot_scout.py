import asyncio
import datetime
import random
import os
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import FSInputFile
from playwright.async_api import async_playwright

# --- КОНФИГУРАЦИЯ ---
TOKEN = '8500298428:AAFMzTQs-msfODa26CrpztetZEgV1YMIeGA'
GROUP_ID = -1002972518784
DB_FILE = 'seen_ads.txt'
LINKS_FILE = 'links.txt'
USER_DATA_DIR = 'user_data'
SCREENSHOT_PATH = 'params_shot.png'

bot = Bot(token=TOKEN)
dp = Dispatcher()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def load_seen():
    if not os.path.exists(DB_FILE): return set()
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())


def save_ad(address):
    with open(DB_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{address}\n")


def clean_text(text):
    if not text: return "—"
    return text.replace('\xa0', ' ').strip()


# --- ТОЛЬКО СКРИНШОТ ХАРАКТЕРИСТИК ---
async def take_params_screenshot(url):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Делаю скриншот характеристик...")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            slow_mo=200,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=45000)
            await asyncio.sleep(3)

            # Ищем блок параметров по маркеру Авито
            params_block = await page.query_selector('[data-marker="item-view/item-params"]')
            if params_block:
                await params_block.scroll_into_view_if_needed()
                await asyncio.sleep(1)
                await params_block.screenshot(path=SCREENSHOT_PATH)
                print("✅ Скриншот сделан")
            else:
                # Если блок не найден, скриним верхнюю часть (обычно там основные данные)
                await page.screenshot(path=SCREENSHOT_PATH, clip={"x": 0, "y": 400, "width": 800, "height": 800})
                print("⚠️ Блок не найден, заскринил область")

            await context.close()
            return True
        except Exception as e:
            print(f"Ошибка при скриншоте: {e}")
            await context.close()
            return False


# --- ОБРАБОТЧИК REPLY-КОМАНДЫ "ЗАЯВКА" ---
@dp.message(F.reply_to_message)
async def handle_reply(message: types.Message):
    if message.text.lower().strip() in ["заявка", "+"]:
        reply = message.reply_to_message
        url = None

        # Ищем ссылку в реплае
        if reply.entities:
            for entity in reply.entities:
                if entity.type == "text_link":
                    url = entity.url
                    break
        if not url:
            links = re.findall(r'(https?://\S+)', reply.text or "")
            if links: url = links[0].strip().replace(')', '')

        if not url:
            await message.answer("❌ Ссылка не найдена.")
            return

        status = await message.answer("⌛ Делаю скриншот характеристик...")

        # Делаем скрин
        success = await take_params_screenshot(url)

        # Вытягиваем цену и адрес из текста старого сообщения
        price = "—"
        addr = "—"
        for line in (reply.text or "").split('\n'):
            if "Цена:" in line: price = line.replace("💰 Цена:", "").strip()
            if "Адрес:" in line: addr = line.replace("📍 Адрес:", "").strip()

        # Чистая анкета
        template = (
            f"📝 **ЗАЯВКА:**\n\n"
            f"📍 Адрес: {addr}\n"
            f"💰 Цена: **{price}**\n\n"
            f"🔗 [Открыть на Avito]({url})"
        )

        await status.delete()

        if success and os.path.exists(SCREENSHOT_PATH):
            photo = FSInputFile(SCREENSHOT_PATH)
            await message.answer_photo(photo, caption=template, parse_mode="Markdown")
            os.remove(SCREENSHOT_PATH)
        else:
            await message.answer(f"⚠️ Не удалось сделать скрин, отправляю текстом:\n\n{template}",
                                 parse_mode="Markdown")


# --- МОНИТОРИНГ (БЕЗ ИЗМЕНЕНИЙ) ---
async def parse_avito_list(url):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Проверка списка...")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            slow_mo=300,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5)
            await page.mouse.wheel(0, 1000)
            await asyncio.sleep(2)

            items = await page.query_selector_all('[data-marker="item"], article')
            ads = []
            for item in items:
                addr_el = await item.query_selector('[data-marker="item-address"]')
                link_el = await item.query_selector('a[data-marker="item-title"], a[itemprop="url"]')
                price_el = await item.query_selector('[data-marker="item-price"]')
                if addr_el and link_el:
                    addr = clean_text(await addr_el.inner_text())
                    price = clean_text(await price_el.inner_text()) if price_el else "—"
                    link = "https://www.avito.ru" + await link_el.get_attribute('href')
                    ads.append({'address': addr, 'price': price, 'link': link})
            await context.close()
            return ads
        except Exception as e:
            print(f"Ошибка парсера: {e}")
            await context.close()
            return []


async def monitor_loop():
    print("=== МОНИТОРИНГ ЗАПУЩЕН ===")
    while True:
        if not os.path.exists(LINKS_FILE):
            await asyncio.sleep(30)
            continue
        with open(LINKS_FILE, 'r', encoding='utf-8') as f:
            links = [line.strip() for line in f if line.strip()]

        for url in links:
            seen = load_seen()
            ads = await parse_avito_list(url)
            new_count = 0
            for ad in ads:
                if ad['address'] not in seen:
                    msg = (f"🏠 **НОВЫЙ ОБЪЕКТ!**\n\n"
                           f"📍 Адрес: {ad['address']}\n"
                           f"💰 Цена: {ad['price']}\n"
                           f"🔗 [Открыть на Avito]({ad['link']})")
                    try:
                        await bot.send_message(GROUP_ID, msg, parse_mode="Markdown")
                        save_ad(ad['address'])
                        seen.add(ad['address'])
                        new_count += 1
                        await asyncio.sleep(2)
                    except:
                        pass
            print(f"Найдено новых: {new_count}")
            await asyncio.sleep(random.randint(40, 70))
        await asyncio.sleep(300)


async def main():
    asyncio.create_task(monitor_loop())
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())