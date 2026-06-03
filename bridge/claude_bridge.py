"""Puente OpenAI-compatible -> Claude Code CLI (suscripcion, NO API key).

Expone POST /v1/chat/completions como un proveedor LLM. Por cada request
invoca `claude -p` por subprocess y envuelve la respuesta en formato OpenAI.

Auth: usa la SUSCRIPCION (claude.ai / Max) via el token OAuth del keychain.
Para garantizarlo, este puente BORRA ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
del entorno del subprocess (si estuvieran seteadas, el CLI facturaria por API).
NUNCA usa --bare (ese modo ignora OAuth y exige API key -> "Not logged in").

Arranque (Windows):
    cd e:\\Bots trading\\RESURECCIONDEPAQUITA\\TradesAgents
    python -m uvicorn bridge.claude_bridge:app --host 127.0.0.1 --port 8787

Verificado con claude v2.1.132:
  - `claude auth status` -> authMethod "claude.ai", subscriptionType "max".
  - `--bare` con suscripcion -> is_error:true "Not logged in".
  - `--tools ""` -> num_turns 1, sin web search (sin esto el CLI es un AGENTE: 36s).
  - `--output-format json` -> {"result": "<texto>"} (puede traer fences ```json).
"""
import asyncio
import json
import os
import re
import shutil
import sys
import time
import uuid

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Claude Code Bridge (subscription)")


def _resolve_claude_bin() -> str:
    """Devuelve un ejecutable que create_subprocess_exec PUEDA lanzar.

    En Windows `claude` es un shim .cmd/.ps1 (batch) que CreateProcess no puede
    ejecutar directamente -> WinError 2. El binario nativo vive en
    <npm>\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe. Lo derivamos
    desde el shim (portatil: cualquier PC con el CLI instalado por npm).
    """
    env_bin = os.getenv("CLAUDE_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    found = shutil.which(env_bin or "claude")
    if found and sys.platform == "win32" and not found.lower().endswith(".exe"):
        exe = os.path.join(
            os.path.dirname(found),
            "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe",
        )
        if os.path.exists(exe):
            return exe
    return found or env_bin or "claude"


CLAUDE_BIN = _resolve_claude_bin()

# Mapeo de modelo OpenAI-style -> alias de modelo del CLI.
# La app pedira "openai/claude-team" o "openai/claude-jefe" (ver PROVIDERS).
MODEL_MAP = {
    "claude-team": "haiku",
    "claude-jefe": "sonnet",
    "claude-haiku": "haiku",
    "claude-sonnet": "sonnet",
    "claude-opus": "opus",
}
DEFAULT_MODEL = "sonnet"
TIMEOUT_S = int(os.getenv("CLAUDE_BRIDGE_TIMEOUT", "120"))

# Solo un subprocess de claude a la vez evita topar memoria/CPU y respeta
# implicitamente el semaforo de 4 de la app sin pelear por el keychain.
_SEM = asyncio.Semaphore(int(os.getenv("CLAUDE_BRIDGE_CONCURRENCY", "3")))

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class Message(BaseModel):
    role: str
    content: str = ""


class ChatReq(BaseModel):
    model: str = ""
    messages: list[Message] = []
    temperature: float | None = None
    response_format: dict | None = None  # {"type": "json_object"} desde la app
    stream: bool | None = False


def _resolve_model(model: str) -> str:
    name = (model or "").split("/")[-1].strip()  # quita prefijo "openai/"
    return MODEL_MAP.get(name, name or DEFAULT_MODEL)


def _split_messages(msgs: list[Message]) -> tuple[str, str]:
    system = "\n\n".join(m.content for m in msgs if m.role == "system")
    user = "\n\n".join(m.content for m in msgs if m.role in ("user", "assistant"))
    return system, user


def _clean(text: str) -> str:
    """Quita fences ```json que el CLI a veces envuelve alrededor del JSON."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = _FENCE.sub("", t).strip()
    return t


def _child_env(force_json: bool) -> dict:
    env = dict(os.environ)
    # CLAVE: forzar suscripcion. Si estas estan seteadas el CLI factura por API.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


async def _run_claude(system: str, user: str, model: str, force_json: bool) -> str:
    # El user va por STDIN, no como argumento: los prompts de traders (contexto
    # multi-TF + noticias en JSON) pueden ser grandes y en Windows la linea de
    # comando topa ~32k chars. Por stdin no hay ese limite.
    args = [
        CLAUDE_BIN, "-p",
        "--output-format", "json",
        "--model", model,
        "--tools", "",                 # sin herramientas -> 1 turno, sin web search
        "--permission-mode", "plan",   # nunca puede escribir/ejecutar nada
        "--no-session-persistence",
    ]
    if system:
        args += ["--system-prompt", system]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_env(force_json),
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=(user or "").encode("utf-8")), timeout=TIMEOUT_S
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"claude CLI timeout tras {TIMEOUT_S}s")

    raw = out.decode("utf-8", "replace").strip()
    if not raw:
        raise RuntimeError(f"claude CLI sin salida. stderr={err.decode('utf-8','replace')[:500]}")

    data = json.loads(raw)
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {data.get('result') or data}")

    # Con --json-schema el texto viene en structured_output; aqui usamos result.
    text = data.get("result") or ""
    if not text and isinstance(data.get("structured_output"), (dict, list)):
        text = json.dumps(data["structured_output"], ensure_ascii=False)

    cleaned = _clean(text)
    if force_json:
        # Valida/normaliza para que la app reciba JSON limpio (sin fences).
        try:
            cleaned = json.dumps(json.loads(cleaned), ensure_ascii=False)
        except Exception:
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                cleaned = m.group(0)
    return cleaned


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatReq):
    system, user = _split_messages(req.messages)
    model = _resolve_model(req.model)
    force_json = bool(req.response_format and req.response_format.get("type") == "json_object")

    async with _SEM:
        content = await _run_claude(system, user, model, force_json)

    now = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": now,
        "model": req.model or model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/v1/models")
async def list_models():
    now = int(time.time())
    return {"object": "list", "data": [
        {"id": k, "object": "model", "created": now, "owned_by": "claude-code"}
        for k in MODEL_MAP
    ]}


@app.get("/health")
async def health():
    return {"ok": True, "bin": CLAUDE_BIN}
