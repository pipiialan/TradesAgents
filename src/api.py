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

from .orchestrator import analizar
from .market_data import get_context
from .features import preparar_contexto
from config.instruments import INSTRUMENTS

BASE = Path(__file__).resolve().parents[1]
app = FastAPI(title="TradingAgents")


class AnalisisReq(BaseModel):
    par: str
    provider: str = "groq"
    api_key: str
    model_team: str = ""
    model_jefe: str = ""
    modo: str = "scalping"        # scalping | intradia (define la ventana de velas)
    forzar_noticias: bool = False


class OrdenReq(BaseModel):
    par: str
    direccion: str            # LONG | SHORT | FLAT
    qty: int = 1
    tipo: str = "MARKET"      # MARKET | LIMIT | STOP
    entrada: float | None = None   # precio para LIMIT/STOP (null = market)
    vigencia_min: int | None = None  # minutos que la LIMIT/STOP vive antes de cancelarse
    sl: float | None = None
    tp: float | None = None
    cuenta: str = "Sim101"
    confirmado: bool = False  # gate: sin esto NO se escribe la orden para NinjaTrader


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
    # Datos en vivo de NinjaTrader (o stub), recortados al modo + indicadores calculados.
    contexto = preparar_contexto(get_context(req.par), req.modo)
    fecha = date.today().isoformat()
    try:
        res = await analizar(
            req.par, contexto, fecha,
            provider=req.provider, api_key=key,
            model_team=req.model_team or None, model_jefe=req.model_jefe or None,
            forzar_noticias=req.forzar_noticias,
        )
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "api_key" in msg.lower() or "401" in msg or "invalid" in msg.lower():
            msg = "API key inválida o sin permisos para ese proveedor/modelo."
        return JSONResponse({"error": msg}, status_code=500)


@app.post("/ejecutar")
async def ejecutar_ep(req: OrdenReq):
    """Escribe la orden en data/order_request.json; el ejecutor de NinjaScript la toma."""
    import json as _json
    import time

    if req.direccion.upper() not in ("LONG", "SHORT", "FLAT"):
        return JSONResponse({"error": "dirección inválida"}, status_code=400)

    tipo = req.tipo.upper()
    if tipo not in ("MARKET", "LIMIT", "STOP"):
        tipo = "MARKET"
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
    }
    # GATE server-side: sin confirmado=True NO se escribe nada para NinjaTrader.
    # Asi un POST directo (curl, otro cliente) tampoco puede disparar la orden.
    if not req.confirmado:
        return {
            "requiere_confirmacion": True,
            "orden": orden,
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
