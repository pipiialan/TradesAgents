"""Entrypoint de prueba: analiza un par desde la CLI.

Uso:
    python -m src.main NQ
    python -m src.main GC --contexto data/sample_context_NQ.json --forzar-noticias

NOTA: el contexto de mercado (velas multi-TF, niveles) es por ahora un STUB que se lee
de un JSON. Cuando exista el bridge de NinjaTrader 8, ese contexto vendrá en vivo del MCP.
"""
import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .orchestrator import analizar

load_dotenv()
BASE = Path(__file__).resolve().parents[1]


def _cargar_contexto(ruta: str | None) -> dict:
    if ruta:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    stub = BASE / "data" / "sample_context_NQ.json"
    if stub.exists():
        return json.loads(stub.read_text(encoding="utf-8"))
    return {"nota": "STUB vacío: conectar bridge NT8 para datos reales"}


async def _run(args):
    contexto = _cargar_contexto(args.contexto)
    fecha = args.fecha or date.today().isoformat()
    resultado = await analizar(args.par, contexto, fecha, forzar_noticias=args.forzar_noticias)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description="Equipo de traders IA orquestado (Jefe IA).")
    p.add_argument("par", help="Símbolo: NQ, ES, GC, XAUUSD")
    p.add_argument("--contexto", help="Ruta a JSON con el contexto de mercado (stub)")
    p.add_argument("--fecha", help="YYYY-MM-DD (por defecto hoy)")
    p.add_argument("--forzar-noticias", action="store_true", help="Ignora el caché de noticias")
    args = p.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
