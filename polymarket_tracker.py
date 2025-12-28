import json
import threading
from websocket import WebSocketApp

class PolymarketMonitor:
    """Monitor a Polymarket wallet for real-time trades"""
    
    def __init__(self, wallet_address, on_trade_callback):
        """
        Initialize monitor
        
        Args:
            wallet_address: The wallet address to monitor (0x...)
            on_trade_callback: Function to call when trade detected, receives trade dict
        """
        self.wallet = wallet_address.lower()
        self.on_trade = on_trade_callback
        self.ws_url = "wss://ws-live-data.polymarket.com"
        self.ws_app = None
        self.connected = False
        self.running = False
        self.seen_trades = set()
        self.first_message_logged = False
        
    def start(self):
        """Start monitoring"""
        if self.running:
            return
        
        self.running = True
        self._connect_websocket()
        
    def stop(self):
        """Stop monitoring"""
        self.running = False
        self.connected = False
        
        if self.ws_app:
            self.ws_app.close()
            
    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            
            # Log first message only
            if not self.first_message_logged:
                print(f"✅ Monitor connected for {self.wallet[:10]}...")
                self.first_message_logged = True
            
            # Check for trades in 'payload' field
            if "payload" in data:
                payload = data["payload"]
                
                # Handle list or single trade
                if isinstance(payload, list):
                    trades = payload
                elif isinstance(payload, dict):
                    trades = [payload]
                else:
                    return
                
                for trade in trades:
                    # Check if trade is from our wallet
                    proxy_wallet = trade.get("proxyWallet", "").lower()
                    
                    if proxy_wallet == self.wallet:
                        trade_id = trade.get("transactionHash") or trade.get("id")
                        
                        if trade_id and trade_id not in self.seen_trades:
                            self.seen_trades.add(trade_id)
                            print(f"🔥 Trade detected from {self.wallet[:10]}...")
                            # Call the callback
                            self.on_trade(trade)
            
            # Alternative: trades directly in data
            elif "trades" in data:
                for trade in data["trades"]:
                    proxy_wallet = trade.get("proxyWallet", "").lower()
                    
                    if proxy_wallet == self.wallet:
                        trade_id = trade.get("transactionHash") or trade.get("id")
                        
                        if trade_id and trade_id not in self.seen_trades:
                            self.seen_trades.add(trade_id)
                            self.on_trade(trade)
            
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"⚠️ Error processing message for {self.wallet[:10]}...: {e}")
    
    def _on_error(self, ws, error):
        """Handle WebSocket errors"""
        print(f"⚠️ WebSocket error for {self.wallet[:10]}...: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close"""
        self.connected = False
        
        if self.running:
            print(f"⚠️ Connection closed for {self.wallet[:10]}..., reconnecting...")
            import time
            time.sleep(5)
            self._connect_websocket()
    
    def _on_open(self, ws):
        """Handle WebSocket connection open"""
        self.connected = True
        
        # Subscribe to activity/trades feed
        subscription = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": "activity",
                    "type": "trades"
                }
            ]
        }
        
        ws.send(json.dumps(subscription))
        
        # Keep connection alive with pings
        def ping_thread():
            import time
            while self.connected and self.running:
                try:
                    time.sleep(5)
                    if self.connected and self.running:
                        ping_msg = {"action": "ping"}
                        ws.send(json.dumps(ping_msg))
                except:
                    break
        
        threading.Thread(target=ping_thread, daemon=True).start()
    
    def _connect_websocket(self):
        """Connect to Polymarket RTDS WebSocket"""
        self.ws_app = WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        
        # Run in separate thread
        ws_thread = threading.Thread(target=self.ws_app.run_forever)
        ws_thread.daemon = True
        ws_thread.start()