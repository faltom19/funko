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
import json

# ==============================
# CONFIGURAZIONE
# ==============================
TELEGRAM_BOT_TOKEN = "7861319577:AAEd-RY5TcD7_GlN5EKzErRTTrYvHeQ73-k"
CHANNEL_ID = "@fpitcanale"  # ID del canale dove pubblicare il post
REF_TAG = "funkoitalia0c-21"
MAX_RETRIES = 5  # Aumentato il numero di tentativi
RETRY_DELAY = 3  # Aumentato il ritardo tra tentativi
TEMPLATE_PATH = "template.png"  # Percorso al template dell'immagine
REQUEST_TIMEOUT = 20  # Aumentato il timeout

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

# Lista ampliata di User-Agent per rotazione
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.133",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

# Domini Amazon supportati
AMAZON_DOMAINS = [
    "amazon.it", "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", 
    "amazon.es", "amazon.ca", "amazon.com.au", "amazon.co.jp", "amazon.in",
    "amazon.com.mx", "amazon.com.br", "amazon.nl", "amazon.se", "amazon.pl",
    "amazon.com.tr", "amazon.ae", "amazon.sa", "amazon.sg"
]

# Domini di link abbreviati Amazon
SHORT_LINK_DOMAINS = ["amzn.to", "amzn.eu", "a.co", "amazon.to"]


# ==============================
# FUNZIONI DI UTILITÀ MIGLIORATE
# ==============================
def expand_amazon_link(short_url):
    """Espande i link abbreviati Amazon con maggiore robustezza."""
    try:
        session = requests.Session()
        session.max_redirects = 10
        
        # Headers per sembrare un browser reale
        headers = {
            "User-Agent": choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        # Prima prova con HEAD request
        try:
            response = session.head(short_url, allow_redirects=True, timeout=15, headers=headers)
            if response.url and any(domain in response.url for domain in AMAZON_DOMAINS):
                logger.info(f"Link espanso con HEAD: {short_url} -> {response.url}")
                return response.url
        except:
            pass
        
        # Se HEAD fallisce, prova con GET request
        response = session.get(short_url, allow_redirects=True, timeout=15, headers=headers)
        if response.url and any(domain in response.url for domain in AMAZON_DOMAINS):
            logger.info(f"Link espanso con GET: {short_url} -> {response.url}")
            return response.url
            
        logger.warning(f"Link espanso non sembra essere Amazon: {response.url}")
        return response.url  # Restituisce comunque il link espanso
        
    except requests.RequestException as e:
        logger.error(f"Errore nell'espansione del link {short_url}: {e}")
        return short_url  # fallback: se fallisce restituisce quello originale


def clean_amazon_url(url: str) -> str:
    """Pulisce e normalizza l'URL di Amazon con maggiore robustezza."""
    try:
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        path = parsed_url.path
        
        # Assicurati che sia un URL Amazon
        if not any(amazon_domain in domain for amazon_domain in AMAZON_DOMAINS):
            logger.warning(f"URL non sembra essere Amazon: {url}")
            return url
        
        # Estrai l'ASIN con metodi multipli
        asin = None
        
        # Metodo 1: /dp/
        if "/dp/" in path:
            asin = path.split("/dp/")[1].split("/")[0].split("?")[0]
        
        # Metodo 2: /gp/product/
        elif "/gp/product/" in path:
            asin = path.split("/gp/product/")[1].split("/")[0].split("?")[0]
        
        # Metodo 3: ASIN nei parametri URL
        if not asin:
            params = parse_qs(parsed_url.query)
            if 'ASIN' in params:
                asin = params['ASIN'][0]
            elif 'asin' in params:
                asin = params['asin'][0]
        
        # Metodo 4: Cerca pattern ASIN nel path
        if not asin:
            asin_pattern = re.search(r'/([A-Z0-9]{10})', path)
            if asin_pattern:
                asin = asin_pattern.group(1)
        
        # Se abbiamo trovato l'ASIN valido, costruiamo un URL pulito
        if asin and len(asin) == 10 and re.match(r'^[A-Z0-9]{10}$', asin):
            clean_url = f"https://{domain}/dp/{asin}"
            logger.info(f"URL pulito: {url} -> {clean_url}")
            return clean_url
        
        # Se non riusciamo a estrarre l'ASIN, rimuoviamo solo i parametri superflui
        logger.warning(f"ASIN non trovato in {url}, rimuovo solo parametri superflui")
        return f"https://{domain}{path}"
        
    except Exception as e:
        logger.error(f"Errore nella pulizia dell'URL {url}: {e}")
        return url


def add_affiliate_tag(url: str, tag: str) -> str:
    """Aggiunge o sostituisce il tag di affiliazione all'URL."""
    try:
        parsed_url = urlparse(url)
        params = parse_qs(parsed_url.query, keep_blank_values=True)
        
        # Rimuovi tag esistenti che potrebbero entrare in conflitto
        conflicting_tags = ['tag', 'linkCode', 'creative', 'creativeASIN', 'ref']
        for conflict_tag in conflicting_tags:
            if conflict_tag in params:
                del params[conflict_tag]
        
        # Aggiungi il nostro tag di affiliazione
        params['tag'] = [tag]
        
        # Ricostruisci l'URL
        new_query = urlencode(params, doseq=True)
        affiliate_url = urlunparse((
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment
        ))
        
        logger.info(f"Tag affiliazione aggiunto: {url} -> {affiliate_url}")
        return affiliate_url
        
    except Exception as e:
        logger.error(f"Errore nell'aggiunta del tag affiliazione: {e}")
        return url


def extract_price(price_text: str) -> float:
    """Estrae il valore numerico da una stringa di prezzo con maggiore robustezza."""
    if not price_text or "non disponibile" in price_text.lower() or "unavailable" in price_text.lower():
        return None
    
    # Rimuovi simboli di valuta e spazi
    clean_price = re.sub(r'[€$£¥₹\s]', '', price_text)
    
    # Gestisci diversi formati numerici
    if ',' in clean_price and '.' in clean_price:
        # Formato tipo: 1.234,56 o 1,234.56
        if clean_price.rindex(',') > clean_price.rindex('.'):
            # La virgola è dopo il punto, quindi è il separatore decimale
            clean_price = clean_price.replace('.', '').replace(',', '.')
        else:
            # Il punto è dopo la virgola, quindi è il separatore decimale
            clean_price = clean_price.replace(',', '')
    elif ',' in clean_price:
        # Solo virgola presente
        if len(clean_price.split(',')[-1]) <= 2:
            # Probabilmente separatore decimale
            clean_price = clean_price.replace(',', '.')
        else:
            # Probabilmente separatore delle migliaia
            clean_price = clean_price.replace(',', '')
    
    # Estrai solo numeri, punti e possibili decimali
    clean_price = re.sub(r'[^\d.]', '', clean_price)
    
    try:
        price_value = float(clean_price)
        logger.debug(f"Prezzo estratto: {price_text} -> {price_value}")
        return price_value
    except (ValueError, TypeError):
        logger.warning(f"Impossibile convertire il prezzo: {price_text} -> {clean_price}")
        return None


async def fetch_url_with_retries(session, url, headers, retries=MAX_RETRIES):
    """Esegue una richiesta HTTP con tentativi multipli e strategie diverse."""
    last_exception = None
    
    for attempt in range(retries):
        try:
            # Varia gli headers ad ogni tentativo
            current_headers = headers.copy()
            current_headers["User-Agent"] = choice(USER_AGENTS)
            
            # Aggiungi un piccolo delay randomico per sembrare più umano
            if attempt > 0:
                await asyncio.sleep(1 + (attempt * 0.5))
            
            async with session.get(url, headers=current_headers, timeout=REQUEST_TIMEOUT) as response:
                if response.status == 200:
                    html_content = await response.text()
                    logger.info(f"Richiesta riuscita al tentativo {attempt + 1}")
                    return html_content
                elif response.status == 503:
                    logger.warning(f"Tentativo {attempt+1}/{retries}: Servizio temporaneamente non disponibile (503)")
                    # Per 503, aspetta di più
                    await asyncio.sleep(5 + attempt * 2)
                elif response.status == 429:
                    logger.warning(f"Tentativo {attempt+1}/{retries}: Rate limit (429)")
                    # Per rate limit, aspetta ancora di più
                    await asyncio.sleep(10 + attempt * 5)
                else:
                    logger.warning(f"Tentativo {attempt+1}/{retries}: Risposta HTTP {response.status}")
                    
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exception = e
            logger.warning(f"Tentativo {attempt+1}/{retries} fallito: {str(e)}")
        
        # Delay esponenziale tra i tentativi
        if attempt < retries - 1:
            delay = RETRY_DELAY * (2 ** attempt) + (attempt * 2)  # Delay più lungo
            logger.info(f"Attendo {delay} secondi prima del prossimo tentativo...")
            await asyncio.sleep(delay)
    
    logger.error(f"Tutti i {retries} tentativi falliti per URL: {url}")
    if last_exception:
        logger.error(f"Ultima eccezione: {last_exception}")
    return None


# ==============================
# FUNZIONE DI SCRAPING MIGLIORATA
# ==============================
async def parse_amazon_product(url: str) -> dict:
    """Estrae i dati del prodotto dalla pagina Amazon con robustezza migliorata."""
    # Prima espandi il link se necessario
    original_url = url
    if any(short_domain in url for short_domain in SHORT_LINK_DOMAINS):
        logger.info(f"Espansione link abbreviato: {url}")
        url = expand_amazon_link(url)
        if url == original_url:
            logger.warning("Espansione del link fallita, continuo con l'URL originale")
    
    clean_url = clean_amazon_url(url)
    
    # Headers più completi per sembrare un browser reale
    base_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Pragma": "no-cache"
    }

    logger.info(f"Inizio elaborazione prodotto: {clean_url}")
    
    # Configurazione SSL più flessibile
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    # Configurazione connettore con timeout più lunghi
    connector = aiohttp.TCPConnector(
        ssl=ssl_context,
        ttl_dns_cache=300,
        use_dns_cache=True,
        limit=30,
        limit_per_host=10
    )
    
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=10)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        html_content = await fetch_url_with_retries(session, clean_url, base_headers)
        
        if not html_content:
            logger.error("Impossibile ottenere il contenuto HTML")
            return None
        
        soup = BeautifulSoup(html_content, "html.parser")
        data = {}
        
        # --- Estrazione del titolo con selettori multipli ---
        title_selectors = [
            "#productTitle",
            "h1.product-title-word-break",
            "h1 span.a-size-large",
            "h1.a-size-large",
            "[data-automation-id='product-title']",
            ".product-title",
            "h1 span",
            "#title .a-size-large"
        ]
        
        title_found = False
        for selector in title_selectors:
            title_tag = soup.select_one(selector)
            if title_tag and title_tag.get_text(strip=True):
                raw_title = title_tag.get_text(strip=True)
                # Pulisci il titolo
                clean_title = re.sub(r"Figura in Vinile.*", "", raw_title).strip()
                clean_title = re.sub(r"\s+", " ", clean_title)  # Normalizza spazi
                data["title"] = clean_title
                title_found = True
                logger.info(f"Titolo trovato con selettore {selector}: {clean_title}")
                break
        
        if not title_found:
            data["title"] = "Titolo non trovato"
            logger.warning("Titolo prodotto non trovato con nessun selettore")
        
        # --- Estrazione del prezzo attuale con selettori ampliati ---
        price_selectors = [
            "#priceblock_ourprice",
            "#priceblock_dealprice", 
            "#priceblock_saleprice",
            ".a-price .a-offscreen",
            "span.a-price span.a-offscreen",
            "span.a-price-whole",
            "#corePrice_feature_div .a-price .a-offscreen",
            ".a-price.a-size-medium .a-offscreen",
            ".a-price-range .a-offscreen",
            "[data-automation-id='product-price'] .a-offscreen",
            ".kindle-price .a-offscreen",
            "#price_inside_buybox",
            ".header-price",
            "[aria-label*='price'] .a-offscreen"
        ]
        
        price_found = False
        for selector in price_selectors:
            price_tag = soup.select_one(selector)
            if price_tag and price_tag.get_text(strip=True):
                price_text = price_tag.get_text(strip=True)
                if price_text and any(char.isdigit() for char in price_text):
                    data["price"] = price_text
                    data["price_value"] = extract_price(price_text)
                    price_found = True
                    logger.info(f"Prezzo trovato con selettore {selector}: {price_text}")
                    break
        
        if not price_found:
            data["price"] = "Prezzo non disponibile"
            data["price_value"] = None
            logger.warning("Prezzo prodotto non trovato con nessun selettore")
        
        # --- Estrazione del prezzo di listino con selettori ampliati ---
        list_price_selectors = [
            "span.priceBlockStrikePriceString",
            "span.a-price.a-text-price span.a-offscreen",
            ".a-text-price .a-offscreen",
            "span.a-text-strike",
            ".a-price.a-text-price .a-offscreen",
            ".a-text-strike .a-offscreen",
            "[aria-label*='List Price'] .a-offscreen",
            ".a-price-was .a-offscreen"
        ]
        
        list_price_found = False
        for selector in list_price_selectors:
            list_price_tag = soup.select_one(selector)
            if list_price_tag and list_price_tag.get_text(strip=True):
                list_price_text = list_price_tag.get_text(strip=True)
                if list_price_text and any(char.isdigit() for char in list_price_text):
                    data["list_price"] = list_price_text
                    data["list_price_value"] = extract_price(list_price_text)
                    list_price_found = True
                    logger.info(f"Prezzo di listino trovato: {list_price_text}")
                    break
        
        if not list_price_found:
            data["list_price"] = None
            data["list_price_value"] = None
        
        # --- Estrazione numero recensioni con selettori ampliati ---
        review_selectors = [
            "#acrCustomerReviewText",
            "#acrCustomerReviewLink span",
            ".a-link-normal span.a-size-base",
            "[data-automation-id='reviews-count']",
            "a[href*='customerReviews'] span",
            ".cr-widget-ContentReadReviews span"
        ]
        
        reviews_found = False
        for selector in review_selectors:
            review_tag = soup.select_one(selector)
            if review_tag:
                review_text = review_tag.get_text(strip=True).lower()
                if any(keyword in review_text for keyword in ["recensioni", "recensione", "review", "valutazioni"]):
                    data["reviews"] = review_tag.get_text(strip=True)
                    reviews_found = True
                    logger.info(f"Recensioni trovate: {data['reviews']}")
                    break
        
        if not reviews_found:
            data["reviews"] = "0 recensioni"
        
        # --- Estrazione URL immagine con metodi multipli ---
        image_url = None
        
        # Metodo 1: Meta tag Open Graph
        meta_image_tag = soup.find("meta", property="og:image")
        if meta_image_tag and meta_image_tag.get("content"):
            image_url = meta_image_tag["content"]
            logger.info("Immagine trovata tramite meta tag og:image")
        
        # Metodo 2: Immagine principale del prodotto
        if not image_url:
            image_selectors = [
                "#imgTagWrapperId img",
                "#landingImage",
                "#imgBlkFront",
                "#main-image",
                ".a-dynamic-image",
                "#img-canvas img",
                "[data-automation-id='product-image'] img",
                ".image-wrapper img"
            ]
            
            for selector in image_selectors:
                img_tag = soup.select_one(selector)
                if img_tag:
                    # Prova diversi attributi per l'URL dell'immagine
                    for attr in ["src", "data-old-hires", "data-a-dynamic-image", "data-src"]:
                        if img_tag.get(attr):
                            if attr == "data-a-dynamic-image":
                                try:
                                    # Parsing JSON per le immagini dinamiche
                                    img_data = json.loads(img_tag[attr])
                                    if img_data:
                                        # Prendi l'immagine con risoluzione più alta
                                        image_url = max(img_data.keys(), 
                                                      key=lambda url: img_data[url][0] * img_data[url][1])
                                        logger.info("Immagine trovata tramite data-a-dynamic-image")
                                        break
                                except (json.JSONDecodeError, KeyError):
                                    continue
                            else:
                                image_url = img_tag[attr]
                                logger.info(f"Immagine trovata tramite attributo {attr}")
                                break
                    if image_url:
                        break
        
        data["image_url"] = image_url
        if not image_url:
            logger.warning("Immagine prodotto non trovata")
        
        # --- Calcolo del link con affiliazione ---
        data["ref_link"] = add_affiliate_tag(clean_url, REF_TAG)
        
        # --- Calcolo dello sconto ---
        if (data["price_value"] and data["list_price_value"] and 
            data["list_price_value"] > data["price_value"]):
            discount = round(100 - (data["price_value"] / data["list_price_value"] * 100))
            data["discount"] = discount if discount > 0 else None
            logger.info(f"Sconto calcolato: {discount}%")
        else:
            data["discount"] = None
        
        logger.info(f"Dati estratti con successo per: {data['title']}")
        return data


def process_image_in_thread(image_url, template_path):
    """Processa l'immagine in un thread separato con gestione migliorata degli errori."""
    try:
        # Headers per il download dell'immagine
        headers = {
            "User-Agent": choice(USER_AGENTS),
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        
        # Scarica l'immagine del prodotto
        response = requests.get(image_url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        
        # Carica l'immagine del prodotto
        with Image.open(BytesIO(response.content)).convert("RGBA") as product_img:
            try:
                # Prova a usare il template se esiste
                if os.path.exists(template_path):
                    with Image.open(template_path).convert("RGBA") as template_img:
                        # Calcola dimensioni mantenendo l'aspect ratio
                        max_size = min(template_img.width, template_img.height) - 40
                        
                        # Ridimensiona mantenendo le proporzioni
                        scale = 2
                        resized_img = product_img.resize(
                            (int(product_img.width * scale), int(product_img.height * scale)),
                            Image.LANCZOS
                        )
                        
                        # Centra l'immagine
                        pos_x = (template_img.width - resized_img.width) // 2
                        pos_y = (template_img.height - resized_img.height) // 2
                        
                        # Crea composito
                        composite = template_img.copy()
                        composite.paste(resized_img, (pos_x, pos_y), 
                                      resized_img if resized_img.mode == 'RGBA' else None)
                        
                        # Salva in buffer
                        buf = BytesIO()
                        composite.save(buf, format="PNG", optimize=True)
                        buf.seek(0)
                        return buf
                        
            except Exception as e:
                logger.warning(f"Errore con template, uso immagine originale: {e}")
            
            # Fallback: usa solo l'immagine del prodotto
            buf = BytesIO()
            # Ottimizza dimensioni se troppo grande
            if product_img.width > 1920 or product_img.height > 1920:
                max_size = 1920
                if product_img.width > product_img.height:
                    new_width = max_size
                    new_height = int(product_img.height * (max_size / product_img.width))
                else:
                    new_height = max_size
                    new_width = int(product_img.width * (max_size / product_img.height))
                product_img = product_img.resize((new_width, new_height), Image.LANCZOS)
            
            product_img.save(buf, format="PNG", optimize=True)
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
        "🤖 *Bot Amazon Scraper*\n\n"
        "Inviami un link di un prodotto Amazon (anche abbreviato) e creerò automaticamente un post per il canale!\n\n"
        "✅ Supporto per link abbreviati (amzn.to, a.co, ecc.)\n"
        "✅ Aggiunta automatica tag affiliazione\n"
        "✅ Elaborazione immagini con template\n"
        "✅ Calcolo sconti automatico",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context):
    """Gestisce il comando /help."""
    await update.message.reply_text(
        "📋 *Guida al Bot Amazon Scraper*\n\n"
        "*Come usare il bot:*\n"
        "• Invia un link Amazon (normale o abbreviato)\n"
        "• Il bot elaborerà automaticamente il prodotto\n"
        "• Verrà creato un post con immagine e dettagli\n\n"
        "*Link supportati:*\n"
        "• amazon.it/dp/ASIN\n"
        "• amzn.to/xxxxx\n"
        "• a.co/xxxxx\n"
        "• amazon.com/dp/ASIN\n\n"
        "*Comandi disponibili:*\n"
        "/start - Avvia il bot\n"
        "/help - Mostra questa guida\n"
        "/test - Test funzionalità",
        parse_mode="Markdown"
    )


async def test_command(update: Update, context):
    """Comando di test per verificare il funzionamento del bot."""
    await update.message.reply_text(
        "🔧 *Test Bot Amazon Scraper*\n\n"
        f"✅ Bot attivo e funzionante\n"
        f"✅ Canale configurato: {CHANNEL_ID}\n"
        f"✅ Tag affiliazione: {REF_TAG}\n"
        f"✅ Template disponibile: {'Sì' if os.path.exists(TEMPLATE_PATH) else 'No'}\n\n"
        "Invia un link Amazon per testare la funzionalità!",
        parse_mode="Markdown"
    )


# ==============================
# GESTORE DEI MESSAGGI MIGLIORATO
# ==============================
async def handle_message(update: Update, context):
    """Gestisce l'arrivo di un messaggio con un link Amazon con robustezza migliorata."""
    text = update.message.text.strip()
    
    # Verifica che il messaggio contenga un link Amazon (normale o corto)
    amazon_found = any(domain in text.lower() for domain in AMAZON_DOMAINS + SHORT_LINK_DOMAINS)
    
    if not amazon_found:
        await update.message.reply_text(
            "❌ Non ho trovato un link Amazon valido nel messaggio.\n\n"
            "✅ *Link supportati:*\n"
            "• amazon.it/dp/ASIN\n"
            "• amzn.to/xxxxx\n"
            "• a.co/xxxxx\n"
            "• Tutti i domini Amazon internazionali",
            parse_mode="Markdown"
        )
        return
    
    # Estrai l'URL dal messaggio (può contenere altro testo)
    url_pattern = r'https?://[^\s<>"\']+|www\.[^\s<>"\']+|[^\s<>"\']*(?:amazon\.|amzn\.|a\.co)[^\s<>"\']+'
    urls = re.findall(url_pattern, text, re.IGNORECASE)
    
    if not urls:
        await update.message.reply_text("❌ Non riesco a identificare l'URL nel messaggio.")
        return
    
    # Prendi il primo URL trovato
    amazon_url = urls[0]
    if not amazon_url.startswith(('http://', 'https://')):
        amazon_url = 'https://' + amazon_url
    
    # Invia messaggio di stato
    status_message = await update.message.reply_text("⏳ Sto elaborando il link Amazon...")
    
    try:
        # Aggiorna status
        await status_message.edit_text("🔍 Analisi del link in corso...")
        
        # Ottieni i dati del prodotto
        product_data = await parse_amazon_product(amazon_url)
        
        if not product_data:
            await status_message.edit_text(
                "❌ *Errore nell'elaborazione*\n\n"
                "Non sono riuscito a ottenere i dati dal link fornito.\n"
                "Possibili cause:\n"
                "• Prodotto non disponibile\n"
                "• Protezioni anti-bot di Amazon\n"
                "• Problemi di connessione\n\n"
                "🔄 *Suggerimenti:*\n"
                "• Riprova tra qualche minuto\n"
                "• Verifica che il link sia corretto\n"
                "• Prova con un link diverso dello stesso prodotto",
                parse_mode="Markdown"
            )
            return
        
        await status_message.edit_text("📝 Preparazione del post...")
        
        # Prepara il messaggio per il post
        msg_lines = [f"📍 <b>{product_data['title']}</b>\n"]
        
        # Gestione prezzi e sconti
        if product_data["discount"] and product_data["discount"] > 0:
            msg_lines.append(f"🔥 <b>SCONTO {product_data['discount']}%</b>")
            msg_lines.append(f"✂️ <s>{product_data['list_price']}</s> → <b>{product_data['price']}</b>\n")
        else:
            msg_lines.append(f"💰 <b>{product_data['price']}</b>\n")
        
        # Aggiungi link e recensioni
        msg_lines.append(f'🛒 <a href="{product_data["ref_link"]}">Acquista ora su Amazon</a>')
        
        if product_data['reviews'] and product_data['reviews'] != "0 recensioni":
            msg_lines.append(f"⭐ {product_data['reviews']}")
        
        # Aggiungi hashtag per visibilità
        msg_lines.append("\n#Amazon #Offerte #Shopping")
        
        final_message = "\n".join(msg_lines)
        
        # Se c'è un'immagine, processala
        if product_data["image_url"]:
            await status_message.edit_text("🖼️ Elaborazione immagine...")
            
            with ThreadPoolExecutor(max_workers=2) as executor:
                # Elabora l'immagine in un thread separato
                loop = asyncio.get_event_loop()
                image_buffer = await loop.run_in_executor(
                    executor, 
                    process_image_in_thread, 
                    product_data["image_url"], 
                    TEMPLATE_PATH
                )
                
                if image_buffer:
                    await status_message.edit_text("📤 Invio del post al canale...")
                    
                    # Invia il post con l'immagine
                    sent_message = await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=image_buffer,
                        caption=final_message,
                        parse_mode="HTML"
                    )
                    
                    await status_message.edit_text(
                        "✅ *Post inviato con successo!*\n\n"
                        f"📋 Prodotto: {product_data['title'][:50]}...\n"
                        f"💰 Prezzo: {product_data['price']}\n"
                        f"🖼️ Immagine: Elaborata\n"
                        f"🔗 Link affiliazione: Aggiunto",
                        parse_mode="Markdown"
                    )
                else:
                    # Fallback: invia solo il testo
                    await status_message.edit_text("📤 Invio del post (senza immagine)...")
                    
                    sent_message = await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=final_message,
                        parse_mode="HTML"
                    )
                    
                    await status_message.edit_text(
                        "✅ *Post inviato con successo!*\n\n"
                        f"📋 Prodotto: {product_data['title'][:50]}...\n"
                        f"💰 Prezzo: {product_data['price']}\n"
                        f"🖼️ Immagine: Non disponibile\n"
                        f"🔗 Link affiliazione: Aggiunto",
                        parse_mode="Markdown"
                    )
        else:
            # Invia solo il testo
            await status_message.edit_text("📤 Invio del post al canale...")
            
            sent_message = await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=final_message,
                parse_mode="HTML"
            )
            
            await status_message.edit_text(
                "✅ *Post inviato con successo!*\n\n"
                f"📋 Prodotto: {product_data['title'][:50]}...\n"
                f"💰 Prezzo: {product_data['price']}\n"
                f"🖼️ Immagine: Non trovata\n"
                f"🔗 Link affiliazione: Aggiunto",
                parse_mode="Markdown"
            )
    
    except Exception as e:
        logger.error(f"Errore durante la gestione del messaggio: {e}", exc_info=True)
        await status_message.edit_text(
            f"❌ *Errore imprevisto*\n\n"
            f"Si è verificato un errore durante l'elaborazione:\n"
            f"`{str(e)[:200]}...`\n\n"
            f"🔄 Per favore riprova o contatta l'amministratore se il problema persiste.",
            parse_mode="Markdown"
        )
    
    finally:
        # Forza il garbage collector per liberare memoria
        gc.collect()


# ==============================
# GESTORE ERRORI GLOBALE
# ==============================
async def error_handler(update: Update, context):
    """Gestisce gli errori globali del bot."""
    logger.error(f"Errore causato dall'update {update}: {context.error}", exc_info=True)
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ *Errore interno del bot*\n\n"
                "Si è verificato un errore imprevisto. "
                "L'errore è stato registrato e verrà risolto quanto prima.\n\n"
                "🔄 Riprova tra qualche minuto.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Errore nell'invio del messaggio di errore: {e}")


# ==============================
# FUNZIONI DI UTILITÀ AGGIUNTIVE
# ==============================
def validate_config():
    """Valida la configurazione del bot all'avvio."""
    errors = []
    
    if not TELEGRAM_BOT_TOKEN or len(TELEGRAM_BOT_TOKEN) < 40:
        errors.append("Token Telegram non valido")
    
    if not CHANNEL_ID:
        errors.append("ID canale non configurato")
    
    if not REF_TAG:
        errors.append("Tag di affiliazione non configurato")
    
    if errors:
        logger.critical(f"Errori di configurazione: {', '.join(errors)}")
        raise ValueError(f"Configurazione non valida: {', '.join(errors)}")
    
    logger.info("✅ Configurazione validata con successo")


def setup_logging():
    """Configura il logging avanzato."""
    # Crea directory logs se non esiste
    os.makedirs("logs", exist_ok=True)
    
    # File handler con rotazione
    from logging.handlers import RotatingFileHandler
    
    file_handler = RotatingFileHandler(
        "logs/bot.log", 
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    ))
    
    # Configura logger principale
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.setLevel(logging.INFO)
    
    # Logger per aiohttp (troppo verboso)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)


# ==============================
# FUNZIONE PRINCIPALE MIGLIORATA
# ==============================
def main():
    """Funzione principale che avvia il bot con gestione errori migliorata."""
    try:
        # Setup logging
        setup_logging()
        logger.info("🚀 Avvio Bot Amazon Scraper...")
        
        # Valida configurazione
        validate_config()
        
        # Verifica template
        if not os.path.exists(TEMPLATE_PATH):
            logger.warning(f"⚠️ File template non trovato: {TEMPLATE_PATH}")
            logger.warning("Le immagini verranno inviate senza template")
        else:
            logger.info(f"✅ Template trovato: {TEMPLATE_PATH}")
        
        # Inizializza l'applicazione
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Aggiungi i gestori
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("test", test_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Aggiungi gestore errori globale
        app.add_error_handler(error_handler)
        
        # Statistiche di avvio
        logger.info("📊 Configurazione caricata:")
        logger.info(f"   • Canale: {CHANNEL_ID}")
        logger.info(f"   • Tag affiliazione: {REF_TAG}")
        logger.info(f"   • Max tentativi: {MAX_RETRIES}")
        logger.info(f"   • Timeout richieste: {REQUEST_TIMEOUT}s")
        logger.info(f"   • User agents disponibili: {len(USER_AGENTS)}")
        logger.info(f"   • Domini Amazon supportati: {len(AMAZON_DOMAINS)}")
        
        # Avvia il bot
        logger.info("🤖 Bot in esecuzione e in attesa di messaggi...")
        app.run_polling(
            poll_interval=1.0, 
            timeout=30, 
            drop_pending_updates=True,
            allowed_updates=["message", "channel_post"]
        )
    
    except KeyboardInterrupt:
        logger.info("🛑 Arresto del bot richiesto dall'utente")
    except Exception as e:
        logger.critical(f"💥 Errore fatale durante l'avvio del bot: {e}", exc_info=True)
        raise
    finally:
        logger.info("👋 Bot arrestato")


if __name__ == "__main__":
    main()

# ==============================
# ISTRUZIONI PER L'ESECUZIONE
# ==============================
"""
Per eseguire il bot:

1. Installa le dipendenze:
   pip install python-telegram-bot beautifulsoup4 pillow aiohttp requests

2. Configura le variabili:
   - TELEGRAM_BOT_TOKEN: Token del tuo bot Telegram
   - CHANNEL_ID: ID del canale dove pubblicare (@nomecanale)
   - REF_TAG: Il tuo tag di affiliazione Amazon

3. (Opzionale) Aggiungi un file template.png per le immagini

4. Esegui il bot:
   python bot.py

5. Per eseguire in background:
   nohup python bot.py > bot_output.log 2>&1 &

MIGLIORAMENTI IMPLEMENTATI:
✅ Gestione robusta dei link abbreviati (amzn.to, a.co, ecc.)
✅ Retry multipli con delay esponenziale
✅ User-Agent rotation per evitare blocchi
✅ Selettori CSS multipli per ogni elemento
✅ Gestione errori migliorata con messaggi informativi
✅ Validazione della configurazione all'avvio
✅ Logging avanzato con rotazione file
✅ Ottimizzazione memoria e performance
✅ Supporto domini Amazon internazionali
✅ Calcolo sconti automatico
✅ Elaborazione immagini ottimizzata
✅ Tag affiliazione sempre aggiunto correttamente
"""
