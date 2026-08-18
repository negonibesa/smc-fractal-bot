"""
Reporter — daily/weekly performance reports via Telegram.
Rolling PF, per-pair stats, drawdown, regime, param changes.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from collections import defaultdict

logger = logging.getLogger(__name__)


class Reporter:
    """Calculate and send performance reports."""

    def __init__(self, redis_store=None, notifier=None):
        self.redis = redis_store
        self.notifier = notifier
        self._last_optimize: Dict[str, datetime] = {}
        self._optimize_log: List[dict] = []

    def log_optimization(self, symbol: str, old_params: dict, new_params: dict):
        """Record an optimization event for weekly report."""
        self._last_optimize[symbol] = datetime.utcnow()
        self._optimize_log.append({
            'symbol': symbol,
            'time': datetime.utcnow().isoformat(),
            'changes': {k: (old_params.get(k), new_params.get(k))
                        for k in new_params if old_params.get(k) != new_params.get(k)},
        })

    # ─── METRICS ─────────────────────────────────────────────────

    def _load_trades(self, limit: int = 500) -> list:
        if not self.redis:
            return []
        return self.redis.load_closed_trades(limit=limit)

    def _trades_in_window(self, trades: list, since: datetime) -> list:
        since_ts = since.timestamp()
        return [t for t in trades if t.get('close_time', 0) >= since_ts]

    def _rolling_pf(self, trades: list, n: int = 20) -> Optional[float]:
        """Profit factor over last N trades."""
        recent = trades[-n:] if len(trades) >= n else trades
        if not recent:
            return None
        gross_profit = sum(t['pnl'] for t in recent if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in recent if t['pnl'] < 0))
        if gross_loss == 0:
            return 999.99
        return gross_profit / gross_loss

    def _rolling_wr(self, trades: list, n: int = 30) -> Optional[float]:
        recent = trades[-n:] if len(trades) >= n else trades
        if not recent:
            return None
        wins = sum(1 for t in recent if t['pnl'] > 0)
        return wins / len(recent)

    def _avg_r(self, trades: list, n: int = 30) -> Optional[float]:
        """Average R-multiple (pnl / risk_unit approximation)."""
        recent = trades[-n:] if len(trades) >= n else trades
        if not recent:
            return None
        rs = []
        for t in recent:
            entry = t.get('entry', 0)
            exit_p = t.get('exit_price', 0)
            if entry > 0 and exit_p > 0 and t.get('pnl', 0) != 0:
                risk = abs(entry - exit_p)
                if risk > 0:
                    rs.append(t['pnl'] / risk)
        return sum(rs) / len(rs) if rs else None

    def _max_drawdown(self, trades: list) -> float:
        """Max drawdown in % from equity curve of trade PnLs."""
        if not trades:
            return 0.0
        equity = 10000.0
        peak = equity
        max_dd = 0.0
        for t in trades:
            equity += t.get('pnl', 0)
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    def _max_drawdown_window(self, trades: list, since: datetime) -> float:
        window = self._trades_in_window(trades, since)
        return self._max_drawdown(window)

    def _per_pair_stats(self, trades: list) -> Dict[str, dict]:
        by_pair = defaultdict(list)
        for t in trades:
            by_pair[t.get('symbol', '?')].append(t)

        stats = {}
        for sym, pair_trades in by_pair.items():
            total = len(pair_trades)
            wins = sum(1 for t in pair_trades if t['pnl'] > 0)
            gross_p = sum(t['pnl'] for t in pair_trades if t['pnl'] > 0)
            gross_l = abs(sum(t['pnl'] for t in pair_trades if t['pnl'] < 0))
            pf = gross_p / gross_l if gross_l > 0 else 999.99
            stats[sym] = {
                'trades': total,
                'wins': wins,
                'losses': total - wins,
                'wr': wins / total if total > 0 else 0,
                'pf': pf,
                'pnl': sum(t.get('pnl', 0) for t in pair_trades),
            }
        return stats

    def _long_short_stats(self, trades: list) -> Dict[str, dict]:
        by_side = defaultdict(list)
        for t in trades:
            by_side[t.get('side', '?')].append(t)
        stats = {}
        for side, side_trades in by_side.items():
            total = len(side_trades)
            wins = sum(1 for t in side_trades if t['pnl'] > 0)
            stats[side] = {
                'trades': total,
                'wr': wins / total if total > 0 else 0,
                'pnl': sum(t.get('pnl', 0) for t in side_trades),
            }
        return stats

    def _avg_duration(self, trades: list) -> Optional[float]:
        """Average trade duration in hours."""
        durations = []
        for t in trades:
            et = t.get('entry_time', 0)
            ct = t.get('close_time', 0)
            if et > 0 and ct > 0 and ct > et:
                durations.append((ct - et) / 3600)
        return sum(durations) / len(durations) if durations else None

    def _time_in_position(self, trades: list, window_seconds: float = 86400) -> float:
        """% of time any position was open in the window."""
        now = time.time()
        window_start = now - window_seconds
        total_seconds = 0
        for t in trades:
            et = max(t.get('entry_time', 0), window_start)
            ct = min(t.get('close_time', now), now)
            if ct > et:
                total_seconds += (ct - et)
        return (total_seconds / window_seconds * 100) if window_seconds > 0 else 0

    def _total_equity_pnl(self, trades: list) -> float:
        return sum(t.get('pnl', 0) for t in trades)

    def _daily_pnl(self, trades: list, today: datetime) -> float:
        today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
        window = self._trades_in_window(trades, today_start)
        return sum(t.get('pnl', 0) for t in window)

    # ─── DAILY REPORT ────────────────────────────────────────────

    def build_daily_report(self, equity: float, open_positions: Dict,
                           regime_state: Dict = None) -> str:
        """Build daily report string (Grok spec)."""
        now = datetime.utcnow()
        trades = self._load_trades(500)

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        trades_today = self._trades_in_window(trades, today_start)
        n_today = len(trades_today)
        wins_today = sum(1 for t in trades_today if t['pnl'] > 0)
        losses_today = n_today - wins_today
        pnl_today = sum(t.get('pnl', 0) for t in trades_today)

        rolling_pf = self._rolling_pf(trades, 20)
        peak = max((t.get('close_time', 0) for t in trades), default=0)
        drawdown = 0.0
        if trades:
            eq = 10000.0
            pk = eq
            for t in trades:
                eq += t.get('pnl', 0)
                pk = max(pk, eq)
            drawdown = (pk - eq) / pk * 100 if pk > 0 else 0

        # Open positions detail
        pos_lines = []
        for sym, pos in open_positions.items():
            side_emoji = "🟢" if pos.get('side') == 'LONG' else "🔴"
            pos_lines.append(f"  {side_emoji} {sym} {pos.get('side')} "
                             f"@ {pos.get('entry_price', 0):.2f}")

        # Regime
        regime_line = ""
        if regime_state:
            for sym, rs in regime_state.items():
                r = rs.get('regime', '?') if isinstance(rs, dict) else str(rs)
                emoji_r = {'bull': '🟢', 'bear': '🔴', 'sideways': '🟡'}.get(r, '⚪')
                adx = rs.get('adx', 0) if isinstance(rs, dict) else 0
                regime_line += f"  {sym}: {emoji_r} {r} (ADX {adx:.0f})\n"

        pf_status = ""
        if rolling_pf is not None:
            if rolling_pf >= 1.6:
                pf_status = "🟢 healthy"
            elif rolling_pf >= 1.3:
                pf_status = "🟡 attention"
            else:
                pf_status = "🔴 degraded"

        last_opt = "never"
        for sym, dt in self._last_optimize.items():
            last_opt = dt.strftime('%Y-%m-%d')

        text = (
            f"📊 <b>DAILY REPORT — {now.strftime('%Y-%m-%d')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Equity: <code>${equity:,.2f}</code>\n"
            f"📈 Daily PnL: <code>${pnl_today:+.2f}</code>\n"
            f"🔄 Trades today: {n_today}  (W:{wins_today} / L:{losses_today})\n"
            f"📉 Current DD: {drawdown:.1f}%\n"
            f"📊 Rolling PF (20): {f'{rolling_pf:.2f}' if rolling_pf is not None else 'N/A'} {pf_status}\n"
        )

        if open_positions:
            text += f"\n📦 <b>Open Positions ({len(open_positions)})</b>\n"
            text += "\n".join(pos_lines) + "\n"
        else:
            text += "\n📦 Open Positions: none\n"

        if regime_line:
            text += f"\n🌐 <b>Regime</b>\n{regime_line}"

        text += (
            f"\n⚙️ Last optimization: {last_opt}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {now.strftime('%H:%M UTC')}"
        )
        return text

    def send_daily(self, equity: float, open_positions: Dict,
                   regime_state: Dict = None):
        """Build and send daily report."""
        text = self.build_daily_report(equity, open_positions, regime_state)
        if self.notifier:
            self.notifier._send(text)
        logger.info("Daily report sent")
        return text

    # ─── WEEKLY REPORT ───────────────────────────────────────────

    def build_weekly_report(self, equity: float, open_positions: Dict,
                            regime_state: Dict = None) -> str:
        """Build weekly report string (Grok spec)."""
        now = datetime.utcnow()
        week_start = now - timedelta(days=now.weekday())  # Monday
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

        trades = self._load_trades(500)
        weekly_trades = self._trades_in_window(trades, week_start)

        n = len(weekly_trades)
        wins = sum(1 for t in weekly_trades if t['pnl'] > 0)
        losses = n - wins
        pnl = sum(t.get('pnl', 0) for t in weekly_trades)
        wr = wins / n * 100 if n > 0 else 0

        rolling_pf = self._rolling_pf(trades, 30)
        rolling_wr = self._rolling_wr(trades, 30)
        avg_r = self._avg_r(trades, 30)
        avg_dur = self._avg_duration(weekly_trades)
        max_dd_week = self._max_drawdown_window(trades, week_start)
        max_dd_total = self._max_drawdown(trades)

        # Per-pair
        pair_stats = self._per_pair_stats(weekly_trades)
        # Long/Short
        ls_stats = self._long_short_stats(weekly_trades)
        # Time in position
        time_pct = self._time_in_position(weekly_trades, 7 * 86400)

        # Optimize count this week
        opt_count = sum(1 for sym, dt in self._last_optimize.items()
                        if dt >= week_start)
        # Param changes
        changes = [o for o in self._optimize_log
                   if datetime.fromisoformat(o['time']) >= week_start]

        # Regime
        regime_line = ""
        if regime_state:
            for sym, rs in regime_state.items():
                r = rs.get('regime', '?') if isinstance(rs, dict) else str(rs)
                emoji_r = {'bull': '🟢', 'bear': '🔴', 'sideways': '🟡'}.get(r, '⚪')
                adx = rs.get('adx', 0) if isinstance(rs, dict) else 0
                regime_line += f"  {sym}: {emoji_r} {r} (ADX {adx:.0f})\n"

        text = (
            f"📊 <b>WEEKLY REPORT — {week_start.strftime('%b %d')} → {now.strftime('%b %d')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Equity: <code>${equity:,.2f}</code>\n"
            f"📈 Weekly Return: <code>${pnl:+.2f}</code>\n"
            f"🔄 Trades: {n}  (W:{wins} / L:{losses})\n"
            f"📊 Win Rate: {wr:.0f}%\n"
            f"📊 Rolling PF (30): {f'{rolling_pf:.2f}' if rolling_pf is not None else 'N/A'}\n"
            f"📊 Rolling WR (30): {f'{rolling_wr:.0%}' if rolling_wr is not None else 'N/A'}\n"
            f"📐 Avg R: {f'{avg_r:.2f}' if avg_r is not None else 'N/A'}\n"
            f"📉 Max DD week: {max_dd_week:.1f}%\n"
            f"📉 Max DD total: {max_dd_total:.1f}%\n"
            f"⏱ Avg duration: {avg_dur:.1f}h\n"
            f"⏳ Time in position: {time_pct:.0f}%\n"
        )

        # Per-pair
        if pair_stats:
            text += "\n🪙 <b>Per Pair</b>\n"
            for sym, ps in pair_stats.items():
                text += (f"  {sym}: PF={ps['pf']:.2f} WR={ps['wr']:.0%} "
                         f"T={ps['trades']} PnL=${ps['pnl']:+.2f}\n")

        # Long/Short
        if ls_stats:
            text += "\n↕️ <b>Long vs Short</b>\n"
            for side, ss in ls_stats.items():
                emoji = "🟢" if side == "LONG" else "🔴"
                text += (f"  {emoji} {side}: T={ss['trades']} "
                         f"WR={ss['wr']:.0%} PnL=${ss['pnl']:+.2f}\n")

        if regime_line:
            text += f"\n🌐 <b>Regime</b>\n{regime_line}"

        # Optimization
        text += (
            f"\n⚙️ Optimizations this week: {opt_count}\n"
        )
        if changes:
            text += "  Changes:\n"
            for c in changes:
                for param, (old, new) in c['changes'].items():
                    text += f"    {c['symbol']}.{param}: {old} → {new}\n"

        text += (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {now.strftime('%H:%M UTC')}"
        )
        return text

    def send_weekly(self, equity: float, open_positions: Dict,
                    regime_state: Dict = None):
        """Build and send weekly report."""
        text = self.build_weekly_report(equity, open_positions, regime_state)
        if self.notifier:
            self.notifier._send(text)
        logger.info("Weekly report sent")
        return text

    # ─── ON-DEMAND ───────────────────────────────────────────────

    def build_status_report(self, equity: float, open_positions: Dict,
                            risk_status: Dict = None,
                            regime_state: Dict = None) -> str:
        """Quick status on /report command."""
        now = datetime.utcnow()
        trades = self._load_trades(500)
        total_trades = len(trades)
        rolling_pf = self._rolling_pf(trades, 20)
        total_pnl = sum(t.get('pnl', 0) for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)

        dd = 0.0
        if trades:
            eq = 10000.0
            pk = eq
            for t in trades:
                eq += t.get('pnl', 0)
                pk = max(pk, eq)
            dd = (pk - eq) / pk * 100 if pk > 0 else 0

        regime_line = ""
        if regime_state:
            for sym, rs in regime_state.items():
                r = rs.get('regime', '?') if isinstance(rs, dict) else str(rs)
                emoji_r = {'bull': '🟢', 'bear': '🔴', 'sideways': '🟡'}.get(r, '⚪')
                adx = rs.get('adx', 0) if isinstance(rs, dict) else 0
                regime_line += f"  {sym}: {emoji_r} {r} (ADX {adx:.0f})\n"

        text = (
            f"📊 <b>BOT STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Equity: <code>${equity:,.2f}</code>\n"
            f"📈 Total PnL: <code>${total_pnl:+.2f}</code>\n"
            f"🔄 Total trades: {total_trades} (W:{wins} / L:{total_trades - wins})\n"
            f"📊 Rolling PF (20): {f'{rolling_pf:.2f}' if rolling_pf is not None else 'N/A'}\n"
            f"📉 Max DD: {dd:.1f}%\n"
        )
        if open_positions:
            text += f"\n📦 Open ({len(open_positions)}):\n"
            for sym, pos in open_positions.items():
                e = "🟢" if pos.get('side') == 'LONG' else "🔴"
                text += f"  {e} {sym} {pos.get('side')} @ {pos.get('entry_price', 0):.2f}\n"
        else:
            text += "\n📦 No open positions\n"

        if regime_line:
            text += f"\n🌐 Regime\n{regime_line}"

        text += f"━━━━━━━━━━━━━━━━━━\n⏰ {now.strftime('%H:%M UTC')}"
        return text
