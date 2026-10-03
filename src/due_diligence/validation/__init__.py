"""
Validación post-hoc de las salidas del LLM contra fuentes primarias (ADR-005, ADR-032).

`citations.py` comprueba que cada cita existe en el texto fuente que vio el LLM y que
sus números aparecen en la misma ventana del texto. Es determinista y sin red: los
agentes la llaman dentro del nodo, justo después de `structured()`, con el corpus local.
"""
