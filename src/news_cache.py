"""Caché de noticias por (fecha + par) para no gastar tokens re-analizando lo mismo.

Regla: si ya hay análisis de HOY para el par y no hay evento de alto impacto nuevo,
se reutiliza. Solo se refresca con: día nuevo, refresh forzado, o evento pendiente
del calendario que ya pasó desde el último análisis (TODO al conectar calendario real).
"""
import json
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "news_cache.json"


def _load() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save(data: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _key(fecha: str, par: str) -> str:
    return f"{fecha}:{par.upper()}"


def get(fecha: str, par: str):
    """Devuelve el payload de noticias cacheado para (fecha, par) o None."""
    entry = _load().get(_key(fecha, par))
    return entry["payload"] if entry else None


def set_entry(fecha: str, par: str, payload: dict, timestamp: str | None = None) -> None:
    data = _load()
    data[_key(fecha, par)] = {"timestamp": timestamp, "payload": payload}
    _save(data)


def needs_refresh(fecha: str, par: str, force: bool = False) -> bool:
    """True si hay que (re)analizar noticias. Por ahora: si no hay caché de hoy o se fuerza.

    TODO: marcar True también cuando un evento de alto impacto del calendario (CPI/FOMC/NFP)
    haya ocurrido despues del timestamp guardado.
    """
    if force:
        return True
    return get(fecha, par) is None
