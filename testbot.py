import re
import requests
from bs4 import BeautifulSoup
import logging
import gc
from io import BytesIO
from PIL import Image
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CallbackContext

# ==============================
# CONFIGURAZIONE
# ==============================
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
CHANNEL_ID = "@fpitcanale"  # ID del canale dove pubblicare il post
REF_TAG = "funkoitalia0c-21"

# Memorizza l'ultimo messaggio generato per ogni utente
pending_messages = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


# ==============================
# FUNZIONE PER RISOLVERE LINK ABBREVIATI
# ==============================
def resolve_short_url(short_url: str) -> str:
    """Segue il link accorciato e restituisce l'URL completo."""
    try:
        response = requests.head(short_url, allow_redirects=True, timeout=10)
        return response.url
    except requests.RequestException as e:
        logging.error(f"Errore nel risolvere il link abbreviato: {e}")
        return short_url


# ==============================
# FUNZIONE DI SCRAPING
# ==============================
def parse_amazon_product(url: str) -> dict:
    """Esegue il web scraping dei dettagli di un prodotto Amazon."""

    url = resolve_short_url(url)  # Risolve link abbreviati
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
    data["title"] = (
        title_tag.get_text(strip=True) if title_tag else "Titolo non trovato"
    )

    price_tag = soup.select_one(
        "#priceblock_ourprice, #priceblock_dealprice, #priceblock_saleprice, span.a-price span.a-offscreen"
    )
    data["price"] = (
        price_tag.get_text(strip=True) if price_tag else "Prezzo non disponibile"
    )

    list_price_tag = soup.select_one(
        "span.priceBlockStrikePriceString, span.a-price.a-text-price span.a-offscreen"
    )
    data["list_price"] = list_price_tag.get_text(strip=True) if list_price_tag else None

    review_tag = soup.select_one("#acrCustomerReviewText")
    data["reviews"] = review_tag.get_text(strip=True) if review_tag else "0 recensioni"

    meta_image_tag = soup.find("meta", property="og:image")
    data["image_url"] = (
        meta_image_tag["content"]
        if meta_image_tag and "content" in meta_image_tag.attrs
        else None
    )

    data["ref_link"] = f"{clean_url}?tag={REF_TAG}"

    return data


# ==============================
# GESTORE DEI MESSAGGI
# ==============================
async def handle_message(update: Update, context: CallbackContext):
    """Gestisce i messaggi ricevuti dal bot."""

    text = update.message.text.strip()

    if not any(domain in text for domain in ["amazon.", "amzn.to", "amzn.eu"]):
        await update.message.reply_text("Per favore, inviami un link di Amazon valido.")
        return

    product_data = parse_amazon_product(text)

    if not product_data:
        await update.message.reply_text(
            "Impossibile ottenere i dati dal link fornito. Riprova più tardi."
        )
        return

    title = product_data["title"]
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

    # Salva il messaggio in memoria per futura conferma
    pending_messages[update.message.chat_id] = final_message

    if image_url:
        try:
            response = requests.get(image_url, stream=True, timeout=10)
            response.raise_for_status()
            product_img = Image.open(BytesIO(response.content)).convert("RGB")

            buf = BytesIO()
            product_img.save(buf, format="PNG")
            buf.seek(0)

            await update.message.reply_photo(
                photo=buf, caption=final_message, parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Errore invio foto: {e}")
            await update.message.reply_text(final_message, parse_mode="HTML")
    else:
        await update.message.reply_text(final_message, parse_mode="HTML")

    gc.collect()


# ==============================
# GESTORE DELLA RISPOSTA "OK"
# ==============================
async def handle_ok_response(update: Update, context: CallbackContext):
    """Se l'utente risponde con 'ok', il post viene pubblicato nel canale."""

    chat_id = update.message.chat_id
    reply_to = update.message.reply_to_message

    if reply_to and chat_id in pending_messages:
        message = pending_messages[chat_id]
        await context.bot.send_message(
            chat_id=CHANNEL_ID, text=message, parse_mode="HTML"
        )
        await update.message.reply_text("✅ Post pubblicato nel canale!")
        del pending_messages[chat_id]  # Rimuove il messaggio dalla memoria


# ==============================
# FUNZIONE PRINCIPALE
# ==============================
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(re.compile(r"^ok$", re.IGNORECASE)),
            handle_ok_response,
        )
    )

    print("Bot in esecuzione...")
    app.run_polling(poll_interval=3, timeout=10, drop_pending_updates=True)


if __name__ == "__main__":
    main()

#  source myenv/bin/activate
#  cd funko/
#  nohup python testbot.py &

# pip install requests beautifulsoup4 pillow python-telegram-bot
# pip3 install requests beautifulsoup4 pillow python-telegram-bot
