# ADR-005: Control de alucinaciones por arquitectura, no por crítica

**Status**: Accepted
**Date**: 2026-04-19

## Context
Research 2025-2026 demuestra:
- LLMs alcanzan ~17% accuracy mapeando números extraídos a taxonomías US-GAAP.
- Self-critique loops tienen eficacia limitada (SelfCheckGPT muestra colapso sin tools externas).
- Patrones de tool-based extraction + XBRL estructurado muestran +42% en numeric reasoning.
- Claude Citations API reduce alucinaciones del 10% al 0% en un caso documentado de un financial research firm.

Para un sistema de inversión personal donde los números equivocados = decisiones de capital equivocadas, **se requiere tolerancia cero a alucinación numérica**.

## Decision
Tres patrones no-negociables, implementados por arquitectura (no como mitigación post-hoc):

### 1. Tool-based numerical extraction
Toda cifra (revenue, FCF, ROIC, deuda, ratios, precios) sale de **tools deterministas** contra fuentes primarias:
- `edgartools` para XBRL (10-K, 10-Q).
- `polygon-api-client` para precios.
- `finnhub-python` para transcripts.
- Cálculos derivados (FCF = CFO − capex, WACC, etc.) en módulos puros deterministas.

El LLM **razona** sobre esos valores, **no los genera**.

### 2. Schema-enforced outputs con citas validadas
Todo claim cualitativo se emite como:
```python
class Citation(BaseModel):
    claim: str
    source_url: str
    excerpt: str  # texto literal del filing
    confidence: float
    gaap_vs_nongaap_flag: Literal["GAAP", "non-GAAP", "N/A"]
    currency_code: Optional[str] = None
```
Validación **post-hoc** por substring-match del `excerpt` contra el filing cacheado. Si no match → `confidence = 0` y el claim se **excluye de la síntesis** pero se lista en `unverified_claims` del report (transparencia).

### 3. Lane separation
Tool-calls y LLM-calls viven en **lanes distintas** dentro del grafo, nunca se mezclan. Si un tool crítico falla → el grafo falla **loud** (ver invariante 5 del CLAUDE.md). Prohibido fallback a LLM-generated number.

## Consequences
- **+** Números trustworthy by design.
- **+** Auditable: todo claim tiene cita verificable.
- **+** Transparencia explícita de lo no verificable.
- **−** Gasto inicial en diseño de schemas Pydantic (se paga una vez).
- **−** Menos "creatividad" del LLM en outputs — trade-off deseado.

## Pitfalls conocidos (a blindar)
- **GAAP vs non-GAAP**: flag obligatorio en toda métrica.
- **Restatements**: detectar 10-K/A + Form 8-K Item 4.02 antes de usar el histórico.
- **Currency translation**: `currency_code` obligatorio; conversión explícita y loggeada.
- **Pro-forma vs reported**: flag explícito.
- **XBRL tagging inconsistencies** entre años: Pydantic validation + cross-check con texto 10-K cuando haya anomalía.

## Alternatives considered
- **Self-critique reflexion loops**: rechazado por eficacia marginal y coste alto.
- **LLM-extracted con post-validation**: rechazado — si el extract puede ser wrong, el validation también puede serlo. Primary source → tool → reasoning es la única cadena robusta.

## Nota de corrección (H1, 2026-10)

La validación post-hoc de citas descrita en *Decision* no se implementó hasta H1 (2026-10). Entre Fase 0 y H1 las citas se mostraban sin verificar y varias cifras generadas por el LLM llegaban a la síntesis como datos (EC-69). Implementación: `validation/citations.py` (substring normalizado o `rapidfuzz.partial_ratio ≥ 90`, y todos los números del excerpt presentes en la ventana alineada del texto fuente). Los números que emite el LLM siguen la jerarquía determinista > cita verificada contra fuente primaria > descarte (ADR-032). Pendiente H2: validación semántica claim↔excerpt.
