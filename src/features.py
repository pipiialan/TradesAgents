"""Prepara el contexto para los agentes según el modo (scalping/intradía).

El exporter de NinjaTrader manda una ventana generosa por TF. Aquí:
  1. Recortamos a la cantidad de velas que corresponde al modo elegido.
  2. Calculamos indicadores por TF (EMA9, EMA20, ADX, VWAP) — el código los calcula,
     no la IA, para que los agentes decidan sobre números reales.
"""

# Velas por TF según el modo (confirmado con el usuario para scalping).
WINDOWS = {
    "scalping": {"1m": 100, "5m": 60, "15m": 50, "1h": 24, "1d": 3},
    "intradia": {"1m": 45,  "5m": 60, "15m": 60, "1h": 48, "1d": 10},
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


def preparar_contexto(raw: dict, modo: str = "scalping") -> dict:
    """Recorta por modo y agrega indicadores calculados por TF."""
    modo = modo if modo in WINDOWS else "scalping"
    win = WINDOWS[modo]
    ctx = dict(raw)
    ctx["modo"] = modo

    nuevos = {}
    for tf, data in (raw.get("timeframes") or {}).items():
        bars = (data or {}).get("ultimas_barras", []) or []
        n = win.get(tf)
        if n is not None:
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
    return ctx
