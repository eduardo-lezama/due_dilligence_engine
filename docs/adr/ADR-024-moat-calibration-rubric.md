# ADR-024: Moat Calibration Rubric — criterios estrictos para wide moat

**Status**: Accepted
**Date**: 2026-05-10
**Context**: Cuarta auditoría externa de BBW reveló que el sistema clasificó el moat como "wide" basándose principalmente en ROIC alto (40.2%). Análisis profundo demostró que la clasificación era incorrecta — BBW tiene narrow moat con returns altos, no wide moat compounder.

---

## Contexto

El system prompt anterior del `competitive_agent` contenía una rúbrica de moat mínima:

```
- "wide": durable competitive advantage likely to persist 10+ years
- "narrow": some competitive advantage but vulnerable to erosion in 5-10 years
- "none": no identifiable structural advantage
```

Sin criterios objetivos ni evidencia requerida. El LLM tenía libertad completa, y el heurístico mental por defecto ("ROIC alto = moat fuerte = wide") producía clasificaciones agresivas.

### El caso BBW

- ROIC 40.2% sostenido 4 años → high
- Capital base $128M, **estática** (99% del FCF se devuelve al accionista)
- FCF flat 4 años: $34M → $46M → $40M
- Revenue 3y CAGR 8.8%, decente pero no compounding
- Peer set: 3 de 4 peers en distress (FNKO -17%, PLCE -1.8%, VRA -28%)

El sistema clasificó como **wide** principalmente porque (a) ROIC dominaba 3 de 4 peers, (b) ROIC sostenido 4 años. **Ambos signals son necesarios pero no suficientes para wide moat**.

### Por qué la clasificación era incorrecta (teoría value investing)

El framework Buffett/Morningstar para wide moat exige **ventajas estructurales que protejan economics 20+ años**:
- Network effects (Visa, Meta)
- Switching costs (Bloomberg, ERP enterprise)
- Cost advantage por scale (Costco, Walmart)
- Brand pricing power AT SCALE (Coca-Cola, LVMH)
- Efficient scale (1-2 jugadores dominantes en mercado pequeño)
- Regulatory protection

**ROIC alto puede venir de DOS perfiles cualitativamente distintos**:

| Perfil | Mecánica | Moat |
|--------|----------|------|
| **Compounder durable** | ROIC alto × reinvestment alto × runway largo → capital base CRECE → FCF crece exponencialmente | Wide |
| **Cash cow nicho descontada** | ROIC alto × reinvestment limitado × runway saturado → capital base ESTÁTICA → FCF estable | Narrow con returns altos |

BBW es claramente el segundo perfil. Es exactamente lo que el Critic identificó: *"high ROIC and large shareholder distributions mask the fact that the company is not converting that economic quality into growing free cash flow."*

### El meta-patrón

Esto es la misma clase de error que EC-15, EC-18, EC-21: **el sistema aplica heurísticos simples (un signal → una conclusión) a problemas multifactoriales del value investing**. ROIC alto no es suficiente para wide moat, igual que FCF CAGR no es suficiente para growth assumption (EC-15) y `OperatingIncomeLoss` no siempre existe (EC-21). Cada iteración hemos descubierto otra dimensión donde los heurísticos simples fallan.

---

## Decisiones

### 1. Rúbrica estricta multi-criterio en system prompt

Wide moat ahora requiere **TODOS** estos criterios:

```
WIDE moat — requires ALL of:
1. ROIC > 15% sustained ≥ 4 consecutive years (full roic_series, not just latest)
2. Capital base trending UP or stable (proxy: buybacks <70% of FCF)
3. Revenue 3y CAGR ≥ 4% (real organic growth, not cyclical bounce)
4. At least ONE specific structural advantage with EVIDENCE in citations
```

Si LLM clasifica como WIDE, MUST listar evidencia para cada criterio. Si alguno falla, downgrade a NARROW.

Adicionalmente, la rúbrica explica el caso del "high-ROIC niche business":
> *"high-ROIC niche businesses with limited reinvestment runway (high payout, flat FCF, stagnant capital base) are NARROW with high returns, NOT wide. They are cash cows on small niches, not compounding machines."*

### 2. Clamp post-hoc en `competitive_agent`

Defensa en profundidad — el LLM puede ignorar la rúbrica del prompt. El clamp programático verifica los criterios cuantitativos tras el parse y downgrade si fallan:

```python
def _check_wide_moat_criteria(financials):
    fails = []
    # Criterio 1: ROIC > 15% sostenido ≥ 4 años
    if not financials.roic_series or len(financials.roic_series) < 4:
        fails.append("ROIC series < 4 years")
    elif any(r.value < 0.15 for r in financials.roic_series[:4]):
        fails.append("ROIC fell below 15% in some year")
    # Criterio 2: Buybacks < 70% del FCF
    if buybacks / fcf >= 0.70:
        fails.append("Buybacks ≥ 70% of FCF (stagnant capital base)")
    # Criterio 3: Revenue 3y CAGR ≥ 4%
    if revenue_cagr_3y < 0.04:
        fails.append(f"Revenue CAGR {cagr:.1%} (<4%)")
    return fails

if classification == "wide" and check_wide_moat_criteria(financials):
    classification = "narrow"  # downgrade
    log.warning("competitive_moat_downgraded_wide_to_narrow", reasons=...)
```

Mismo patrón que EC-24 (sizing clamp por confidence). El LLM puede razonar mal; el clamp impone disciplina.

### 3. Source attribution ya implícita

La rúbrica obliga al LLM a listar evidencia específica en `moat_analysis`. Esto se valida implícitamente — si el LLM dice "wide" pero la evidencia listada no menciona scale/network/switching/regulatory/brand, la clasificación es indefendible aunque pase el clamp cuantitativo.

---

## Consecuencias

### Beneficios

- Wide moat se reserva para casos genuinos (Costco, Visa, Hermès), no para cualquier ROIC alto.
- "High-ROIC niche business" se clasifica correctamente como narrow con returns altos.
- El VERDICT del Value Expert se ancla en una clasificación de moat más rigurosa, evitando el sesgo "wide moat → BUY confiado".
- Critic puede atacar el bull case con base más sólida cuando el moat es narrow.

### Trade-offs

- Algunas clasificaciones que antes eran "wide" pasan a "narrow" — requerirá comunicarlo al usuario en docs.
- El test fixture AAPL ahora produce "narrow" en lugar de "wide" (datos limitados de 3 años), lo que es comportamiento correcto del clamp pero requiere actualizar el test.
- Posible falso negativo: una empresa con verdaderamente wide moat pero con buybacks puntualmente altos (>70% un año específico por ejemplo de retorno extraordinario) podría ser clasificada narrow. Aceptable: prefer el conservadurismo, el Critic ataca el bull si lo cree wide.

### Patrón aplicable a futuros refinements

Esta decisión sigue el patrón emergente del proyecto:

1. **Prompt rule** (instructiva, primer intento)
2. **Algorithmic clamp** (defensa en profundidad)
3. **Logged decision** (auditabilidad)

Aplicar a futuros casos donde signal único no debe determinar conclusión multifactorial.
