"""Fuente de datos de mercado para los agentes.

Si NinjaTrader (el indicador TradingAgentsExporter) está escribiendo el archivo
data/live_<PAR>.json, se usa ese (datos en vivo). Si no, cae al stub de ejemplo.
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def get_context(par: str) -> dict:
    par = par.upper()
    live = BASE / "data" / f"live_{par}.json"
    if live.exists():
        try:
            ctx = json.loads(live.read_text(encoding="utf-8"))
            ctx["_fuente"] = "NinjaTrader (vivo)"
            return ctx
        except Exception:
            pass  # archivo a medio escribir o corrupto -> usa stub

    stub = BASE / "data" / "sample_context_NQ.json"
    ctx = json.loads(stub.read_text(encoding="utf-8")) if stub.exists() else {"nota": "sin datos"}
    ctx["_fuente"] = "STUB (ejemplo, sin NinjaTrader)"
    return ctx


def precio_actual(par: str) -> dict:
    """Precio ACTUAL solo de datos vivos (para verificar antes de ejecutar).

    Devuelve {'precio': float|None, 'timestamp': str|None, 'vivo': bool}.
    Si no hay live_<PAR>.json (cae al stub), 'vivo' es False y 'precio' None:
    el precio del stub es de otro instrumento, NO se debe usar para validar.
    """
    ctx = get_context(par)
    vivo = str(ctx.get("_fuente", "")).startswith("NinjaTrader")
    return {
        "precio": ctx.get("precio_actual") if vivo else None,
        "timestamp": ctx.get("timestamp") if vivo else None,
        "vivo": vivo,
    }
