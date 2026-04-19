# ADR-010: Deploy target NAS QNAP TS264

**Status**: Accepted
**Date**: 2026-04-19

## Context
El usuario dispone de un NAS QNAP TS264 con Container Station (Docker). Potencialmente utilizable como target self-hosted para:
- Storage persistente (SQLite, reports, cache)
- Webapp (FastAPI + UI ligera) accesible desde LAN
- Backups de análisis

El uso de APIs LLM (Claude, OpenAI) desde el NAS es igual de viable que desde la máquina local — no se consideran LLMs locales (usuario descartó modelos locales explícitamente).

## Decision
### Fases 1-4
Ejecutar **localmente en la máquina del usuario** vía CLI (`due-diligence analyze TICKER`). Storage en `./reports/`, `./data/`, `./cache/`.

### Fase 5 — Webapp en NAS
- Backend **FastAPI** exponiendo los mismos graphs vía HTTP.
- Frontend **Streamlit** (rápido, Python-nativo) o React ligero (mejor UX, mayor esfuerzo). Decisión al arrancar Fase 5.
- **Docker Compose stack** corriendo en QNAP Container Station.
- **Auth ligera**: single-user con password básico para LAN (no Internet exposure por defecto).
- Storage movido a volumen persistente del NAS, accesible desde la app.

### Requisitos que arrastramos desde Fase 0
Para que Fase 5 sea **portable sin refactor**:
1. Rutas relativas al root del proyecto (no absolutas).
2. Config por env vars (`.env` + `pydantic-settings`).
3. Secretos fuera del repo.
4. SQLite file movible (sin paths hardcoded).
5. Separación backend (graphs puros) / frontend (CLI en Fase 1-4, webapp en Fase 5).

## Consequences
- **+** Cero overhead en fases 1-4 por respetar estas reglas desde día 0.
- **+** Fase 5 es deploy puro, no reescritura.
- **+** Self-hosted → sin recurring cloud costs, control total, privacidad.
- **−** Configuración de Container Station + auth es ceremonia no-trivial en Fase 5.

## Alternatives considered
- **Cloud deploy** (Railway, Fly.io, etc.): rechazado por coste y privacidad (reports contienen tesis de inversión personales).
- **Quedarse en CLI forever**: válido, pero el usuario quiere UI navegable eventualmente.
- **Stack sin Docker**: rechazado — el NAS espera contenedores; sin Docker la operación es frágil.
