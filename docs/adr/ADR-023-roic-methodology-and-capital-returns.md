# ADR-023: ROIC Methodology robusta + Capital Returns en FinancialsReport

**Status**: Accepted
**Date**: 2026-05-10
**Context**: Auditoría de informe BBW reveló que el ROIC no se calcula para empresas con buybacks acumulados — y que el sistema no extrae buybacks/dividends en absoluto.

---

## Contexto

La fórmula ROIC anterior usaba `Invested Capital = Equity + Total_Debt - Cash` exclusivamente. Esta fórmula falla sistemáticamente para una clase entera del universo de value investing: **empresas que devuelven mucho capital al accionista**.

### Por qué falla

Cuando una empresa hace buybacks agresivos, el treasury stock acumulado reduce el book equity. Eventualmente, el equity puede acercarse a cero o volverse negativo. Ejemplos reales:

- **AutoZone (AZO)**: equity persistentemente negativo desde ~2010 — buybacks superan retained earnings.
- **Domino's Pizza (DPZ)**: equity negativo consistente.
- **McDonald's (MCD)**: equity bajo relativo a su balance sheet.
- **Build-A-Bear (BBW)**: equity reducido por años de buybacks; el run de 2026-05-10 produjo `roic_series=[]` y el §2 del Value Expert decía "BBW's FY2026 ROIC is not provided".

Estas empresas son **exactamente el tipo de compounders que un value investor quiere identificar** (Buffett, Greenblatt). El sistema las excluía silenciosamente.

### Causa raíz arquitectónica

La fórmula `Equity + Debt - Cash` representa "capital aportado por financiadores que sigue en la empresa". Si los financiadores recuperaron su capital (vía buybacks), el numerador se acerca a cero — pero **los activos operativos (PP&E, inventario, working capital) siguen ahí** y siguen generando NOPAT. La fórmula no captura el "capital realmente desplegado en la operación".

Adicionalmente: el sistema **no extraía buybacks ni dividends** de XBRL. La tesis core de muchas empresas value (BBW: "100% del FCF al accionista", AAPL: "$95B/año en buybacks") era invisible al Value Expert.

---

## Decisiones

### 1. ROIC con fallback Greenblatt-light

**Decisión**: usar dos fórmulas con threshold de degeneración.

```python
ic_primary  = equity + total_debt - cash             # balance sheet view
ic_fallback = total_assets - cash                    # operating asset view

threshold = 0.10 * ic_fallback                       # 10% como floor

if ic_primary > threshold and ic_primary > 0:
    ic_value = ic_primary
    ic_method = "equity+debt-cash"
elif ic_fallback > 0:
    ic_value = ic_fallback
    ic_method = "total_assets-cash (fallback for low/negative book equity)"
else:
    ic_value = None
```

**Razonamiento**:
- Para empresas normales (equity grande): la primaria domina, comportamiento sin cambios respecto a antes.
- Para empresas con equity reducido o negativo: el fallback `total_assets - cash` representa la base de activos operativos, que es siempre meaningful independiente del capital structure.
- El threshold 10% detecta automáticamente cuándo el primary es "degenerado" sin requerir un flag manual.
- El método elegido se almacena en `MetricValue.source` para auditabilidad — el lector ve "total_assets-cash (fallback)" cuando aplica.

**Limitación**: `total_assets - cash` incluye goodwill e intangibles. Para empresas muy adquisitivas, esto puede inflar IC y deprimir ROIC. La verdadera fórmula Greenblatt (`NetWorkingCapital + NetPP&E`) requeriría extraer Net PP&E del XBRL — diferido a una iteración futura si el fallback simple no es suficiente. La aproximación actual es estrictamente más informativa que devolver `roic_series=[]`.

**Alternativas rechazadas**:
- *Solo usar `total_assets - cash` siempre*: rompe la comparabilidad con análisis previos de empresas con equity normal y empieza a incluir operating liabilities en el denominador.
- *Calcular `NetWorkingCapital + NetPP&E` Greenblatt completo*: requiere extraer 4 conceptos XBRL adicionales y manejar empresas que no los reportan. Complejidad innecesaria para resolver el caso degenerado.
- *Devolver ROIC=∞ o ROIC=NaN*: técnicamente coherente, pero no actionable para el Value Expert.

### 2. Capital returns como first-class citizens en FinancialsReport

**Decisión**: añadir `buybacks: list[MetricValue]` y `dividends: list[MetricValue]` al schema, parsear desde XBRL.

Conceptos XBRL parseados (con fallbacks):
- Buybacks: `PaymentsForRepurchaseOfCommonStock` → `PaymentsForRepurchaseOfEquity`
- Dividends: `PaymentsOfDividendsCommonStock` → `PaymentsOfDividends`

Los valores se almacenan en magnitud absoluta (XBRL los reporta como salidas de caja negativas; `_mv_list_abs` normaliza).

### 3. Value Expert MUST report capital returns

**Decisión**: regla dura en `_SYSTEM_PROMPT` (§4 Financial Quality):

> *"If 'Capital returned to shareholders' is in context, you MUST report it explicitly: how much was returned, what % of FCF, split between buybacks/dividends. Capital allocation is core to value investing thesis."*

El `_format_context` precalcula la línea con magnitud + porcentaje del FCF para que el Expert pueda citarla verbatim:

> "Capital returned to shareholders (FY2026): $39M (buybacks $28M, dividends $11M) = 97% of FCF"

### 4. Subject row siempre presente en peer comparison

**Decisión**: la primera fila de la tabla `## Peer Comparison — ROIC` muestra el ticker analizado siempre, incluso si `roic_series=[]`. En ese caso, valor = "N/A — IC indeterminate".

**Razonamiento**: una tabla de peers sin la empresa analizada como ancla es estéril. El lector necesita ver dónde está el subject incluso si el dato no es calculable. La nueva fórmula con fallback debería hacer que este caso sea raro, pero el subject row es defensa en profundidad.

---

## Consecuencias

- ROIC se calcula para empresas con equity bajo/negativo (BBW, AutoZone, Domino's) usando el fallback. ROIC vuelve a ser una métrica fiable para el universo value real.
- El Value Expert ahora puede analizar capital allocation de forma explícita — las tesis de tipo "100% del FCF al accionista" son ahora visibles en §4.
- Tests añadidos: `test_parse_financials_invested_capital_fallback_for_buyback_heavy` valida el fallback con equity negativo.
- Cambio backwards-compatible para AAPL y otras empresas con equity normal (verificado: el test existente sigue pasando con valor exacto).
- Anotación en el `MetricValue.source` permite auditar qué fórmula se usó por análisis.
