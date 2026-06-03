# Arranca el puente Claude Code (suscripcion) en 127.0.0.1:8787
# Borra cualquier API key del entorno para garantizar que use la SUSCRIPCION.
$env:ANTHROPIC_API_KEY = $null
$env:ANTHROPIC_AUTH_TOKEN = $null
Set-Location "e:\Bots trading\RESURECCIONDEPAQUITA\TradesAgents"
# Usa el python del .venv (ahi estan fastapi/uvicorn instalados).
& ".\.venv\Scripts\python.exe" -m uvicorn bridge.claude_bridge:app --host 127.0.0.1 --port 8787
