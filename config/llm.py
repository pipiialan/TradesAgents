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


# Opus 4.7/4.8 y Fable 5 / Mythos 5 ELIMINARON temperature/top_p/top_k: enviarlos da 400.
_MODELOS_SIN_SAMPLING = ("claude-opus-4-7", "claude-opus-4-8", "claude-fable-5", "claude-mythos-5")


def _sin_sampling(model: str) -> bool:
    """True si el modelo rechaza temperature (Opus 4.7/4.8). Entonces no se lo mandamos."""
    return any(x in (model or "").lower() for x in _MODELOS_SIN_SAMPLING)


# Modelos de la API directa de Anthropic que soportan el parámetro effort (incl. max):
# Opus 4.6/4.7/4.8 y Fable 5 / Mythos 5. El CLI/puente usa 'openai/...' (effort por nombre) -> NO se toca.
_EFFORT_MAX_OK = ("claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
                  "claude-fable-5", "claude-mythos-5")


def _usa_effort_max(model: str) -> bool:
    """True si es un modelo Anthropic por API directa que soporta effort. CLI ('openai/...') no."""
    m = (model or "").lower()
    if m.startswith("openai/"):           # CLI/puente: no aplica
        return False
    return any(x in m for x in _EFFORT_MAX_OK)


def _split_effort(model: str) -> tuple[str, str | None]:
    """'claude-opus-4-8::max' -> ('claude-opus-4-8', 'max'). Sin sufijo -> (model, None).
    El dropdown de la API codifica el nivel de razonamiento así."""
    if "::" in (model or ""):
        base, eff = model.split("::", 1)
        return base.strip(), eff.strip().lower()
    return model, None


async def complete(system: str, user: str, model: str,
                   api_key: str | None = None, api_base: str | None = None,
                   json_mode: bool = True) -> str:
    """Una llamada de chat. Si se pasa api_key, se usa esa (modo app); si no, LiteLLM usa la env var."""
    model, effort = _split_effort(model)   # 'claude-opus-4-8::max' -> ('claude-opus-4-8', 'max')
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if not _sin_sampling(model):      # Opus 4.7/4.8 rechazan temperature (400)
        kwargs["temperature"] = 0.3
    # Effort (razonamiento): el que venga del dropdown; si no viene, default 'max' en Opus por API.
    # SOLO API directa de Anthropic con Opus (el CLI maneja su effort por nombre de modelo). Sin thinking.
    if _usa_effort_max(model):
        nivel = effort or "max"
        kwargs["extra_body"] = {"output_config": {"effort": nivel}}
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
        kwargs.pop("temperature", None)        # algunos modelos (Opus 4.7/4.8) la rechazan
        kwargs.pop("extra_body", None)         # degradación elegante si el effort no se acepta
        resp = await litellm.acompletion(**kwargs)

    return resp["choices"][0]["message"]["content"] or ""
