"""Paquete del sistema de due diligence. Al importarse, deja la consola a prueba de logs no ASCII (A6)."""
from due_diligence.utils.console import make_console_safe

make_console_safe()
