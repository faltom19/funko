import re
import requests
from bs4 import BeautifulSoup
import logging
import gc  # Garbage collector
from io import BytesIO
from PIL import Image
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

# ==============================
# CONFIGURAZIONE
# ==============================
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
REF_TAG = "funkoitalia0c-21"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


# ==============================
# FUNZIONE DI SCRAPING
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
    data["title"] = (
        title_tag.get_text(strip=True) if title_tag else "Titolo non trovato"
    )

    price_tag = soup.select_one(
        "#priceblock_ourprice, #priceblock_dealprice, #priceblock_saleprice, span.a-price span.a-offscreen"
    )
    if price_tag:
        data["price"] = price_tag.get_text(strip=True)
    else:
        data["price"] = "Prezzo non disponibile"

    list_price_tag = soup.select_one(
        "span.priceBlockStrikePriceString, span.a-price.a-text-price span.a-offscreen"
    )
    if list_price_tag:
        data["list_price"] = list_price_tag.get_text(strip=True)
    else:
        data["list_price"] = None

    review_tag = soup.select_one("#acrCustomerReviewText")
    data["reviews"] = review_tag.get_text(strip=True) if review_tag else "0 recensioni"

    meta_image_tag = soup.find("meta", property="og:image")
    if meta_image_tag and "content" in meta_image_tag.attrs:
        data["image_url"] = meta_image_tag["content"]
    else:
        landing_image = soup.select_one("#imgTagWrapperId img, #landingImage")
        data["image_url"] = (
            landing_image["src"]
            if landing_image and "src" in landing_image.attrs
            else None
        )

    data["ref_link"] = f"{clean_url}?tag={REF_TAG}"

    return data


# ==============================
# GESTORE DEI MESSAGGI
# ==============================
async def handle_message(update: Update, context):
    text = update.message.text.strip()

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

    msg_lines = []
    msg_lines.append(f"📍 <b>{title}</b>\n")  # Spazio dopo il titolo

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
            # Apri il template da file sul server
            with open("template.png", "rb") as f:
                template_img = Image.open(f).convert("RGB")

            # Scarica l'immagine del prodotto in memoria
            response = requests.get(image_url, stream=True, timeout=10)
            response.raise_for_status()
            product_img = Image.open(BytesIO(response.content)).convert("RGB")

            # Ridimensiona l'immagine del prodotto a 600x600 px
            product_img = product_img.resize((600, 600), Image.ANTIALIAS)

            # Calcola la posizione centrale per incollare l'immagine del prodotto
            template_width, template_height = template_img.size
            pos = ((template_width - 600) // 2, (template_height - 600) // 2)

            # Incolla l'immagine del prodotto sul template
            template_img.paste(product_img, pos)

            # Salva l'immagine combinata in un buffer in memoria
            buf = BytesIO()
            template_img.save(buf, format="PNG")
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
# FUNZIONE PRINCIPALE
# ==============================
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot in esecuzione...")
    app.run_polling()


if __name__ == "__main__":
    main()

    #  source myenv/bin/activate
    #  cd funko/
    #  nohup python testbot.py &
