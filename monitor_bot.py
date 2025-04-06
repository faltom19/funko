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

# ==============================
# FUNZIONI UTILI PER IL PREZZO
# ==============================
def estrai_float(valore):
    try:
        valore = valore.replace("€", "").strip()
        valore = valore.replace(".", "").replace(",", ".")
        return float(valore)
    except Exception:
        return None

def estrai_prezzo(parte_intera_tag, parte_decimale_tag):
    intero = parte_intera_tag.get_text().strip()
    fraz = parte_decimale_tag.get_text().strip()
    if intero and intero[-1] in [".", ","]:
        intero = intero[:-1]
    prezzo_testo = intero + "," + fraz
    return estrai_float(prezzo_testo)

# ==============================
# GESTIONE FILE DI LOG
# ==============================
def carica_prodotti_salvati():
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
            except Exception:
                continue

        with open(FILE_PATH, "w") as f:
            f.writelines(nuovi_lines)

    return prodotti

def salva_prodotto(link, timestamp):
    with open(FILE_PATH, "a") as f:
        f.write(f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}{DELIMITER}{link}\n")

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
            return "https://www.amazon.it" + product_path
    return url.split("?")[0]

# ==============================
# FUNZIONE DI SCRAPING DEL PRODOTTO (OTTIMIZZATA)
# ==============================
def parse_amazon_product(url: str) -> dict:
    clean_url = url.split("?")[0]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/98.0.4758.102 Safari/537.36"
        )
    }

    try:
        with requests.Session() as session:
            response = session.get(clean_url, headers=headers, timeout=10)
            response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Errore richiesta Amazon: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    data = {}

    title_tag = soup.find(id="productTitle")
    data["title"] = title_tag.get_text(strip=True) if title_tag else "Titolo non trovato"

    price_tag = soup.select_one("#priceblock_ourprice, #priceblock_dealprice, #priceblock_saleprice, span.a-price span.a-offscreen")
    data["price"] = price_tag.get_text(strip=True) if price_tag else "Prezzo non disponibile"

    list_price_tag = soup.select_one("span.priceBlockStrikePriceString, span.a-price.a-text-price span.a-offscreen")
    data["list_price"] = list_price_tag.get_text(strip=True) if list_price_tag else None

    review_tag = soup.select_one("#acrCustomerReviewText")
    data["reviews"] = review_tag.get_text(strip=True) if review_tag else "0 recensioni"

    meta_image_tag = soup.find("meta", property="og:image")
    if meta_image_tag and "content" in meta_image_tag.attrs:
        data["image_url"] = meta_image_tag["content"]
    else:
        landing_image = soup.select_one("#imgTagWrapperId img, #landingImage")
        data["image_url"] = landing_image["src"] if landing_image and "src" in landing_image.attrs else None

    data["ref_link"] = f"{clean_url}?tag={REF_TAG}"

    return data

# ==============================
# COSTRUZIONE DEL MESSAGGIO E COMPOSIZIONE DELL'IMMAGINE
# ==============================
def build_telegram_message(product_data: dict) -> str:
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
        except ValueError:
            msg_lines.append(f"💰 <b>{price}</b>\n")
    else:
        msg_lines.append(f"💰 <b>{price}</b>\n")
    ref_link = product_data.get("ref_link", "")
    msg_lines.append(f'🔗 <a href="{ref_link}">Acquista ora su Amazon</a>')
    msg_lines.append(f"⭐ {reviews}")
    return "\n".join(msg_lines)

def compose_image(product_data: dict) -> bytes:
    image_url = product_data.get("image_url")
    if not image_url:
        return None
    try:
        template_img = Image.open(TEMPLATE_IMAGE_PATH).convert("RGB")
        response = requests.get(image_url, stream=True, timeout=10)
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
        return buf.read()
    except Exception:
        return None

# ==============================
# INVIO SU TELEGRAM
# ==============================
def send_to_telegram(message, photo_bytes=None):
    if photo_bytes:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        data = {"chat_id": CHANNEL_ID, "caption": message, "parse_mode": "HTML"}
        files = {"photo": ("image.png", photo_bytes, "image/png")}
        try:
            response = requests.post(url, data=data, files=files)
            response.raise_for_status()
        except requests.RequestException:
            pass
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHANNEL_ID, "text": message, "parse_mode": "HTML"}
        try:
            response = requests.post(url, data=data)
            response.raise_for_status()
        except requests.RequestException:
            pass

def post_product(product_link: str):
    product_data = parse_amazon_product(product_link)
    if not product_data:
        return
    message = build_telegram_message(product_data)
    photo_bytes = compose_image(product_data)
    send_to_telegram(message, photo_bytes)
    gc.collect()

# ==============================
# MONITORAGGIO DEI PRODOTTI
# ==============================
def controlla_prodotti():
    ora_corrente = datetime.datetime.now()
    if ora_corrente.hour < 8 or ora_corrente.hour >= 22:
        return
    try:
        risposta = requests.get(AMAZON_SEARCH_URL)
        risposta.raise_for_status()
    except Exception:
        return
    soup = BeautifulSoup(risposta.text, "html.parser")
    prodotti = soup.find_all("div", {"data-asin": True})
    if not prodotti:
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

        link_tag = prodotto.find("a", class_="a-link-normal")
        if link_tag and link_tag.get("href"):
            raw_link = "https://www.amazon.it" + link_tag.get("href")
            clean_link = clean_amazon_url(raw_link)
            if clean_link in prodotti_salvati:
                saved_time = prodotti_salvati[clean_link]
                delta = (ora_corrente - saved_time).total_seconds()
                if delta < 72 * 3600:
                    continue
            salva_prodotto(clean_link, ora_corrente)
            post_product(clean_link)
            break

# ==============================
# FUNZIONE PRINCIPALE
# ==============================
def main():
    while True:
        ora_corrente = datetime.datetime.now()
        if 8 <= ora_corrente.hour < 22:
            controlla_prodotti()
        ora_successiva = (ora_corrente.replace(minute=0, second=0, microsecond=0) +
                          datetime.timedelta(hours=1))
        tempo_attesa = (ora_successiva - datetime.datetime.now()).total_seconds()
        time.sleep(tempo_attesa)

if __name__ == "__main__":
    main()

    #  source myenv/bin/activate
    #  cd funko/
    #  nohup python monitor_bot.py &
