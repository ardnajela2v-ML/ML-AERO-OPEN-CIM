# WindPro Platform / AERO OPEN CIM

Plataforma de prefactibilidad eólica (recurso, layouts, MCDA y economía). Lee primero `CONTEXT.md` y `HANDOFF.md`.

## Comandos
- Ejecutar: `source .venv/bin/activate && python run.py` (puerto 5051)
- Pruebas: `python -m pytest -q`

## Reglas obligatorias de colaboración
1. Trabajar en pasos pequeños y verificables; commit antes de cada fase.
2. Presentar plan detallado antes de editar lógica económica o energética.
3. Reportar inconsistencias en lugar de corregirlas o asumirlas en silencio.
4. Todo cruce de series temporales se hace en UTC.
5. No cambiar supuestos metodológicos ni económicos sin confirmación previa.
6. No usar `PML_usd.txt`; el nodo base es `02JUD115`.
7. `CERTE_FOLDER` está fuera del repo; ver `HANDOFF.md`.
