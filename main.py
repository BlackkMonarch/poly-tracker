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

# Railway provides this automatically
PORT = int(os.environ.get("PORT", 8080))
# Get Railway's public domain from environment
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", None)

if not RAILWAY_DOMAIN:
    print("⚠️ WARNING: RAILWAY_PUBLIC_DOMAIN not set!")
    print("⚠️ Please add RAILWAY_PUBLIC_DOMAIN variable in Railway dashboard")
    RAILWAY_DOMAIN = "your-app.up.railway.app"

WEBHOOK_URL = f"https://{RAILWAY_DOMAIN}/{TELEGRAM_BOT_TOKEN}"

# Use Railway's persistent storage path
CONFIG_DIR = "/data"
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Global monitors dictionary {wallet: monitor_instance}
monitors = {}

# Queue for messages from WebSocket threads
message_queue = queue.Queue()

async def safe_reply(message, text, max_retries=3, **kwargs):
    """Send reply with retry logic"""
    for attempt in range(max_retries):
        try:
            return await message.reply_text(text, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"❌ Failed to send message after {max_retries} attempts: {e}")
                raise
            wait_time = (attempt + 1) * 2
            print(f"⚠️ Message send failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)

def ensure_config_dir():
    """Ensure the config directory exists"""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        print(f"✅ Config directory ensured: {CONFIG_DIR}")
    except Exception as e:
        print(f"⚠️  Could not create config dir: {e}")
        global CONFIG_FILE
        CONFIG_FILE = "config.json"
        print(f"⚠️  Falling back to: {CONFIG_FILE}")

def load_config():
    """Load tracked wallets from config file"""
    ensure_config_dir()
    
    local_config = "config.json"
    if os.path.exists(local_config) and not os.path.exists(CONFIG_FILE):
        print("📦 Migrating config from local to persistent storage...")
        try:
            import shutil
            shutil.copy2(local_config, CONFIG_FILE)
            print(f"✅ Config migrated to {CONFIG_FILE}")
        except Exception as e:
            print(f"⚠️  Could not migrate config: {e}")
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                print(f"📁 Config loaded from {CONFIG_FILE}")
                
                if "wallets" not in config:
                    config["wallets"] = []
                if "chat_ids" not in config:
                    config["chat_ids"] = []
                if "wallet_names" not in config:
                    config["wallet_names"] = {}
                    
                return config
        except Exception as e:
            print(f"❌ Error loading config: {e}")
            return {"wallets": [], "chat_ids": [], "wallet_names": {}}
    
    print(f"📝 Creating new config at {CONFIG_FILE}")
    return {"wallets": [], "chat_ids": [], "wallet_names": {}}

def save_config(config):
    """Save tracked wallets and chat IDs to config file"""
    ensure_config_dir()
    
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"💾 Config saved to {CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"❌ Error saving config: {e}")
        
        try:
            local_file = "config.json"
            with open(local_file, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"💾 Config saved locally as backup to {local_file}")
            return True
        except Exception as e2:
            print(f"❌ Failed to save backup: {e2}")
            return False

def format_trade_message(trade):
    """Format trade data for Telegram"""
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
    
    message = f"""
🔥 *NEW TRADE DETECTED!* 🔥

⚡ *Action:* {action}
📊 *Market:* {market_link}
🎯 *Outcome:* {outcome}
💰 *Size:* {size:.2f} shares
💵 *Price:* ${price:.4f}
💸 *Total:* ${total_value:.2f}

👤 *Wallet:* {wallet_display}
🔗 [View Transaction](https://polygonscan.com/tx/{tx_hash})

💡 *To copy:* Click market link above → {side} "{outcome}"
"""
    return message

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show welcome message"""
    config_info = f"\n💾 *Storage:* Using persistent storage at `/data`"
    
    welcome_message = f"""
🤖 *Polymarket Copy Trading Bot*

*Commands:*
/add [name] <wallet> - Add wallet to track with optional name
/remove <wallet> - Remove wallet
/list - Show tracked wallets
/status - Show monitoring status
/help - Show this message

*Examples:*
`/add luk 0xaED1f1F120C1aB95958719BEb984D5b2013cF0cD`
`/add 0xaED1f1F120C1aB95958719BEb984D5b2013cF0cD`
{config_info}
"""
    await safe_reply(update.message, welcome_message, parse_mode='Markdown')

async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a wallet to track"""
    print(f"📨 Received /add from user {update.effective_user.id}")
    
    if not context.args:
        await safe_reply(update.message, "❌ Please provide a wallet address\nExample: /add 0x... [optional name]")
        return
    
    wallet = context.args[0].strip().lower()
    if not wallet.startswith("0x"):
        wallet = "0x" + wallet
    
    wallet_name = None
    if len(context.args) > 1:
        wallet_name = " ".join(context.args[1:]).strip()
    
    config = load_config()
    
    chat_id = update.effective_chat.id
    if chat_id not in config.get("chat_ids", []):
        if "chat_ids" not in config:
            config["chat_ids"] = []
        config["chat_ids"].append(chat_id)
        print(f"✅ Added chat ID {chat_id} to config")
    
    if wallet in config["wallets"]:
        if wallet_name:
            config["wallet_names"][wallet] = wallet_name
            save_config(config)
            await safe_reply(update.message, f"✅ Updated name to: *{wallet_name}*\n✅ Monitoring is active!", parse_mode='Markdown')
        else:
            await safe_reply(update.message, f"⚠️ Already tracking: `{wallet}`\n✅ Monitoring is active!", parse_mode='Markdown')
        return
    
    config["wallets"].append(wallet)
    if wallet_name:
        if "wallet_names" not in config:
            config["wallet_names"] = {}
        config["wallet_names"][wallet] = wallet_name
    
    if save_config(config):
        await safe_reply(update.message, f"💾 Config saved to persistent storage")
    else:
        await safe_reply(update.message, f"⚠️ Config saved locally (persistent storage failed)")
    
    def on_trade(trade):
        """Callback when trade is detected"""
        message = format_trade_message(trade)
        print(f"🔥 Trade detected for {wallet[:10]}..., queuing message for chat {chat_id}")
        
        for cid in config.get("chat_ids", [chat_id]):
            message_queue.put((cid, message))
    
    monitor = PolymarketMonitor(wallet, on_trade)
    monitor.start()
    monitors[wallet] = monitor
    
    print(f"✅ Started monitoring {wallet[:10]}...")
    name_display = f" as *{wallet_name}*" if wallet_name else ""
    await safe_reply(update.message, f"✅ Now tracking: `{wallet}`{name_display}\n⚡ You'll receive instant alerts with clickable market links!", parse_mode='Markdown')

async def remove_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a wallet from tracking"""
    if not context.args:
        await safe_reply(update.message, "❌ Please provide a wallet address\nExample: /remove 0x...")
        return
    
    wallet = context.args[0].strip().lower()
    if not wallet.startswith("0x"):
        wallet = "0x" + wallet
    
    config = load_config()
    
    if wallet not in config["wallets"]:
        await safe_reply(update.message, f"⚠️ Not tracking: `{wallet}`", parse_mode='Markdown')
        return
    
    config["wallets"].remove(wallet)
    if wallet in config.get("wallet_names", {}):
        del config["wallet_names"][wallet]
    
    save_config(config)
    
    if wallet in monitors:
        monitors[wallet].stop()
        del monitors[wallet]
    
    await safe_reply(update.message, f"✅ Stopped tracking: `{wallet}`", parse_mode='Markdown')

async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tracked wallets"""
    config = load_config()
    
    if not config["wallets"]:
        await safe_reply(update.message, "🔭 No wallets being tracked\n\nUse /add [name] <wallet> to start tracking")
        return
    
    message = "📋 *Tracked Wallets:*\n\n"
    for i, wallet in enumerate(config["wallets"], 1):
        wallet_name = config.get("wallet_names", {}).get(wallet, None)
        if wallet_name:
            message += f"{i}. *{wallet_name}* 🟢\n   `{wallet}`\n\n"
        else:
            message += f"{i}. `{wallet}` 🟢\n\n"
    
    message += f"✅ All {len(config['wallets'])} wallet(s) are being monitored!"
    
    await safe_reply(update.message, message, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show monitoring status"""
    config = load_config()
    
    total = len(config["wallets"])
    active = len(monitors)
    
    storage_status = "✅ Using persistent storage" if os.path.exists("/data") else "⚠️ Using local storage"
    
    message = f"""
📊 *Monitoring Status*

👥 Tracked wallets: {total}
🟢 Active monitors: {active}
💾 Storage: {storage_status}
🌐 Mode: Webhook
🔗 Webhook URL: `{WEBHOOK_URL[:50]}...`

💡 Wallets persist across bot restarts!
"""
    
    await safe_reply(update.message, message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    await start(update, context)

async def process_message_queue(context: ContextTypes.DEFAULT_TYPE):
    """Process queued messages from WebSocket threads"""
    while not message_queue.empty():
        try:
            chat_id, message = message_queue.get_nowait()
            
            for attempt in range(3):
                try:
                    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"❌ Failed to send queued message after 3 attempts: {e}")
                    else:
                        await asyncio.sleep((attempt + 1) * 2)
                        
        except queue.Empty:
            break
        except Exception as e:
            print(f"❌ Error processing message queue: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    print(f"❌ Error occurred: {context.error}")
    
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An error occurred. Please try again in a moment."
            )
        except Exception as e:
            print(f"❌ Could not send error message to user: {e}")

async def post_init(app: Application):
    """Initialize bot after startup"""
    try:
        # Set bot commands menu
        commands = [
            BotCommand("start", "Show welcome message and commands"),
            BotCommand("add", "Add a wallet to track (with optional name)"),
            BotCommand("remove", "Remove a wallet from tracking"),
            BotCommand("list", "Show all tracked wallets"),
            BotCommand("status", "Show monitoring status"),
            BotCommand("help", "Show help message"),
        ]
        await app.bot.set_my_commands(commands)
        print("✅ Bot commands menu configured")
    except Exception as e:
        print(f"⚠️ Could not set bot commands (non-critical): {e}")

def main():
    """Start the bot"""
    print("=" * 60)
    print("POLYMARKET BOT - OPTIMIZED FOR RAILWAY")
    print("=" * 60)
    
    print(f"📂 Config path: {CONFIG_FILE}")
    print(f"📦 Persistent storage mounted: {os.path.exists('/data')}")
    print(f"📄 Config exists: {os.path.exists(CONFIG_FILE)}")
    print(f"🌐 Webhook URL: {WEBHOOK_URL}")
    print(f"🔌 Port: {PORT}")
    
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Error: Please set your Telegram Bot Token!")
        return
    
    print(f"🔑 Bot token: {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-5:]}")
    
    # Create HTTP client with optimized settings
    print("🔧 Creating HTTP client with optimized settings...")
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    
    # Create application
    app = Application.builder()\
        .token(TELEGRAM_BOT_TOKEN)\
        .request(request)\
        .build()
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Add command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_wallet))
    app.add_handler(CommandHandler("remove", remove_wallet))
    app.add_handler(CommandHandler("list", list_wallets))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_command))
    
    # Set up post-init for bot commands
    app.post_init = post_init
    
    # Load existing wallets and start monitoring
    config = load_config()
    print(f"📋 Found {len(config['wallets'])} wallets")
    
    if config["wallets"]:
        print("🔄 Restoring monitors...")
        
        for wallet in config["wallets"]:
            def make_callback(w):
                def on_trade(trade):
                    message = format_trade_message(trade)
                    print(f"🔥 Trade detected for {w[:10]}...")
                    for chat_id in config.get("chat_ids", []):
                        message_queue.put((chat_id, message))
                return on_trade
            
            monitor = PolymarketMonitor(wallet, make_callback(wallet))
            monitor.start()
            monitors[wallet] = monitor
            print(f"  ✅ {wallet[:10]}...")
    
    # Start message queue processor
    app.job_queue.run_repeating(process_message_queue, interval=2.0, first=1.0)
    
    print("🚀 Starting webhook server...")
    print("=" * 60)
    
    # Run webhook (NOT polling)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=WEBHOOK_URL,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()
