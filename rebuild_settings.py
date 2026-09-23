"""Пересборка settings.yaml: пул 6 монет на ZDev A 2.0 (боевой с 2026-09-23).
Заменяет ТОЛЬКО список assets. Всё остальное (risk, dynamic_risk, filters,
optimizer, trailing, notifications) сохраняется байт-в-байт из текущего файла.
Пишет резервную копию. Usage: python rebuild_settings.py
Результат: settings.yaml -> ADAUSDT NEARUSDT ARBUSDT SOLUSDT DOGEUSDT XRPUSDT (все zdev, enabled)
Исключены: ETH, BNB, DOT, LINK, RENDER, APT, AVAX, INJ, GRAM, SUI, SOL-false и пр.
"""
import sys, yaml
from pathlib import Path
from copy import deepcopy

ROOT = Path(__file__).parent
CFG = ROOT / "config" / "settings.yaml"
BAK = ROOT / "config" / "settings.yaml.pool6.bak"

# ─── НОВЫЙ ПУЛ (walk-forward 4/6+): ZDev A 2.0, 4H ───────────
POOL = [
    # (symbol, enabled, strategy_type, comment)
    ("ADAUSDT",  "ADA ZDev rolling: AVG PF=3.94, MIN PF=2.79, WF 6/6"),
    ("NEARUSDT", "NEAR ZDev rolling: AVG PF=2.75, MIN PF=1.45, WF 5/6"),
    ("ARBUSDT",  "ARB ZDev rolling: AVG PF=3.36, MIN PF=1.11, WF 4/6"),
    ("SOLUSDT",  "SOL ZDev rolling: AVG PF=2.20, MIN PF=1.47, WF 6/6"),
    ("DOGEUSDT", "DOGE ZDev rolling: AVG PF=2.38, MIN PF=1.35, WF 5/6"),
    ("XRPUSDT",  "XRP ZDev rolling: AVG PF=3.07, MIN PF=1.76, WF 4/6"),
]

# Единый конфиг ZDev A 2.0 (как в экране screen_zdev и в боевых ETH/INJ/SUI)
BASE_ASSET = {
    "enabled": True,
    "strategy_type": "zdev",
    "config": {
        "strategy": {
            "lookback": 12,
            "sweep_threshold": 0.008,
            "center_proximity": 0.012,
            "tp_multiplier": 1.0,
            "timeout": 15,
        },
        "filters": {
            "adx_filter": {
                "enabled": True,
                "min_adx": 25,
            }
        },
        "trailing": {
            "enabled": True,
            "breakeven_at": 0.3,
            "trail_activate": 0.8,
            "trail_step": 0.3,
        },
    },
}


def main():
    with open(CFG, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    # резервная копия
    BAK.write_text(CFG.read_text(encoding='utf-8'), encoding='utf-8')
    print(f"Backup -> {BAK}")

    assets = []
    for sym, comment in POOL:
        a = deepcopy(BASE_ASSET)
        a["symbol"] = sym
        a["#"] = comment
        assets.append(a)

    # ставим ТОЛЬКО assets; остальные разделы остаются неизменными
    data["assets"] = assets

    with open(CFG, 'w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    # ── валидация ──
    with open(CFG, encoding='utf-8') as f:
        check = yaml.safe_load(f)
    en = [a["symbol"] for a in check.get("assets", []) if a.get("enabled")]
    assert en == [s for s, _ in POOL], f"Enabled mismatch: {en}"
    types = {a["symbol"]: a["strategy_type"] for a in check["assets"]}
    assert set(types.values()) == {"zdev"}, types
    for k in ("risk", "dynamic_risk", "filters", "optimizer", "notifications"):
        if k in data:
            assert k in check, f"top-level section lost: {k}"
    print(f"  OK: assets={en} (все zdev) | разделы: "
          f"{[k for k in ('risk','dynamic_risk','filters','optimizer','notifications') if k in check]}")


if __name__ == '__main__':
    main()
