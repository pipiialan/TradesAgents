"""Capa LLM agnóstica de proveedor (vía LiteLLM).

Dos modos de uso:
  - CLI: el proveedor/modelo/clave salen de .env (PROVIDER + *_API_KEY).
  - App web: el usuario manda provider + api_key en cada petición (override de .env).
"""
import os

import litellm
from dotenv import load_dotenv

load_dotenv()

# Modelos por defecto de cada proveedor: (equipo de traders | Jefe IA).
PROVIDERS = {
    "groq":       {"team": "groq/llama-3.3-70b-versatile",          "jefe": "groq/llama-3.3-70b-versatile"},
    "openai":     {"team": "gpt-4o-mini",                           "jefe": "gpt-4o"},
    "gemini":     {"team": "gemini/gemini-2.5-flash",               "jefe": "gemini/gemini-2.5-flash"},
    "anthropic":  {"team": "claude-haiku-4-5",                      "jefe": "claude-sonnet-4-5"},
    "openrouter": {"team": "openrouter/qwen/qwen-2.5-72b-instruct", "jefe": "openrouter/qwen/qwen-2.5-72b-instruct"},
    # Puente local al CLI de Claude (suscripcion, NO API key). El prefijo openai/
    # hace que LiteLLM honre api_base (LLM_API_BASE -> http://127.0.0.1:8787/v1).
    "claudecli":  {"team": "openai/claude-team",                    "jefe": "openai/claude-jefe"},
}

PROVIDER = (os.getenv("PROVIDER") or "groq").lower()
_defaults = PROVIDERS.get(PROVIDER, PROVIDERS["groq"])

# Defaults para el modo CLI (override con LLM_MODEL / LLM_MODEL_JEFE).
MODEL_TEAM = os.getenv("LLM_MODEL") or _defaults["team"]
MODEL_JEFE = os.getenv("LLM_MODEL_JEFE") or _defaults["jefe"]


def models_for(provider: str) -> tuple[str, str]:
    """Devuelve (modelo_equipo, modelo_jefe) para un proveedor dado."""
    p = PROVIDERS.get((provider or "").lower(), PROVIDERS["groq"])
    return p["team"], p["jefe"]


async def complete(system: str, user: str, model: str,
                   api_key: str | None = None, api_base: str | None = None,
                   json_mode: bool = True) -> str:
    """Una llamada de chat. Si se pasa api_key, se usa esa (modo app); si no, LiteLLM usa la env var."""
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    if api_key:
        kwargs["api_key"] = api_key
    # api_base sigue siendo opcional per-request; si no llega, sale del .env
    # (LLM_API_BASE) para apuntar al puente local sin tocar el orquestador.
    base = api_base or os.getenv("LLM_API_BASE")
    if base:
        kwargs["api_base"] = base
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = await litellm.acompletion(**kwargs)
    except Exception:
        kwargs.pop("response_format", None)
        resp = await litellm.acompletion(**kwargs)

    return resp["choices"][0]["message"]["content"] or ""
