# Validación automatizada de Jensen

El control compara el solapamiento circular exacto de WindPro con PyWake NOJ
2.6.19: Ct=0.8, k=0.075, velocidad libre de 9 m/s desde 270°, inducción
`ct2a_mom1d`, `AreaOverlapAvgModel` y superposición `SquaredSum`.
Se comprueban el déficit escalar, las velocidades vectorizadas y la potencia
con una misma curva Gamesa de 90 m de diámetro.

Los 15 escenarios incluyen tres turbinas alineadas a 5D (con una referencia
analítica de superposición), ocho solapamientos parciales a ambos lados del eje
y seis posiciones tangentes o completamente exteriores. Los puntos parciales
incluyen la banda que una corrección lineal antigua descartaba incorrectamente.
Las tolerancias absolutas son 1e-10 m/s y 1e-7 kW, sin tolerancia relativa.
Un desacuerdo provoca AssertionError y salida distinta de cero.

Desde la raíz del repositorio:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r validation/requirements-jensen.txt
.venv/bin/python -m pytest -q tests/test_jensen_pywake.py tests/test_farm_mcda.py
.venv/bin/python validation/control_case_jensen_pywake.py > validation/control_case_jensen_pywake.json
```

La suite habitual (`python -m pytest`) descubre los tres nuevos tests. Si PyWake
no está instalado, estos se marcan explícitamente como omitidos; el script de
validación requiere PyWake y falla si falta. Para exigir el control en CI,
instalar los requisitos de validación y ejecutar también el script.
Importar el módulo no ejecuta simulaciones ni carga archivos de turbina.
El script resuelve los archivos desde su propia ubicación y puede ejecutarse
por ruta absoluta desde cualquier directorio.

Entorno verificado: Python 3.9.6, NumPy 2.0.2, SciPy 1.13.1, PyWake 2.6.19,
pytest 8.4.2. El JSON generado incluye versiones, tolerancias y errores por caso.
No constituye una validación contra mediciones de campo.

Resultado de la ejecución verificada: **32 tests aprobados** en los dos archivos
indicados (64.49 s); 15 escenarios externos aprobados, error máximo de velocidad
1.7763568394002505e-15 m/s y de potencia 6.821210263296962e-13 kW.
También se verificó que reintroducir temporalmente el corte prematuro en
`|dy| >= r_wake` mediante un mock hace fallar el caso parcial.
La ejecución emitió 17 avisos: compatibilidad binaria de una dependencia NumPy,
deprecaciones de Matplotlib/Pyparsing y densidad de aire por defecto en los
tests existentes. No hubo fallos ni omisiones.
