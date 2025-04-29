import os
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time
from dotenv import load_dotenv

# Initialization
load_dotenv()

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
BUBBLEMAPS_API_URL = "https://api-legacy.bubblemaps.io/map-data"
COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPPORTED_CHAINS = ['eth', 'bsc', 'ftm', 'avax', 'cro', 'arbi', 'poly', 'base', 'sol', 'sonic']

# Global objects for reuse
CHROME_OPTIONS = Options()
CHROME_OPTIONS.add_argument("--headless=new")
CHROME_OPTIONS.add_argument("--no-sandbox")
CHROME_OPTIONS.add_argument("--disable-dev-shm-usage")
CHROME_OPTIONS.add_argument("--window-size=1200,800")
CHROME_OPTIONS.add_argument("--disable-gpu")
CHROME_OPTIONS.add_argument("--disable-extensions")
CHROME_OPTIONS.add_argument("--blink-settings=imagesEnabled=false")

# Thread pool for blocking operations
IO_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# Selenium driver
DRIVER = None

def init_driver():
    global DRIVER
    if DRIVER is None:
        service = Service(ChromeDriverManager().install())
        DRIVER = webdriver.Chrome(service=service, options=CHROME_OPTIONS)
        DRIVER.set_page_load_timeout(20)
    return DRIVER

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    await update.message.reply_text(
        "🔍 *Bubblemaps Bot* 🔍\n\n"
        "Main features:\n"
        "1. Generates token bubble map screenshot\n"
        "2. Provides detailed token information\n"
        "3. Analyzes distribution decentralization\n"
        "4. Shows market data (if available)\n\n"
        "Send me token contract address in format:\n"
        "`<address> <chain>`\n\n"
        "Example: `0x603c7f932ED1fc6575303D8Fb018fDCBb0f39a95 bsc`\n"
        "Or for Solana: `EjpUeZQ3xT2Q35b9t5uAqxcJq1QqykZzBJbJxDoX1eK sol`\n\n"
        f"Supported chains: {', '.join(SUPPORTED_CHAINS)}",
        parse_mode='Markdown'
    )

async def get_token_data(contract_address: str, chain: str) -> dict:
    """Get token data from Bubblemaps API"""
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            IO_EXECUTOR,
            lambda: requests.get(
                BUBBLEMAPS_API_URL,
                params={'token': contract_address, 'chain': chain},
                timeout=5
            )
        )
        
        if response.status_code == 401:
            logger.warning(f"Map not computed for {contract_address} on {chain}")
            return None
        elif response.status_code == 404:
            logger.warning(f"Token not found: {contract_address} on {chain}")
            return None
            
        response.raise_for_status()
        
        data = response.json()
        data.update({
            'token_address': contract_address,
            'symbol': data.get('symbol', 'N/A'),
            'full_name': data.get('full_name', 'N/A')
        })
            
        return data
    except Exception as e:
        logger.error(f"API request error: {e}")
        return None

async def get_market_data(contract_address: str, chain: str) -> dict:
    """Get market data from CoinGecko API"""
    if chain == 'sol':
        return None
        
    chain_map = {
        'eth': 'ethereum',
        'bsc': 'binance-smart-chain',
        'ftm': 'fantom',
        'avax': 'avalanche',
        'poly': 'polygon-pos',
        'arbi': 'arbitrum-one',
        'base': 'base',
        'cro': 'cronos'
    }
    
    if chain not in chain_map:
        return None

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            IO_EXECUTOR,
            lambda: requests.get(
                f"{COINGECKO_API_URL}/coins/{chain_map[chain]}/contract/{contract_address}",
                timeout=5
            )
        )
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        return {
            'price': data.get('market_data', {}).get('current_price', {}).get('usd'),
            'market_cap': data.get('market_data', {}).get('market_cap', {}).get('usd'),
            'volume': data.get('market_data', {}).get('total_volume', {}).get('usd'),
            'price_change_24h': data.get('market_data', {}).get('price_change_percentage_24h')
        }
    except Exception as e:
        logger.error(f"Market data error: {e}")
        return None

async def generate_screenshot(contract_address: str, chain: str) -> str:
    """Generate Bubblemaps page screenshot"""
    try:
        loop = asyncio.get_event_loop()
        screenshot_path = f"temp_{contract_address}.png"
        
        def _take_screenshot():
            driver = init_driver()
            driver.get(f"https://app.bubblemaps.io/{chain}/token/{contract_address}")
            time.sleep(4)
            driver.save_screenshot(screenshot_path)
            return screenshot_path
            
        return await loop.run_in_executor(IO_EXECUTOR, _take_screenshot)
    except Exception as e:
        logger.error(f"Screenshot error: {e}")
        return None

def analyze_decentralization(nodes: list) -> dict:
    """Analyze decentralization level"""
    if not nodes or len(nodes) < 3:
        return {'score': 0, 'description': 'Insufficient data for analysis'}
    
    top10_percentage = sum(node.get('percentage', 0) for node in nodes[:10])
    
    if top10_percentage > 90: return {'score': 1, 'description': 'Very low decentralization'}
    elif top10_percentage > 70: return {'score': 2, 'description': 'Low decentralization'}
    elif top10_percentage > 50: return {'score': 3, 'description': 'Medium decentralization'}
    elif top10_percentage > 30: return {'score': 4, 'description': 'High decentralization'}
    return {'score': 5, 'description': 'Very high decentralization'}

async def handle_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle contract address from user"""
    text = update.message.text.strip().split()
    if len(text) != 2 or text[1].lower() not in SUPPORTED_CHAINS:
        await update.message.reply_text(
            "❌ Invalid format. Use:\n`<address> <chain>`\n\n"
            f"Supported chains: {', '.join(SUPPORTED_CHAINS)}",
            parse_mode='Markdown'
        )
        return
    
    contract_address, chain = text
    chain = chain.lower()
    logger.info(f"Processing contract: {contract_address} on {chain}")
    
    await update.message.reply_text("🔄 Processing request...")
    
    try:
        # Parallel execution of requests
        token_data, market_data, screenshot_path = await asyncio.gather(
            get_token_data(contract_address, chain),
            get_market_data(contract_address, chain) if chain != 'sol' else asyncio.sleep(0),
            generate_screenshot(contract_address, chain)
        )
        
        if not token_data:
            await update.message.reply_text("❌ Map not computed or error occurred.")
            return
            
        # Form response
        response = [
            f"📊 *{token_data.get('full_name', 'N/A')} ({token_data.get('symbol', 'N/A')})*",
            f"",
            f"• Network: {chain.upper()}",
            f"• Address: `{token_data.get('token_address')}`",
            f"• Updated: {token_data.get('dt_update', 'N/A')}"
        ]
        
        if market_data:
            response.extend([
                f"",
                f"💹 *Market Data:*",
                f"• Price: ${market_data.get('price', 'N/A'):,.6f}",
                f"• Market Cap: ${market_data.get('market_cap', 'N/A'):,.2f}",
                f"• Volume (24h): ${market_data.get('volume', 'N/A'):,.2f}",
                f"• Price Change (24h): {market_data.get('price_change_24h', 'N/A'):.2f}%"
            ])
        
        if 'nodes' in token_data and token_data['nodes']:
            holder = token_data['nodes'][0]
            decentralization = analyze_decentralization(token_data['nodes'])
            response.extend([
                f"",
                f"🏆 *Top Holder:*",
                f"• Address: `{holder['address']}`",
                f"• Percentage: {holder.get('percentage', 0):.2f}%",
                f"",
                f"🔍 *Decentralization Analysis:*",
                f"• Score: {decentralization['score']}/5",
                f"• Description: {decentralization['description']}"
            ])
        
        # Send result
        if screenshot_path:
            try:
                with open(screenshot_path, 'rb') as photo:
                    await update.message.reply_photo(photo, caption="\n".join(response), parse_mode='Markdown')
            finally:
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
        else:
            await update.message.reply_text("\n".join(response), parse_mode='Markdown', disable_web_page_preview=True)
            
    except Exception as e:
        logger.error(f"Processing error: {e}")
        await update.message.reply_text("❌ Error processing request. Please try again later.")

async def on_shutdown(app: Application):
    """Cleanup resources on shutdown"""
    global DRIVER
    if DRIVER is not None:
        DRIVER.quit()
        DRIVER = None
    IO_EXECUTOR.shutdown(wait=True)

def main():
    """Start the bot"""
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is missing")
    
    app = Application.builder().token(TOKEN).post_shutdown(on_shutdown).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_contract))
    
    app.run_polling()

if __name__ == '__main__':
    main()