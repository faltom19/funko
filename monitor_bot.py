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

# Configurazione logging: output sia su file che su console
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("monitor_bot_debug.log"),
        logging.StreamHandler()
    ]
)

logging.debug("Avvio dello script monitor_bot")

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

# (Opzionale) Configurazione proxy, se necessario
PROXIES = {}  # Ad esempio: {'http': 'http://localhost:8080', 'https': 'http://localhost:8080'}

# ==============================
# SESSION GLOBALE CON RETRY E COOKIE
# ==============================
session = requests.Session()
retry_strategy = Retry(
    total=5,
    backoff_factor=2,  # Il backoff è aumentato per ridurre la frequenza dei retry
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# ==============================
# LISTA DI USER-AGENT RANDOM
# ==============================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:93.0) Gecko/20100101 Firefox/93.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Mobile/15E148 Safari/604.1",
]

def get_random_headers():
    # Aggiungo ulteriori headers per emulare un browser completo
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

def random_delay():
    # Incremento il delay per cercare di sfuggire a eventuali blocchi
    delay = random.uniform(3, 7)
    logging.debug(f"🕒 Ritardo random di {delay:.2f} secondi")
    time.sleep(delay)

# ==============================
# FUNZIONI UTILI PER IL PREZZO
# ==============================
def estrai_float(valore):
    try:
        valore = valore.replace("€", "").strip()
        valore = valore.replace(".", "").replace(",", ".")
        result = float(valore)
        logging.debug(f"estrai_float: convertito '{valore}' in {result}")
        return result
    except Exception as e:
        logging.error(f"estrai_float: errore con valore '{valore}': {e}")
        return None

def estrai_prezzo(parte_intera_tag, parte_decimale_tag):
    intero = parte_intera_tag.get_text().strip()
    fraz = parte_decimale_tag.get_text().strip()
    if intero and intero[-1] in [".", ","]:
        intero = intero[:-1]
    prezzo_testo = intero + "," + fraz
    result = estrai_float(prezzo_testo)
    logging.debug(f"estrai_prezzo: '{prezzo_testo}' convertito in {result}")
    return result

# ==============================
# GESTIONE FILE DI LOG
# ==============================
def carica_prodotti_salvati():
    logging.debug("Caricamento prodotti salvati")
    prodotti = {}
    ora_attuale = datetime.datetime.now()
    if os.path.exists(FILE_PATH):
        with open(FILE_PATH, "r") as f:
            lines = f.readlines()
        nuovi_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                timestamp_str, link = line.split(DELIMITER, 1)
                timestamp = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                delta = (ora_attuale - timestamp).total_seconds()
                if delta < 72 * 3600:
                    prodotti[link] = timestamp
                    nuovi_lines.append(line + "\n")
            except Exception as e:
                logging.error(f"Errore nel parsing della linea '{line}': {e}")
                continue
        with open(FILE_PATH, "w") as f:
            f.writelines(nuovi_lines)
        logging.debug(f"Prodotti salvati caricati: {len(prodotti)}")
    else:
        logging.debug("File dei prodotti non esiste, verrà creato.")
    return prodotti

def salva_prodotto(link, timestamp):
    try:
        with open(FILE_PATH, "a") as f:
            f.write(f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}{DELIMITER}{link}\n")
        logging.debug(f"Salvato prodotto: {link} a {timestamp}")
    except Exception as e:
        logging.error(f"Errore nel salvataggio del prodotto {link}: {e}")

# ==============================
# PULIZIA DELL'URL AMAZON
# ==============================
def clean_amazon_url(url: str) -> str:
    parsed = urlparse(url)
    if "sspa/click" in parsed.path:
        qs = parse_qs(parsed.query)
        if "url" in qs:
            product_path = qs["url"][0]
            product_path = unquote(product_path)
            clean_url = "https://www.amazon.it" + product_path
            logging.debug(f"Pulizia URL: {url} -> {clean_url}")
            return clean_url
    result = url.split("?")[0]
    logging.debug(f"Pulizia URL (senza sspa/click): {url} -> {result}")
    return result

# ==============================
# FUNZIONE DI SCRAPING DEL PRODOTTO (CON SESSIONE GLOBALE)
# ==============================
def parse_amazon_product(url: str) -> dict:
    logging.debug(f"Inizio parsing prodotto: {url}")
    clean_url = url.split("?")[0]
    headers = get_random_headers()
    random_delay()

    try:
        response = session.get(clean_url, headers=headers, timeout=10, proxies=PROXIES)
        response.raise_for_status()
        logging.debug("Richiesta ad Amazon completata con successo")
    except requests.RequestException as e:
        logging.error(f"Errore richiesta Amazon: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    data = {}

    title_tag = soup.find(id="productTitle")
    data["title"] = title_tag.get_text(strip=True) if title_tag else "Titolo non trovato"
    logging.debug(f"Titolo prodotto: {data['title']}")

    price_tag = soup.select_one("#priceblock_ourprice, #priceblock_dealprice, #priceblock_saleprice, span.a-price span.a-offscreen")
    data["price"] = price_tag.get_text(strip=True) if price_tag else "Prezzo non disponibile"
    logging.debug(f"Prezzo prodotto: {data['price']}")

    list_price_tag = soup.select_one("span.priceBlockStrikePriceString, span.a-price.a-text-price span.a-offscreen")
    data["list_price"] = list_price_tag.get_text(strip=True) if list_price_tag else None
    logging.debug(f"List price: {data['list_price']}")

    review_tag = soup.select_one("#acrCustomerReviewText")
    data["reviews"] = review_tag.get_text(strip=True) if review_tag else "0 recensioni"
    logging.debug(f"Reviews: {data['reviews']}")

    meta_image_tag = soup.find("meta", property="og:image")
    if meta_image_tag and "content" in meta_image_tag.attrs:
        data["image_url"] = meta_image_tag["content"]
    else:
        landing_image = soup.select_one("#imgTagWrapperId img, #landingImage")
        data["image_url"] = landing_image["src"] if landing_image and "src" in landing_image.attrs else None
    logging.debug(f"Image URL: {data['image_url']}")

    data["ref_link"] = f"{clean_url}?tag={REF_TAG}"
    logging.debug(f"Ref link: {data['ref_link']}")

    return data

# ==============================
# COSTRUZIONE DEL MESSAGGIO E COMPOSIZIONE DELL'IMMAGINE
# ==============================
def build_telegram_message(product_data: dict) -> str:
    logging.debug("Costruzione messaggio Telegram")
    title = product_data.get("title", "Titolo non trovato")
    title = title.replace("Animation: ", "").strip()
    title = re.sub(r"- Figura in Vinile.*", "", title).strip()
    price = product_data.get("price", "Prezzo non disponibile")
    list_price = product_data.get("list_price")
    reviews = product_data.get("reviews", "")
    msg_lines = [f"📍 <b>{title}</b>\n"]
    if list_price and "non disponibile" not in price.lower():
        try:
            original_price = float(re.sub(r"[^\d,]", "", list_price).replace(",", "."))
            current_price = float(re.sub(r"[^\d,]", "", price).replace(",", "."))
            discount = round(100 - (current_price / original_price * 100))
            if discount > 0:
                msg_lines.append(f"🔻 Sconto: {discount}%")
                msg_lines.append(f"✂️ <s>{list_price}</s> → <b>{price}</b>\n")
            else:
                msg_lines.append(f"💰 <b>{price}</b>\n")
        except ValueError as e:
            logging.error(f"Errore nel calcolo del discount: {e}")
            msg_lines.append(f"💰 <b>{price}</b>\n")
    else:
        msg_lines.append(f"💰 <b>{price}</b>\n")
    ref_link = product_data.get("ref_link", "")
    msg_lines.append(f'🔗 <a href="{ref_link}">Acquista ora su Amazon</a>')
    msg_lines.append(f"⭐ {reviews}")
    message = "\n".join(msg_lines)
    logging.debug(f"Messaggio Telegram costruito: {message}")
    return message

def compose_image(product_data: dict) -> bytes:
    logging.debug("Composizione immagine prodotto")
    image_url = product_data.get("image_url")
    if not image_url:
        logging.debug("Nessun URL immagine trovato")
        return None
    try:
        template_img = Image.open(TEMPLATE_IMAGE_PATH).convert("RGB")
        response = session.get(image_url, stream=True, timeout=10, proxies=PROXIES)
        response.raise_for_status()
        product_img = Image.open(BytesIO(response.content)).convert("RGB")
        orig_width, orig_height = product_img.size
        scale_factor = 2
        new_width = min(template_img.width, orig_width * scale_factor)
        new_height = min(template_img.height, orig_height * scale_factor)
        product_img = product_img.resize((new_width, new_height), Image.LANCZOS)
        pos_x = (template_img.width - new_width) // 2
        pos_y = (template_img.height - new_height) // 2
        template_img.paste(product_img, (pos_x, pos_y))
        buf = BytesIO()
        template_img.save(buf, format="PNG")
        buf.seek(0)
        logging.debug("Immagine composta correttamente")
        return buf.read()
    except Exception as e:
        logging.error(f"Errore nella composizione dell'immagine: {e}")
        return None

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
    product_data = parse_amazon_product(product_link)
    if not product_data:
        logging.error("Dati prodotto non trovati, interrompo post_product")
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
            if clean_link in prodotti_salvati:
                saved_time = prodotti_salvati[clean_link]
                delta = (ora_corrente - saved_time).total_seconds()
                if delta < 72 * 3600:
                    print("⏩ Prodotto già pubblicato di recente, salto.")
                    continue
            print(f"📨 Invio prodotto: {clean_link}")
            salva_prodotto(clean_link, ora_corrente)
            post_product(clean_link)
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
