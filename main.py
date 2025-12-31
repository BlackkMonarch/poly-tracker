import json
import os
import asyncio
import queue
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest
from polymarket_tracker import PolymarketMonitor

# YOUR TELEGRAM BOT TOKEN
TELEGRAM_BOT_TOKEN = "8268755391:AAETur8_5id_EX8XMqdv9UnxC7tQutRMKqg"

# Use Railway's persistent storage path
CONFIG_DIR = "/data"
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Global monitors dictionary
monitors = {}
message_queue = queue.Queue()

async def safe_reply(message, text, max_retries=2, **kwargs):
    """Send reply with retry logic - reduced retries for faster feedback"""
    for attempt in range(max_retries):
        try:
            return await message.reply_text(text, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"❌ Failed after {max_retries} attempts: {e}")
                # Don't raise - just log and continue
                return None
            await asyncio.sleep(2)

def ensure_config_dir():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except:
        global CONFIG_FILE
        CONFIG_FILE = "config.json"

def load_config():
    ensure_config_dir()
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                config.setdefault("wallets", [])
                config.setdefault("chat_ids", [])
                config.setdefault("wallet_names", {})
                return config
        except:
            pass
    
    return {"wallets": [], "chat_ids": [], "wallet_names": {}}

def save_config(config):
    ensure_config_dir()
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except:
        return False

def format_trade_message(trade):
    side = trade.get("side", "").upper()
    action = "🟢 BUY" if side == "BUY" else "🔴 SELL"
    
    price = float(trade.get("price", 0))
    size = float(trade.get("size", 0))
    usdc_size = float(trade.get("usdcSize", 0))
    total_value = usdc_size if usdc_size > 0 else (price * size)
    
    market = trade.get("title", "Unknown Market")
    outcome = trade.get("outcome", "Unknown")
    tx_hash = trade.get("transactionHash", "Unknown")
    wallet = trade.get("proxyWallet", "Unknown")
    
    config = load_config()
    wallet_name = config.get("wallet_names", {}).get(wallet.lower(), None)
    wallet_display = f"*{wallet_name}*" if wallet_name else f"`{wallet[:10]}...{wallet[-8:]}`"
    
    event_slug = trade.get("eventSlug", "")
    if event_slug:
        market_url = f"https://polymarket.com/event/{event_slug}"
        market_link = f"[{market[:80]}]({market_url})"
    else:
        import urllib.parse
        search_query = urllib.parse.quote(market[:50])
        market_url = f"https://polymarket.com/search?q={search_query}"
        market_link = f"[{market[:80]}]({market_url})"
    
    return f"""🔥 *NEW TRADE* 🔥

⚡ {action}
📊 {market_link}
🎯 {outcome}
💰 {size:.2f} shares @ ${price:.4f}
💸 Total: ${total_value:.2f}

👤 {wallet_display}
🔗 [TX](https://polygonscan.com/tx/{tx_hash})
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update.message, """🤖 *Polymarket Copy Bot*

/add [name] <wallet> - Track wallet
/remove <wallet> - Stop tracking
/list - Show wallets
/status - Bot status

Example: `/add luk 0xaED1...`""", parse_mode='Markdown')

async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"📨 /add from {update.effective_user.id}")
    
    if not context.args:
        await safe_reply(update.message, "❌ Usage: /add 0x...")
        return
    
    wallet = context.args[0].strip().lower()
    if not wallet.startswith("0x"):
        wallet = "0x" + wallet
    
    wallet_name = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None
    
    config = load_config()
    chat_id = update.effective_chat.id
    
    if chat_id not in config.get("chat_ids", []):
        config.setdefault("chat_ids", []).append(chat_id)
    
    if wallet in config["wallets"]:
        if wallet_name:
            config["wallet_names"][wallet] = wallet_name
            save_config(config)
            await safe_reply(update.message, f"✅ Updated: *{wallet_name}*", parse_mode='Markdown')
        else:
            await safe_reply(update.message, f"⚠️ Already tracking", parse_mode='Markdown')
        return
    
    config["wallets"].append(wallet)
    if wallet_name:
        config.setdefault("wallet_names", {})[wallet] = wallet_name
    
    save_config(config)
    
    def on_trade(trade):
        msg = format_trade_message(trade)
        for cid in config.get("chat_ids", [chat_id]):
            message_queue.put((cid, msg))
    
    monitor = PolymarketMonitor(wallet, on_trade)
    monitor.start()
    monitors[wallet] = monitor
    
    name_txt = f" (*{wallet_name}*)" if wallet_name else ""
    await safe_reply(update.message, f"✅ Tracking{name_txt}\n`{wallet[:10]}...`", parse_mode='Markdown')

async def remove_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update.message, "❌ Usage: /remove 0x...")
        return
    
    wallet = context.args[0].strip().lower()
    if not wallet.startswith("0x"):
        wallet = "0x" + wallet
    
    config = load_config()
    
    if wallet not in config["wallets"]:
        await safe_reply(update.message, "⚠️ Not tracking this wallet")
        return
    
    config["wallets"].remove(wallet)
    config.get("wallet_names", {}).pop(wallet, None)
    save_config(config)
    
    if wallet in monitors:
        monitors[wallet].stop()
        del monitors[wallet]
    
    await safe_reply(update.message, "✅ Stopped tracking")

async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    
    if not config["wallets"]:
        await safe_reply(update.message, "📭 No wallets\n\nUse /add <wallet>")
        return
    
    msg = "📋 *Tracking:*\n\n"
    for i, wallet in enumerate(config["wallets"], 1):
        name = config.get("wallet_names", {}).get(wallet)
        if name:
            msg += f"{i}. *{name}* 🟢\n`{wallet[:10]}...`\n\n"
        else:
            msg += f"{i}. `{wallet[:10]}...` 🟢\n\n"
    
    await safe_reply(update.message, msg, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    storage = "✅ /data" if os.path.exists("/data") else "⚠️ local"
    
    await safe_reply(update.message, f"""📊 *Status*

Tracked: {len(config["wallets"])}
Active: {len(monitors)}
Storage: {storage}
""", parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def process_message_queue(context: ContextTypes.DEFAULT_TYPE):
    """Process trade notifications from queue"""
    processed = 0
    max_per_cycle = 5  # Limit to avoid blocking
    
    while not message_queue.empty() and processed < max_per_cycle:
        try:
            chat_id, message = message_queue.get_nowait()
            processed += 1
            
            # Single retry for trade notifications
            try:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=message, 
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
            except Exception as e:
                print(f"⚠️ Failed to send trade alert: {e}")
                # Put back in queue to retry later
                message_queue.put((chat_id, message))
                
        except queue.Empty:
            break
        except Exception as e:
            print(f"❌ Queue error: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors without crashing"""
    import traceback
    print(f"❌ Error: {context.error}")
    print(traceback.format_exc())

async def post_init(app: Application):
    """Try to set commands, but don't fail if it times out"""
    print("⏳ Setting up bot commands...")
    try:
        # Use very aggressive timeout
        await asyncio.wait_for(
            app.bot.set_my_commands([
                BotCommand("start", "Show help"),
                BotCommand("add", "Add wallet"),
                BotCommand("remove", "Remove wallet"),
                BotCommand("list", "List wallets"),
                BotCommand("status", "Show status"),
            ]),
            timeout=5.0  # Only 5 seconds
        )
        print("✅ Commands set")
    except asyncio.TimeoutError:
        print("⚠️ Command setup timeout (continuing anyway)")
    except Exception as e:
        print(f"⚠️ Command setup failed: {e} (continuing anyway)")

def main():
    print("=" * 60)
    print("POLYMARKET BOT - OPTIMIZED FOR RAILWAY")
    print("=" * 60)
    
    # Very aggressive timeout settings
    print("🔧 Creating HTTP client with optimized settings...")
    request = HTTPXRequest(
        connection_pool_size=4,  # Reduced pool size
        connect_timeout=45.0,
        read_timeout=45.0,
        write_timeout=45.0,
        pool_timeout=45.0
    )
    
    app = Application.builder()\
        .token(TELEGRAM_BOT_TOKEN)\
        .request(request)\
        .get_updates_request(request)\
        .build()
    
    # Register handlers
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_wallet))
    app.add_handler(CommandHandler("remove", remove_wallet))
    app.add_handler(CommandHandler("list", list_wallets))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_command))
    
    # Post-init for commands (non-blocking)
    app.post_init = post_init
    
    # Restore monitors
    config = load_config()
    print(f"📋 Found {len(config['wallets'])} wallets")
    
    if config["wallets"] and config.get("chat_ids"):
        print("🔄 Restoring monitors...")
        for wallet in config["wallets"]:
            def make_callback(w):
                def on_trade(trade):
                    for chat_id in config.get("chat_ids", []):
                        message_queue.put((chat_id, format_trade_message(trade)))
                return on_trade
            
            try:
                monitor = PolymarketMonitor(wallet, make_callback(wallet))
                monitor.start()
                monitors[wallet] = monitor
                print(f"  ✅ {wallet[:10]}...")
            except Exception as e:
                print(f"  ❌ Failed to start monitor for {wallet[:10]}: {e}")
    
    # Message queue processor - check every 2 seconds
    app.job_queue.run_repeating(process_message_queue, interval=2.0, first=1.0)
    print("✅ Message queue processor started")
    
    print("=" * 60)
    print("🚀 Starting bot with polling...")
    print("💡 This may take 30-60 seconds on first start")
    print("=" * 60)
    
    # Start polling with optimized settings
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # Ignore old messages
        poll_interval=2.0,  # Check every 2 seconds
        timeout=30,  # 30 second long polling
        bootstrap_retries=3,  # Only retry 3 times on startup
        read_timeout=45,
        write_timeout=45,
        connect_timeout=45,
        pool_timeout=45
    )

if __name__ == "__main__":
    main()
