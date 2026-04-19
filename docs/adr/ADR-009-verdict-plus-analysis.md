# ADR-009: Value Expert emite análisis estructurado + veredicto explícito

**Status**: Accepted
**Date**: 2026-04-19

## Context
Decisión pedida al usuario: ¿el Value Expert emite **análisis estructurado** (para aprender) o **veredicto explícito** (para decidir)? Respuesta: **ambos**.

Tensión conocida: un veredicto LLM puede generar anclaje cognitivo ("el sistema dice BUY → compro sin cuestionar"). Mitigación explícita necesaria.

## Decision
### Análisis estructurado
Markdown en 9 secciones alineadas al checklist canónico (ver `docs/checklist-value-investing.md`):
1. Business understanding
2. Moat analysis
3. Management & skin in the game
4. Financial quality
5. Growth & reinvestment
6. Valuation
7. Risks & red flags
8. Psychological & contrarian lens
9. Decision frame

### Bloque VERDICT al final
```
VERDICT: BUY | WATCHLIST | HOLD | AVOID
INTRINSIC_VALUE: $X.XX (range: $X.XX – $X.XX)
MARGIN_OF_SAFETY vs current price: XX%
SUGGESTED_POSITION_SIZING: X% of portfolio (Kelly-adjusted, capped 10%)
CONFIDENCE: high | medium | low
KEY_ASSUMPTIONS_IF_VIOLATED: [lista de thesis-breakers]
```

### Critic Agent — mitigación del anclaje
Inmediatamente después del Value Expert, un **Critic Agent** con **modelo distinto** ejecuta un **pre-mortem**:
> "Si este veredicto resulta equivocado en 3 años, ¿cuál es la razón más probable?"

Output: 3-5 bullets de weaknesses + contra-thesis. Adjunto al report. El usuario ve ambos (Expert + Critic) antes de tomar decisión.

### Ordenación del report
```
1. Executive summary (incluye VERDICT)
2. Análisis estructurado en 9 secciones
3. Critic pre-mortem
4. Metadata (user inputs en checkpoints, cost, run_id, unverified claims)
```

## Consequences
- **+** El usuario obtiene tanto didáctica como señal de decisión.
- **+** Critic Agent mitiga anclaje por diversidad cognitiva (distinto modelo base — e.g., si Expert=Opus, Critic=GPT-4o).
- **+** Transparencia: `KEY_ASSUMPTIONS_IF_VIOLATED` explicita qué observaciones futuras rompen la tesis.
- **−** Coste extra del Critic (mitigable con modelo algo más barato que Expert).

## Política de uso del veredicto
El usuario mantiene **siempre la decisión final**. El VERDICT es **opinión estructurada del sistema**, no instrucción de ejecución. El sistema no se integra con brokers en Fase 1-4.

## Alternatives considered
- **Solo análisis estructurado sin veredicto**: rechazado por el usuario — quiere señal directa.
- **Solo veredicto sin análisis**: rechazado — pierde el valor didáctico y la auditabilidad.
- **Critic con mismo modelo que Expert**: rechazado — la diversidad cognitiva requiere stacks base distintos.
