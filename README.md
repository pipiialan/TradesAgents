# TradingAgents — Equipo de traders IA orquestado

App multi-agente: un **Jefe IA** (orquestador) lanza un equipo de traders especializados según el par,
cada uno con la **esencia** (metodología) de un trader profesional documentado. Recolecta sus veredictos
+ el análisis de noticias y emite una recomendación de consenso. **El usuario decide el tamaño y confirma
la orden** (no se ejecuta sola).

**Proveedor de IA agnóstico:** funciona con cualquier modelo (OpenAI, Claude, Groq, Gemini,
Qwen, MiniMax, OpenRouter, local) vía [LiteLLM]. El usuario solo pega su API key en `.env` y elige
el modelo. Las "esencias" son prompts de texto, así que no dependen de ningún proveedor.

## 🚀 Inicio rápido (clonar en otra PC)

Requisitos: [Git](https://git-scm.com/download/win) y [Python 3.10+](https://www.python.org/downloads/).

```powershell
# 1. Clonar (repo privado -> pedirá login de GitHub la 1a vez)
git clone https://github.com/pipiialan/TradesAgents.git
cd TradesAgents

# 2. Crear entorno e instalar dependencias
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Crear tu .env (NO viene en el repo) y poner tu API key
copy .env.example .env
#    -> abre .env y pega tu key. Gemini gratis: aistudio.google.com/apikey

# 4. Correr la app
.\.venv\Scripts\python.exe -m uvicorn src.api:app --port 8850
```

Luego abre **http://localhost:8850** en el navegador.

> 🔑 El `.env` con tu API key NUNCA se sube a GitHub (seguridad): recréalo en cada PC.
> 📊 Para datos en vivo necesitas **NinjaTrader 8** abierto con el indicador
> `ninjatrader/TradingAgentsExporter.cs` compilado en un gráfico de 1 minuto.
> Sin NinjaTrader, la app usa datos de ejemplo (`data/sample_context_NQ.json`).

### Sincronizar entre tus PCs
```powershell
git pull      # antes de empezar: trae lo último
git push      # al terminar: sube tus cambios
```

## Arquitectura

```
Usuario: "Analízame NQ"
        │
        ▼
   JEFE IA (orquestador, opus)
        ├── Pool según el par:
        │     • indices (NQ/ES): 6 traders
        │     • oro (GC/XAUUSD): 6 traders
        ├── Analista de noticias (#7, con caché por fecha+par)
        ▼
   Consenso (convicción + dirección + SL/TP + % riesgo)
        ▼
   Usuario elige micros/minis → confirma → (futuro) orden a NT8
```

### Pools (la "esencia" vive en `agents/`)

**Índices (NQ/ES)** — `agents/indices/`
1. Al Brooks — price action puro (STOP confirmación)
2. Linda Raschke — setups corto plazo (mixto)
3. Brian Shannon — Anchored VWAP (LIMIT pullback)
4. ICT — liquidez/killzones (LIMIT en FVG)
5. Order Flow — footprint/DOM (MARKET reactivo)
6. Toby Crabel — Opening Range Breakout (STOP ruptura)

**Oro (GC/XAUUSD)** — `agents/oro/`
1. ICT (oro), 2. SMC, 3. Oferta/Demanda (Seiden),
4. Price action/niveles, 5. VWAP/sesión, 6. Order flow/Bookmap

**Noticias** — `agents/news/news-analyst.md` (3 horizontes: régimen, calendario, sorpresas del día; dato real vs forecast).
**Jefe IA** — `agents/orchestrator/jefe-ia.md`.
**Skill compartida** — `skills/risk-checklist/`.

## Estructura

```
agents/        fichas de los subagentes (.md con frontmatter = esencia)
skills/        skills compartidas (gestión de riesgo)
config/        instrumentos y pools
src/           orquestador + loader + caché de noticias + main
data/          caché de noticias y stubs de contexto
```

## Instalar y correr

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # elige LLM_MODEL y pega tu LLM_API_KEY

python -m src.main NQ
python -m src.main GC --forzar-noticias
```

El equipo de traders + noticias corre en `LLM_MODEL`; el Jefe en `LLM_MODEL_JEFE`
(conviene un modelo más capaz). Cambiar de proveedor = cambiar esas variables.

## Estado / Roadmap

- [x] Esencia de los 12 traders + noticias + Jefe IA (fichas en formato oficial Anthropic)
- [x] Orquestador en Python, caché de noticias, skill de riesgo
- [x] Motor multi-proveedor (LiteLLM): OpenAI, Claude, Groq, Gemini, Qwen, MiniMax, local…
- [ ] **Bridge NinjaTrader 8** (NinjaScript AddOn) que entregue velas 1m multi-TF y posiciones — el contexto hoy es un STUB (`data/sample_context_*.json`)
- [ ] MCP sobre el bridge
- [ ] Ejecución en Sim101 (paper) con confirmación humana
- [ ] Cuenta real con micros

> El multi-TF se resuelve en el bridge: se entrega 1m y se resamplea internamente (mismo patrón que Zentryx).

## Disclaimer

Análisis asistido por IA con fines educativos. **No es asesoría financiera.** El trading de futuros conlleva
riesgo de pérdida. La casa/mercado no se "vence" con IA: esto busca consistencia y confluencia, no certezas.
