import re
import requests
from bs4 import BeautifulSoup
import logging
import gc  # Garbage collector
from io import BytesIO
from PIL import Image
from telegram import Update
from telegram.ext import Application, MessageHandler, filters
from urllib.parse import urlparse

# ==============================
# CONFIGURAZIONE
# ==============================
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
CHANNEL_ID = "@fpitcanale"  # ID del canale dove pubblicare il post
REF_TAG = "funkoitalia0c-21"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ==============================
# UTILITY: verifica URL Amazon o short link
# ==============================
def is_amazon_url(url: str) -> bool:
    """
    Controlla se l'URL appartiene ad Amazon (inclusi i domini .to/.eu di short link).
    """
    try:
        domain = urlparse(url).netloc.lower()
        # domini Amazon o short link comuni
        return (
            'amazon.' in domain
            or domain in ['amzn.to', 'amzn.eu']
        )
    except Exception:
        return False

# ==============================
# FUNZIONE DI SCRAPING
# ==============================
def parse_amazon_product(url: str) -> dict:
    """
    Risolve eventuali redirect da short link, estrae titolo, prezzo, prezzo di listino,
    numero di recensioni, immagine e costruisce link di affiliazione.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/98.0.4758.102 Safari/537.36"
        )
    }

    try:
        with requests.Session() as session:
            # segui redirect per short link
            response = session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            final_url = response.url
    except requests.RequestException as e:
        logging.error(f"Errore richiesta Amazon: {e}")
        return None

    # Pulisci URL finale da parametri
    clean_url = final_url.split("?")[0]

    soup = BeautifulSoup(response.text, "html.parser")
    data = {}

    title_tag = soup.find(id="productTitle")
    data["title"] = (
        title_tag.get_text(strip=True) if title_tag else "Titolo non trovato"
    )

    price_tag = soup.select_one(
        "#priceblock_ourprice, #priceblock_dealprice, #priceblock_saleprice, span.a-price span.a-offscreen"
    )
    data["price"] = price_tag.get_text(strip=True) if price_tag else "Prezzo non disponibile"

    list_price_tag = soup.select_one(
        "span.priceBlockStrikePriceString, span.a-price.a-text-price span.a-offscreen"
    )
    data["list_price"] = (
        list_price_tag.get_text(strip=True) if list_price_tag else None
    )

    review_tag = soup.select_one("#acrCustomerReviewText")
    data["reviews"] = review_tag.get_text(strip=True) if review_tag else "0 recensioni"

    meta_image_tag = soup.find("meta", property="og:image")
    if meta_image_tag and "content" in meta_image_tag.attrs:
        data["image_url"] = meta_image_tag["content"]
    else:
        landing_image = soup.select_one("#imgTagWrapperId img, #landingImage")
        data["image_url"] = (
            landing_image["src"] if landing_image and "src" in landing_image.attrs else None
        )

    # Costruisci link di affiliazione su clean_url
    data["ref_link"] = f"{clean_url}?tag={REF_TAG}"

    return data

# ==============================
# GESTORE DEI MESSAGGI
# ==============================
async def handle_message(update: Update, context):
    text = update.message.text.strip()

    # Accetta sia amazon.* che short link amzn.to/.eu
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
    title = product_data["title"].replace("Animation: ", "").strip()
    title = re.sub(r"- Figura in Vinile.*", "", title).strip()

    price = product_data["price"]
    list_price = product_data["list_price"]
    reviews = product_data["reviews"]
    ref_link = product_data["ref_link"]
    image_url = product_data["image_url"]

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

    msg_lines.append(f'🔗 <a href="{ref_link}">Acquista ora su Amazon</a>')
    msg_lines.append(f"⭐ {reviews}")

    final_message = "\n".join(msg_lines)

    if image_url:
        try:
            with open("template.png", "rb") as f:
                template_img = Image.open(f).convert("RGB")

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

            await context.bot.send_photo(
                chat_id=CHANNEL_ID, photo=buf, caption=final_message, parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Errore invio foto: {e}")
            await context.bot.send_message(
                chat_id=CHANNEL_ID, text=final_message, parse_mode="HTML"
            )
    else:
        await context.bot.send_message(
            chat_id=CHANNEL_ID, text=final_message, parse_mode="HTML"
        )

    gc.collect()

# ==============================
# FUNZIONE PRINCIPALE
# ==============================
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot in esecuzione...")
    app.run_polling(poll_interval=3, timeout=10, drop_pending_updates=True)

if __name__ == "__main__":
    main()


    #  source myenv/bin/activate
    #  cd funko/
    #  nohup python testbot.py &

# pip install requests beautifulsoup4 pillow python-telegram-bot

# pip3 install requests beautifulsoup4 pillow python-telegram-bot
