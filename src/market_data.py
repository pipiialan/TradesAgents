"""Fuente de datos de mercado para los agentes.

Si NinjaTrader (el indicador TradingAgentsExporter) está escribiendo el archivo
data/live_<PAR>.json, se usa ese (datos en vivo). Si no, cae al stub de ejemplo.
"""
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]

# Si el archivo live no se actualiza en este tiempo, NinjaTrader está cerrado / el
# exporter caído / el mercado cerrado -> los datos están viejos y NO son fiables.
FRESCO_MAX_SEG = 180  # 3 minutos


def get_context(par: str) -> dict:
    par = par.upper()
    live = BASE / "data" / f"live_{par}.json"
    if live.exists():
        try:
            ctx = json.loads(live.read_text(encoding="utf-8"))
            edad = time.time() - live.stat().st_mtime
            ctx["_edad_seg"] = round(edad)
            ctx["_fuente"] = ("NinjaTrader (vivo)" if edad <= FRESCO_MAX_SEG
                              else "NinjaTrader (DESACTUALIZADO)")
            return ctx
        except Exception:
            pass  # archivo a medio escribir o corrupto -> usa stub

    stub = BASE / "data" / "sample_context_NQ.json"
    ctx = json.loads(stub.read_text(encoding="utf-8")) if stub.exists() else {"nota": "sin datos"}
    ctx["_fuente"] = "STUB (ejemplo, sin NinjaTrader)"
    ctx["_edad_seg"] = None
    return ctx


def precio_actual(par: str) -> dict:
    """Precio ACTUAL solo de datos vivos (para verificar antes de ejecutar).

    Devuelve {'precio': float|None, 'timestamp': str|None, 'vivo': bool}.
    Si no hay live_<PAR>.json (cae al stub), 'vivo' es False y 'precio' None:
    el precio del stub es de otro instrumento, NO se debe usar para validar.
    """
    ctx = get_context(par)
    vivo = ctx.get("_fuente") == "NinjaTrader (vivo)"   # solo datos FRESCOS cuentan como vivos
    return {
        "precio": ctx.get("precio_actual") if vivo else None,
        "timestamp": ctx.get("timestamp") if vivo else None,
        "vivo": vivo,
    }
