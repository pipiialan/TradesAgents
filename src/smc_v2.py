"""SMC V2 — réplica EXACTA (bit-perfect) del bot smcStrategyV2.js + smc.js (LuxAlgo SMC).

Port fiel del código de Zen. NO depende del proyecto Zen: es una copia verificada
contra el JS original (mismas velas de entrada -> mismas señales de salida).

Opera sobre arrays de velas YA resampleadas que manda el exporter de NinjaTrader:
  - c1m  (entryTf  = ejecución/retest)
  - c15m (structureTf = estructura + Order Block)
  - c30m (biasTf   = sesgo macro; solo se usa si requireHtfTrend, que está OFF)

Cada vela es un dict: {"time": int(seg), "open", "high", "low", "close", "volume"}.

Parámetros fijos del bot (panel del usuario):
  swingLen 65 · internalLen 4 · retestWindow 20 · atrLen 10 · slBufferAtr 1 ·
  maxSlPct 3 · tpRR 2.5 · minRR 1 · filtros HTF/zona OFF · liquidez OFF · retest ON · ATR buffer ON.
"""
from __future__ import annotations
import math

# ── Constantes (espejo de las de Pine/JS) ──────────────────────────────────
BULLISH_LEG = 1
BEARISH_LEG = 0
BULLISH = 1
BEARISH = -1

SMC_V2_CONFIG = {
    "swingLen": 65,
    "internalLen": 4,
    "retestWindow": 20,     # máx velas (1m) a esperar el retest del OB
    "slBufferAtr": 1.0,
    "atrLen": 10,
    "maxSlPct": 3.0,        # tope de SL (crypto/% ; default slLimitMode='pct')
    "tpRR": 2.5,
    "minRR": 1.0,
    "requireHtfTrend": False,
    "requireZone": False,
    "enableRetest": True,
    "enableLiquidityTp": False,
    "enableAtrSlBuffer": True,
}


# ── Helpers numéricos ───────────────────────────────────────────────────────
def _rma(values, length):
    """Wilder's RMA — port de rmaArr()."""
    n = len(values)
    out = [None] * n
    s = 0.0
    for i in range(min(length, n)):
        s += values[i]
    if length - 1 < n:
        out[length - 1] = s / length
    for i in range(length, n):
        out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def _atr(candles, length):
    """ATR Wilder — port de computeATR()."""
    n = len(candles)
    if n == 0:
        return []
    tr = [0.0] * n
    tr[0] = candles[0]["high"] - candles[0]["low"]
    for i in range(1, n):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    return _rma(tr, length)


def _compute_leg(highs, lows, size):
    """Port exacto de computeLeg() (LuxAlgo leg())."""
    n = len(highs)
    leg = [BEARISH_LEG] * n
    for i in range(size, n):
        max_h, min_l = -math.inf, math.inf
        for j in range(i - size + 1, i + 1):
            if highs[j] > max_h:
                max_h = highs[j]
            if lows[j] < min_l:
                min_l = lows[j]
        new_leg_high = highs[i - size] > max_h
        new_leg_low = lows[i - size] < min_l
        if new_leg_high:
            leg[i] = BEARISH_LEG
        elif new_leg_low:
            leg[i] = BULLISH_LEG
        else:
            leg[i] = leg[i - 1]
    return leg


def _find_order_block(highs, lows, times, pivot_idx, break_bar, bias):
    """Port de findOrderBlock()."""
    ob_idx = pivot_idx
    if bias == BULLISH:
        min_low = math.inf
        for k in range(pivot_idx, break_bar + 1):
            if lows[k] < min_low:
                min_low = lows[k]
                ob_idx = k
    else:
        max_high = -math.inf
        for k in range(pivot_idx, break_bar + 1):
            if highs[k] > max_high:
                max_high = highs[k]
                ob_idx = k
    return {
        "startIdx": ob_idx,
        "startTime": times[ob_idx],
        "endTime": None,
        "top": highs[ob_idx],
        "bottom": lows[ob_idx],
        "isBull": bias == BULLISH,
        "mitigated": False,
    }


def _run_structure(candles, length):
    """Port de runStructure(): devuelve {structure, swings, orderBlocks}."""
    n = len(candles)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    times = [c["time"] for c in candles]

    structure = []
    swings = []
    order_blocks = []

    leg_arr = _compute_leg(highs, lows, length)

    swing_high_level = math.nan
    swing_high_idx = -1
    swing_high_crossed = False
    swing_low_level = math.nan
    swing_low_idx = -1
    swing_low_crossed = False
    prev_swing_high_px = math.nan
    prev_swing_low_px = math.nan
    trend = 0

    for i in range(1, n):
        change = leg_arr[i] - leg_arr[i - 1]
        pivot_low = change == 1
        pivot_high = change == -1

        if pivot_low and i >= length:
            piv_idx = i - length
            px = lows[piv_idx]
            typ = "HL" if math.isnan(prev_swing_low_px) else ("HL" if px > prev_swing_low_px else "LL")
            swings.append({
                "idx": piv_idx, "time": times[piv_idx],
                "confirmedIdx": i, "confirmedTime": times[i],
                "price": px, "isHigh": False, "type": typ,
            })
            prev_swing_low_px = px
            swing_low_level = px
            swing_low_idx = piv_idx
            swing_low_crossed = False
            if trend == 0:
                trend = BEARISH

        if pivot_high and i >= length:
            piv_idx = i - length
            px = highs[piv_idx]
            typ = "LH" if math.isnan(prev_swing_high_px) else ("HH" if px > prev_swing_high_px else "LH")
            swings.append({
                "idx": piv_idx, "time": times[piv_idx],
                "confirmedIdx": i, "confirmedTime": times[i],
                "price": px, "isHigh": True, "type": typ,
            })
            prev_swing_high_px = px
            swing_high_level = px
            swing_high_idx = piv_idx
            swing_high_crossed = False
            if trend == 0:
                trend = BULLISH

        prev_close = closes[i - 1]
        close = closes[i]

        # Cruce alcista
        if (not math.isnan(swing_high_level) and not swing_high_crossed
                and prev_close <= swing_high_level and close > swing_high_level):
            is_bos = trend == BULLISH
            structure.append({
                "time1": times[swing_high_idx], "time2": times[i],
                "price": swing_high_level, "isBOS": is_bos, "isBull": True,
                "label": "BOS" if is_bos else "CHoCH",
            })
            swing_high_crossed = True
            trend = BULLISH
            if swing_high_idx >= 0:
                order_blocks.append(_find_order_block(highs, lows, times, swing_high_idx, i, BULLISH))

        # Cruce bajista
        if (not math.isnan(swing_low_level) and not swing_low_crossed
                and prev_close >= swing_low_level and close < swing_low_level):
            is_bos = trend == BEARISH
            structure.append({
                "time1": times[swing_low_idx], "time2": times[i],
                "price": swing_low_level, "isBOS": is_bos, "isBull": False,
                "label": "BOS" if is_bos else "CHoCH",
            })
            swing_low_crossed = True
            trend = BEARISH
            if swing_low_idx >= 0:
                order_blocks.append(_find_order_block(highs, lows, times, swing_low_idx, i, BEARISH))

    # Mitigación de OB
    for ob in order_blocks:
        for i in range(ob["startIdx"] + 1, n):
            if ob["isBull"] and lows[i] < ob["bottom"]:
                ob["mitigated"] = True
                ob["endTime"] = times[i]
                break
            if (not ob["isBull"]) and highs[i] > ob["top"]:
                ob["mitigated"] = True
                ob["endTime"] = times[i]
                break
        if ob["endTime"] is None:
            ob["endTime"] = times[n - 1]

    return {"structure": structure, "swings": swings, "orderBlocks": order_blocks}


def _compute_smc(candles, swing_len, internal_len):
    """Port de computeSMC() (solo swingResult + internalResult, que es lo que usa V2)."""
    n = len(candles)
    if n < swing_len + 5:
        return {"swingResult": None, "internalResult": None}
    swing_result = _run_structure(candles, swing_len)
    internal_result = _run_structure(candles, internal_len) if n >= internal_len + 5 else None
    return {"swingResult": swing_result, "internalResult": internal_result}


def _htf_trend_at(time_sec, htf_structure):
    """Port de htfTrendAt() — solo se usa si requireHtfTrend (OFF)."""
    last_bull, last_bear = -1, -1
    for st in htf_structure:
        if st["time2"] > time_sec:
            break
        if st["isBull"]:
            last_bull = st["time2"]
        else:
            last_bear = st["time2"]
    if last_bull < 0 and last_bear < 0:
        return None
    if last_bull < 0:
        return "bear"
    if last_bear < 0:
        return "bull"
    return "bull" if last_bull > last_bear else "bear"


# ── Señales (port de smcV2_Signals, adaptado a arrays 1m/15m/30m directos) ──
def _signals(c1m, c15m, c30m, cfg):
    swing_len = cfg["swingLen"]
    if len(c15m) < swing_len + 5:
        return []

    smc15m = _compute_smc(c15m, swing_len, cfg["internalLen"])
    if not smc15m["internalResult"]:
        return []
    smc30m = _compute_smc(c30m, swing_len, cfg["internalLen"]) if cfg["requireHtfTrend"] else None

    atr1m = _atr(c1m, cfg["atrLen"]) if cfg["enableAtrSlBuffer"] else None

    int15m = smc15m["internalResult"]["structure"]
    ob15m = smc15m["internalResult"]["orderBlocks"]
    htf_str = (smc30m["swingResult"]["structure"] if (smc30m and smc30m["swingResult"]) else [])

    time15m_map = {c["time"]: i for i, c in enumerate(c15m)}
    base_time_map = {c["time"]: i for i, c in enumerate(c1m)}

    retest_window = cfg["retestWindow"]
    signals = []

    for st_idx in range(len(int15m)):
        st = int15m[st_idx]
        if st_idx >= len(ob15m):
            continue
        ob = ob15m[st_idx]
        if not ob:
            continue

        bar15m_idx = time15m_map.get(st["time2"])
        if bar15m_idx is None:
            continue
        bar15m = c15m[bar15m_idx]
        is_long = st["isBull"]

        # Filtro HTF (OFF por config; se respeta igual para bit-perfect)
        if cfg["requireHtfTrend"] and len(htf_str) > 0:
            trend = _htf_trend_at(bar15m["time"], htf_str)
            if trend is None:
                continue
            if is_long and trend != "bull":
                continue
            if (not is_long) and trend != "bear":
                continue

        # ── Retest (enableRetest ON) ─────────────────────────────────────────
        entry_bar = None
        entry_idx1m = None
        if not cfg["enableRetest"]:
            entry_bar = bar15m
            entry_idx1m = base_time_map.get(bar15m["time"])
        else:
            start_idx = None
            for j in range(len(c1m)):
                if c1m[j]["time"] > bar15m["time"]:
                    start_idx = j
                    break
            if start_idx is None:
                continue
            end_idx = min(start_idx + retest_window, len(c1m) - 1)
            for j in range(start_idx, end_idx + 1):
                bar = c1m[j]
                if is_long and bar["close"] < ob["bottom"]:
                    break
                if (not is_long) and bar["close"] > ob["top"]:
                    break
                touches_ob = (ob["bottom"] <= bar["low"] <= ob["top"]) if is_long \
                    else (ob["bottom"] <= bar["high"] <= ob["top"])
                closes_right = (bar["close"] > bar["open"]) if is_long else (bar["close"] < bar["open"])
                if touches_ob and closes_right:
                    entry_bar = bar
                    entry_idx1m = j
                    break

        if not entry_bar:
            continue

        entry = entry_bar["close"]

        # ── SL con buffer ATR ────────────────────────────────────────────────
        atr_val = (atr1m[entry_idx1m] if (cfg["enableAtrSlBuffer"] and atr1m and entry_idx1m is not None
                                          and entry_idx1m < len(atr1m)) else None)
        atr_buf = atr_val * cfg["slBufferAtr"] if atr_val is not None else 0
        if is_long:
            sl = ob["bottom"] - atr_buf
            if sl >= entry:
                continue
        else:
            sl = ob["top"] + atr_buf
            if sl <= entry:
                continue

        sl_dist = abs(entry - sl)
        if (sl_dist / entry * 100) > cfg["maxSlPct"]:   # slLimitMode='pct'
            continue

        # ── TP: liquidez OFF -> RR ───────────────────────────────────────────
        tp = None
        if tp is None or abs(tp - entry) / sl_dist < cfg["minRR"]:
            # !enableLiquidityTp -> usa RR
            tp = entry + sl_dist * cfg["tpRR"] if is_long else entry - sl_dist * cfg["tpRR"]

        rr = abs(tp - entry) / sl_dist
        if rr < cfg["minRR"]:
            continue

        signals.append({
            "direction": "long" if is_long else "short",
            "entryPrice": entry,
            "sl": sl,
            "tp": tp,
            "isBOS": st["isBOS"],
            "reason": f"{st['label']} {'↑' if is_long else '↓'} V2",
            "slDist": sl_dist,
            "rr": round(rr, 2),
            "entryIdx1m": entry_idx1m,
            "entryTime": entry_bar["time"],
        })

    # ordenar por tiempo de entrada y deduplicar
    signals.sort(key=lambda s: s["entryTime"])
    seen, deduped = set(), []
    for s in signals:
        if s["entryTime"] in seen:
            continue
        seen.add(s["entryTime"])
        deduped.append(s)
    return deduped


def senal_smc_v2(c1m, c15m, c30m, cfg=None):
    """Calcula la señal SMC V2 más RECIENTE desde los arrays de velas.

    Devuelve:
      {"setup": bool, "signal": {...}|None, "n_signals": int, "barras_atras": int|None}
    'barras_atras' = cuántas velas de 1m pasaron desde la entrada (frescura).
    """
    cfg = {**SMC_V2_CONFIG, **(cfg or {})}
    sigs = _signals(c1m, c15m, c30m, cfg)
    if not sigs:
        return {"setup": False, "signal": None, "n_signals": 0, "barras_atras": None}
    last = sigs[-1]
    ei = last["entryIdx1m"]
    barras_atras = (len(c1m) - 1 - ei) if ei is not None else None

    # ¿El precio tocó el SL o el TP DESDE la entrada? (para juzgar si el setup sigue vivo)
    toco_sl = toco_tp = False
    if ei is not None:
        for b in c1m[ei + 1:]:
            if last["direction"] == "long":
                if b["low"] <= last["sl"]:
                    toco_sl = True
                if b["high"] >= last["tp"]:
                    toco_tp = True
            else:
                if b["high"] >= last["sl"]:
                    toco_sl = True
                if b["low"] <= last["tp"]:
                    toco_tp = True

    sig = {
        "direccion": "LONG" if last["direction"] == "long" else "SHORT",
        "entrada": round(last["entryPrice"], 5),
        "sl": round(last["sl"], 5),
        "tp": round(last["tp"], 5),
        "rr": last["rr"],
        "tipo_estructura": "BOS" if last["isBOS"] else "CHoCH",
        "razon": last["reason"],
    }
    return {"setup": True, "signal": sig, "n_signals": len(sigs),
            "barras_atras": barras_atras, "toco_sl": toco_sl, "toco_tp": toco_tp}
