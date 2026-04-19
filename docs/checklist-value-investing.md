# Canonical Value-Investing Checklist

Checklist detallado ejecutado por el sistema en cada análisis. Sintetizado de Buffett/Munger, Philip Fisher, Peter Lynch, Joel Greenblatt, Howard Marks, Ben Graham, Terry Smith, Nick Sleep, Mohnish Pabrai, Guy Spier.

Cada ítem está marcado con su **routing**:
- **[quant]** — tool-lane (datos deterministas desde APIs / XBRL).
- **[qual]** — LLM-lane con citas obligatorias al filing.
- **[hybrid]** — ambas (valor numérico de tool + interpretación / fuente de LLM).

---

## 1. Business understanding

### 1.1 Two-minute story [qual]
- ¿Qué vende la empresa, a quién y cómo hace dinero?
- ¿Se puede articular sin jerga, en 2 minutos?
- **Fail state**: no se puede articular → stop, fuera del circle of competence.

### 1.2 Revenue decomposition [hybrid]
- Segments, geografías, product mix.
- % revenue recurring vs transactional.
- Concentración: top-5 customers / suppliers.
- **Good**: single coherent value proposition, revenue stable-growing, recurring dominant.
- **Concerning**: conglomerate sprawl, pivots frecuentes, revenue depende de 1-2 clientes.

### 1.3 Circle of competence gate [qual]
- ¿Entendemos la economía del sector (unit economics, márgenes típicos, CAPEX profile)?
- ¿Qué vectores de disrupción a 5 años son plausibles?
- **Fail state**: si respondemos "no lo sé" a cualquiera → descartar o invertir tiempo en formarnos antes de decidir.

---

## 2. Moat analysis

### 2.1 Moat type classification [qual]
Identificar tipo según taxonomía Buffett/Munger/Morningstar:
- **Brand** (Coca-Cola, Hermès)
- **Switching costs** (SAP, Bloomberg terminal)
- **Network effects** (Visa, Meta)
- **Cost advantage** (Costco, Geico)
- **Efficient scale** (Union Pacific, utility regulada)
- **Intangibles / patents** (Big pharma durante patent life)
- **Regulatory** (utilities, licencias limitadas)

Si no aplica ninguno claramente → **no hay moat** (ventaja cíclica, commodity, first-mover temporal).

### 2.2 Margin evidence [quant]
- **Gross margin** estable o creciendo, absoluto >40% ideal para compounder.
- **Operating margin** trend 10 años.
- **Gross → Operating → Net** cascade — si opera con leverage operativo alto, entender por qué.
- **Good**: márgenes expandiéndose con scale o estables >10 años.
- **Concerning**: margin compression sostenida, volatilidad alta.

### 2.3 Pricing power [hybrid]
- ¿Ha subido precios por encima de inflación sin perder volumen?
- Cross-check: crecimiento de revenue vs unidades vendidas vs precio medio.
- **Good**: demonstrated pricing power a través de ciclos (ver earnings calls).
- **Concerning**: descuentos frecuentes, promociones para sostener volumen.

### 2.4 Market share trajectory [hybrid]
- Share 5-10 años vs top-3 competidores.
- **Good**: ganando share o estable como #1/#2.
- **Concerning**: share loss a new entrants o incumbents.

### 2.5 ROIC persistence [quant]
- ROIC >15% sostenido >7 años = moat estructural.
- ROIC >20% = candidato alta calidad.
- **Good**: ROIC alto y estable/creciente.
- **Concerning**: ROIC alto pero deteriorándose → moat eroding.

### 2.6 Widening vs eroding signals [qual]
- R&D como % ventas, trend.
- Customer retention / churn / NPS si disponible.
- Glassdoor ratings (proxy de cultura → retención de talento).
- Patent filings y expiraciones.
- **Good**: inversión sostenida que amplía moat.
- **Concerning**: cutting R&D, deteriorating retention, brain drain.

---

## 3. Management & skin in the game

### 3.1 Insider ownership [quant]
- % acciones en manos de insiders (founders + officers + directors).
- Fuente: DEF 14A (proxy statement).
- **Good**: >5% combined, founder-CEO con stake relevante a su net worth.
- **Concerning**: <1% o 100% options-based.

### 3.2 Insider transactions 12 meses [quant]
- Fuente: Form 4.
- Net buying vs net selling.
- **Cluster buys** (varios insiders comprando en corto plazo) = señal muy positiva.
- Planned selling (10b5-1) vs discretionary selling.
- **Good**: cluster buying, directo (no option exercise + sell).
- **Concerning**: selling masivo sostenido, especialmente CEO/CFO.

### 3.3 CEO tenure, background, track record [qual]
- Años en el puesto.
- Trayectoria previa (éxitos/fracasos).
- Prior companies y su performance post-departure.
- **Good**: CEO >7 años con track record demostrable.
- **Concerning**: CEO <2 años con plan agresivo de M&A o reestructuración.

### 3.4 Compensation structure [hybrid]
- Fixed vs variable split.
- LTI metrics: **ROIC-linked = green**, TSR-only = yellow, revenue-only = red.
- **Clawback provisions** para restatements.
- Stock ownership requirements para executives.
- **Good**: ROIC + per-share FCF growth como LTI metrics.
- **Concerning**: comp con repricing de options histórico, o métricas manipulables (adj. EBITDA sin ROIC).

### 3.5 Related-party transactions [qual]
- DEF 14A section "Related Person Transactions".
- 10-K: Item 13 "Certain Relationships".
- **Good**: inexistentes o triviales.
- **Concerning**: transacciones materiales con familiares / entities controladas por insiders.

### 3.6 Capital allocation track record [hybrid]
Referencia: Pabrai, Spier, "The Outsiders" (William Thorndike).
- **M&A ROI histórico**: compran a múltiplos razonables? earn-outs integrados?
- **Buybacks**: ¿compran below intrinsic value o en tops de mercado?
- **Dividend policy**: coherente con ROIC incremental (si ROIC >cost of capital, retener es mejor que pagar).
- **Reinvestment vs distribution**: razonado en shareholder letters.
- **Good**: M&A disciplined, buybacks en drawdowns, política clara y consistente.
- **Concerning**: buybacks at peaks, M&A "scale acquisitions" dilutivas, dividends financiados con debt.

### 3.7 Shareholder letters [qual]
- Referencia: test de Nick Sleep — ¿describe un "destino" o solo el año pasado?
- Candidez: ¿admite errores?
- Framing long-term.
- Especificidad de métricas vs generalidades.
- **Good**: letters tipo Buffett/Bezos — específicas, candidas, con business philosophy clara.
- **Concerning**: corporate boilerplate, cero reconocimiento de errores, frase hueca como "we had a strong year".

---

## 4. Financial quality

### 4.1 Revenue growth [quant]
- CAGR 5y y 10y.
- Std deviation de growth rates YoY (medida de consistencia).
- **Good**: CAGR positivo con baja varianza.
- **Concerning**: growth "lumpy" dependiente de M&A o un cliente.

### 4.2 Margin trends [quant]
- Gross / Operating / Net 10-year series.
- **Good**: estables o expandiéndose (moat validation).
- **Concerning**: compression sostenida.

### 4.3 FCF conversion [quant]
- **FCF / Net Income**, average 5-10 años.
- Target: >0.8 = clean earnings.
- FCF = CFO − capex (método tradicional) vs Owner Earnings (Buffett: NI + D&A − maintenance capex − WC changes).
- **Good**: >0.9 sostenido.
- **Concerning**: <0.6 persistente (earnings quality cuestionable, working capital balloon, aggressive capitalization).

### 4.4 ROIC vs WACC [quant]
- Spread ROIC − WACC >5% persistente = creating value.
- **Good**: spread ≥10% sostenido.
- **Concerning**: spread negativo o cayendo.

### 4.5 ROE decomposition (DuPont) [quant]
- ROE = Net Margin × Asset Turnover × Leverage
- Flag si leverage-driven (ratio equity/assets bajo sostiene el ROE).
- **Good**: ROE alto impulsado por margin + turnover, no leverage.
- **Concerning**: ROE alto solo por leverage → riesgo oculto.

### 4.6 Balance sheet strength [quant]
- **Net Debt / EBITDA** <3x (tech/staples) o <4x (utility/infra).
- **Interest coverage** (EBIT/interest) >5x.
- **Current ratio** >1.5 (liquidez corto plazo).
- **Quick ratio** >1 (excl inventory).
- **Maturity schedule**: debt walls próximos (<2y)? Refinanciación plausible?
- **Good**: net cash position o deuda modesta bien escalonada.
- **Concerning**: leverage creciendo, maturity wall con FCF insuficiente.

### 4.7 Share count trend 10y [quant]
- Buybacks: ¿a qué múltiplo promedio?
- Dilution: SBC as % FCF.
- **Good**: share count declining con buybacks below intrinsic.
- **Concerning**: SBC >20% FCF o dilution sostenida.

---

## 5. Growth & reinvestment

### 5.1 Incremental ROIC [quant]
- ΔNOPAT / ΔInvested Capital últimos 3-5 años.
- **Good**: incremental ROIC ≥ historical ROIC → runway intacto.
- **Concerning**: incremental ROIC decreciente → saturación.

### 5.2 Reinvestment rate × incremental ROIC [quant]
- Intrinsic growth rate = reinvestment rate × ROIC.
- Si no pueden reinvertir, deben distribuir → acotar growth asumido.

### 5.3 TAM sizing + penetration [hybrid]
- TAM source (investor presentations, 10-K, third-party).
- Current penetration %.
- **Good**: penetration baja con moat que protege expansion.
- **Concerning**: TAM inflado o penetration ya dominante.

### 5.4 Unit economics [hybrid]
(Si aplica — SaaS, subscription, DTC, etc.)
- LTV / CAC, payback period.
- Cohort retention curves.
- Gross margin per unit.
- **Good**: LTV/CAC >3x, payback <18 meses, retention cohort plana o creciente.
- **Concerning**: CAC subiendo, retention cayendo, growth dependiente de marketing spend.

---

## 6. Valuation

### 6.1 DCF con supuestos explícitos [hybrid]
- Revenue growth projection (years 1-5 + terminal).
- Operating margin trajectory.
- CAPEX / reinvestment rate.
- WACC (bottom-up beta, tax rate, debt cost).
- Terminal: perpetual growth (Gordon) o exit multiple.
- **Sensitivity table**: intrinsic value con perturbaciones ±1% en WACC y growth.
- **Good**: base case razonable con upside optionality.
- **Concerning**: base case requiere supuestos agresivos.

### 6.2 Reverse DCF [quant]
Referencia: Mauboussin.
- Dado el current price, ¿qué crecimiento está implícito?
- Comparar con growth histórico y TAM.
- **Good**: implied growth < historical growth y < TAM realista.
- **Concerning**: implied growth > historical → precio requiere perfección.

### 6.3 Multiples [quant]
- EV/EBIT, EV/FCF, EV/Sales, P/E.
- vs 10-year own history (¿en qué percentil?).
- vs peer group (top-3 comparables).
- **Good**: múltiples en bottom tercile histórico con negocio sin deterioro.
- **Concerning**: múltiples expandidos sostenidos por narrativa.

### 6.4 PEG [quant]
- P/E / earnings growth rate.
- Referencia Peter Lynch:
  - PEG <1 attractive
  - PEG <0.5 strong
- Útil para growth stocks, menos para mature.

### 6.5 Earnings yield vs Treasury [quant]
- Earnings yield (1/PE) vs 10-year Treasury.
- Referencia: Greenblatt/Buffett "bond equivalent".
- **Good**: spread cómodo (e.g. EY 8% vs Treasury 4% = 4% cushion).
- **Concerning**: EY por debajo de Treasury sin justificación growth.

### 6.6 Magic Formula composite [quant]
- Ranking combinado ROIC + earnings yield (Greenblatt).
- Útil para sizing relativo en watchlist.

### 6.7 Graham Number / NCAV [quant]
Aplica a asset-heavy, distressed, cíclicas deep value.
- Graham number = √(22.5 × EPS × BVPS).
- NCAV = (current assets − total liabilities) / shares.
- **Good**: price < 2/3 NCAV = deep value candidate.
- Menos relevante para compounders modernos.

---

## 7. Risks & red flags

### 7.1 Customer / supplier concentration [quant]
- Top 1 / 5 / 10 customers as % revenue.
- Supplier concentration para inputs críticos.
- **Good**: top-5 <30% revenue.
- **Concerning**: top-1 >20% revenue (dependencia), supplier único para componente crítico.

### 7.2 Regulatory exposure [qual]
- Jurisdicciones clave.
- Pending legislation que impacte el modelo.
- Historical relationship con reguladores (fines, consent decrees).

### 7.3 Disruption vector [qual]
- Technology disruption identificable (e.g. Blockbuster vs streaming).
- Business model disruption (e.g. DTC vs traditional retail).
- **Good**: empresa es la disruptor.
- **Concerning**: empresa es potencial disrupted sin respuesta clara.

### 7.4 Accounting red flags [hybrid]
Auditoría sistemática:
- **Receivables growing > revenue** (aggressive revenue recognition).
- **Inventory growing > COGS** (demand slowing).
- **Capitalized costs rising** (P&L → BS shift).
- **Goodwill > 50% of equity** (risk of impairment).
- **Auditor changes** sin explicación satisfactoria.
- **Restatements** (10-K/A) — automatic deep dive.
- **Non-GAAP vs GAAP distance widening** over time.
- **"One-time" charges frecuentes** (nothing is one-time if it happens every year).
- **Revenue recognition policy changes**.
- **Off-balance-sheet liabilities**: operating leases pre-ASC 842, factoring, etc.
- **Beneish M-score / Altman Z-score** como screens adicionales.

### 7.5 Debt walls [quant]
- Maturity schedule YoY.
- Refinanciación plausible con FCF actual + current credit markets?
- **Good**: maturidades escalonadas, FCF cubre amortización.
- **Concerning**: >30% debt matura en próximos 2 años sin capacidad de refinanciación.

### 7.6 Dilution risk [quant]
- Convertibles outstanding y dilution potencial en conversión.
- SBC as % FCF (ya en 4.7).
- **Good**: dilución anual <2%.
- **Concerning**: dilución >5% anual, especially en down market (toxic convertibles).

### 7.7 Going concern [qual]
- Auditor language (10-K opinion).
- Covenant compliance — cuán cerca de breach?
- **Concerning**: going concern language explicit.

### 7.8 Cycle position [qual]
Referencia: Howard Marks.
- ¿En qué punto del ciclo de crédito / industria estamos?
- ¿Margin at peak? ¿Reversal a la media probable?
- **Good**: análisis realizado con supuestos mid-cycle o through-cycle.
- **Concerning**: extrapolación de peak margins/growth indefinida.

---

## 8. Psychological & contrarian lens

### 8.1 Mispricing thesis [qual]
¿Por qué el precio es wrong? Motivos posibles:
- Temporary headwind (cyclic).
- Neglect (small-cap sin cobertura).
- Forced selling (index rebalance, tax-loss harvesting).
- Spin-off (Greenblatt — ignorados sistemáticamente).
- Post-IPO lockup pressure.
- Index exclusion pressure.
- Regulatory overhang con resolución clara venidera.

**Good**: thesis específica y falsable con catalyst timeline realista.
**Concerning**: "it's just cheap" sin thesis accionable.

### 8.2 Consensus vs variant view [qual]
- ¿Qué supuestos tiene el sell-side consensus?
- ¿Dónde discrepamos y por qué?
- **Good**: variant view con evidencia concreta (industry data, channel checks).
- **Concerning**: variant view solo por ser contrarian.

### 8.3 Base rates [qual]
- Histórico de setups similares — ¿qué % funcionó?
- Spinoffs de compañías con founder-CEO vs corporate spin: base rates distintas.

### 8.4 Pre-mortem [qual]
- Si esto es un value trap, ¿cuál sería el narrative post-hoc?
- Kill criteria derivados de este pre-mortem.

---

## 9. Decision frame

### 9.1 Margin of safety target [hybrid]
- **Quality compounder**: MOS 30% suficiente.
- **Average business**: MOS 50%.
- **Cyclical / turnaround / low-quality**: MOS 60%+.
- MOS = (intrinsic − price) / intrinsic.

### 9.2 Position sizing [quant]
- Kelly-informed pero capped (Pabrai: ~10% max; Spier style).
- Never all-in (incluso alta convicción: 10-15% max).
- Porfolio concentration: ≤15 posiciones ideal (Sleep/Pabrai) vs ≤25.

### 9.3 Kill criteria [qual]
Definidos **ex-ante** — no inventar después.
Ejemplos:
- Moat breach evidenciable (share loss >X%, margin compression >Y%).
- Capital allocation change (nueva CEO con M&A agresivo).
- Leverage threshold (Net Debt/EBITDA > 4x).
- Accounting restatement material.
- Insider selling pattern sostenido (3+ quarters).

### 9.4 Sell triggers [hybrid]
- **IV reached**: el precio alcanza intrinsic value → trim o sell.
- **Thesis broken**: kill criteria triggered.
- **Better opportunity**: opportunity cost (otra posición con mayor MOS).
- **Permanent capital loss risk**: material deterioro en fundamentals.
- **Compounder ganador**: no vender "solo porque subió" si compounding intacto.

### 9.5 Review cadence [qual]
- Quarterly earnings review contra thesis KPIs definidos en 9.3.
- Si un KPI trending wrong → downgrade convicción.
- Anual: re-análisis completo (se re-genera en el sistema con versioning).

---

## Mapeo de routing (resumen para el sistema)

| Sección | % tool-lane | % LLM-lane | Especialista owner |
|---|---|---|---|
| 1. Business understanding | 10% | 90% | Filings RAG + Supervisor |
| 2. Moat analysis | 30% | 70% | Competitive & Moat |
| 3. Management & skin | 40% | 60% | Management & Skin |
| 4. Financial quality | 95% | 5% | Financials |
| 5. Growth & reinvestment | 60% | 40% | Financials + Competitive |
| 6. Valuation | 90% | 10% | Valuation Agent |
| 7. Risks & red flags | 50% | 50% | Financials + Filings RAG |
| 8. Psychological & contrarian | 10% | 90% | Value Expert (synthesis) |
| 9. Decision frame | 40% | 60% | Value Expert + user (checkpoint) |

Las secciones dominadas por **tool-lane** deben ser trustworthy by construction (ADR-005). Las **LLM-lane** requieren citas validadas.
