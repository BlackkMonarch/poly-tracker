import json
import os
import asyncio
import queue
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from polymarket_tracker import PolymarketMonitor

# YOUR TELEGRAM BOT TOKEN - Replace this with your actual token from BotFather
TELEGRAM_BOT_TOKEN = "8268755391:AAETur8_5id_EX8XMqdv9UnxC7tQutRMKqg"

# File to store tracked wallets
CONFIG_FILE = "config.json"

# Global monitors dictionary {wallet: monitor_instance}
monitors = {}

# Queue for messages from WebSocket threads
message_queue = queue.Queue()

def load_config():
    """Load tracked wallets from config file"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Ensure chat_ids field exists
                if "chat_ids" not in config:
                    config["chat_ids"] = []
                return config
        except:
            return {"wallets": [], "chat_ids": []}
    return {"wallets": [], "chat_ids": []}

def save_config(config):
    """Save tracked wallets and chat IDs to config file"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

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
    
    # Get eventSlug for correct URL (not market slug!)
    event_slug = trade.get("eventSlug", "")
    if event_slug:
        market_url = f"https://polymarket.com/event/{event_slug}"
        market_link = f"[{market[:80]}]({market_url})"
    else:
        # Fallback: search URL
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

👤 *Wallet:* `{wallet[:10]}...{wallet[-8:]}`
🔗 [View Transaction](https://polygonscan.com/tx/{tx_hash})

💡 *To copy:* Click market link above → {side} "{outcome}"
"""
    return message

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show welcome message"""
    welcome_message = """
🤖 *Polymarket Copy Trading Bot*

*Commands:*
/add <wallet> - Add wallet to track
/remove <wallet> - Remove wallet
/list - Show tracked wallets
/status - Show monitoring status
/help - Show this message

*Example:*
`/add 0xaED1f1F120C1aB95958719BEb984D5b2013cF0cD`
"""
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a wallet to track"""
    print(f"📨 Received /add from user {update.effective_user.id}")
    
    if not context.args:
        await update.message.reply_text("❌ Please provide a wallet address\nExample: /add 0x...")
        return
    
    wallet = context.args[0].strip().lower()
    if not wallet.startswith("0x"):
        wallet = "0x" + wallet
    
    # Load config
    config = load_config()
    
    # Save chat ID for future alerts
    chat_id = update.effective_chat.id
    if chat_id not in config.get("chat_ids", []):
        if "chat_ids" not in config:
            config["chat_ids"] = []
        config["chat_ids"].append(chat_id)
        save_config(config)
    
    # Check if already tracking
    if wallet in config["wallets"]:
        await update.message.reply_text(f"⚠️ Already tracking: `{wallet}`\n✅ Monitoring is active!", parse_mode='Markdown')
        return
    
    # Add to config
    config["wallets"].append(wallet)
    save_config(config)
    
    # Start monitoring this wallet
    def on_trade(trade):
        """Callback when trade is detected"""
        message = format_trade_message(trade)
        print(f"🔥 Trade detected for {wallet[:10]}..., queuing message for chat {chat_id}")
        
        # Queue message for all registered chats
        for cid in config.get("chat_ids", [chat_id]):
            message_queue.put((cid, message))
    
    monitor = PolymarketMonitor(wallet, on_trade)
    monitor.start()
    monitors[wallet] = monitor
    
    print(f"✅ Started monitoring {wallet[:10]}...")
    await update.message.reply_text(f"✅ Now tracking: `{wallet}`\n⚡ You'll receive instant alerts with clickable market links!", parse_mode='Markdown')

async def remove_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a wallet from tracking"""
    if not context.args:
        await update.message.reply_text("❌ Please provide a wallet address\nExample: /remove 0x...")
        return
    
    wallet = context.args[0].strip().lower()
    if not wallet.startswith("0x"):
        wallet = "0x" + wallet
    
    # Load config
    config = load_config()
    
    # Check if tracking
    if wallet not in config["wallets"]:
        await update.message.reply_text(f"⚠️ Not tracking: `{wallet}`", parse_mode='Markdown')
        return
    
    # Remove from config
    config["wallets"].remove(wallet)
    save_config(config)
    
    # Stop monitoring
    if wallet in monitors:
        monitors[wallet].stop()
        del monitors[wallet]
    
    await update.message.reply_text(f"✅ Stopped tracking: `{wallet}`", parse_mode='Markdown')

async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tracked wallets"""
    config = load_config()
    
    if not config["wallets"]:
        await update.message.reply_text("📭 No wallets being tracked\n\nUse /add <wallet> to start tracking")
        return
    
    message = "📋 *Tracked Wallets:*\n\n"
    for i, wallet in enumerate(config["wallets"], 1):
        # All wallets in config are considered active since they auto-restart
        message += f"{i}. `{wallet[:10]}...{wallet[-8:]}` 🟢\n"
    
    message += f"\n✅ All {len(config['wallets'])} wallet(s) are being monitored!"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show monitoring status"""
    config = load_config()
    
    total = len(config["wallets"])
    active = len(monitors)
    
    message = f"""
📊 *Monitoring Status*

👥 Tracked wallets: {total}
🟢 Active monitors: {active}

💡 All tracked wallets auto-restart when bot restarts!
"""
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    await start(update, context)

async def process_message_queue(context: ContextTypes.DEFAULT_TYPE):
    """Process queued messages from WebSocket threads - called every 0.5s"""
    # Process all queued messages in this run
    while not message_queue.empty():
        try:
            chat_id, message = message_queue.get_nowait()
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
        except queue.Empty:
            break
        except Exception as e:
            print(f"❌ Error sending message: {e}")

def main():
    """Start the bot"""
    print("=" * 60)
    print("POLYMARKET TELEGRAM BOT")
    print("=" * 60)
    
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Error: Please set your Telegram Bot Token!")
        print("1. Talk to @BotFather on Telegram")
        print("2. Create a new bot and get the token")
        print("3. Set it in the code or environment variable")
        return
    
    print(f"🔑 Bot token: {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-5:]}")
    
    # Create application
    print("🔧 Creating application...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    print("📝 Adding command handlers...")
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_wallet))
    app.add_handler(CommandHandler("remove", remove_wallet))
    app.add_handler(CommandHandler("list", list_wallets))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_command))
    
    # Load existing wallets and start monitoring
    config = load_config()
    print(f"📋 Loaded {len(config['wallets'])} wallets from config")
    print(f"📋 Loaded {len(config.get('chat_ids', []))} chat IDs")
    
    # Auto-restart monitoring for all saved wallets
    if config["wallets"] and config.get("chat_ids"):
        print("🔄 Restarting monitors for saved wallets...")
        
        for wallet in config["wallets"]:
            def make_callback(w):
                def on_trade(trade):
                    message = format_trade_message(trade)
                    print(f"🔥 Trade detected for {w[:10]}..., queuing for {len(config['chat_ids'])} chats")
                    # Send to all registered chats
                    for chat_id in config.get("chat_ids", []):
                        message_queue.put((chat_id, message))
                return on_trade
            
            monitor = PolymarketMonitor(wallet, make_callback(wallet))
            monitor.start()
            monitors[wallet] = monitor
            print(f"  ✅ Monitoring: {wallet[:10]}...")
    
    # Start message queue processor as a background job
    app.job_queue.run_repeating(process_message_queue, interval=0.5, first=0)
    print("✅ Message queue processor started")
    
    print("✅ Bot started! Waiting for messages...")
    print("💡 Send /start to the bot on Telegram to begin")
    print("🔍 Watching for incoming commands...")
    
    # Start the bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":

    main()
