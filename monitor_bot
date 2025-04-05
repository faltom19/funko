import requests
from bs4 import BeautifulSoup
import re
import json
import time
from datetime import datetime, timedelta
import logging

# === CONFIG ===
SEARCH_URL = "https://www.amazon.it/s?k=funko+pop"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
}
STORAGE_FILE = "funko_seen.json"
BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"  # Token di @FunkoItaly_bot
BOT_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
CHAT_ID = "@FunkoItaly_bot"  # Invia direttamente al bot
MIN_DISCOUNT = 15

logging.basicConfig(level=logging.INFO)


# === Gestione file JSON ===
def load_seen_links():
    try:
        with open(STORAGE_FILE, "r") as f:
            data = json.load(f)
        # Pulisce i prodotti più vecchi di 72h
        now = time.time()
        data = {k: v for k, v in data.items() if now - v < 72 * 3600}
    except Exception:
        data = {}
    return data


def save_seen_links(data):
    with open(STORAGE_FILE, "w") as f:
        json.dump(data, f)


# === Parsing pagina Amazon ===
def get_discounted_products():
    response = requests.get(SEARCH_URL, headers=HEADERS)
    soup = BeautifulSoup(response.content, "html.parser")
    products = []

    for div in soup.select("div.s-result-item[data-asin]"):
        asin = div["data-asin"]
        link_tag = div.select_one("h2 a.a-link-normal")
        price_now_tag = div.select_one("span.a-price span.a-offscreen")
        old_price_tag = div.select_one("span.a-price.a-text-price span.a-offscreen")

        if not (link_tag and price_now_tag and old_price_tag):
            continue

        try:
            price_now = float(
                re.sub(r"[^\d,]", "", price_now_tag.text).replace(",", ".")
            )
            price_old = float(
                re.sub(r"[^\d,]", "", old_price_tag.text).replace(",", ".")
            )
            discount = round(100 - (price_now / price_old * 100))
        except:
            continue

        if discount >= MIN_DISCOUNT:
            product_url = "https://www.amazon.it" + link_tag["href"].split("?")[0]
            products.append((asin, product_url, discount))

    return products


# === Invio al Bot ===
def send_to_bot(link):
    try:
        payload = {"chat_id": CHAT_ID, "text": link}
        response = requests.post(BOT_URL, data=payload)
        if response.status_code == 200:
            logging.info(f"Inviato: {link}")
            return True
        else:
            logging.error(f"Errore invio: {response.text}")
    except Exception as e:
        logging.error(f"Errore invio a bot: {e}")
    return False


# === Ciclo orario ===
def run_monitor():
    while True:
        now = datetime.now()
        if 8 <= now.hour <= 22:
            logging.info(f"Controllo alle {now.strftime('%H:%M')}")

            seen_links = load_seen_links()
            found = get_discounted_products()

            for asin, link, discount in found:
                if asin not in seen_links:
                    if send_to_bot(link):
                        seen_links[asin] = time.time()

            save_seen_links(seen_links)
        else:
            logging.info("Fuori orario, in attesa...")

        time.sleep(3600)  # Aspetta 1 ora


if __name__ == "__main__":
    run_monitor()
