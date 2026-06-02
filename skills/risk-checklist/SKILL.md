---
name: risk-checklist
description: Checklist de gestión de riesgo y validación previa a la entrada (tamaño de posición, colocación de SL, R:R mínimo, ventanas de noticias). Úsala antes de proponer cualquier entrada para verificar que el setup es válido.
---

# Checklist de riesgo (pre-entrada)

Aplica esta validación ANTES de proponer una entrada. Si falla cualquier punto crítico, el veredicto debe ser NO-TRADE.

## Gestión de riesgo
- Riesgo por operación: máximo 1-2% de la cuenta.
- Pérdida diaria máxima: 3-5% de la cuenta -> si se alcanza, parar.
- El SL define el tamaño, no al revés. Tamaño = riesgo$ / (distancia al SL en ticks x valor del tick).

## Validación de entrada (crítica)
- [ ] ¿Hay estructura/contexto claro a favor de la dirección?
- [ ] ¿La entrada está en confluencia (2+ factores) o en una zona definida?
- [ ] ¿El SL está detrás de una estructura real (no un número arbitrario)?
- [ ] ¿El R:R es >= 1:1.5?
- [ ] ¿El tipo de orden (LIMIT/MARKET/STOP) corresponde al gatillo?

## Filtro de mercado
- [ ] ¿NO estamos dentro de una ventana de no-trade por noticias (±30 min de evento de alto impacto)?
- [ ] ¿La sesión es la adecuada para el método (apertura NY/London)?
- [ ] ¿El sesgo de noticias no contradice fuerte la dirección?

## Regla final
Si algún punto CRÍTICO (SL real, R:R >= 1:1.5, fuera de ventana de noticias) no se cumple, devuelve NO-TRADE con el motivo. No fuerces operaciones.
