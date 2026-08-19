"""
Dashboard — Flask web dashboard for bot monitoring.
Serves on port 80 inside the container.
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify

logger = logging.getLogger(__name__)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMC Fractal Bot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0e0;font-family:'SF Mono',Consolas,'Courier New',monospace;font-size:13px;padding:16px}
h1{color:#00ff88;font-size:20px;margin-bottom:4px}
.subtitle{color:#666;font-size:11px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin-bottom:16px}
.card{background:#111118;border:1px solid #222;border-radius:8px;padding:14px}
.card h2{color:#00ff88;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;border-bottom:1px solid #222;padding-bottom:6px}
.row{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #1a1a1a}
.row:last-child{border-bottom:none}
.label{color:#888}
.val{color:#fff;font-weight:bold}
.val.green{color:#00ff88}
.val.red{color:#ff4444}
.val.yellow{color:#ffaa00}
.val.blue{color:#4488ff}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:bold}
.tag.long{background:#00ff8822;color:#00ff88;border:1px solid #00ff8844}
.tag.short{background:#ff444422;color:#ff4444;border:1px solid #ff444444}
.tag.bull{background:#00ff8822;color:#00ff88}
.tag.bear{background:#ff444422;color:#ff4444}
.tag.sideways{background:#ffaa0022;color:#ffaa00}
table{width:100%;border-collapse:collapse}
th{text-align:left;color:#666;font-size:11px;text-transform:uppercase;padding:4px 8px;border-bottom:1px solid #333}
td{padding:4px 8px;border-bottom:1px solid #1a1a1a}
.log-box{background:#080810;border:1px solid #1a1a1a;border-radius:6px;padding:10px;max-height:300px;overflow-y:auto;font-size:11px;line-height:1.6;color:#888}
.log-box .error{color:#ff4444}
.log-box .warn{color:#ffaa00}
.log-box .info{color:#4488ff}
.pulse{animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.footer{text-align:center;color:#333;font-size:10px;margin-top:16px}
</style>
</head>
<body>
<h1>⚡ SMC Fractal Bot</h1>
<div class="subtitle" id="meta">Loading...</div>

<div class="grid">
  <div class="card">
    <h2>💰 Account</h2>
    <div id="account">--</div>
  </div>
  <div class="card">
    <h2>⚙️ Bot Status</h2>
    <div id="status">--</div>
  </div>
</div>

<div class="card" style="margin-bottom:16px">
  <h2>📦 Positions</h2>
  <div id="positions">--</div>
</div>

<div class="grid">
  <div class="card">
    <h2>📊 Regime</h2>
    <div id="regime">--</div>
  </div>
  <div class="card">
    <h2>🛡️ Risk</h2>
    <div id="risk">--</div>
  </div>
</div>

<div class="card" style="margin-bottom:16px">
  <h2>📜 Recent Signals</h2>
  <div id="signals">--</div>
</div>

<div class="card">
  <h2>📝 Logs (last 50)</h2>
  <div class="log-box" id="logs">Loading...</div>
</div>

<div class="footer">SMC Fractal Bot &mdash; <span id="refresh">--</span></div>

<script>
function h(tag,cls,html){return '<'+tag+(cls?' class="'+cls+'"':'')+'>'+html+'</'+tag+'>'}
function row(l,v,c){return '<div class="row"><span class="label">'+l+'</span><span class="val '+(c||'')+'">'+v+'</span></div>'}

function renderAccount(d){
  var s='';
  s+=row('Equity','$'+Number(d.equity||0).toLocaleString(undefined,{minimumFractionDigits:2}),'green');
  s+=row('Available','$'+Number(d.available||0).toLocaleString(undefined,{minimumFractionDigits:2}),'blue');
  s+=row('Unrealized PnL','$'+Number(d.unrealized_pnl||0).toLocaleString(undefined,{minimumFractionDigits:2}),d.unrealized_pnl>=0?'green':'red');
  document.getElementById('account').innerHTML=s;
}

function renderStatus(d){
  var s='';
  s+=row('Mode',d.mode.toUpperCase(),d.mode==='demo'?'yellow':'green');
  s+=row('Running',d.running?'YES':'NO',d.running?'green':'red');
  s+=row('Symbols',d.symbols.join(', '));
  s+=row('Uptime',d.uptime);
  s+=row('Pairs Active',d.active_pairs);
  document.getElementById('status').innerHTML=s;
}

function renderPositions(d){
  if(!d||d.length===0){document.getElementById('positions').innerHTML='<div style="color:#555;padding:8px">No open positions</div>';return}
  var s='<table><tr><th>Symbol</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>Size</th></tr>';
  d.forEach(function(p){
    var cls=p.side==='LONG'?'long':'short';
    s+='<tr><td>'+p.symbol+'</td><td><span class="tag '+cls+'">'+p.side+'</span></td><td>'+p.entry+'</td><td style="color:#ff4444">'+p.stop+'</td><td style="color:#00ff88">'+p.tp+'</td><td>'+p.size+'</td></tr>';
  });
  s+='</table>';
  document.getElementById('positions').innerHTML=s;
}

function renderRegime(d){
  var s='';
  for(var sym in d){
    var r=d[sym];
    var cls=r.regime==='bear'?'bear':r.regime==='bull'?'bull':'sideways';
    s+=row(sym,'<span class="tag '+cls+'">'+r.regime+'</span> adx='+Number(r.adx).toFixed(1)+' str='+Number(r.strength).toFixed(2));
  }
  document.getElementById('regime').innerHTML=s||'<div style="color:#555">--</div>';
}

function renderRisk(d){
  var s='';
  s+=row('Can Trade',d.can_trade?'YES':'NO',d.can_trade?'green':'red');
  s+=row('Risk %',d.risk_percent+'%');
  s+=row('Total Trades',d.total_trades);
  s+=row('Win Rate',d.win_rate+'%');
  s+=row('Profit Factor',d.profit_factor,d.profit_factor>=2?'green':'yellow');
  s+=row('Real PnL','$'+Number(d.real_pnl||0).toFixed(2),d.real_pnl>=0?'green':'red');
  s+=row('Real PnL %',Number(d.real_pnl_pct||0).toFixed(2)+'%',d.real_pnl_pct>=0?'green':'red');
  document.getElementById('risk').innerHTML=s;
}

function renderSignals(d){
  if(!d||d.length===0){document.getElementById('signals').innerHTML='<div style="color:#555;padding:8px">No recent signals</div>';return}
  var s='<table><tr><th>Time</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th></tr>';
  d.forEach(function(sig){
    var cls=sig.direction==='BUY'?'long':'short';
    s+='<tr><td>'+sig.time+'</td><td>'+sig.symbol+'</td><td><span class="tag '+cls+'">'+sig.direction+'</span></td><td>'+sig.entry+'</td><td style="color:#ff4444">'+sig.stop+'</td><td style="color:#00ff88">'+sig.tp+'</td></tr>';
  });
  s+='</table>';
  document.getElementById('signals').innerHTML=s;
}

function renderLogs(d){
  var el=document.getElementById('logs');
  el.innerHTML=d.map(function(l){
    var cls=l.toLowerCase().includes('error')?'error':l.toLowerCase().includes('warn')?'warn':'info';
    return '<div class="'+cls+'">'+l+'</div>';
  }).join('');
}

function refresh(){
  fetch('/api/status').then(r=>r.json()).then(d=>{
    renderAccount(d.account);
    renderStatus(d.bot);
    renderPositions(d.positions);
    renderRegime(d.regime);
    renderRisk(d.risk);
    renderSignals(d.signals);
    renderLogs(d.logs);
    document.getElementById('meta').innerHTML='Mode: '+d.bot.mode.toUpperCase()+' | Updated: '+d.bot.time+' | Auto-refresh 10s';
    document.getElementById('refresh').textContent=d.bot.time;
  }).catch(e=>{document.getElementById('meta').innerHTML='Error: '+e});
}

refresh();
setInterval(refresh,10000);
</script>
</body>
</html>"""


class Dashboard:
    """Flask web dashboard for bot monitoring."""

    def __init__(self, bot, port: int = 80):
        self.bot = bot
        self.port = port
        self.app = Flask(__name__)
        self._setup_routes()

    def _setup_routes(self):
        @self.app.route("/")
        def index():
            return DASHBOARD_HTML

        @self.app.route("/api/status")
        def api_status():
            return jsonify(self._collect_status())

    def _collect_status(self) -> dict:
        bot = self.bot

        # Account
        account = {"equity": 0, "available": 0, "unrealized_pnl": 0}
        try:
            balance = bot.client.get_wallet_balance()
            account["equity"] = float(balance.get("totalEquity", 0))
            account["available"] = float(balance.get("availableToWithdraw", 0))
            coins = balance.get("coin", [])
            upnl = sum(float(c.get("unrealisedPnl", 0)) for c in coins)
            account["unrealized_pnl"] = upnl
        except Exception:
            pass

        # Positions
        positions = []
        for sym, pos in bot.tracker.positions.items():
            positions.append({
                "symbol": sym,
                "side": pos.side,
                "entry": f"{pos.entry_price:.4f}",
                "stop": f"{pos.stop_price:.4f}",
                "tp": f"{pos.tp_price:.4f}" if pos.tp_price else "--",
                "size": f"{pos.size:.4f}",
            })

        # Regime
        regime = {}
        for sym, r in bot.regime_state.items():
            regime[sym] = {
                "regime": r.get("regime", "?"),
                "adx": r.get("adx", 0),
                "strength": r.get("strength", 0),
            }

        # Risk
        risk = {"can_trade": True, "risk_percent": 1.0, "total_trades": 0,
                "win_rate": 0, "profit_factor": 0, "daily_pnl": 0}
        try:
            rs = bot.risk.get_status()
            risk["can_trade"] = rs.get("can_trade", True)
            risk["risk_percent"] = rs.get("current_risk", 1.0)
            risk["total_trades"] = rs.get("daily_trades", 0)
            daily_wins = rs.get("daily_wins", 0)
            daily_losses = rs.get("daily_losses", 0)
            total_d = daily_wins + daily_losses
            risk["win_rate"] = round(daily_wins / total_d * 100, 1) if total_d > 0 else 0
            risk["profit_factor"] = rs.get("profit_factor", 0)
            risk["daily_pnl"] = rs.get("daily_pnl", 0)
        except Exception:
            pass

        # Real PnL = current equity - start equity
        try:
            if bot.start_equity > 0:
                risk["real_pnl"] = account["equity"] - bot.start_equity
                risk["real_pnl_pct"] = round(risk["real_pnl"] / bot.start_equity * 100, 2) if bot.start_equity > 0 else 0
        except Exception:
            pass

        # Uptime
        uptime_s = (datetime.now(timezone.utc) - bot.start_time.replace(tzinfo=timezone.utc)).total_seconds()
        if uptime_s < 60:
            uptime = f"{int(uptime_s)}s"
        elif uptime_s < 3600:
            uptime = f"{int(uptime_s/60)}m"
        else:
            uptime = f"{int(uptime_s/3600)}h {int((uptime_s%3600)/60)}m"

        # Logs
        logs = []
        log_file = Path(__file__).parent.parent / "logs" / "bot.log"
        if log_file.exists():
            try:
                lines = log_file.read_text(errors="ignore").strip().split("\n")
                logs = lines[-50:]
            except Exception:
                pass

        # Recent signals
        signals = []
        for sym, gen in bot.signal_gens.items():
            if gen.sweep_price and gen.center_at_sweep:
                signals.append({
                    "time": "--",
                    "symbol": sym,
                    "direction": gen.sweep_direction or "?",
                    "entry": f"{gen.center_at_sweep:.4f}" if gen.center_at_sweep else "--",
                    "stop": f"{gen.sweep_price:.4f}" if gen.sweep_price else "--",
                    "tp": "--",
                })

        return {
            "account": account,
            "bot": {
                "mode": "demo" if os.getenv("BYBIT_DEMO", "true").lower() == "true" else
                        "testnet" if os.getenv("BYBIT_TESTNET", "false").lower() == "true" else "mainnet",
                "running": bot.running,
                "symbols": bot.symbols,
                "active_pairs": len(bot.symbols),
                "uptime": uptime,
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            },
            "positions": positions,
            "regime": regime,
            "risk": risk,
            "signals": signals,
            "logs": logs,
        }

    def run_in_thread(self):
        """Start Flask in a background thread."""
        t = threading.Thread(target=self._run, daemon=True, name="dashboard")
        t.start()
        logger.info(f"Dashboard started on http://0.0.0.0:{self.port}")

    def _run(self):
        self.app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False)
