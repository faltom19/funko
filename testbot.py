import re
import time
import requests
from bs4 import BeautifulSoup
import logging
import gc  # Garbage collector
from io import BytesIO
from PIL import Image
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters
from random import choice
import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import aiohttp
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ssl

# ==============================
# CONFIGURAZIONE
# ==============================
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
CHANNEL_ID = "@fpitcanale"  # ID del canale dove pubblicare il post
REF_TAG = "funkoitalia0c-21"
MAX_RETRIES = 3  # Numero massimo di tentativi
RETRY_DELAY = 2  # Secondi di attesa tra un tentativo e l'altro
TEMPLATE_PATH = "template.png"  # Percorso al template dell'immagine
REQUEST_TIMEOUT = 15  # Timeout per le richieste in secondi

# Configurazione logging avanzata
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Lista di User-Agent per rotazione
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/112.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/112.0.5615.46 Mobile/15E148 Safari/604.1"
]


# ==============================
# FUNZIONI DI UTILITÀ
# ==============================
def expand_amazon_link(short_url):
    try:
        session = requests.Session()
        response = session.head(short_url, allow_redirects=True, timeout=10)
        return response.url
    except requests.RequestException:
        return short_url  # fallback: se fallisce restituisce quello originale
    
def clean_amazon_url(url: str) -> str:
    """Pulisce e normalizza l'URL di Amazon."""
    # Rimuove i parametri superflui tranne l'ASIN
    parsed_url = urlparse(url)
    
    # Estrai dominio e percorso
    domain = parsed_url.netloc
    path = parsed_url.path
    
    # Assicurati che sia un URL Amazon
    if "amazon." not in domain:
        return url
    
    # Estrai l'ASIN (se presente)
    asin = None
    if "/dp/" in path:
        asin = path.split("/dp/")[1].split("/")[0]
    elif "/gp/product/" in path:
        asin = path.split("/gp/product/")[1].split("/")[0]
    
    # Se abbiamo trovato l'ASIN, costruiamo un URL pulito
    if asin and len(asin) == 10:
        return f"https://{domain}/dp/{asin}"
    
    # Se non riusciamo a estrarre l'ASIN, rimuoviamo solo i parametri
    return f"https://{domain}{path}"


def add_affiliate_tag(url: str, tag: str) -> str:
    """Aggiunge o sostituisce il tag di affiliazione all'URL."""
    parsed_url = urlparse(url)
    
    # Estrai i parametri esistenti
    params = parse_qs(parsed_url.query)
    
    # Aggiorna o aggiungi il tag
    params['tag'] = [tag]
    
    # Ricostruisci l'URL con i nuovi parametri
    new_query = urlencode(params, doseq=True)
    return urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        parsed_url.params,
        new_query,
        parsed_url.fragment
    ))


def extract_price(price_text: str) -> float:
    """Estrae il valore numerico da una stringa di prezzo."""
    if not price_text or "non disponibile" in price_text.lower():
        return None
    
    # Rimuovi tutto eccetto numeri, virgole e punti
    clean_price = re.sub(r'[^\d,.]', '', price_text)
    
    # Gestisci formati diversi (punto o virgola come separatore decimale)
    if ',' in clean_price and '.' in clean_price:
        # Se ci sono entrambi, il punto è separatore delle migliaia
        clean_price = clean_price.replace('.', '')
        clean_price = clean_price.replace(',', '.')
    elif ',' in clean_price:
        # Solo virgola presente, assume sia il separatore decimale
        clean_price = clean_price.replace(',', '.')
    
    try:
        return float(clean_price)
    except ValueError:
        logger.warning(f"Impossibile convertire il prezzo: {price_text}")
        return None


async def fetch_url_with_retries(session, url, headers, retries=MAX_RETRIES):
    """Esegue una richiesta HTTP con tentativi in caso di errore."""
    for attempt in range(retries):
        try:
            async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    html_content = await response.text()
                    return html_content
                else:
                    logger.warning(f"Tentativo {attempt+1}/{retries}: Risposta non valida ({response.status})")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Tentativo {attempt+1}/{retries} fallito: {str(e)}")
        
        if attempt < retries - 1:
            # Attendi prima di riprovare con ritardo esponenziale
            delay = RETRY_DELAY * (2 ** attempt)
            await asyncio.sleep(delay)
    
    logger.error(f"Tutti i tentativi falliti per URL: {url}")
    return None


# ==============================
# FUNZIONE DI SCRAPING
# ==============================
async def parse_amazon_product(url: str) -> dict:
    """Estrae i dati del prodotto dalla pagina Amazon."""
    clean_url = clean_amazon_url(url)
    user_agent = choice(USER_AGENTS)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    logger.info(f"Elaborazione prodotto: {clean_url}")
    
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        html_content = await fetch_url_with_retries(session, clean_url, headers)
        
        if not html_content:
            return None
        
        soup = BeautifulSoup(html_content, "html.parser")
        data = {}
        
        # --- Estrazione del titolo ---
        title_tags = [
            soup.find(id="productTitle"),
            soup.select_one("h1.product-title-word-break"),
            soup.select_one("h1 span.a-size-large")
        ]
        
        for tag in title_tags:
            if tag:
                raw_title = tag.get_text(strip=True)
                clean_title = re.sub(r"Figura in Vinile.*", "", raw_title).strip()
                data["title"] = clean_title
                break
        else:
            data["title"] = "Titolo non trovato"
            logger.warning("Titolo prodotto non trovato")
        
        # --- Estrazione del prezzo attuale ---
        price_selectors = [
            "#priceblock_ourprice", 
            "#priceblock_dealprice",
            "#priceblock_saleprice", 
            "span.a-price span.a-offscreen",
            "span.a-price span.a-price-whole",
            ".a-price .a-offscreen",
            "#corePrice_feature_div .a-price .a-offscreen"
        ]
        
        price_tag = None
        for selector in price_selectors:
            price_tag = soup.select_one(selector)
            if price_tag:
                break
        
        if price_tag:
            data["price"] = price_tag.get_text(strip=True)
            data["price_value"] = extract_price(data["price"])
        else:
            data["price"] = "Prezzo non disponibile"
            data["price_value"] = None
            logger.warning("Prezzo prodotto non trovato")
        
        # --- Estrazione del prezzo di listino ---
        list_price_selectors = [
            "span.priceBlockStrikePriceString",
            "span.a-price.a-text-price span.a-offscreen",
            ".a-text-price .a-offscreen",
            "span.a-text-strike",
            ".a-price.a-text-price .a-offscreen"
        ]
        
        list_price_tag = None
        for selector in list_price_selectors:
            list_price_tag = soup.select_one(selector)
            if list_price_tag:
                break
        
        if list_price_tag:
            data["list_price"] = list_price_tag.get_text(strip=True)
            data["list_price_value"] = extract_price(data["list_price"])
        else:
            data["list_price"] = None
            data["list_price_value"] = None
        
        # --- Estrazione numero recensioni ---
        review_selectors = [
            "#acrCustomerReviewText",
            "#acrCustomerReviewLink span",
            ".a-link-normal span.a-size-base"
        ]
        
        review_tag = None
        for selector in review_selectors:
            review_tag = soup.select_one(selector)
            if review_tag and ("recensioni" in review_tag.get_text(strip=True).lower() or 
                              "recensione" in review_tag.get_text(strip=True).lower() or
                              "review" in review_tag.get_text(strip=True).lower()):
                break
        
        data["reviews"] = review_tag.get_text(strip=True) if review_tag else "0 recensioni"
        
        # --- Estrazione URL immagine ---
        # Metodo 1: Dai meta tag
        meta_image_tag = soup.find("meta", property="og:image")
        
        # Metodo 2: Dal tag immagine principale
        landing_image_selectors = [
            "#imgTagWrapperId img", 
            "#landingImage",
            "#imgBlkFront", 
            "#main-image",
            ".a-dynamic-image",
            "#img-canvas img"
        ]
        
        landing_image = None
        for selector in landing_image_selectors:
            landing_image = soup.select_one(selector)
            if landing_image:
                break
        
        if meta_image_tag and "content" in meta_image_tag.attrs:
            data["image_url"] = meta_image_tag["content"]
        elif landing_image:
            for attr in ["src", "data-old-hires", "data-a-dynamic-image"]:
                if attr in landing_image.attrs:
                    img_src = landing_image[attr]
                    if attr == "data-a-dynamic-image":
                        try:
                            # La proprietà data-a-dynamic-image contiene un JSON con URL e dimensioni
                            import json
                            img_data = json.loads(img_src)
                            urls = list(img_data.keys())
                            if urls:
                                # Ordina per dimensione e prendi l'immagine più grande
                                data["image_url"] = sorted(urls, key=lambda url: img_data[url][0] * img_data[url][1], reverse=True)[0]
                                break
                        except Exception as e:
                            logger.error(f"Errore parsing data-a-dynamic-image: {e}")
                    else:
                        data["image_url"] = img_src
                        break
        else:
            data["image_url"] = None
            logger.warning("Immagine prodotto non trovata")
        
        # --- Calcolo del link con affiliazione ---
        data["ref_link"] = add_affiliate_tag(clean_url, REF_TAG)
        
        # --- Calcolo dello sconto (se possibile) ---
        if data["price_value"] and data["list_price_value"] and data["list_price_value"] > data["price_value"]:
            discount = round(100 - (data["price_value"] / data["list_price_value"] * 100))
            data["discount"] = discount if discount > 0 else None
        else:
            data["discount"] = None
        
        return data


def process_image_in_thread(image_url, template_path):
    """Processa l'immagine in un thread separato."""
    try:
        # Scarica l'immagine del prodotto
        response = requests.get(image_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        # Carica l'immagine del prodotto e il template
        with Image.open(BytesIO(response.content)).convert("RGBA") as product_img:
            try:
                with Image.open(template_path).convert("RGBA") as template_img:
                    # Dimensioni originali
                    orig_width, orig_height = product_img.size
                    
                    # Mantieni l'aspect ratio dell'immagine
                    max_size = min(template_img.width, template_img.height) - 40  # margine di 20px per lato
                    
                    # Calcola le nuove dimensioni mantenendo le proporzioni
                    if orig_width > orig_height:
                        new_width = max_size
                        new_height = int(orig_height * (max_size / orig_width))
                    else:
                        new_height = max_size
                        new_width = int(orig_width * (max_size / orig_height))
                    
                    # Ridimensiona l'immagine del prodotto
                    # Riduci del 25%
                    scale = 2
                    resized_img = product_img.resize(
                        (int(product_img.width * scale), int(product_img.height * scale)),
                        Image.LANCZOS
                    )
                    new_width, new_height = resized_img.size
                    # Calcola la posizione centrale
                    pos_x = (template_img.width - new_width) // 2
                    pos_y = (template_img.height - new_height) // 2
                    
                    # Crea un'immagine composita
                    composite = template_img.copy()
                    composite.paste(resized_img, (pos_x, pos_y), resized_img if resized_img.mode == 'RGBA' else None)
                    
                    # Salva l'immagine composita in un buffer
                    buf = BytesIO()
                    composite.save(buf, format="PNG")
                    buf.seek(0)
                    
                    return buf
            except Exception as e:
                logger.error(f"Errore durante l'elaborazione del template: {e}")
                
                # Fallback: usa solo l'immagine del prodotto senza template
                buf = BytesIO()
                product_img.save(buf, format="PNG")
                buf.seek(0)
                return buf
    except Exception as e:
        logger.error(f"Errore durante l'elaborazione dell'immagine: {e}")
        return None


# ==============================
# GESTORI DEI COMANDI
# ==============================
async def start_command(update: Update, context):
    """Gestisce il comando /start."""
    await update.message.reply_text(
        "Ciao! Inviami un link di un prodotto Amazon e creerò un post per il canale."
    )


async def help_command(update: Update, context):
    """Gestisce il comando /help."""
    await update.message.reply_text(
        "Questo bot crea post per il canale partendo da link Amazon.\n\n"
        "Per usarlo, semplicemente invia un link Amazon valido e il bot farà il resto.\n\n"
        "Comandi disponibili:\n"
        "/start - Avvia il bot\n"
        "/help - Mostra questo messaggio di aiuto"
    )


# ==============================
# GESTORE DEI MESSAGGI
# ==============================
async def handle_message(update: Update, context):
    """Gestisce l'arrivo di un messaggio con un link Amazon."""
    text = update.message.text.strip()
    
    # Verifica che il messaggio contenga un link Amazon (normale o corto)
    if not any(domain in text for domain in ["amazon.", "amzn.to", "amzn.eu", "a.co"]):
        await update.message.reply_text("Per favore, inviami un link di Amazon valido.")
        return
    
    # Invia messaggio di stato
    status_message = await update.message.reply_text("⏳ Sto elaborando il link Amazon...")
    
    try:
        # Espandi link corti se necessario
        if any(short_domain in text for short_domain in ["amzn.to", "amzn.eu", "a.co"]):
            logger.info(f"Espansione link corto: {text}")
            expanded_url = expand_amazon_link(text)
            logger.info(f"Link espanso: {expanded_url}")
            text = expanded_url
            
        # Ottieni i dati del prodotto
        product_data = await parse_amazon_product(text)
        
        if not product_data:
            await status_message.edit_text("❌ Impossibile ottenere i dati dal link fornito. Riprova più tardi.")
            return
        
        # Prepara il messaggio per il post
        msg_lines = [f"📍 <b>{product_data['title']}</b>\n"]
        
        # Gestione prezzi e sconti
        if product_data["discount"]:
            msg_lines.append(f"🔻 Sconto: {product_data['discount']}%")
            msg_lines.append(f"✂️ <s>{product_data['list_price']}</s> → <b>{product_data['price']}</b>\n")
        else:
            msg_lines.append(f"💰 <b>{product_data['price']}</b>\n")
        
        # Aggiungi link e recensioni
        msg_lines.append(f'🔗 <a href="{product_data["ref_link"]}">Acquista ora su Amazon</a>')
        msg_lines.append(f"⭐ {product_data['reviews']}")
        
        final_message = "\n".join(msg_lines)
        
        # Se c'è un'immagine, processala in un thread separato
        if product_data["image_url"]:
            with ThreadPoolExecutor() as executor:
                # Elabora l'immagine in un thread separato
                loop = asyncio.get_event_loop()
                image_buffer = await loop.run_in_executor(
                    executor, 
                    process_image_in_thread, 
                    product_data["image_url"], 
                    TEMPLATE_PATH
                )
                
                if image_buffer:
                    # Invia il post con l'immagine
                    sent_message = await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=image_buffer,
                        caption=final_message,
                        parse_mode="HTML"
                    )
                    
                    await status_message.edit_text("✅ Post inviato con successo al canale!")
                else:
                    # Fallback: invia solo il testo
                    sent_message = await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=final_message,
                        parse_mode="HTML"
                    )
                    await status_message.edit_text("✅ Post inviato con successo al canale (senza immagine).")
        else:
            # Invia solo il testo
            sent_message = await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=final_message,
                parse_mode="HTML"
            )
            await status_message.edit_text("✅ Post inviato con successo al canale (senza immagine).")
    
    except Exception as e:
        logger.error(f"Errore durante la gestione del messaggio: {e}", exc_info=True)
        await status_message.edit_text(f"❌ Si è verificato un errore: {str(e)[:100]}...")
    
    finally:
        # Forza il garbage collector per liberare memoria
        gc.collect()


# ==============================
# FUNZIONE PRINCIPALE
# ==============================
def main():
    """Funzione principale che avvia il bot."""
    try:
        # Verifica l'esistenza del file template.png
        if not os.path.exists(TEMPLATE_PATH):
            logger.warning(f"File template non trovato: {TEMPLATE_PATH}. Le immagini verranno inviate senza template.")
        
        # Inizializza l'applicazione
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Aggiungi i gestori
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Avvia il bot
        logger.info("Bot in esecuzione...")
        app.run_polling(poll_interval=1.0, timeout=30, drop_pending_updates=True)
    
    except Exception as e:
        logger.critical(f"Errore fatale durante l'avvio del bot: {e}", exc_info=True)


if __name__ == "__main__":
    main()

# Per eseguire il bot:
# source myenv/bin/activate
# cd funko/
# nohup python bot.py > bot_output.log 2>&1 &
