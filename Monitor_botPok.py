import requests
from bs4 import BeautifulSoup
import time
import datetime
import json
import random

# === CONFIG ===
TOKEN = '7909094251:AAFCIgZ6y8ccfxoRtIa-EQav4tF_FxXg5Xg'
CHAT_ID = '125505180'
CHECK_INTERVAL = 30  # secondi
PREZZO_MAX = 60.00

# Lista link da monitorare
LINKS = [
    "https://www.amazon.it/gp/aw/d/B0F1G6H7DR/ref=ox_sc_saved_title_2?smid=A11IL2PNWYJU7H&psc=1"
]

# === LOGICA ===

def get_headers():
    return {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:89.0)",
        ])
    }

def load_notified():
    try:
        with open("notified_log.json", "r") as f:
            return json.load(f)
    except:
        return {}

def save_notified(data):
    with open("notified_log.json", "w") as f:
        json.dump(data, f)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, data=data)

def clean_old_entries(data):
    now = time.time()
    return {k: v for k, v in data.items() if now - v < 86400}

def parse_amazon(url):
    try:
        res = requests.get(url, headers=get_headers(), timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title = soup.find('span', {'id': 'productTitle'})
        title = title.get_text(strip=True) if title else "Prodotto sconosciuto"

        price_el = soup.find('span', {'class': 'a-price-whole'})
        price_frac = soup.find('span', {'class': 'a-price-fraction'})
        if not price_el or not price_frac:
            return None
        
        price = float(price_el.text.replace('.', '').replace(',', '') + '.' + price_frac.text)

        sold_by = soup.find(text="Venduto da Amazon") or soup.find(text="Venduto e spedito da Amazon")

        if sold_by and price <= PREZZO_MAX:
            return {
                "title": title,
                "price": price,
                "url": url
            }

        return None

    except Exception as e:
        print("Errore parsing:", e)
        return None

def monitor():
    notified = load_notified()
    while True:
        now = datetime.datetime.now()
        if 8 <= now.hour < 20:
            print(f"[{now.strftime('%H:%M:%S')}] Controllo prodotti...")
            notified = clean_old_entries(notified)
            for link in LINKS:
                if link in notified:
                    continue
                result = parse_amazon(link)
                if result:
                    message = (
                        f"✅ *Prodotto disponibile!*\n\n"
                        f"📦 {result['title']}\n"
                        f"💰 Prezzo: {result['price']}€\n"
                        f"🔗 {result['url']}"
                    )
                    send_telegram_message(message)
                    notified[link] = time.time()
                    save_notified(notified)
        else:
            print(f"[{now.strftime('%H:%M:%S')}] Fuori orario. In pausa.")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
