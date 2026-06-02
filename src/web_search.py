"""Búsqueda web agnóstica de proveedor para el analista de noticias.

Como no todos los LLM tienen búsqueda integrada, hacemos la búsqueda ANTES y le
inyectamos los resultados al prompt del agente. Así funciona con cualquier modelo.

Usa Tavily si hay TAVILY_API_KEY; si no, devuelve aviso (modo degradado).
"""
import os

import httpx


async def buscar(par: str) -> str:
    """Devuelve un bloque de texto con titulares recientes para el par, o un aviso."""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return "(Sin búsqueda web: configura TAVILY_API_KEY para noticias en vivo.)"

    consulta = f"market news today {par} futures macro Fed CPI yields"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": consulta,
                    "topic": "news",
                    "days": 3,
                    "max_results": 6,
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        return f"(Búsqueda web falló: {e})"

    items = data.get("results", [])
    if not items:
        return "(Búsqueda web sin resultados.)"
    return "\n".join(f"- {it.get('title')} :: {it.get('url')}\n  {it.get('content', '')[:300]}" for it in items)
