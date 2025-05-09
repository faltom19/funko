import re
import requests
from bs4 import BeautifulSoup
import logging
import gc
from io import BytesIO
from PIL import Image
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from urllib.parse import urlparse, parse_qs

# ==============================
# CONFIGURAZIONE
# ==============================
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
CHANNEL_ID = "@fpitcanale"
REF_TAG = "funkoitalia0c-21"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# domini Amazon supportati
AMAZON_DOMAINS = [r"https?://(?:www\.)?amazon\.(?:it|com|co\.uk|de|fr|es)/"]
SHORT_REGEX = re.compile(r"https?://amzn\.to/\w+")

# session con retry
session = requests.Session()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
retry = Retry(total=3, backoff_factor=1, status_forcelist=[500,502,503,504])
session.mount("https://", HTTPAdapter(max_retries=retry))

# helper

def expand_short(url):
    try:
        r = session.head(url, allow_redirects=True, timeout=5)
        return r.url
    except Exception:
        return url


def is_amazon(url):
    for p in AMAZON_DOMAINS:
        if re.match(p, url): return True
    return False

# scraping

def parse_amazon_product(url: str) -> dict:
    url = url.split("?",1)[0]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = session.get(url, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Errore richiesta Amazon: {e}")
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.find(id="productTitle")
    price = soup.select_one("#priceblock_ourprice, #priceblock_dealprice, span.a-price span.a-offscreen")
    listp = soup.select_one("span.priceBlockStrikePriceString, span.a-text-price span.a-offscreen")
    rev = soup.select_one("#acrCustomerReviewText")
    img = soup.find("meta", property="og:image")
    data = {
        "title": title.get_text(strip=True) if title else "Titolo non trovato",
        "price": price.get_text(strip=True) if price else "Prezzo non disponibile",
        "list_price": listp.get_text(strip=True) if listp else None,
        "reviews": rev.get_text(strip=True) if rev else "0 recensioni",
        "image_url": img["content"] if img and img.get("content") else None,
        "ref_link": f"{url}?tag={REF_TAG}"
    }
    return data

# handler

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if SHORT_REGEX.match(text):
        text = expand_short(text)
    if not is_amazon(text):
        await update.message.reply_text("Per favore, inviami un link di Amazon valido.")
        return
    data = parse_amazon_product(text)
    if not data:
        await update.message.reply_text("Impossibile ottenere dati dal link.")
        return
    # format
    title = re.sub(r"- Figura in Vinile.*","", data["title"]).replace("Animation: ","").strip()
    price, listp, rev, ref = data["price"], data["list_price"], data["reviews"], data["ref_link"]
    msg=[f"📍 <b>{title}</b>\n"]
    if listp:
        try:
            o=float(re.sub(r"[^\d,]","",listp).replace(',','.'))
            c=float(re.sub(r"[^\d,]","",price).replace(',','.'))
            d=round(100-(c/o*100))
            if d>0: msg+= [f"🔻 Sconto: {d}%", f"✂️ <s>{listp}</s> → <b>{price}</b>\n"]
            else: msg.append(f"💰 <b>{price}</b>\n")
        except: msg.append(f"💰 <b>{price}</b>\n")
    else: msg.append(f"💰 <b>{price}</b>\n")
    msg.append(f'🔗 <a href="{ref}">Acquista ora</a>')
    msg.append(f"⭐ {rev}")
    final="\n".join(msg)
    # send
    if data["image_url"]:
        try:
            buf= BytesIO()
            img = session.get(data["image_url"],stream=True,timeout=10).raw
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=img, caption=final, parse_mode="HTML")
        except:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=final, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=final, parse_mode="HTML")
    gc.collect()

# main

def main():
    app=Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__=="__main__": main()
