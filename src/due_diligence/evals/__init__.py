"""
evals — harness de evaluación del Value Expert (H2-1, ADR-038).

Funciones puras (dataset, checks, scorecard, gate) testeadas offline, más un runner que es la
única pieza que llama al LLM y solo se ejecuta bajo demanda (scripts/run_evals.py). Mide
obediencia a reglas verificables y estabilidad; NO mide si el veredicto acierta como inversión.
"""
