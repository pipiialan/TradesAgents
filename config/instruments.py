"""Catálogo de instrumentos y a qué pool de agentes pertenece cada uno."""

# Cada símbolo apunta a un pool ("indices" u "oro") -> determina qué 6 traders se lanzan.
INSTRUMENTS = {
    "NQ": {
        "nombre": "Micro/Mini Nasdaq 100",
        "pool": "indices",
        "correlacionados": ["ES"],   # se analizan juntos por correlación
        "tick": 0.25,
        "valor_tick_micro": 0.50,    # MNQ
        "sesion_preferida": "NY",
    },
    "ES": {
        "nombre": "Micro/Mini S&P 500",
        "pool": "indices",
        "correlacionados": ["NQ"],
        "tick": 0.25,
        "valor_tick_micro": 1.25,    # MES
        "sesion_preferida": "NY",
    },
    "MNQ": {
        "nombre": "Micro Nasdaq 100",
        "pool": "indices",
        "correlacionados": ["MES"],
        "tick": 0.25,
        "valor_tick_micro": 0.50,    # MNQ
        "sesion_preferida": "NY",
    },
    "MES": {
        "nombre": "Micro S&P 500",
        "pool": "indices",
        "correlacionados": ["MNQ"],
        "tick": 0.25,
        "valor_tick_micro": 1.25,    # MES
        "sesion_preferida": "NY",
    },
    "GC": {
        "nombre": "Oro (Gold futures)",
        "pool": "oro",
        "correlacionados": ["XAUUSD"],
        "tick": 0.10,
        "valor_tick_micro": 1.00,    # MGC
        "sesion_preferida": "London/NY",
    },
    "MGC": {
        "nombre": "Micro Oro",
        "pool": "oro",
        "correlacionados": ["GC"],
        "tick": 0.10,
        "valor_tick_micro": 1.00,    # MGC
        "sesion_preferida": "London/NY",
    },
    "XAUUSD": {
        "nombre": "Oro spot",
        "pool": "oro",
        "correlacionados": ["GC"],
        "tick": 0.01,
        "valor_tick_micro": None,
        "sesion_preferida": "London/NY",
    },
}

# Directorios de cada pool (relativos a la raíz del proyecto).
POOL_DIRS = {
    "indices": "agents/indices",
    "oro": "agents/oro",
}


def pool_de(par: str) -> str:
    """Devuelve el pool ('indices'/'oro') para un símbolo."""
    par = par.upper()
    if par not in INSTRUMENTS:
        raise KeyError(f"Instrumento no configurado: {par}. Disponibles: {list(INSTRUMENTS)}")
    return INSTRUMENTS[par]["pool"]
