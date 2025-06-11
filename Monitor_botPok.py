import requests
from bs4 import BeautifulSoup
import time
import datetime
import json
import random
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional

# === CONFIGURAZIONE ===
TOKEN = '7909094251:AAFCIgZ6y8ccfxoRtIa-EQav4tF_FxXg5Xg'
CHAT_ID = '125505180'
CHECK_INTERVAL = 30  # secondi
PREZZO_MAX = 60.00
ORARIO_INIZIO = 8
ORARIO_FINE = 20

# Lista link da monitorare
LINKS = [
    "https://www.amazon.it/gp/aw/d/B0F1G6H7DR/ref=ox_sc_saved_title_2?smid=A11IL2PNWYJU7H&psc=1"
]

# === SETUP LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('price_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AmazonPriceMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.notified_file = Path("notified_log.json")
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0"
        ]
    
    def get_headers(self) -> Dict[str, str]:
        """Genera headers casuali per evitare il rilevamento"""
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    
    def load_notified(self) -> Dict[str, float]:
        """Carica la lista dei prodotti già notificati"""
        try:
            if self.notified_file.exists():
                with open(self.notified_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Errore nel caricamento notified_log.json: {e}")
        return {}
    
    def save_notified(self, data: Dict[str, float]):
        """Salva la lista dei prodotti notificati"""
        try:
            with open(self.notified_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Errore nel salvataggio notified_log.json: {e}")
    
    def clean_old_entries(self, data: Dict[str, float]) -> Dict[str, float]:
        """Rimuove le notifiche più vecchie di 24 ore"""
        now = time.time()
        return {k: v for k, v in data.items() if now - v < 86400}
    
    def send_telegram_message(self, text: str) -> bool:
        """Invia messaggio Telegram"""
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }
            response = self.session.post(url, data=data, timeout=10)
            response.raise_for_status()
            logger.info("Messaggio Telegram inviato con successo")
            return True
        except Exception as e:
            logger.error(f"Errore nell'invio del messaggio Telegram: {e}")
            return False
    
    def extract_price(self, soup: BeautifulSoup) -> Optional[float]:
        """Estrae il prezzo dalla pagina Amazon con multiple strategie"""
        price_selectors = [
            # Prezzo principale
            'span.a-price-whole',
            'span#priceblock_dealprice',
            'span#priceblock_ourprice',
            'span.a-price.a-text-price.a-size-medium.apexPriceToPay span.a-offscreen',
            'span.a-price-range',
            # Altri selettori comuni
            '.a-price .a-offscreen',
            '.a-price-whole',
            '#apex_desktop .a-price .a-offscreen'
        ]
        
        for selector in price_selectors:
            try:
                price_element = soup.select_one(selector)
                if price_element:
                    price_text = price_element.get_text(strip=True)
                    # Rimuovi simboli di valuta e spazi
                    price_clean = re.sub(r'[€$£,\s]', '', price_text)
                    # Gestisci i decimali
                    if '.' in price_clean:
                        return float(price_clean)
                    elif len(price_clean) > 2:
                        # Assumendo che gli ultimi 2 caratteri siano i centesimi
                        euros = price_clean[:-2]
                        cents = price_clean[-2:]
                        return float(f"{euros}.{cents}")
            except (ValueError, AttributeError):
                continue
        
        # Strategia alternativa per prezzi frazionari
        try:
            whole = soup.select_one('span.a-price-whole')
            fraction = soup.select_one('span.a-price-fraction')
            if whole and fraction:
                whole_text = re.sub(r'[^\d]', '', whole.get_text())
                frac_text = re.sub(r'[^\d]', '', fraction.get_text())
                return float(f"{whole_text}.{frac_text}")
        except (ValueError, AttributeError):
            pass
        
        return None
    
    def parse_amazon(self, url: str) -> Optional[Dict[str, any]]:
        """Analizza una pagina Amazon e restituisce i dati del prodotto"""
        try:
            logger.info(f"Controllo URL: {url}")
            
            # Aggiungi delay casuale per sembrare più umano
            time.sleep(random.uniform(1, 3))
            
            response = self.session.get(url, headers=self.get_headers(), timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Estrai titolo
            title_selectors = [
                '#productTitle',
                'span#productTitle',
                'h1.a-size-large span',
                'h1 span#productTitle'
            ]
            
            title = "Prodotto sconosciuto"
            for selector in title_selectors:
                title_element = soup.select_one(selector)
                if title_element:
                    title = title_element.get_text(strip=True)
                    break
            
            # Estrai prezzo
            price = self.extract_price(soup)
            if not price:
                logger.warning(f"Prezzo non trovato per {url}")
                return None
            
            # Verifica disponibilità e venditore
            availability_indicators = [
                "Disponibile",
                "In magazzino",
                "Disponibilità immediata",
                "Spedito da Amazon"
            ]
            
            page_text = soup.get_text().lower()
            is_available = any(indicator.lower() in page_text for indicator in availability_indicators)
            
            # Verifica se è venduto da Amazon (opzionale, puoi rimuovere se non necessario)
            amazon_seller = any(seller in page_text for seller in [
                "venduto da amazon",
                "spedito da amazon",
                "sold by amazon"
            ])
            
            if price <= PREZZO_MAX and is_available:
                logger.info(f"Prodotto trovato: {title} - €{price}")
                return {
                    "title": title,
                    "price": price,
                    "url": url,
                    "amazon_seller": amazon_seller
                }
            else:
                logger.info(f"Prodotto non idoneo: prezzo €{price}, disponibile: {is_available}")
                
        except requests.RequestException as e:
            logger.error(f"Errore di rete per {url}: {e}")
        except Exception as e:
            logger.error(f"Errore nel parsing di {url}: {e}")
        
        return None
    
    def is_working_hours(self) -> bool:
        """Verifica se siamo negli orari di lavoro"""
        now = datetime.datetime.now()
        return ORARIO_INIZIO <= now.hour < ORARIO_FINE
    
    def monitor(self):
        """Funzione principale di monitoraggio"""
        logger.info("🚀 Avvio monitoraggio prezzi Amazon")
        logger.info(f"📊 Prodotti da monitorare: {len(LINKS)}")
        logger.info(f"💰 Prezzo massimo: €{PREZZO_MAX}")
        logger.info(f"🕐 Orario di lavoro: {ORARIO_INIZIO}:00 - {ORARIO_FINE}:00")
        
        notified = self.load_notified()
        consecutive_errors = 0
        max_errors = 5
        
        while True:
            try:
                now = datetime.datetime.now()
                
                if not self.is_working_hours():
                    logger.info(f"⏰ Fuori orario di lavoro ({now.strftime('%H:%M:%S')}). In pausa...")
                    time.sleep(CHECK_INTERVAL)
                    continue
                
                logger.info(f"🔍 [{now.strftime('%H:%M:%S')}] Controllo prodotti...")
                
                # Pulisci vecchie notifiche
                notified = self.clean_old_entries(notified)
                
                found_deals = 0
                
                for i, link in enumerate(LINKS, 1):
                    logger.info(f"📦 Controllo prodotto {i}/{len(LINKS)}")
                    
                    # Salta se già notificato nelle ultime 24h
                    if link in notified:
                        logger.info("⏭️ Già notificato nelle ultime 24h, salto")
                        continue
                    
                    result = self.parse_amazon(link)
                    
                    if result:
                        found_deals += 1
                        seller_info = "🏪 Amazon" if result['amazon_seller'] else "🏪 Terze parti"
                        
                        message = (
                            f"🎉 *OFFERTA TROVATA!*\n\n"
                            f"📦 *{result['title'][:100]}{'...' if len(result['title']) > 100 else ''}*\n\n"
                            f"💰 *Prezzo: €{result['price']:.2f}*\n"
                            f"{seller_info}\n\n"
                            f"🛒 [ACQUISTA ORA]({result['url']})\n\n"
                            f"⚡ _Monitoraggio automatico attivo_"
                        )
                        
                        if self.send_telegram_message(message):
                            notified[link] = time.time()
                            self.save_notified(notified)
                            logger.info("✅ Notifica inviata e salvata")
                        
                        # Delay tra le notifiche
                        time.sleep(2)
                
                if found_deals == 0:
                    logger.info("😴 Nessuna offerta trovata in questo ciclo")
                
                consecutive_errors = 0  # Reset errori consecutivi
                
            except KeyboardInterrupt:
                logger.info("🛑 Monitoraggio interrotto dall'utente")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"🚨 Errore generale: {e}")
                
                if consecutive_errors >= max_errors:
                    logger.error(f"💥 Troppi errori consecutivi ({max_errors}). Interruzione.")
                    break
            
            logger.info(f"⏳ Attesa {CHECK_INTERVAL} secondi...")
            time.sleep(CHECK_INTERVAL)

def main():
    """Funzione principale"""
    try:
        monitor = AmazonPriceMonitor()
        monitor.monitor()
    except Exception as e:
        logger.error(f"Errore critico: {e}")
        raise

if __name__ == "__main__":
    main()
