import requests
from bs4 import BeautifulSoup
import datetime
import time
import os
import re
from io import BytesIO
from PIL import Image
from urllib.parse import urlparse, parse_qs, unquote
import gc
import logging
import random
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==============================
# CONFIGURAZIONE
# ==============================
FILE_PATH = "products.txt"
DELIMITER = ";"
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
CHANNEL_ID = "@fpitcanale"
REF_TAG = "funkoitalia0c-21"
AMAZON_SEARCH_URL = "https://www.amazon.it/s?k=funko+pop"
TIME_INTERVAL = 3600
TEMPLATE_IMAGE_PATH = "template.png"
RETENTION_SECONDS = 5 * 24 * 3600  # 5 giorni
PROXIES = {}

# ==============================
# LOGGING
# ==============================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("monitor_bot_debug.log"), logging.StreamHandler()]
)

# ==============================
# SESSION CON RETRY
# ==============================
session = requests.Session()
retry_strategy = Retry(
    total=5,
    backoff_factor=2,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# ==============================
# USER-AGENTS & HEADERS
# ==============================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:93.0) Gecko/20100101 Firefox/93.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Mobile/15E148 Safari/604.1",
]

def get_random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.amazon.it/",
        "sec-ch-ua": '"Chromium";v="112", "Google Chrome";v="112", "Not:A-Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }

def random_delay():
    delay = random.uniform(7, 20)
    logging.debug(f"Delay random: {delay:.2f}s")
    time.sleep(delay)

# ==============================
# FUNZIONI PREZZO
# ==============================
def estrai_float(valore: str) -> float:
    try:
        v = valore.replace("€", "").strip().replace(".", "").replace(",", ".")
        return float(v)
    except Exception as e:
        logging.error(f"estrai_float errore su '{valore}': {e}")
        return None


def estrai_prezzo(intero_tag, decimale_tag) -> float:
    intero = intero_tag.get_text().strip().rstrip('.,')
    fraz = decimale_tag.get_text().strip()
    testo = f"{intero},{fraz}"
    return estrai_float(testo)

# ==============================
# GESTIONE FILE
# ==============================
def carica_prodotti_salvati() -> dict:
    now = datetime.datetime.now()
    prodotti = {}
    if os.path.exists(FILE_PATH):
        with open(FILE_PATH) as f:
            lines = f.readlines()
        valid = []
        for line in lines:
            try:
                ts_str, link = line.strip().split(DELIMITER, 1)
                ts = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                if (now - ts).total_seconds() < RETENTION_SECONDS:
                    prodotti[link] = ts
                    valid.append(line)
            except Exception:
                continue
        with open(FILE_PATH, 'w') as f:
            f.writelines(valid)
    return prodotti


def salva_prodotto(link: str, ts: datetime.datetime):
    with open(FILE_PATH, 'a') as f:
        f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')}{DELIMITER}{link}\n")

# ==============================
# PULIZIA URL & ASIN
# ==============================
def clean_amazon_url(url: str) -> str:
    p = urlparse(url)
    if "sspa/click" in p.path:
        qs = parse_qs(p.query)
        if "url" in qs:
            return unquote(qs['url'][0].split('?')[0])
    return url.split('?')[0]


def extract_asin(url: str) -> str:
    m = re.search(r"/dp/(\w{10})", url)
    return m.group(1) if m else None

# ==============================
# SCRAPING PRODOTTO
# ==============================
def parse_amazon_product(url: str) -> dict:
    clean = clean_amazon_url(url)
    headers = get_random_headers()
    random_delay()
    try:
        r = session.get(clean, headers=headers, timeout=10, proxies=PROXIES)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Errore scraping {clean}: {e}")
        return None

    soup = BeautifulSoup(r.text, 'html.parser')
    # Titolo
    title_el = soup.find(id='productTitle')
    title = title_el.get_text(strip=True) if title_el else None
    # Prezzo corrente
    price_el = soup.select_one(
        '#priceblock_ourprice, #priceblock_dealprice, #priceblock_saleprice, span.a-price span.a-offscreen'
    )
    price = price_el.get_text(strip=True) if price_el else None
    # List price
    list_price_el = soup.select_one(
        'span.priceBlockStrikePriceString, span.a-price.a-text-price span.a-offscreen'
    )
    list_price = list_price_el.get_text(strip=True) if list_price_el else None
    # Recensioni
    review_el = soup.select_one('#acrCustomerReviewText')
    reviews = review_el.get_text(strip=True) if review_el else '0 recensioni'
    # Immagine
    meta_img = soup.find('meta', property='og:image')
    if meta_img and meta_img.get('content'):
        image_url = meta_img['content']
    else:
        img_tag = soup.select_one('#imgTagWrapperId img, #landingImage')
        image_url = img_tag['src'] if img_tag and img_tag.get('src') else None

    return {
        'title': title,
        'price': price,
        'list_price': list_price,
        'reviews': reviews,
        'image_url': image_url,
        'ref_link': f"{clean}?tag={REF_TAG}"
    }

# ==============================
# MESSAGGIO & IMMAGINE
# ==============================
def build_telegram_message(d: dict) -> str:
    title = d.get('title', '').replace("Animation: ", "").strip()
    title = re.sub(r"- Figura in Vinile.*", "", title)
    lines = [f"📍 <b>{title}</b>\n"]
    price = d.get('price')
    list_price = d.get('list_price')
    if list_price and price:
        o = estrai_float(list_price)
        c = estrai_float(price)
        if o and c:
            disc = round(100 - (c / o * 100))
            if disc > 0:
                lines.append(f"🔻 Sconto: {disc}%")
                lines.append(f"✂️ <s>{list_price}</s> → <b>{price}</b>\n")
            else:
                lines.append(f"💰 <b>{price}</b>\n")
        else:
            lines.append(f"💰 <b>{price}</b>\n")
    else:
        lines.append(f"💰 <b>{price or 'Prezzo non disponibile'}</b>\n")
    lines.append(f"🔗 <a href=\"{d.get('ref_link')}\">Acquista ora</a>")
    lines.append(f"⭐ {d.get('reviews')}")
    return "\n".join(lines)


def compose_image(d: dict) -> bytes:
    url = d.get('image_url')
    if not url:
        return None
    try:
        tpl = Image.open(TEMPLATE_IMAGE_PATH).convert('RGB')
        r = session.get(url, stream=True, timeout=10, proxies=PROXIES)
        r.raise_for_status()
        pi = Image.open(BytesIO(r.content)).convert('RGB')
        w, h = pi.size
        sf = 2
        nw = min(tpl.width, w * sf)
        nh = min(tpl.height, h * sf)
        pi = pi.resize((nw, nh), Image.LANCZOS)
        tpl.paste(pi, ((tpl.width - nw) // 2, (tpl.height - nh) // 2))
        buf = BytesIO()
        tpl.save(buf, 'PNG')
        return buf.getvalue()
    except Exception as e:
        logging.error(f"Errore composizione immagine: {e}")
        return None

# Rest of code (send_to_telegram, post_product, controlla_prodotti, main) unchanged

# ==============================
# INVIO SU TELEGRAM
# ==============================
def send_to_telegram(message, photo_bytes=None):
    logging.debug("Invio messaggio a Telegram")
    if photo_bytes:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        data = {"chat_id": CHANNEL_ID, "caption": message, "parse_mode": "HTML"}
        files = {"photo": ("image.png", photo_bytes, "image/png")}
        try:
            response = session.post(url, data=data, files=files, proxies=PROXIES)
            response.raise_for_status()
            logging.debug("Messaggio con foto inviato a Telegram")
        except requests.RequestException as e:
            logging.error(f"Errore nell'invio della foto a Telegram: {e}")
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHANNEL_ID, "text": message, "parse_mode": "HTML"}
        try:
            response = session.post(url, data=data, proxies=PROXIES)
            response.raise_for_status()
            logging.debug("Messaggio di testo inviato a Telegram")
        except requests.RequestException as e:
            logging.error(f"Errore nell'invio del messaggio a Telegram: {e}")

def post_product(product_link: str):
    logging.debug(f"Post prodotto: {product_link}")
    max_retries = 3
    retry = 0
    product_data = None
    # Riprova lo scraping se il titolo risulta vuoto
    while retry < max_retries:
        product_data = parse_amazon_product(product_link)
        if product_data and product_data.get("title"):
            if product_data.get("title").strip() != "":
                break
        retry += 1
        logging.debug(f"Retry {retry} per lo scraping del prodotto: {product_link}")
        time.sleep(2)
    if not product_data or product_data.get("title").strip() == "":
        logging.error("Dati prodotto non validi dopo 3 tentativi, salto questo prodotto.")
        return
    message = build_telegram_message(product_data)
    photo_bytes = compose_image(product_data)
    send_to_telegram(message, photo_bytes)
    gc.collect()
    logging.debug("post_product completato")

# ==============================
# MONITORAGGIO DEI PRODOTTI
# ==============================
def controlla_prodotti():
    print("🟡 Avvio controllo prodotti...")
    logging.debug("Controllo prodotti in corso")
    ora_corrente = datetime.datetime.now()
    if ora_corrente.hour < 8 or ora_corrente.hour >= 22:
        print("🔕 Fuori orario (8-22), nessun controllo effettuato.")
        logging.debug("Fuori orario, uscita da controlla_prodotti")
        return
    try:
        headers = get_random_headers()
        random_delay()
        risposta = session.get(AMAZON_SEARCH_URL, headers=headers, timeout=10, proxies=PROXIES)
        risposta.raise_for_status()
        print("✅ Pagina Amazon caricata con successo.")
        logging.debug("Richiesta Amazon search completata")
    except Exception as e:
        print(f"❌ Errore nel caricamento della pagina Amazon: {e}")
        logging.error(f"Errore durante la richiesta di ricerca su Amazon: {e}")
        return

    soup = BeautifulSoup(risposta.text, "html.parser")
    prodotti = soup.find_all("div", {"data-asin": True})
    print(f"📦 Trovati {len(prodotti)} prodotti nella pagina.")
    if not prodotti:
        print("⚠️ Nessun prodotto trovato nella ricerca.")
        logging.debug("Nessun prodotto trovato nella ricerca")
        return

    prodotti_salvati = carica_prodotti_salvati()
    for prodotto in prodotti:
        if not prodotto.get("data-asin"):
            continue
        prezzo_corrente = None
        parte_intera = prodotto.find("span", class_="a-price-whole")
        parte_decimale = prodotto.find("span", class_="a-price-fraction")
        if parte_intera and parte_decimale:
            prezzo_corrente = estrai_prezzo(parte_intera, parte_decimale)
        if prezzo_corrente is None:
            continue

        prezzo_originale = None
        prezzo_originale_tag = prodotto.find("span", class_="a-price a-text-price")
        if prezzo_originale_tag:
            offscreen = prezzo_originale_tag.find("span", class_="a-offscreen")
            if offscreen:
                prezzo_originale = estrai_float(offscreen.get_text())
        if prezzo_originale is None or prezzo_originale == 0:
            continue

        sconto = ((prezzo_originale - prezzo_corrente) / prezzo_originale) * 100
        if sconto < 15:
            continue

        print(f"💥 Prodotto con sconto > 15% trovato: {prezzo_corrente}€ vs {prezzo_originale}€ ({round(sconto)}%)")
        link_tag = prodotto.find("a", class_="a-link-normal")
        if link_tag and link_tag.get("href"):
            raw_link = "https://www.amazon.it" + link_tag.get("href")
            clean_link = clean_amazon_url(raw_link)
            # Controllo duplicati migliorato: controllo per link esatto o ASIN corrispondente
            asin_new = extract_asin(clean_link)
            duplicate = False
            for saved_link in prodotti_salvati.keys():
                asin_saved = extract_asin(saved_link)
                if asin_new and asin_saved and asin_new == asin_saved:
                    duplicate = True
                    break
            if duplicate or (clean_link in prodotti_salvati):
                print("⏩ Prodotto già pubblicato di recente, salto.")
                continue
            print(f"📨 Invio prodotto: {clean_link}")
            salva_prodotto(clean_link, ora_corrente)
            post_product(clean_link)
            # Una volta pubblicato un prodotto valido si esce dal ciclo
            break
    print("✅ Controllo prodotti completato.\n")

def main():
    print("🚀 Avvio del monitor Amazon Funko")
    logging.debug("Entrata nella funzione main")
    while True:
        ora_corrente = datetime.datetime.now()
        if 8 <= ora_corrente.hour < 22:
            controlla_prodotti()
        else:
            print("🕒 Orario fuori fascia (monitoraggio attivo 8-22)")
        ora_successiva = (ora_corrente.replace(minute=0, second=0, microsecond=0) +
                          datetime.timedelta(hours=1))
        tempo_attesa = (ora_successiva - datetime.datetime.now()).total_seconds()
        print(f"⏳ Aspetto fino alle {ora_successiva.strftime('%H:%M')}")
        logging.debug(f"Sleep per {tempo_attesa} secondi")
        time.sleep(tempo_attesa)

if __name__ == "__main__":
    main()
