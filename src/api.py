"""Servidor web: sirve la pantalla y expone /analizar.

El usuario pega su API key en la pantalla; se envía aquí y se usa solo para esa
petición (no se guarda en disco).
"""
import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .orchestrator import analizar, revalidar
from .market_data import get_context, precio_actual
from .features import preparar_contexto, chequear_datos
from config.instruments import INSTRUMENTS

BASE = Path(__file__).resolve().parents[1]
app = FastAPI(title="TradingAgents")


class AnalisisReq(BaseModel):
    par: str
    provider: str = "groq"
    api_key: str
    model_team: str = ""
    model_jefe: str = ""
    modo: str = "scalping"        # scalping | scalping2 | intradia | swing (define la ventana de velas y el pool)
    forzar_noticias: bool = False


class OrdenReq(BaseModel):
    par: str
    direccion: str            # LONG | SHORT | FLAT
    qty: int = 1
    tipo: str = "MARKET"      # MARKET | LIMIT | STOP | LADDER
    entrada: float | None = None   # precio para LIMIT/STOP (null = market)
    vigencia_min: int | None = None  # minutos que la LIMIT/STOP vive antes de cancelarse
    sl: float | None = None
    tp: float | None = None
    tps: list | None = None        # LADDER: TPs escalonados (scale-out: un TP por escalón)
    escalones: list | None = None  # LADDER: [{"precio":x,"pct":n}] niveles de la escalera
    cuenta: str = "Sim101"
    be_on: bool = False            # breakeven automático activado
    be_trigger_pct: float = 1.0    # % de movimiento a favor que activa el BE
    be_offset_pct: float = 0.3     # mover el SL a entrada + este % (ganancia bloqueada)
    precio_analisis: float | None = None  # precio al momento del análisis (para chequear drift)
    confirmado: bool = False  # gate: sin esto NO se escribe la orden para NinjaTrader


class RevalidarReq(BaseModel):
    par: str
    provider: str = "claudecli"
    api_key: str = ""
    model_jefe: str = ""
    modo: str = "scalping"
    veredictos: list = []          # veredictos cacheados del análisis (no se re-corren los traders)
    noticias: dict = {}
    precio_analisis: float | None = None


@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE / "web" / "index.html").read_text(encoding="utf-8")


@app.get("/instrumentos")
async def instrumentos():
    return {k: v["nombre"] for k, v in INSTRUMENTS.items()}


@app.post("/analizar")
async def analizar_ep(req: AnalisisReq):
    # claudecli usa la suscripcion del CLI (puente local): no requiere API key.
    es_cli = req.provider == "claudecli"
    key = req.api_key.strip() or None
    if not es_cli and not key:
        return JSONResponse({"error": "Falta la API key."}, status_code=400)
    # Datos en vivo de NinjaTrader (o stub). raw = todas las TFs (lo usa el bot SMC V2);
    # contexto = recortado al modo + indicadores (lo usan los 6 traders).
    raw = get_context(req.par)
    contexto = preparar_contexto(raw, req.modo)
    fecha = date.today().isoformat()
    try:
        res = await analizar(
            req.par, contexto, fecha,
            provider=req.provider, api_key=key,
            model_team=req.model_team or None, model_jefe=req.model_jefe or None,
            forzar_noticias=req.forzar_noticias, raw=raw,
        )
        res["precio_analisis"] = contexto.get("precio_actual")  # para el chequeo de drift al ejecutar
        res["advertencias_datos"] = chequear_datos(contexto, req.modo)  # avisa si faltó historia para el modo
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "api_key" in msg.lower() or "401" in msg or "invalid" in msg.lower():
            msg = "API key inválida o sin permisos para ese proveedor/modelo."
        return JSONResponse({"error": msg}, status_code=500)


@app.get("/precio")
async def precio_ep(par: str):
    """Precio actual en vivo (instantáneo, sin IA) para refrescar la pantalla antes de ejecutar."""
    return precio_actual(par)


@app.post("/revalidar")
async def revalidar_ep(req: RevalidarReq):
    """Re-corre SOLO al Jefe con los veredictos cacheados + precio actual (rápido, sin re-analizar)."""
    es_cli = req.provider == "claudecli"
    key = req.api_key.strip() or None
    if not es_cli and not key:
        return JSONResponse({"error": "Falta la API key."}, status_code=400)
    contexto = preparar_contexto(get_context(req.par), req.modo)
    try:
        res = await revalidar(
            req.par, contexto, req.veredictos, req.noticias,
            provider=req.provider, api_key=key,
            model_jefe=req.model_jefe or None, precio_analisis=req.precio_analisis,
        )
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)


def _chequeo_precio(orden: dict, info: dict, precio_analisis):
    """B: compara el precio ACTUAL vs el plan y devuelve (advertencias, severidad). Sin IA."""
    if orden["accion"] == "FLAT":
        return [], "ninguna"
    if not info.get("vivo") or info.get("precio") is None:
        return ["No hay precio en vivo de NinjaTrader: no se pudo verificar el movimiento. Cuidado."], "media"

    cur = float(info["precio"])
    dir_ = orden["accion"]
    tipo = orden["tipo"]
    sl, tp, entrada = orden.get("sl"), orden.get("tp"), orden.get("entrada")
    adv, sev = [], "ninguna"

    if precio_analisis is not None:
        adv.append(f"Precio al analizar: {precio_analisis} · ahora: {cur} · movió {round(cur - precio_analisis, 4):+}.")

    ref = entrada if (entrada and tipo in ("LIMIT", "STOP")) else precio_analisis

    # COHERENCIA tipo de orden vs precio en vivo (evita el rechazo del broker).
    # Buy Stop va ARRIBA del precio; Buy Limit ABAJO. (Sell al revés.)
    if tipo in ("LIMIT", "STOP") and entrada:
        tol = cur * 0.00005           # ~1-2 ticks: ignora cruces mínimos por redondeo
        malo = None
        if dir_ == "LONG":
            if tipo == "STOP" and entrada < cur - tol:
                malo = (f"STOP de compra @ {entrada} quedaría DEBAJO del precio actual ({cur}). "
                        f"Un Buy Stop debe ir ARRIBA del precio: el broker la rechaza. "
                        f"Usa LIMIT (comprar en retroceso) o MARKET (entrar ya).")
            elif tipo == "LIMIT" and entrada > cur + tol:
                malo = (f"LIMIT de compra @ {entrada} quedaría ARRIBA del precio actual ({cur}). "
                        f"Un Buy Limit debe ir ABAJO del precio. "
                        f"Usa STOP (comprar en ruptura) o MARKET (entrar ya).")
        else:  # SHORT
            if tipo == "STOP" and entrada > cur + tol:
                malo = (f"STOP de venta @ {entrada} quedaría ARRIBA del precio actual ({cur}). "
                        f"Un Sell Stop debe ir ABAJO del precio: el broker la rechaza. "
                        f"Usa LIMIT (vender en rebote) o MARKET (entrar ya).")
            elif tipo == "LIMIT" and entrada < cur - tol:
                malo = (f"LIMIT de venta @ {entrada} quedaría DEBAJO del precio actual ({cur}). "
                        f"Un Sell Limit debe ir ARRIBA del precio. "
                        f"Usa STOP (vender en ruptura) o MARKET (entrar ya).")
        if malo:
            return ["🛑 ORDEN INVÁLIDA: " + malo] + adv, "bloqueo"
        # Demasiado lejos del precio: posible rechazo por límites del producto (banding).
        if abs(entrada - cur) > 0.02 * cur:
            adv.append(f"La entrada ({entrada}) está MUY lejos del precio actual ({cur}): "
                       f"el broker podría rechazarla por límites de precio.")
            if sev == "ninguna":
                sev = "media"

    if sl:
        cruzo_sl = (dir_ == "LONG" and cur <= sl) or (dir_ == "SHORT" and cur >= sl)
        if cruzo_sl:
            adv.append(f"🚨 El precio ({cur}) ya está en/pasó el SL ({sl}): entrarías directo en pérdida.")
            sev = "alta"
        else:
            riesgo = abs((ref if ref else cur) - sl)
            margen = abs(cur - sl)
            if riesgo > 0 and margen < 0.25 * riesgo:
                adv.append(f"El precio ({cur}) está MUY cerca del SL ({sl}): solo {round(margen, 4)} de margen.")
                sev = "alta" if sev != "alta" else sev
                if sev == "ninguna":
                    sev = "media"

    if tp:
        cruzo_tp = (dir_ == "LONG" and cur >= tp) or (dir_ == "SHORT" and cur <= tp)
        if cruzo_tp:
            adv.append(f"El precio ({cur}) ya alcanzó el TP ({tp}): el movimiento ya ocurrió.")
            if sev == "ninguna":
                sev = "media"

    if tipo == "MARKET" and precio_analisis is not None and sl:
        riesgo = abs(precio_analisis - sl)
        adverso = (cur - precio_analisis) if dir_ == "LONG" else (precio_analisis - cur)
        if riesgo > 0 and adverso > 0.2 * riesgo:
            adv.append(f"En MARKET entrarías {round(adverso, 4)} peor que el setup ({precio_analisis}).")
            if sev == "ninguna":
                sev = "media"

    return adv, sev


@app.post("/ejecutar")
async def ejecutar_ep(req: OrdenReq):
    """Escribe la orden en data/order_request.json; el ejecutor de NinjaScript la toma."""
    import json as _json
    import time

    if req.direccion.upper() not in ("LONG", "SHORT", "FLAT"):
        return JSONResponse({"error": "dirección inválida"}, status_code=400)

    tipo = req.tipo.upper()
    if tipo not in ("MARKET", "LIMIT", "STOP", "LADDER"):
        tipo = "MARKET"

    if tipo == "LADDER" and req.escalones:
        # Escalera: reparte el TOTAL de micros entre los escalones (mín 1 c/u).
        # Si no alcanza para todos, reduce el número de escalones.
        n = len(req.escalones)
        total = max(1, req.qty)
        if total < n:
            req.escalones = req.escalones[:total]
            n = total
        base_q, extra = (total // n), (total - (total // n) * n)
        esc_out = []
        for idx, e in enumerate(req.escalones):
            try:
                precio = float(e.get("precio"))
            except (TypeError, ValueError, AttributeError):
                continue
            esc_out.append({"precio": precio, "qty": max(1, base_q + (1 if idx < extra else 0))})
        tps = [float(t) for t in (req.tps or []) if t] or ([float(req.tp)] if req.tp else [])
        orden = {
            "id": str(time.time()),
            "accion": req.direccion.upper(),
            "par": req.par,
            "qty": total,
            "tipo": "LADDER",
            "escalones": esc_out,
            "tps": tps,
            "vigencia_min": req.vigencia_min,
            "sl": req.sl,
            "cuenta": req.cuenta,
            "be_on": req.be_on,
            "be_trigger_pct": req.be_trigger_pct,
            "be_offset_pct": req.be_offset_pct,
        }
        advertencias = ["Orden ESCALONADA (ladder): revisa los niveles, el SL global y los TPs antes de confirmar."]
        severidad = "media"
    else:
        orden = {
            "id": str(time.time()),
            "accion": req.direccion.upper(),
            "par": req.par,
            "qty": max(1, req.qty),
            "tipo": tipo,
            "entrada": req.entrada,
            "vigencia_min": req.vigencia_min,
            "sl": req.sl,
            "tp": req.tp,
            "cuenta": req.cuenta,
            "be_on": req.be_on,
            "be_trigger_pct": req.be_trigger_pct,
            "be_offset_pct": req.be_offset_pct,
        }
        # B: chequeo de precio actual vs el plan (instantáneo, sin IA).
        advertencias, severidad = _chequeo_precio(orden, precio_actual(req.par), req.precio_analisis)

    # GATE DURO: una orden inválida para el precio actual (lado equivocado) NUNCA se envía,
    # ni siquiera con confirmado=True. El broker la rechazaría y detendría la estrategia.
    if severidad == "bloqueo":
        return JSONResponse({
            "bloqueado": True,
            "error": (advertencias[0] if advertencias else "Orden inválida para el precio actual."),
            "advertencias": advertencias,
            "orden": orden,
        }, status_code=409)

    # GATE server-side: sin confirmado=True NO se escribe nada para NinjaTrader.
    # Asi un POST directo (curl, otro cliente) tampoco puede disparar la orden.
    if not req.confirmado:
        return {
            "requiere_confirmacion": True,
            "orden": orden,
            "advertencias": advertencias,
            "severidad": severidad,
            "mensaje": "Revisa la orden y confírmala para enviarla a NinjaTrader.",
        }
    (BASE / "data" / "order_request.json").write_text(
        _json.dumps(orden, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "ok": True,
        "mensaje": f"Orden {orden['accion']} {orden['qty']} {orden['par']} enviada a la cuenta {orden['cuenta']}. "
                   "El ejecutor de NinjaTrader la tomará en segundos (debe estar habilitado en esa cuenta).",
        "orden": orden,
    }
