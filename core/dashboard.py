"""
Dashboard — Flask web dashboard for bot monitoring.
Serves on port 80 inside the container.
"""

import os
import json
import time
import hmac
import base64
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, Response

logger = logging.getLogger(__name__)


def _auth_ok(auth_header: str) -> bool:
    """Check HTTP Basic credentials against DASH_USER/DASH_PASS env vars."""
    user = os.getenv("DASH_USER", "admin")
    password = os.getenv("DASH_PASS", "")
    if not password:
        logger.warning("DASH_PASS not set — dashboard auth locked (401 for all)")
        return False
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    u, p = decoded.split(":", 1)
    return hmac.compare_digest(u, user) and hmac.compare_digest(p, password)


def _unauthorized_response() -> Response:
    return Response(
        "Unauthorized",
        401,
        {"WWW-Authenticate": 'Basic realm="SMC Bot Dashboard"'},
    )


def _is_legacy_trade(trade: dict, current_strategy: str, switched_at) -> bool:
    """True, если сделка принадлежит ПРЕЖНЕЙ стратегии, а не текущей.

    Основной признак — поле strategy в записи сделки. Если его нет (старые
    записи), откатываемся на время закрытия относительно момента переключения.
    Неопознанное считаем текущей стратегией, чтобы не прятать реальные цифры.
    """
    strat = (trade.get('strategy') or '').strip().lower()
    if strat:
        return strat != current_strategy
    if not switched_at:
        return False
    raw = trade.get('close_time') or trade.get('exit_time')
    if raw is None:
        return False
    try:
        if isinstance(raw, (int, float)):
            ts = datetime.fromtimestamp(float(raw), timezone.utc)
        else:
            s = str(raw).strip().replace('Z', '+00:00')
            try:
                ts = datetime.fromisoformat(s)
            except ValueError:
                ts = datetime.fromisoformat(s.split('.')[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return ts < switched_at


def _recent_signal_events(limit: int = 15) -> list:
    """Recent signals for both SMC and ZDev, read from strategy_signals.jsonl."""
    path = Path(__file__).parent.parent / "logs" / "strategy_signals.jsonl"
    if not path.exists():
        return []
    events = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for ln in lines[-limit * 2:]:
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            direction = ev.get("direction")
            if direction is None:
                continue
            entry = ev.get("entry")
            stop = ev.get("stop")
            tp = ev.get("tp")
            ts = ev.get("time", "")
            events.append({
                "time": ts[11:19] if len(ts) >= 19 else ts,
                "symbol": ev.get("symbol", "?"),
                "strategy": ev.get("strategy", "smc"),
                "direction": direction,
                "entry": f"{entry:.4f}" if entry is not None else "--",
                "stop": f"{stop:.4f}" if stop is not None else "--",
                "tp": f"{tp:.4f}" if tp is not None else "--",
            })
    except Exception as e:
        logger.warning(f"signal events read failed: {e}")
        return []
    return events[-limit:]

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
.tag.smc{background:#ffaa0022;color:#ffaa00;border:1px solid #ffaa0044}
.tag.zdev{background:#4488ff22;color:#4488ff;border:1px solid #4488ff44}
.tag.donchian{background:#00dd8822;color:#00dd88;border:1px solid #00dd8844}
.chip{display:inline-block;padding:3px 10px;border-radius:999px;margin-right:8px;font-size:12px;font-weight:bold;white-space:nowrap}
.chip-p{background:#00ff8822;color:#00ff88;border:1px solid #00ff8844}
.chip-n{background:#ff444422;color:#ff4444;border:1px solid #ff444444}
.chip-z{background:#222228;color:#888;border:1px solid #333}
table{width:100%;border-collapse:collapse}
th{text-align:left;color:#666;font-size:11px;text-transform:uppercase;padding:4px 8px;border-bottom:1px solid #333}
td{padding:4px 8px;border-bottom:1px solid #1a1a1a}
.log-box{background:#080810;border:1px solid #1a1a1a;border-radius:6px;padding:10px;max-height:300px;overflow-y:auto;font-size:11px;line-height:1.6;color:#888}
.log-box .error{color:#ff4444}
.log-box .warn{color:#ffaa00}
.log-box .info{color:#4488ff}
.pulse{animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
canvas.eqcurve{width:100%;height:120px;display:block;margin-top:10px;background:#080810;border:1px solid #1a1a1a;border-radius:6px}
.eqhint{color:#555;font-size:10px;margin-top:5px}
.footer{text-align:center;color:#333;font-size:10px;margin-top:16px}
</style>
</head>
<body>
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
  <h1>⚡ SMC Fractal Bot</h1>
  <a href="http://161.104.18.192:8080" target="_blank" rel="noopener" style="color:#666;font-size:11px;text-decoration:none;border-bottom:1px dashed #333;padding-bottom:1px">🖥 Monitor</a>
</div>
<div class="subtitle" id="meta">Loading...</div>

<div class="grid">
  <div class="card">
    <h2>💰 Account</h2>
    <div id="account">--</div>
    <canvas class="eqcurve" id="eqcurve" width="520" height="120"></canvas>
    <div class="eqhint" id="eqhint">Equity curve: collecting…</div>
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

function drawEquity(curve, startEq){
  var cv=document.getElementById('eqcurve');
  var hint=document.getElementById('eqhint');
  if(!cv) return;
  var dpr=window.devicePixelRatio||1;
  var W=cv.clientWidth||520, H=cv.clientHeight||120;
  cv.width=W*dpr; cv.height=H*dpr;
  var g=cv.getContext('2d');
  g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,W,H);

  // Точек мало или ещё нет — рисуем честную заглушку, а не пустой прямоугольник
  if(!curve||curve.length<2){
    g.fillStyle='#333'; g.font='11px monospace'; g.textAlign='center';
    g.fillText(curve&&curve.length?'1 точка — ждём вторую':'накопление истории…',W/2,H/2);
    hint.textContent=curve&&curve.length?'1 точка — ждём вторую':'Equity curve: collecting…';
    return;
  }

  var t0=curve[0][0], t1=curve[curve.length-1][0];
  var pad=6, ys=[];
  for(var i=0;i<curve.length;i++) ys.push(curve[i][1]);
  var lo=Math.min.apply(null,ys), hi=Math.max.apply(null,ys);
  // start_equity — реальная точка отсчёта, кривая обязана быть читаема
  // и когда весь PnL в одном конце шкалы
  if(startEq>0){ lo=Math.min(lo,startEq); hi=Math.max(hi,startEq); }
  if(hi-lo<1e-9){ hi=lo+1; lo=lo-1; }  // плоская кривая: не делить на ноль
  var span=hi-lo, spanT=(t1-t0)||1;
  var X=function(t){ return pad+(t-t0)/spanT*(W-pad*2); };
  var Y=function(v){ return H-pad-(v-lo)/span*(H-pad*2); };

  // сетка
  g.strokeStyle='#16161e'; g.lineWidth=1;
  for(var k=0;k<=3;k++){
    var y=pad+k*(H-pad*2)/3;
    g.beginPath(); g.moveTo(pad,y); g.lineTo(W-pad,y); g.stroke();
  }
  // линия стартового капитала
  if(startEq>0){
    g.strokeStyle='#ffaa0055'; g.setLineDash([3,3]);
    g.beginPath(); g.moveTo(pad,Y(startEq)); g.lineTo(W-pad,Y(startEq)); g.stroke();
    g.setLineDash([]);
  }

  var last=curve[curve.length-1][1];
  var up=startEq>0?last>=startEq:last>=curve[0][1];
  var col=up?'#00ff88':'#ff4444';

  // заливка под линией
  g.beginPath(); g.moveTo(X(t0),H-pad);
  for(var j=0;j<curve.length;j++) g.lineTo(X(curve[j][0]),Y(curve[j][1]));
  g.lineTo(X(t1),H-pad); g.closePath();
  g.fillStyle=up?'#00ff8818':'#ff444418'; g.fill();
  // линия
  g.beginPath();
  for(var m=0;m<curve.length;m++){
    var px=X(curve[m][0]), py=Y(curve[m][1]);
    if(m===0) g.moveTo(px,py); else g.lineTo(px,py);
  }
  g.strokeStyle=col; g.lineWidth=1.5; g.stroke();
  // последняя точка
  g.fillStyle=col;
  g.beginPath(); g.arc(X(t1),Y(last),2.5,0,6.284); g.fill();

  // подписи: min/max и период
  g.font='9px monospace'; g.textAlign='left';
  g.fillStyle='#555';
  g.fillText('$'+hi.toLocaleString(undefined,{maximumFractionDigits:0}),pad+2,pad+8);
  g.fillText('$'+lo.toLocaleString(undefined,{maximumFractionDigits:0}),pad+2,H-pad-3);
  g.textAlign='right'; g.fillStyle='#444';
  var days=Math.max(1,Math.round(spanT/86400));
  g.fillText(days+' дн',W-pad-2,H-pad-3);

  // подпись под графиком: без неё остаётся надпись из заглушки
  // «1 точка — ждём вторую» навсегда, даже когда криная уже есть
  var base0=startEq>0?startEq:curve[0][1];
  var pct=base0>0?((last-base0)/base0*100):0;
  hint.textContent='Equity: '+curve.length+' pts · '+
    (startEq>0?('start $'+base0.toLocaleString(undefined,{maximumFractionDigits:0})+' · '):'')+
    (pct>=0?'+':'')+pct.toFixed(2)+'%';
  hint.style.color=up?'#00ff88':'#ff4444';
}

function renderStatus(d){
  var s='';
  s+=row('Mode',d.mode.toUpperCase(),d.mode==='demo'?'yellow':'green');
  s+=row('Running',d.running?'YES':'NO',d.running?'green':'red');
  s+=row('Strategy',(d.strategy||'?').toUpperCase(),'green');
  s+=row('Uptime',d.uptime,d.uptime&&d.uptime.indexOf('m')>=0?'green':'yellow');
  var hc={ok:'green',quiet:'yellow',degraded:'yellow',stalled:'red',unknown:'yellow'}[d.health]||'yellow';
  s+=row('Health',(d.health||'?').toUpperCase()+(d.health_note?' — '+d.health_note:''),hc);
  s+=row('Bars Evaluated',(d.beat&&d.beat.bars_evaluated_total!=null)?d.beat.bars_evaluated_total:'—');
  s+=row('Last Signal',(d.beat&&d.beat.quiet_hours!=null)?(d.beat.quiet_hours.toFixed(1)+'h назад'):'ещё не было');
  s+=row('Trading Since',(d.trading_since||'?')+' ('+(d.started_at||'?')+')');
  s+=row('Pairs Active',d.active_pairs);
  var coins=(d.coins&&d.coins.length)?d.coins:(d.symbols||[]).map(function(x){return {symbol:x,strategy:'smc'}});
  if(coins.length){
    var cs=coins.map(function(c){
      var p=c.pnl||0;
      var cls=p>0?'chip-p':(p<0?'chip-n':'chip-z');
      var sign=p>0?'+':(p<0?'-':'');
      var sym=c.symbol.replace(/USDT$/,'');
      var badge=(c.trades||0)>0?'<span style="color:#ffaa00">('+(c.trades||0)+')</span> ':'';
      var leg=(c.legacy_trades||0)>0?'<span style="color:#888" title="closed before strategy switch"> +'+(c.legacy_trades||0)+' prev</span>':'';
      return '<span class="chip '+cls+'">'+sym+badge+sign+'$'+Math.abs(p).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})+leg+'</span>';
    }).join('');
    s+='<div class="row"><span class="label">Coins</span></div><div style="text-align:right;padding:4px 0 6px;line-height:2.1">'+cs+'</div>';
  }
  if(d.legacy_trades>0){
    s+='<div class="row"><span class="label">Note</span></div><div style="padding:2px 0 6px;color:#888">'+d.legacy_trades+' closed trade(s) belong to the previous strategy and are excluded from current PnL</div>';
  }
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
  var s='<table><tr><th>Time</th><th>Symbol</th><th>Strat</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th></tr>';
  d.forEach(function(sig){
    var cls=sig.direction==='BUY'?'long':'short';
    var stcl=(sig.strategy==='zdev'||sig.strategy==='donchian')?sig.strategy:'smc';
    s+='<tr><td>'+sig.time+'</td><td>'+sig.symbol+'</td>'
      +'<td><span class="tag '+stcl+'">'+(sig.strategy||'smc').toUpperCase()+'</span></td>'
      +'<td><span class="tag '+cls+'">'+sig.direction+'</span></td>'
      +'<td>'+sig.entry+'</td><td style="color:#ff4444">'+sig.stop+'</td><td style="color:#00ff88">'+sig.tp+'</td></tr>';
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
    drawEquity(d.equity_curve, d.start_equity);
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
// Канвас не перерисовывается сам при изменении ширины окна — график
// растягивался бы вместе с битыми пикселями. Перерисовываем на resize.
window.addEventListener('resize',function(){
  fetch('/api/status').then(r=>r.json()).then(d=>{
    drawEquity(d.equity_curve, d.start_equity);
  }).catch(e=>{});
});
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
        @self.app.before_request
        def require_auth():
            if not _auth_ok(request.headers.get("Authorization", "")):
                return _unauthorized_response()

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
            # unified-аккаунт Bybit: availableToWithdraw существует только
            # внутри coin[] и на демо приходит пустой строкой. На верхнем
            # уровне доступная маржа называется totalAvailableBalance.
            account["available"] = float(balance.get("totalAvailableBalance", 0))
            coins = balance.get("coin", [])
            if "totalPerpUPL" in balance:
                account["unrealized_pnl"] = float(balance.get("totalPerpUPL", 0))
            else:
                account["unrealized_pnl"] = sum(
                    float(c.get("unrealisedPnl", 0)) for c in coins
                )
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

        # Uptime: два разных числа. bot.start_time восстанавливается из Redis
        # и при рестарте не меняется — по нему видно общий стаж торговли, но
        # НЕ видно зависание или рестарт-луп. Для этого аптайм самого процесса.
        def _fmt(seconds: float) -> str:
            seconds = int(max(0, seconds))
            if seconds < 60:
                return f"{seconds}s"
            if seconds < 3600:
                return f"{seconds // 60}m"
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m"

        now_utc = datetime.now(timezone.utc)
        uptime = _fmt((now_utc - bot.process_start).total_seconds())
        trading_since = _fmt((now_utc - bot.start_time.replace(tzinfo=timezone.utc)).total_seconds())

        # Liveness: без этого тишина неотличима от поломки. «signals: 0» сам
        # по себе не значит ничего — нужно знать, что бары реально
        # оценивались и пульс живой.
        beat = {}
        health = "ok"
        health_note = ""
        try:
            if bot.redis:
                m = bot.redis.load_meta() or {}
                beat = m.get("beat") or {}
        except Exception:
            pass
        if beat:
            bsym = beat.get("symbols") or {}
            # Данные от другого процесса — нельзя выдавать за текущие.
            # Иначе после рестарта до первого цикла дашборд показывал бы
            # verdict (stalled/degraded) и last_error умершего процесса.
            ps = beat.get("process_start")
            mine = bot.process_start.timestamp()
            if ps and abs(ps - mine) > 1:
                health = "unknown"
                health_note = "пульс от предыдущего процесса, ждём первый цикл"
                beat = {}
        if beat:
            bsym = beat.get("symbols") or {}
            ages = [now_utc.timestamp() - v.get("last_cycle", 0)
                    for v in bsym.values() if v.get("last_cycle")]
            worst_age = max(ages) if ages else None
            bars_total = int(beat.get("bars_evaluated_total", 0))
            sig_ts = float(beat.get("last_signal_ts") or 0)
            quiet_h = (now_utc.timestamp() - sig_ts) / 3600 if sig_ts else None
            errs = [f"{k}: {v.get('last_error')}" for k, v in bsym.items()
                    if v.get("last_error")]
            if worst_age is None:
                health, health_note = "unknown", "нет данных о пульсе"
            elif worst_age > bot.STALE_CYCLE_SEC:
                health = "stalled"
                health_note = f"цикл молчит {worst_age / 60:.0f} мин"
            elif errs:
                health, health_note = "degraded", errs[0]
            elif bars_total == 0:
                health, health_note = "unknown", "ещё ни одного бара не оценено"
            elif quiet_h is not None and quiet_h >= bot.STALE_SIGNAL_CRIT_H:
                health = "quiet"
                health_note = f"{quiet_h / 24:.1f} дн без сигнала при живом пульсе"
            elif quiet_h is not None and quiet_h >= bot.STALE_SIGNAL_WARN_H:
                health = "quiet"
                health_note = f"{quiet_h / 24:.1f} дн без сигнала"
            beat = {
                "bars_evaluated_total": bars_total,
                "last_cycle_age_min": round(worst_age / 60, 1) if worst_age else None,
                "quiet_hours": round(quiet_h, 1) if quiet_h else None,
                "symbols": {k: {"bars_evaluated": v.get("bars_evaluated", 0),
                                "last_cycle_age_min": round(
                                    (now_utc.timestamp() - v.get("last_cycle", 0)) / 60, 1),
                                "last_error": v.get("last_error", "")}
                            for k, v in sorted(bsym.items())},
            }
        else:
            health, health_note = "unknown", "пульс ещё не записан (старый бот)"

        # Logs
        logs = []
        log_file = Path(__file__).parent.parent / "logs" / "bot.log"
        if log_file.exists():
            try:
                lines = log_file.read_text(errors="ignore").strip().split("\n")
                logs = lines[-50:]
            except Exception:
                pass

        # Recent signals — unified from strategy_signals.jsonl (SMC + ZDev)
        signals = _recent_signal_events(15)
        if not signals:
            # Fallback: old-style SMC generator state scan
            for sym, gen in getattr(bot, 'signal_gens', {}).items():
                try:
                    if gen.sweep_price and gen.center_at_sweep:
                        signals.append({
                            "time": "--",
                            "symbol": sym,
                            "strategy": "smc",
                            "direction": gen.sweep_direction or "?",
                            "entry": f"{gen.center_at_sweep:.4f}" if gen.center_at_sweep else "--",
                            "stop": f"{gen.sweep_price:.4f}" if gen.sweep_price else "--",
                            "tp": "--",
                        })
                except Exception:
                    pass

        # Per-coin stats: trades count + PnL (one strategy, no tag needed)
        closed = list(getattr(bot.tracker, 'closed_trades', []) or [])
        # Сделки, закрытые ДО переключения стратегии, восстанавливаются из Redis
        # и принадлежат прежней стратегии. Помечаем их, иначе панель приписывает
        # ZDev-результаты Donchian, который их не открывал.
        current_strategy = ''
        strat_map = getattr(bot, 'symbol_strategy', None) or {}
        for sym in bot.symbols:
            st = strat_map.get(sym)
            if st:
                current_strategy = str(st)
                break
        switched_at = getattr(bot, 'strategy_switched_at', None)
        legacy = 0
        coins = []
        for sym in bot.symbols:
            ct = [t for t in closed if t.get('symbol') == sym]
            if current_strategy:
                ct_legacy = [t for t in ct
                             if _is_legacy_trade(t, current_strategy, switched_at)]
            else:
                ct_legacy = []
            ct_new = [t for t in ct if t not in ct_legacy]
            legacy += len(ct_legacy)
            coins.append({
                "symbol": sym,
                "trades": len(ct_new),
                "legacy_trades": len(ct_legacy),
                "pnl": round(sum(t.get('pnl', 0) for t in ct_new), 2),
                "legacy_pnl": round(sum(t.get('pnl', 0) for t in ct_legacy), 2),
            })

        # Кривая доходности для графика в блоке Account
        curve = []
        try:
            if bot.redis:
                for p in (bot.redis.load_equity() or []):
                    ts, eq = p.get("ts"), p.get("eq")
                    if ts is not None and eq is not None:
                        curve.append([round(float(ts), 1), round(float(eq), 2)])
        except Exception:
            pass

        return {
            "account": account,
            "equity_curve": curve,
            "start_equity": round(float(bot.start_equity or 0), 2),
            "bot": {
                "mode": "demo" if os.getenv("BYBIT_DEMO", "true").lower() == "true" else
                        "testnet" if os.getenv("BYBIT_TESTNET", "false").lower() == "true" else "mainnet",
                "running": bot.running,
                "symbols": bot.symbols,
                "coins": coins,
                "active_pairs": len(bot.symbols),
                "uptime": uptime,
                "trading_since": trading_since,
                "started_at": bot.start_time.replace(tzinfo=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC"),
                "legacy_trades": legacy,
                "strategy": current_strategy,
                "health": health,
                "health_note": health_note,
                "beat": beat,
                "time": now_utc.strftime("%H:%M:%S UTC"),
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
        logging.getLogger("werkzeug").setLevel(logging.CRITICAL)
        self.app.run(host="0.0.0.0", port=self.port, debug=False, use_reloader=False)
