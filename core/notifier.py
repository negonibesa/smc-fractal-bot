"""
Telegram Notifier —уведомления о входах, выходах, ошибках, отчёты
"""

import os
import time
import logging
import requests
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Отправка уведомлений в Telegram."""
    
    def __init__(self, bot_token: str = "", chat_id: str = "", enabled: bool = False):
        """
        Args:
            bot_token: Telegram Bot Token
            chat_id: Telegram Chat ID
            enabled: Включить уведомления
        """
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = enabled and self.bot_token and self.chat_id
        self.proxy = os.getenv("TELEGRAM_PROXY", "")
        self.proxies = {"https": self.proxy, "http": self.proxy} if self.proxy else None
        
        if self.enabled:
            proxy_info = f" (proxy={self.proxy[:30]}...)" if self.proxy else ""
            logger.info(f"Telegram notifications ON (chat={self.chat_id[:8]}...{proxy_info})")
        else:
            logger.info("Telegram notifications OFF")
    
    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Отправить сообщение."""
        if not self.enabled:
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=10, proxies=self.proxies)
            
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram send failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    
    def send_entry(self, symbol: str, direction: str, entry: float,
                   stop: float, tp: float, size: float):
        """Уведомление о входе."""
        emoji = "🟢" if direction == "BUY" else "🔴"
        risk = abs(entry - stop)
        rr = risk / abs(tp - entry) if abs(tp - entry) > 0 else 0
        
        text = (
            f"{emoji} <b>ENTRY — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Direction: <b>{direction}</b>\n"
            f"Entry: <code>{entry:.4f}</code>\n"
            f"Stop Loss: <code>{stop:.4f}</code>\n"
            f"Take Profit: <code>{tp:.4f}</code>\n"
            f"Size: <code>{size:.4f}</code>\n"
            f"Risk: {rr:.2f}R\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
        )
        return self._send(text)
    
    def send_exit(self, symbol: str, direction: str, entry: float,
                  exit_price: float, pnl: float, reason: str,
                  size: float = 0):
        """Уведомление о выходе."""
        emoji = "💰" if pnl > 0 else "💸"
        # Correct PnL %: pnl / notional_value * 100
        if size > 0 and entry > 0:
            pnl_pct = pnl / (entry * size) * 100
        else:
            pnl_pct = 0
        
        text = (
            f"{emoji} <b>EXIT — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Direction: <b>{direction}</b>\n"
            f"Entry: <code>{entry:.4f}</code>\n"
            f"Exit: <code>{exit_price:.4f}</code>\n"
            f"Reason: <b>{reason}</b>\n"
            f"PnL: <code>${pnl:+.2f}</code> ({pnl_pct:+.2f}%)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
        )
        return self._send(text)
    
    def send_daily_report(self, trades_today: int, wins: int, losses: int,
                          pnl_today: float, equity: float):
        """Дневной отчёт."""
        wr = wins / trades_today * 100 if trades_today > 0 else 0
        emoji = "📊"
        
        text = (
            f"{emoji} <b>DAILY REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
            f"Trades: {trades_today}\n"
            f"Wins/Losses: {wins}/{losses}\n"
            f"Win Rate: {wr:.0f}%\n"
            f"PnL: <code>${pnl_today:+.2f}</code>\n"
            f"Equity: <code>${equity:,.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        return self._send(text)
    
    def send_error(self, error: str, context: str = ""):
        """Уведомление об ошибке."""
        text = (
            f"⚠️ <b>ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Context: {context}\n"
            f"Error: <code>{error[:200]}</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
        )
        return self._send(text)
    
    def send_status(self, message: str):
        """Общее уведомление."""
        text = (
            f"ℹ️ <b>STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{message}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
        )
        return self._send(text)
    
    def send_start(self, symbols: list, testnet: bool):
        """Уведомление о старте."""
        mode = "🧪 TESTNET" if testnet else "🔴 MAINNET"
        text = (
            f"🚀 <b>BOT STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode}\n"
            f"Symbols: {', '.join(symbols)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
        )
        return self._send(text)
    
    def send_stop(self, reason: str = "Manual"):
        """Уведомление об остановке."""
        text = (
            f"🛑 <b>BOT STOPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Reason: {reason}\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
        )
        return self._send(text)
