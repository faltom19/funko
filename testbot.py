import re
import requests
from bs4 import BeautifulSoup
import logging
import gc  # Garbage collector
from io import BytesIO
from PIL import Image
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ==============================
# CONFIGURAZIONE
# ==============================
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
CHANNEL_ID = "@fpitcanale"  # ID del canale dove pubblicare il post
REF_TAG = "funkoitalia0c-21"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Amazon domains patterns
AMAZON_DOMAINS = [
    r"https?://(?:www\.)?amazon\.(?:it|com|co\.uk|de|fr|es)/"
]

# Regex to extract ASIN or full URL
SHORT_URL_REGEX = re.compile(r"https?://amzn\.to/([A-Za-z0-9]+)")

# ==============================
# HELPER FUNCTIONS
# ==============================
def expand_short_url(url: str) -> str:
    """
    Expande un link abbreviato amzn.to usando una richiesta HEAD.
    """
    try:
        resp = requests.head(url, allow_redirects=True, timeout=5)
        return resp.url
    except requests.RequestException as e:
        logger.warning(f"Impossibile espandere URL abbreviato {url}: {e}")
        return url


def is_amazon_url(url: str) -> bool:
    """
    Verifica se l'URL appartiene a un dominio Amazon supportato.
    """
    for pattern in AMAZON_DOMAINS:
        if re.match(pattern, url):
            return True
    return False

# ==============================
# FUNZIONE DI SCRAPING
# ==============================
def parse_amazon_product(url: str) -> dict:
    # Rimuoviamo parametri di tracking
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
        logger.error(f"Errore richiesta Amazon: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    data = {}

    # Titolo prodotto
    title_tag = soup.find(id="productTitle")
    data["title"] = title_tag.get_text(strip=True) if title_tag else "Titolo non trovato"

    # Prezzo corrente
    price_tag = soup.select_one(
        "#priceblock_ourprice, #priceblock_dealprice, #priceblock_saleprice, span.a-price span.a-offscreen"
    )
    data["price"] = price_tag.get_text(strip=True) if price_tag else "Prezzo non disponibile"

    # Prezzo di listino
    list_price_tag = soup.select_one(
        "span.priceBlockStrikePriceString, span.a-price.a-text-price span.a-offscreen"
    )
    data["list_price"] = list_price_tag.get_text(strip=True) if list_price_tag else None

    # Recensioni
    review_tag = soup.select_one("#acrCustomerReviewText")
    data["reviews"] = review_tag.get_text(strip=True) if review_tag else "0 recensioni"

    # Immagine
    meta_image_tag = soup.find("meta", property="og:image")
    if meta_image_tag and meta_image_tag.get("content"):
        data["image_url"] = meta_image_tag["content"]
    else:
        landing_image = soup.select_one("#imgTagWrapperId img, #landingImage")
        data["image_url"] = landing_image["src"] if landing_image and landing_image.get("src") else None

    # Link di affiliazione
    data["ref_link"] = f"{clean_url}?tag={REF_TAG}"

    return data

# ==============================
# GESTORE DEI MESSAGGI
# ==============================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Gestione link abbreviato amzn.to
    if SHORT_URL_REGEX.search(text):
        text = expand_short_url(text)

    # Verifica URL Amazon
    if not is_amazon_url(text):
        await update.message.reply_text("Per favore, inviami un link di Amazon valido.")
        return

    product_data = parse_amazon_product(text)
    if not product_data:
        await update.message.reply_text(
            "Impossibile ottenere i dati dal link fornito. Riprova più tardi."
        )
        return

    # Pulizia del titolo
    title = re.sub(r"- Figura in Vinile.*", "", product_data["title"])  # rimuove "- Figura in Vinile"
    title = title.replace("Animation: ", "").strip()

    price = product_data["price"]
    list_price = product_data.get("list_price")
    reviews = product_data["reviews"]
    ref_link = product_data["ref_link"]
    image_url = product_data.get("image_url")

    # Costruzione messaggio
    msg_lines = [f"📍 <b>{title}</b>\n"]
    if list_price:
        try:
            orig = float(re.sub(r"[^\d,]", "", list_price).replace(",", "."))
            curr = float(re.sub(r"[^\d,]", "", price).replace(",", "."))
            discount = round(100 - (curr / orig * 100))
            if discount > 0:
                msg_lines.append(f"🔻 Sconto: {discount}%")
                msg_lines.append(f"✂️ <s>{list_price}</s> → <b>{price}</b>\n")
            else:
                msg_lines.append(f"💰 <b>{price}</b>\n")
        except ValueError:
            msg_lines.append(f"💰 <b>{price}</b>\n")
    else:
        msg_lines.append(f"💰 <b>{price}</b>\n")

    msg_lines.append(f'🔗 <a href="{ref_link}">Acquista ora su Amazon</a>')
    msg_lines.append(f"⭐ {reviews}")
    final_message = "\n".join(msg_lines)

    # Invia con o senza immagine
    if image_url:
        try:
            # Caricamento e combinazione immagini
            with open("template.png", "rb") as f:
                template_img = Image.open(f).convert("RGB")
            resp = requests.get(image_url, stream=True, timeout=10)
            resp.raise_for_status()
            prod_img = Image.open(BytesIO(resp.content)).convert("RGB")

            # Ridimensionamento
            w, h = prod_img.size
            factor = min(template_img.width / w, template_img.height / h, 2)
            new_size = (int(w * factor), int(h * factor))
            prod_img = prod_img.resize(new_size, Image.LANCZOS)
            x = (template_img.width - new_size[0]) // 2
            y = (template_img.height - new_size[1]) // 2
            template_img.paste(prod_img, (x, y))

            buf = BytesIO()
            template_img.save(buf, format="PNG")
            buf.seek(0)

            await context.bot.send_photo(
                chat_id=CHANNEL_ID, photo=buf, caption=final_message, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Errore invio foto: {e}")
            await context.bot.send_message(
                chat_id=CHANNEL_ID, text=final_message, parse_mode="HTML"
            )
    else:
        await context.bot.send_message(
            chat_id=CHANNEL_ID, text=final_message, parse_mode="HTML"
        )

    # Forza garbage collection
    gc.collect()

# ==============================
# FUNZIONE PRINCIPALE
# ==============================
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot in esecuzione...")
    app.run_polling(poll_interval=3, timeout=10, drop_pending_updates=True)

if __name__ == "__main__":
    main()
