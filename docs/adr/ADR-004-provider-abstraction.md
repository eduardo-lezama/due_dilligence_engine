# ADR-004: Provider abstraction Claude primary + OpenAI fallback

**Status**: Accepted
**Date**: 2026-04-19

## Context
El usuario dispone de:
- **Claude Pro subscription** actualmente, sin garantía de permanencia a largo plazo.
- **OpenAI API** con pago por uso (sostenible).

No se quiere lock-in. El sistema debe seguir funcionando si el usuario pierde el acceso a Claude. Además, modelos distintos encajan mejor en roles distintos (cheap para extractivo, top para síntesis), y se quiere diversidad cognitiva Expert↔Critic (ADR-003).

## Decision
1. **Thin wrapper propio** (`due_diligence.llm.client`) sobre Anthropic SDK + OpenAI SDK.
2. **Config-driven routing por rol**: `{role: "synth", primary: Claude-Opus, fallback: GPT-4o}`, etc.
3. **Modelos por rol** (defaults iniciales, tuneable):
   - Especialistas extractivos: Claude Haiku / GPT-4o-mini.
   - Valuation Agent: Sonnet / GPT-4o.
   - Value Expert (síntesis): Opus / GPT-4o.
   - Critic Agent: modelo distinto al Expert.
4. **Prompt caching agresivo en Claude** mientras esté disponible (checklist + rúbrica de verdict se cachean).
5. **Fallback automático**: 2 fallos consecutivos en primary → switch a fallback para ese request.
6. **LiteLLM como opción**: se evalúa en Fase 2 si el wrapper propio crece demasiado.

## Consequences
- **+** Sin lock-in.
- **+** Switch a fallback transparente para el caller.
- **+** Coste optimizable por rol.
- **−** Plumbing inicial (paga una vez).
- **−** Testing requiere mockear ambos SDKs.

## Alternatives considered
- **Hardcode Claude**: rechazado por lock-in.
- **LiteLLM desde día 0**: rechazado por dependencia externa para un wrapper que en su esencia son dos SDKs y un if/else. Si el wrapper crece, se reconsidera.
- **Modelos locales (Ollama)**: rechazado por el usuario — overhead de infra no justifica el ahorro en su caso de uso.
