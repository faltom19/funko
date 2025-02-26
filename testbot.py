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
REF_TAG = "funkoitalia0c-21"
CHANNEL_ID = "@fpitcanale"  # ID o username del gruppo/canale

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Memoria temporanea per salvare i messaggi in attesa di conferma
pending_posts = {}


# ==============================
# FUNZIONE PER ESPANDERE LINK BREVI AMAZON
# ==============================
def expand_amazon_link(short_url: str) -> str:
    try:
        response = requests.head(short_url, allow_redirects=True, timeout=10)
        return response.url.split("?")[0]  # Rimuove eventuali parametri extra
    except requests.RequestException as e:
        logging.error(f"Errore nell'espansione del link Amazon: {e}")
        return None


# ==============================
# FUNZIONE DI SCRAPING
# ==============================
def parse_amazon_product(url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/98.0.4758.102 Safari/537.36"
        )
    }

    try:
        with requests.Session() as session:
            response = session.get(url, headers=headers, timeout=10)
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
    data["image_url"] = meta_image_tag["content"] if meta_image_tag else None

    data["ref_link"] = f"{url}?tag={REF_TAG}"

    return data


# ==============================
# GESTORE DEI MESSAGGI
# ==============================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Controlla se il link è abbreviato e lo espande
    if text.startswith("https://amzn.to/") or text.startswith("https://amzn.eu/"):
        expanded_url = expand_amazon_link(text)
        if not expanded_url:
            await update.message.reply_text("Errore nell'espansione del link. Riprova.")
            return
        text = expanded_url

    if "amazon." not in text:
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

    # Salva il messaggio in attesa di conferma
    pending_posts[update.message.chat_id] = final_message

    await update.message.reply_text(final_message, parse_mode="HTML")
    await update.message.reply_text("Rispondi con 'ok' per pubblicarlo nel canale.")

    gc.collect()


# ==============================
# GESTORE DELLE RISPOSTE DI CONFERMA
# ==============================
async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = update.message.text.strip().lower()

    if text == "ok" and chat_id in pending_posts:
        post_message = pending_posts.pop(chat_id)

        try:
            await context.bot.send_message(
                chat_id=CHANNEL_ID, text=post_message, parse_mode="HTML"
            )
            await update.message.reply_text("✅ Post pubblicato nel canale!")
        except Exception as e:
            logging.error(f"Errore nella pubblicazione: {e}")
            await update.message.reply_text("❌ Errore nella pubblicazione del post.")
    else:
        await update.message.reply_text("Comando non riconosciuto.")


# ==============================
# FUNZIONE PRINCIPALE
# ==============================
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Gestisce i link Amazon
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Gestisce la conferma "ok"
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirmation)
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
