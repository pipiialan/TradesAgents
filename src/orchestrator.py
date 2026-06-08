"""Jefe IA: lanza el pool correcto (6 traders) + noticias en paralelo y arma el consenso.

Motor agnóstico de proveedor. El proveedor + la API key pueden venir:
  - de .env (modo CLI), o
  - por parámetro (modo app web: el usuario los manda en cada petición).
"""
import asyncio
import json
import os
import re
from pathlib import Path

from .persona_loader import load_persona, load_pool
from .web_search import buscar
from .smc_v2 import senal_smc_v2
from . import news_cache
from config.instruments import INSTRUMENTS, POOL_DIRS, pool_de
from config.llm import complete, models_for, MODEL_TEAM, MODEL_JEFE

BASE = Path(__file__).resolve().parents[1]

# Límite de llamadas LLM en paralelo. 6 = los 6 traders corren en UNA sola tanda.
# Si el CLI/proveedor empieza a frenar (throttle), bajalo a 4.
_SEM = asyncio.Semaphore(6)


def _api_base(provider: str | None) -> str | None:
    """claudecli enruta al puente local (suscripción); LiteLLM honra api_base con prefijo openai/."""
    if (provider or "").lower() == "claudecli":
        return os.getenv("LLM_API_BASE") or "http://127.0.0.1:8788/v1"
    return None


def _parse_json(raw: str) -> dict:
    """Extrae el primer bloque JSON del texto del agente (tolera ```json fences)."""
    if not raw:
        return {"error": "respuesta vacía"}
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"error": "sin JSON", "raw": raw}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"error": f"JSON inválido: {e}", "raw": raw}


async def _run_agent(persona: dict, user_prompt: str, model: str, api_key: str | None,
                     api_base: str | None = None) -> dict:
    """Corre un agente: su system prompt (esencia) + el contexto -> JSON parseado."""
    async with _SEM:
        raw = await complete(persona["prompt"], user_prompt, model, api_key=api_key, api_base=api_base)
    return _parse_json(raw)


def _prompt_trader(par: str, contexto: dict, noticias: dict) -> str:
    modo = contexto.get("modo", "scalping")
    sn = contexto.get("sesion_ny", {})
    nota_pool = ("El ORO opera casi 24h (Asia/London/NY)." if pool_de(par) == "oro"
                 else "ICT y ORB rinden mejor en killzone/apertura de NY.")
    linea_sesion = (
        f"Hora de Nueva York: {sn.get('hora_ny', '?')} ({sn.get('dia', '')}) — sesión: {sn.get('sesion', '?')}, "
        f"volatilidad {sn.get('volatilidad', '?')} ({sn.get('comportamiento', '')}). {nota_pool}\n"
        f"USA el perfil de la sesión para ajustar tu CONFIANZA y tamaño (más volatilidad = movimientos más reales/fiables; baja = cautela, posible choppy). "
        f"NO devuelvas NO-TRADE solo por la hora: si hay un setup válido, opéralo con la confianza que merezca la sesión. "
        f"Esta lógica de sesión tiene prioridad sobre cualquier regla de horario fija de tu metodología.\n"
        f"SIEMPRE da tu MEJOR lectura con convicción alta/media/baja. Reserva NO-TRADE solo para cuando NO hay NINGÚN setup razonable; "
        f"si hay algo operable aunque no sea perfecto, dalo con confianza baja en vez de callarte. El horario nunca bloquea: solo modula tu confianza.\n"
    )
    reg = (contexto or {}).get("regimen")
    if modo == "swing":
        linea_sesion = (
            f"MODO SWING (holds ~1-5 días): operas en 1D (sesgo de fondo) / 4h (estructura) / 1h (ejecución y gatillo). "
            f"La sesión intradía NO importa para tu decisión (el horario casi no afecta el swing). "
            f"FILTRO DE RÉGIMEN común (MA200 diaria, estilo Paul Tudor Jones): {json.dumps(reg, ensure_ascii=False) if reg else 'sin datos suficientes para MA200'}. "
            f"Prioriza ir a FAVOR del régimen; en contra exige una señal mucho más limpia y baja la confianza. "
            f"Tu SL/TP y vigencia se miden en HORAS-DÍAS (no minutos): da 'vigencia_min' acorde (ej. 240-2880).\n"
            f"SIEMPRE da tu mejor lectura con convicción alta/media/baja; NO-TRADE solo si de verdad no hay setup.\n"
        )
    return (
        f"Par a analizar: {par} ({INSTRUMENTS[par]['nombre']}).\n"
        f"{linea_sesion}"
        f"Modo de operación: {modo} — ajusta tu enfoque de temporalidades a este estilo.\n"
        f"Cada TF trae velas (oldest->newest) + indicadores calculados (ema9, ema20, ema50, sma200, adx, vwap).\n"
        f"Contexto de mercado (multi-TF):\n{json.dumps(contexto, ensure_ascii=False, indent=2)}\n\n"
        f"Resumen de noticias del analista:\n{json.dumps(noticias, ensure_ascii=False, indent=2)}\n\n"
        "Si tu entrada es LIMIT o STOP, incluye 'vigencia_min' (minutos que tu setup sigue válido antes de cancelar; un scalp suele ser pocos minutos). "
        "Aplica TU metodología y devuelve solo tu JSON de veredicto."
    )


def _prompt_jefe(par: str, veredictos: list[dict], noticias: dict, con_bot: bool = True) -> str:
    intro_bot = (
        ("Agrega los veredictos de los 7 participantes: 6 traders + el BOT SMC V2 (trader='smc-v2-bot') y el análisis de noticias.\n"
         "El 'smc-v2-bot' cuenta EXACTAMENTE IGUAL que cualquier trader: MISMO peso, un voto más. Su LONG/SHORT/NO-TRADE pesa "
         "como el de cualquiera — ni más ni menos. NO lo trates como confirmación especial ni lo ignores ni dejes que su narrativa "
         "domine: es uno más del equipo. Si da señal, puedes usar sus niveles exactos igual que los de cualquier trader.\n"
         "CUENTA LOS VOTOS de los 7 y decide por mayoría: si 4 o más coinciden en un lado, DA esa operación con la convicción que toque. "
         "NUNCA devuelvas SIN-SETUP cuando hay mayoría clara (4+ de 7) en un lado.\n")
        if con_bot else
        ("Agrega los veredictos de los 6 traders de SWING + el análisis de noticias (en este modo NO hay bot SMC V2).\n"
         "Es swing intradía-días (holds ~1-5 días) en 1D (sesgo) / 4h (estructura) / 1h (ejecución). "
         "Respeta el FILTRO DE RÉGIMEN MA200 diaria (estilo PTJ) que traen en el contexto: prioriza operaciones a favor del "
         "régimen; en contra del régimen exige consenso más fuerte y reduce el riesgo. La vigencia de las órdenes es de HORAS-DÍAS, no minutos.\n")
    )
    return (
        f"Par: {par}. {intro_bot}"
        "Sobre NOTICIAS: si el analista tiene confianza baja (<=3) o no tiene feed/datos en vivo, trátalo como NEUTRAL — NO degrades ni "
        "marques SIN-SETUP por ventanas especulativas. NUNCA des SIN-SETUP si hay mayoría clara (4+ traders del mismo lado): da la "
        "operación con la convicción que corresponda. Solo ESPERAR si hay un evento de noticias CONFIRMADO activo AHORA.\n\n"
        f"Veredictos:\n{json.dumps(veredictos, ensure_ascii=False, indent=2)}\n\n"
        f"Noticias:\n{json.dumps(noticias, ensure_ascii=False, indent=2)}\n\n"
        "Aplica tus reglas de consenso y filtro de noticias. Devuelve solo tu JSON final."
    )


async def _noticias(par: str, fecha: str, forzar: bool, model: str, api_key: str | None,
                    api_base: str | None = None) -> dict:
    if not news_cache.needs_refresh(fecha, par, force=forzar):
        return news_cache.get(fecha, par)

    persona = load_persona(BASE / "agents" / "news" / "news-analyst.md")
    web = await buscar(par)
    prompt = (
        f"Par: {par}. Fecha de análisis: {fecha}.\n"
        "No hay análisis en caché para hoy (o se forzó refresh). Analiza los 3 horizontes "
        "(régimen, calendario, sorpresas de hoy) y devuelve tu JSON.\n\n"
        f"Resultados de búsqueda web reciente:\n{web}"
    )
    payload = await _run_agent(persona, prompt, model, api_key, api_base)
    news_cache.set_entry(fecha, par, payload, timestamp=fecha)
    return payload


# ── BOT SMC V2 (réplica bit-perfect) + capa determinista de tipo de orden ──
def _bars_para_bot(raw: dict, tf: str, n: int):
    """Mapea las velas del exporter {t,o,h,l,c,v} -> {time,open,...} para el bot.
    Devuelve None si faltan timestamps (exporter sin recompilar)."""
    barras = ((raw.get("timeframes") or {}).get(tf) or {}).get("ultimas_barras") or []
    out = []
    for b in barras[-n:]:
        if "t" not in b:
            return None
        out.append({"time": b["t"], "open": b["o"], "high": b["h"],
                    "low": b["l"], "close": b["c"], "volume": b.get("v", 0)})
    return out


def _tipo_orden_bot(direccion: str, entrada: float, precio):
    """Decide MARKET/LIMIT/STOP según dónde está el precio vs la entrada del bot."""
    if precio is None:
        return "MARKET", "precio actual desconocido -> MARKET."
    tol = abs(entrada) * 0.0007
    if abs(precio - entrada) <= tol:
        return "MARKET", f"precio {precio} pegado a la entrada {entrada} -> entra ya (MARKET)."
    if direccion == "LONG":
        return (("LIMIT", f"precio {precio} por encima de la entrada {entrada} -> LIMIT al retroceso.")
                if precio > entrada else
                ("STOP", f"precio {precio} por debajo de la entrada {entrada} -> STOP a la ruptura."))
    return (("LIMIT", f"precio {precio} por debajo de la entrada {entrada} -> LIMIT al rebote.")
            if precio < entrada else
            ("STOP", f"precio {precio} por encima de la entrada {entrada} -> STOP a la ruptura."))


async def bot_smc_v2_card(raw: dict, contexto: dict, par: str,
                          model: str, api_key: str | None, api_base: str | None = None) -> dict:
    """BOT SMC V2 (bit-perfect) + AGENTE SMC que verifica validez y decide el tipo de orden.
    SIEMPRE usa 30m/15m/1m del dato crudo (independiente del modo). Los niveles
    (entrada/SL/TP) son SIEMPRE los del bot: el agente solo juzga validez + tipo de orden."""
    base = {"trader": "smc-v2-bot", "es_bot": True, "par": par, "temporalidad": "30m·15m·1m"}

    def no_trade(razon, conf=0):
        return {**base, "veredicto": "NO-TRADE", "tipo_orden": None, "entrada": None,
                "sl": None, "tp": None, "rr": None, "vigencia_min": None,
                "confianza": conf, "razon": razon}

    c1m = _bars_para_bot(raw, "1m", 150)
    c15m = _bars_para_bot(raw, "15m", 200)
    c30m = _bars_para_bot(raw, "30m", 200)
    if c1m is None or c15m is None or c30m is None:
        return no_trade("Faltan timestamps en el exporter: recompila el indicador (1m=150 + 't') y re-agrégalo al chart.")

    res = senal_smc_v2(c1m, c15m, c30m)
    if not res["setup"]:
        return no_trade("El bot SMC V2 no detecta setup (sin OB de 15m + retest con RR>=1).")

    sig = res["signal"]
    d, e, sl, tp = sig["direccion"], sig["entrada"], sig["sl"], sig["tp"]
    precio = raw.get("precio_actual")
    riesgo = abs(e - sl) or 1.0
    dist = abs(precio - e) if precio is not None else None
    datos = {
        "direccion": d, "entrada": e, "sl": sl, "tp": tp, "rr": sig["rr"],
        "tipo_estructura": sig["tipo_estructura"], "barras_atras": res["barras_atras"],
        "toco_sl": res["toco_sl"], "toco_tp": res["toco_tp"], "precio_actual": precio,
        "distancia_a_entrada": (round(dist, 5) if dist is not None else None),
        "distancia_en_riesgos": (round(dist / riesgo, 2) if dist is not None else None),
    }
    tipo_det, _nota = _tipo_orden_bot(d, e, precio)

    # ── Agente SMC: verifica validez + decide tipo de orden ──────────────────
    persona = load_persona(BASE / "agents" / "bot" / "smc-v2-bot.md")
    sn = (contexto or {}).get("sesion_ny", {})
    niveles = {"pivots": (contexto or {}).get("pivots"), "niveles_clave": raw.get("niveles_clave"),
               "order_flow_cvd": (raw.get("order_flow") or {}).get("cvd_sesion")}
    prompt = (
        f"Par: {par}. Modo: {(contexto or {}).get('modo', 'scalping')}.\n"
        f"Sesión NY: {sn.get('hora_ny', '?')} {sn.get('sesion', '')} (volatilidad {sn.get('volatilidad', '?')}).\n"
        f"Señal del BOT SMC V2 + datos de validez:\n{json.dumps(datos, ensure_ascii=False, indent=2)}\n"
        f"Niveles del contexto: {json.dumps(niveles, ensure_ascii=False)}\n\n"
        "Verifica con criterio SMC si la señal SIGUE válida y decide el tipo de orden. Devuelve tu JSON."
    )
    out = await _run_agent(persona, prompt, model, api_key, api_base)

    # Fallback determinista si la IA falla o devuelve algo inválido.
    if not isinstance(out, dict) or out.get("error") or out.get("veredicto") not in ("LONG", "SHORT", "NO-TRADE"):
        if res["toco_sl"]:
            return no_trade("El precio ya tocó el SL desde la entrada: setup invalidado.")
        if res["toco_tp"]:
            return no_trade("El precio ya alcanzó el TP desde la entrada: movimiento ocurrido.")
        return {**base, "veredicto": d, "tipo_orden": tipo_det, "entrada": e, "sl": sl, "tp": tp,
                "rr": sig["rr"], "vigencia_min": 15, "tipo_estructura": sig["tipo_estructura"],
                "barras_atras": res["barras_atras"], "confianza": 7,
                "razon": f"Bot SMC V2 ({sig['tipo_estructura']}) {d}; capa IA no disponible, tipo de orden por regla ({tipo_det})."}

    ve = out["veredicto"].upper()
    op = ve != "NO-TRADE"
    return {**base, "veredicto": ve,
            "tipo_orden": (out.get("tipo_orden") if op else None),
            "entrada": (e if op else None), "sl": (sl if op else None), "tp": (tp if op else None),
            "rr": (sig["rr"] if op else None), "vigencia_min": out.get("vigencia_min"),
            "tipo_estructura": sig["tipo_estructura"], "barras_atras": res["barras_atras"],
            "confianza": out.get("confianza", 7), "razon": out.get("razon", sig["razon"])}


def _votos(veredictos: list[dict]) -> dict:
    """Cuenta votos de TODOS los participantes (los 6 traders + el bot, peso IGUAL)."""
    up = lambda v: (v.get("veredicto") or "").upper()
    return {
        "long": sum(1 for v in veredictos if up(v) == "LONG"),
        "short": sum(1 for v in veredictos if up(v) == "SHORT"),
        "no_trade": sum(1 for v in veredictos if up(v) == "NO-TRADE"),
    }


def _mediana(xs: list):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 2)


def _red_seguridad_consenso(decision: dict, veredictos: list[dict], raw: dict | None, modo: str) -> dict:
    """Si hay mayoría clara (4+ del mismo lado, contando el bot) pero el Jefe NO concretó
    una operación (SIN-SETUP/ESPERAR, o su llamada falló), arma el plan por consenso de
    forma determinista para NO quedarnos sin decisión. Si el Jefe ya operó, se respeta."""
    if not isinstance(decision, dict):
        decision = {}
    votos = _votos(veredictos)
    lado = "LONG" if votos["long"] >= votos["short"] else "SHORT"
    n = votos[lado.lower()]
    otro = votos["short" if lado == "LONG" else "long"]
    if n < 4 or n <= otro:
        return decision  # sin mayoría clara: respeta al Jefe

    conv = (decision.get("convicción") or decision.get("conviccion") or "").upper()
    dirj = (decision.get("direccion") or "").upper()
    operable = dirj in ("LONG", "SHORT") and conv in ("ALTA", "MEDIA", "BAJA") and decision.get("entrada") is not None
    if operable:
        return decision  # el Jefe ya entregó una operación: no la tocamos

    # El Jefe no concretó pese a la mayoría -> plan por consenso (mediana de los que coinciden).
    coinciden = [v for v in veredictos if (v.get("veredicto") or "").upper() == lado]
    entrada = _mediana([v.get("entrada") for v in coinciden])
    sl = _mediana([v.get("sl") for v in coinciden])
    tp = _mediana([v.get("tp") for v in coinciden])
    precio = (raw or {}).get("precio_actual")
    if entrada is None:
        entrada = precio
    tipo, _nota = _tipo_orden_bot(lado, entrada, precio) if entrada is not None else ("MARKET", "")
    rr = round(abs(tp - entrada) / abs(entrada - sl), 2) if (entrada and sl and tp and abs(entrada - sl) > 0) else None
    conv_new = "ALTA" if n >= 5 else "MEDIA"
    vig = {"scalping": 10, "scalping2": 10, "intradia": 60, "swing": 720}.get(modo, 30)
    decision = dict(decision)
    decision.update({
        "convicción": conv_new,
        "direccion": lado,
        "votos": votos,
        "tipo_orden": tipo,
        "entrada": entrada,
        "sl": sl, "tp": tp, "rr": rr,
        "vigencia_min": (vig if tipo in ("LIMIT", "STOP") else None),
        "riesgo_sugerido_pct": (1.5 if n >= 5 else 0.75),
        "_red_seguridad": True,
        "resumen": (f"⚙️ Red de seguridad: {n}/7 coinciden en {lado} pero el Jefe no concretó; "
                    f"la app armó el plan por consenso (mediana de entradas/SL/TP de los que coinciden). "
                    + (decision.get("resumen") or "")),
    })
    return decision


async def analizar(par: str, contexto: dict, fecha: str,
                   provider: str | None = None, api_key: str | None = None,
                   model_team: str | None = None, model_jefe: str | None = None,
                   forzar_noticias: bool = False, raw: dict | None = None) -> dict:
    """Análisis completo de un par. provider/api_key/modelos vienen de la app; si no, usa los de .env."""
    par = par.upper()
    pool = pool_de(par)
    modo = contexto.get("modo", "scalping")
    if modo == "scalping2":
        pool_dir = POOL_DIRS[pool] + "-scalping2"     # indices-scalping2 (Nasdaq) u oro-scalping2 (Gold)
    elif modo == "intradia":
        pool_dir = POOL_DIRS[pool] + "-intradia"
    elif modo == "swing":
        pool_dir = POOL_DIRS[pool] + "-swing"         # indices-swing u oro-swing (1D/4h/1h, holds días)
    else:
        pool_dir = POOL_DIRS[pool]
    traders = load_pool(BASE / pool_dir)

    mt, mj = models_for(provider) if provider else (MODEL_TEAM, MODEL_JEFE)
    if model_team:
        mt = model_team
    if model_jefe:
        mj = model_jefe

    api_base = _api_base(provider)

    noticias = await _noticias(par, fecha, forzar_noticias, mt, api_key, api_base)

    prompt_t = _prompt_trader(par, contexto, noticias)
    veredictos = list(await asyncio.gather(*[_run_agent(t, prompt_t, mt, api_key, api_base) for t in traders]))

    # 7ª card: BOT SMC V2 (determinista, bit-perfect) sobre 30m/15m/1m del dato crudo.
    # NO entra en "scalping2" (equipo Nasdaq pro limpio) ni en "swing" (el bot es
    # intradía 30m/15m/1m; en swing los TF son 1D/4h/1h y no aplica).
    con_bot = raw is not None and modo not in ("scalping2", "swing")
    if con_bot:
        try:
            veredictos.append(await bot_smc_v2_card(raw, contexto, par, mt, api_key, api_base))
        except Exception as e:  # noqa: BLE001
            veredictos.append({"trader": "smc-v2-bot", "es_bot": True, "veredicto": "NO-TRADE",
                               "confianza": 0, "razon": f"error del bot: {e}"})

    jefe = load_persona(BASE / "agents" / "orchestrator" / "jefe-ia.md")
    decision = await _run_agent(jefe, _prompt_jefe(par, veredictos, noticias, con_bot=con_bot), mj, api_key, api_base)
    # Red de seguridad: con mayoría clara (4+ de 7) el equipo SIEMPRE entrega operación,
    # aunque el Jefe se haya quedado sin decidir o su llamada haya fallado.
    decision = _red_seguridad_consenso(decision, veredictos, raw, modo)

    return {
        "par": par,
        "fecha": fecha,
        "pool": pool,
        "modelos": {"equipo": mt, "jefe": mj},
        "noticias": noticias,
        "veredictos": veredictos,
        "jefe": decision,
    }


def _prompt_jefe_revalidar(par, veredictos, noticias, precio_ahora, precio_analisis, niveles) -> str:
    mov = (round(precio_ahora - precio_analisis, 4)
           if (precio_ahora is not None and precio_analisis is not None) else "?")
    return (
        f"REVALIDACIÓN RÁPIDA para {par}.\n"
        f"El equipo analizó hace unos minutos con el precio en {precio_analisis}. "
        f"AHORA el precio es {precio_ahora} (se movió {mov}).\n"
        f"Niveles/contexto actuales: {json.dumps(niveles, ensure_ascii=False)}\n\n"
        f"Veredictos del equipo (su lectura sigue válida como sesgo):\n"
        f"{json.dumps(veredictos, ensure_ascii=False, indent=2)}\n\n"
        f"Noticias:\n{json.dumps(noticias, ensure_ascii=False, indent=2)}\n\n"
        "Con el precio ACTUAL, RE-DECIDE y AJUSTA el plan: direccion, tipo_orden, entrada, sl, tp, vigencia_min. "
        "Si el precio ya pasó la zona de entrada, ya tocó el TP, o la entrada quedaría pegada al SL, "
        "AJUSTA a un nivel válido o devuelve conviccion ESPERAR / direccion NO-TRADE con el motivo. "
        "Devuelve SOLO tu JSON final con tu formato habitual."
    )


async def revalidar(par: str, contexto: dict, veredictos: list[dict], noticias: dict, *,
                    provider: str | None = None, api_key: str | None = None,
                    model_jefe: str | None = None, precio_analisis: float | None = None) -> dict:
    """Re-corre SOLO al Jefe (mismo modelo, full inteligencia) con los veredictos ya
    calculados + el precio ACTUAL, para ajustar el plan sin re-analizar los 6 traders."""
    par = par.upper()
    mj = model_jefe or (models_for(provider)[1] if provider else MODEL_JEFE)
    api_base = _api_base(provider)

    precio_ahora = contexto.get("precio_actual")
    niveles = {
        "precio_actual": precio_ahora,
        "niveles_clave": contexto.get("niveles_clave"),
        "pivots": contexto.get("pivots"),
        "order_flow": contexto.get("order_flow"),
        "sesion_ny": contexto.get("sesion_ny"),
    }
    jefe = load_persona(BASE / "agents" / "orchestrator" / "jefe-ia.md")
    prompt = _prompt_jefe_revalidar(par, veredictos, noticias, precio_ahora, precio_analisis, niveles)
    decision = await _run_agent(jefe, prompt, mj, api_key, api_base)
    return {
        "par": par,
        "revalidado": True,
        "precio_actual": precio_ahora,
        "precio_analisis": precio_ahora,   # nueva referencia para el próximo drift
        "modelos": {"jefe": mj},
        "jefe": decision,
    }
