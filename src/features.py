"""Prepara el contexto para los agentes según el modo (scalping/intradía).

El exporter de NinjaTrader manda una ventana generosa por TF. Aquí:
  1. Recortamos a la cantidad de velas que corresponde al modo elegido.
  2. Calculamos indicadores por TF (EMA9, EMA20, ADX, VWAP) — el código los calcula,
     no la IA, para que los agentes decidan sobre números reales.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

# Velas por TF según el modo (confirmado con el usuario para scalping).
WINDOWS = {
    "scalping": {"1m": 100, "5m": 60,  "15m": 50,  "1h": 24,  "1d": 3},
    "intradia": {"5m": 150, "15m": 200, "30m": 230, "1h": 115, "4h": 58, "1d": 30},
}

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Perfil de sesión (hora ET): (inicio_min, fin_min, nombre, volatilidad, comportamiento)
_SESIONES = [
    (120, 300,  "London killzone",        "alta",       "arrancan tendencias, barridos de liquidez"),
    (300, 420,  "pre-NY",                 "media-baja", "transición London -> NY"),
    (420, 570,  "NY killzone (pre-open)",  "alta",       "se arma la apertura, sube el volumen"),
    (570, 660,  "NY open / overlap",      "máxima",     "máximo volumen, movimientos grandes, reversiones"),
    (660, 720,  "media mañana NY",        "alta-media", "continúa el impulso de la apertura"),
    (720, 840,  "lunch NY",               "baja",       "choppy, poco fiable, falsos movimientos"),
    (840, 960,  "PM / cierre NY",         "media-alta", "repunta hacia el cierre"),
    (960, 1140, "after-hours",            "baja",       "fino, ilíquido"),
]


def _sesion_ny() -> dict:
    """Hora actual en NY + perfil de la sesión (volatilidad/comportamiento) para que los agentes lo USEN."""
    now = datetime.now(ZoneInfo("America/New_York"))
    hm = now.hour * 60 + now.minute
    dow = now.weekday()
    sesion, vol, comp = "Asia", "baja", "rangos estrechos, ojo con falsos rompimientos"
    for ini, fin, nom, v, c in _SESIONES:
        if ini <= hm < fin:
            sesion, vol, comp = nom, v, c
            break
    if dow >= 5:
        sesion, vol, comp = "fin de semana", "muy baja", "mercado cerrado/ilíquido"
    return {
        "hora_ny": now.strftime("%H:%M"),
        "dia": _DIAS[dow],
        "sesion": sesion,
        "volatilidad": vol,
        "comportamiento": comp,
        "killzone_london": 120 <= hm < 300,
        "killzone_ny": 420 <= hm < 600,
        "rth": 570 <= hm < 960,
    }


def _ema(values: list[float], period: int):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period      # semilla = SMA
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return round(ema, 2)


def _vwap(bars: list[dict]):
    num = den = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        num += tp * b["v"]
        den += b["v"]
    return round(num / den, 2) if den else None


def _wilder(vals: list[float], period: int) -> list[float]:
    """Suavizado de Wilder (suma móvil), devuelve la serie suavizada."""
    if len(vals) < period:
        return []
    out = [sum(vals[:period])]
    for v in vals[period:]:
        out.append(out[-1] - out[-1] / period + v)
    return out


def _adx(bars: list[dict], period: int = 14):
    if len(bars) < period * 2 + 1:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        ph, pl = bars[i - 1]["h"], bars[i - 1]["l"]
        up, down = h - ph, pl - l
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    atr = _wilder(trs, period)
    pdm = _wilder(plus_dm, period)
    mdm = _wilder(minus_dm, period)
    if not atr:
        return None

    dx = []
    for i in range(len(atr)):
        di_plus = 100 * pdm[i] / atr[i] if atr[i] else 0
        di_minus = 100 * mdm[i] / atr[i] if atr[i] else 0
        denom = di_plus + di_minus
        dx.append(100 * abs(di_plus - di_minus) / denom if denom else 0)

    if len(dx) < period:
        return round(sum(dx) / len(dx), 1) if dx else None
    adx = sum(dx[:period]) / period
    for v in dx[period:]:
        adx = (adx * (period - 1) + v) / period
    return round(adx, 1)


def _poc_vah_val(por_nivel: dict):
    """POC + value area (70% del volumen) desde el mapa precio->volumen del exporter."""
    if not por_nivel:
        return None
    levels = sorted(((float(p), float(v)) for p, v in por_nivel.items()), key=lambda x: x[0])
    if not levels:
        return None
    total = sum(v for _, v in levels)
    poc_i = max(range(len(levels)), key=lambda i: levels[i][1])
    target, acc, lo, hi = total * 0.70, levels[poc_i][1], poc_i, poc_i
    while acc < target and (lo > 0 or hi < len(levels) - 1):
        v_lo = levels[lo - 1][1] if lo > 0 else -1
        v_hi = levels[hi + 1][1] if hi < len(levels) - 1 else -1
        if v_hi >= v_lo:
            hi += 1; acc += levels[hi][1]
        else:
            lo -= 1; acc += levels[lo][1]
    return {"poc": round(levels[poc_i][0], 2), "vah": round(levels[hi][0], 2), "val": round(levels[lo][0], 2)}


def _divergencia(of_1m: list, bars_1m: list):
    """Compara la tendencia reciente del precio vs el CVD (últimas ~10 velas de 1m)."""
    n = min(10, len(of_1m or []), len(bars_1m or []))
    if n < 4:
        return "ninguna"
    closes = [b["c"] for b in bars_1m[-n:]]
    cvds = [r.get("cd", 0) for r in of_1m[-n:]]
    sube_precio, sube_cvd = closes[-1] > closes[0], cvds[-1] > cvds[0]
    if sube_precio and not sube_cvd:
        return "bajista (precio sube pero CVD baja)"
    if not sube_precio and sube_cvd:
        return "alcista (precio baja pero CVD sube)"
    return "ninguna"


def _enriquecer_order_flow(ctx: dict, raw: dict) -> None:
    """Adjunta order_flow (delta/CVD/divergencia) y volume_profile (POC/VAH/VAL) si vienen del exporter."""
    of = raw.get("order_flow")
    if of:
        of_1m = (of.get("1m") or [])[-30:]
        of_5m = (of.get("5m") or [])[-12:]
        bars_1m = ctx.get("timeframes", {}).get("1m", {}).get("ultimas_barras", [])
        ctx["order_flow"] = {
            "nota": of.get("nota", "delta desde trades ejecutados (Level 1), no DOM"),
            "cvd_sesion": of.get("cvd_sesion"),
            "divergencia_1m": _divergencia(of_1m, bars_1m),
            "1m": of_1m,
            "5m": of_5m,
        }
    vp = (raw.get("volume_profile") or {}).get("por_nivel")
    pvv = _poc_vah_val(vp) if vp else None
    if pvv:
        ctx["volume_profile"] = pvv


def preparar_contexto(raw: dict, modo: str = "scalping") -> dict:
    """Recorta por modo y agrega indicadores calculados por TF."""
    modo = modo if modo in WINDOWS else "scalping"
    win = WINDOWS[modo]
    ctx = dict(raw)
    ctx["modo"] = modo

    tfs = raw.get("timeframes") or {}
    nuevos = {}
    for tf, n in win.items():                     # solo los TF del modo (scalping vs intradía)
        bars = ((tfs.get(tf) or {}).get("ultimas_barras")) or []
        if n:
            bars = bars[-n:]                      # las más recientes
        closes = [b["c"] for b in bars]
        nuevos[tf] = {
            "n_velas": len(bars),
            "indicadores": {
                "ema9": _ema(closes, 9),
                "ema20": _ema(closes, 20),
                "adx": _adx(bars, 14),
                "vwap": _vwap(bars),
            },
            "ultimas_barras": bars,
        }
    ctx["timeframes"] = nuevos
    ctx["sesion_ny"] = _sesion_ny()
    _enriquecer_order_flow(ctx, raw)
    return ctx
