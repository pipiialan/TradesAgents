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
from . import news_cache
from config.instruments import INSTRUMENTS, POOL_DIRS, pool_de
from config.llm import complete, models_for, MODEL_TEAM, MODEL_JEFE

BASE = Path(__file__).resolve().parents[1]

# Límite de llamadas LLM en paralelo (evita rate limits del proveedor).
_SEM = asyncio.Semaphore(4)


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
    )
    return (
        f"Par a analizar: {par} ({INSTRUMENTS[par]['nombre']}).\n"
        f"{linea_sesion}"
        f"Modo de operación: {modo} — ajusta tu enfoque de temporalidades a este estilo.\n"
        f"Cada TF trae velas (oldest->newest) + indicadores calculados (ema9, ema20, adx, vwap).\n"
        f"Contexto de mercado (multi-TF):\n{json.dumps(contexto, ensure_ascii=False, indent=2)}\n\n"
        f"Resumen de noticias del analista:\n{json.dumps(noticias, ensure_ascii=False, indent=2)}\n\n"
        "Aplica TU metodología y devuelve solo tu JSON de veredicto."
    )


def _prompt_jefe(par: str, veredictos: list[dict], noticias: dict) -> str:
    return (
        f"Par: {par}. Agrega los siguientes veredictos de los 6 traders y el análisis de noticias.\n\n"
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


async def analizar(par: str, contexto: dict, fecha: str,
                   provider: str | None = None, api_key: str | None = None,
                   model_team: str | None = None, model_jefe: str | None = None,
                   forzar_noticias: bool = False) -> dict:
    """Análisis completo de un par. provider/api_key/modelos vienen de la app; si no, usa los de .env."""
    par = par.upper()
    pool = pool_de(par)
    traders = load_pool(BASE / POOL_DIRS[pool])

    mt, mj = models_for(provider) if provider else (MODEL_TEAM, MODEL_JEFE)
    if model_team:
        mt = model_team
    if model_jefe:
        mj = model_jefe

    # claudecli enruta al puente local (suscripción); LiteLLM honra api_base con el prefijo openai/.
    api_base = None
    if (provider or "").lower() == "claudecli":
        api_base = os.getenv("LLM_API_BASE") or "http://127.0.0.1:8788/v1"

    noticias = await _noticias(par, fecha, forzar_noticias, mt, api_key, api_base)

    prompt_t = _prompt_trader(par, contexto, noticias)
    veredictos = await asyncio.gather(*[_run_agent(t, prompt_t, mt, api_key, api_base) for t in traders])

    jefe = load_persona(BASE / "agents" / "orchestrator" / "jefe-ia.md")
    decision = await _run_agent(jefe, _prompt_jefe(par, list(veredictos), noticias), mj, api_key, api_base)

    return {
        "par": par,
        "fecha": fecha,
        "pool": pool,
        "modelos": {"equipo": mt, "jefe": mj},
        "noticias": noticias,
        "veredictos": list(veredictos),
        "jefe": decision,
    }
