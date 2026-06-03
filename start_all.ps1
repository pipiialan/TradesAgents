# Levanta el puente Claude (suscripcion) + la app TradesAgents en 2 ventanas.
# Uso: clic derecho -> "Ejecutar con PowerShell", o desde una terminal: .\start_all.ps1
$root = "e:\Bots trading\RESURECCIONDEPAQUITA\TradesAgents"
$py   = Join-Path $root ".venv\Scripts\python.exe"

# 1) Puente en :8787 (borra cualquier API key -> fuerza usar la SUSCRIPCION del CLI)
Start-Process powershell -WorkingDirectory $root -ArgumentList @(
  "-NoExit", "-Command",
  "`$env:ANTHROPIC_API_KEY=`$null; `$env:ANTHROPIC_AUTH_TOKEN=`$null; & '$py' -m uvicorn bridge.claude_bridge:app --host 127.0.0.1 --port 8787"
)

# 2) App en :8850
Start-Process powershell -WorkingDirectory $root -ArgumentList @(
  "-NoExit", "-Command",
  "& '$py' -m uvicorn src.api:app --port 8850"
)

Write-Host ""
Write-Host "Puente -> http://127.0.0.1:8787  |  App -> http://localhost:8850" -ForegroundColor Green
Write-Host "Se abrieron 2 ventanas (puente y app). Cierralas para apagar los servidores." -ForegroundColor Gray
Write-Host "Abre en el navegador: http://localhost:8850"
