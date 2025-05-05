import os
import re
import time
import random
import logging
import datetime
from io import BytesIO
from urllib.parse import urlparse, parse_qs, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from telegram import Bot

# ==============================
# CONFIGURAZIONE SICURA
# ==============================
load_dotenv()  # carica .env in os.environ
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID          = os.getenv("CHANNEL_ID", "@fpitcanale")
REF_TAG             = os.getenv("REF_TAG", "funkoitalia0c-21")
AMAZON_SEARCH_URL   = "https://www.amazon.it/s?k=funko+pop"
FILE_PATH           = "products.txt"
DELIMITER           = ";"
TEMPLATE_IMAGE_PATH = "template.png"

# ==============================
# LOGGING STRUTTURATO
# ==============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("monitor_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==============================
# SESSION HTTP CON RETRY
# ==============================
session = requests.Session()
retry_strategy = Retry(
    total=8,
    backoff_factor=2,               # backoff più consistente
    status_forcelist=[500,502,503,504],
    allowed_methods=["GET","POST"]
)
session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
session.mount("http://", HTTPAdapter(max_retries=retry_strategy))

# ==============================
# USER‐AGENT ROTATION & DELAY
# ==============================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    # … altri UA …
]
def get_random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "it-IT,it;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }
def random_delay(min_s=5, max_s=20):
    d = random.uniform(min_s, max_s)
    logger.debug(f"Delay {d:.1f}s")
    time.sleep(d)

# ==============================
# URL CLEAN & ASIN EXTRACTION
# ==============================
def clean_amazon_url(url: str) -> str:
    p = urlparse(url)
    if "sspa/click" in p.path:
        qs = parse_qs(p.query)
        if "url" in qs:
            return unquote(qs["url"][0])
    return url.split("?",1)[0]

def extract_asin(url: str) -> str:
    for pat in [r"/dp/(\w{10})", r"/gp/product/(\w{10})"]:
        m = re.search(pat, url)
        if m: return m.group(1)
    return None

# ==============================
# FILE I/O SICURO (cutoff 5 giorni)
# ==============================
def load_saved():
    saved = {}
    cutoff = datetime.datetime.now() - datetime.timedelta(days=5)
    if os.path.exists(FILE_PATH):
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(FILE_PATH, "w", encoding="utf-8") as fw:
            for L in lines:
                line = L.strip()
                if not line or DELIMITER not in line:
                    continue
                ts,link = line.split(DELIMITER,1)
                try:
                    dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if dt >= cutoff:
                    saved[link] = dt
                    fw.write(f"{ts}{DELIMITER}{link}\n")
    return saved

def save_link(link):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(FILE_PATH,"a",encoding="utf-8") as f:
        f.write(f"{ts}{DELIMITER}{link}\n")

# ==============================
# SCRAPING PRODOTTO
# ==============================
def parse_product(url: str) -> dict:
    url = clean_amazon_url(url)
    headers = get_random_headers(); random_delay()
    try:
        r = session.get(url, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Fetch failed: {e}")
        return {}
    soup = BeautifulSoup(r.text,"html.parser")
    title = soup.find(id="productTitle")
    price = soup.select_one("#priceblock_ourprice, .a-price span.a-offscreen")
    img   = soup.find("meta",property="og:image")
    return {
        "title": title.get_text(strip=True) if title else "",
        "price": price.get_text(strip=True) if price else "N/D",
        "image_url": img["content"] if img and img.get("content") else None,
        "ref_link": f"{url}?tag={REF_TAG}"
    }

# ==============================
# TELEGRAM
# ==============================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
def post_telegram(data):
    msg = f"<b>{data['title']}</b>\n💰 {data['price']}\n🔗 <a href='{data['ref_link']}'>Acquista</a>"
    if data.get("image_url"):
        resp = session.get(data["image_url"], stream=True)
        bot.send_photo(chat_id=CHANNEL_ID, photo=resp.raw, caption=msg, parse_mode="HTML")
    else:
        bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="HTML")

# ==============================
# CONTROLLO OFFERTE CON RETRY SU 503
# ==============================
def check_products():
    logger.info("Controllo Amazon Funko…")
    saved = load_saved()

    # Retry loop per la ricerca, gestendo 503
    for attempt in range(5):
        headers = get_random_headers(); random_delay()
        try:
            r = session.get(AMAZON_SEARCH_URL, headers=headers, timeout=10)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            logger.warning(f"Search attempt {attempt+1} failed: {e}")
            random_delay(30, 60)
    else:
        logger.error("Search failed after retries, skipping this cycle")
        return

    soup = BeautifulSoup(r.text,"html.parser")
    cards = soup.find_all("div",{"data-asin":True})
    for c in cards:
        asin = c["data-asin"]
        whole = c.find("span",class_="a-price-whole")
        frac  = c.find("span",class_="a-price-fraction")
        off   = c.select_one(".a-text-price .a-offscreen")
        if not (whole and frac and off): continue
        curr = float((whole.get_text()+","+frac.get_text()).replace(".","").replace(",","."))
        orig = float(off.get_text().replace("€","").replace(".","").replace(",","."))
        if orig<=0 or (orig-curr)/orig*100 < 15: continue

        link = "https://www.amazon.it"+c.find("a",class_="a-link-normal")["href"]
        link = clean_amazon_url(link)
        if link in saved or (asin and any(extract_asin(l)==asin for l in saved)):
            continue

        logger.info(f"Trovato sconto {round((orig-curr)/orig*100)}% → {curr}€")
        save_link(link)
        data = parse_product(link)
        if data.get("title"):
            post_telegram(data)
            break

# ==============================
# SCHEDULING CON APSCHEDULER
# ==============================
scheduler = BackgroundScheduler()
scheduler.add_job(
    check_products,
    trigger=IntervalTrigger(hours=1, start_date=datetime.datetime.now(), jitter=60),
    next_run_time=datetime.datetime.now()
)
scheduler.start()

try:
    while True:
        time.sleep(60)
except (KeyboardInterrupt, SystemExit):
    scheduler.shutdown()
