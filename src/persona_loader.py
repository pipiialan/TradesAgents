"""Carga las fichas de los agentes (.md con frontmatter) y las convierte en dicts.

Cada ficha = un subagente con su 'esencia' (system prompt). El frontmatter define
name, description, tools, model y skills; el cuerpo es el system prompt.
"""
import re
from pathlib import Path

import yaml


def load_persona(path) -> dict:
    """Lee un .md con frontmatter YAML y devuelve {name, description, tools, model, skills, prompt}."""
    text = Path(path).read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError(f"La ficha no tiene frontmatter válido: {path}")
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()

    tools = meta.get("tools")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    elif tools is None:
        tools = []

    return {
        "name": meta.get("name"),
        "description": meta.get("description", ""),
        "tools": tools,
        "model": meta.get("model", "sonnet"),
        "skills": meta.get("skills") or [],
        "prompt": body,
    }


def load_pool(dir_path) -> list[dict]:
    """Carga las 6 fichas de un pool (carpeta agents/indices o agents/oro)."""
    paths = sorted(Path(dir_path).glob("*.md"))
    if not paths:
        raise FileNotFoundError(f"No hay fichas en {dir_path}")
    return [load_persona(p) for p in paths]
