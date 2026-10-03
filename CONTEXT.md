# Contexto del repositorio

## 1. Objetivo

**AERO OPEN CIM / WindPro Platform** es una aplicación de apoyo a estudios de
prefactibilidad de parques eólicos. Integra datos meteorológicos observados y
de reanálisis, estima el recurso y la producción de aerogeneradores, ayuda a
diseñar y comparar layouts y calcula indicadores técnico-económicos. Su
interfaz está orientada a estudios en México y Latinoamérica.

El flujo de diseño principal está descrito en [`docs/layout_pipeline.md`](docs/layout_pipeline.md):
configurar el proyecto → preparar el recurso → generar alternativas de parque
→ clasificarlas con AHP/TOPSIS → evaluar la alternativa. También hay una
herramienta independiente de validación y reconstrucción histórica MCP.

## 2. Stack tecnológico

| Capa | Tecnologías y archivos |
|---|---|
| Backend principal | Python 3.10+, Flask y Flask-CORS; fábrica de aplicación y API REST en [`api.py`](api.py), configuración en [`config.py`](config.py). |
| Frontend | HTML, CSS y JavaScript sin framework ni proceso de compilación: [`templates/index.html`](templates/index.html), [`static/css/aero.css`](static/css/aero.css) y [`static/js/aero.js`](static/js/aero.js). |
| Datos y cálculo | NumPy, pandas, SciPy, scikit-learn, Shapely; lectura de XLSX/Parquet mediante openpyxl/pyarrow y utilidades HTTP con requests. Dependencias declaradas en [`requirements.txt`](requirements.txt). |
| ML / MCP | scikit-learn para regresores y validación; SciPy para estadísticas, distribución Weibull, clustering y optimización. El paquete separado [`windmcp/`](windmcp) implementa el flujo MCP. |
| Pruebas | pytest para Python y pruebas JavaScript `.test.cjs` en [`tests/`](tests). No hay `package.json` ni configuración de build del frontend. |

## 3. Estructura y módulos importantes

- [`api.py`](api.py): aplicación Flask, sesiones/tareas, importación/exportación
  de proyectos y endpoints de datos, energía, parque, Kriging, MCDA y MCP.
- [`core/`](core): lógica científica y de dominio:
  - [`data.py`](core/data.py), [`resource_period.py`](core/resource_period.py),
    [`historical_resource.py`](core/historical_resource.py): lectura, calidad,
    periodos y preparación de observaciones/recurso.
  - [`energy.py`](core/energy.py), [`wind_stats.py`](core/wind_stats.py):
    curvas de potencia, extrapolación de viento, energía/AEP, rosas,
    estadística y turbulencia.
  - [`kriging.py`](core/kriging.py), [`spatial_calibration.py`](core/spatial_calibration.py),
    [`terrain.py`](core/terrain.py): interpolación espacial, calibración y
    enriquecimiento del terreno.
  - [`farm.py`](core/farm.py), [`layout_generation.py`](core/layout_generation.py),
    [`farm_mcda.py`](core/farm_mcda.py), [`layout_refinement.py`](core/layout_refinement.py):
    estelas, restricciones y generación/comparación de layouts.
  - [`ahp.py`](core/ahp.py), [`multicriteria.py`](core/multicriteria.py):
    ponderación AHP y ranking TOPSIS.
  - [`cost_model.py`](core/cost_model.py), [`project_economics.py`](core/project_economics.py),
    [`sensitivity.py`](core/sensitivity.py): costos, flujos de vida del
    proyecto y sensibilidad.
  - [`hybrid.py`](core/hybrid.py): simulación simplificada de sistemas híbridos
    con generación solar, batería, diésel y demanda.
  - [`osm_infrastructure.py`](core/osm_infrastructure.py),
    [`acoustic_noise.py`](core/acoustic_noise.py),
    [`shadow_flicker.py`](core/shadow_flicker.py): infraestructura y
    restricciones ambientales.
- [`windmcp/`](windmcp): flujo MCP separado para cargar CERTE/reanálisis,
  controlar calidad, validar modelos, reconstruir historia y emitir artefactos
  e informes. Sus principales módulos son `service.py`, `data.py`, `mcp.py`,
  `ml.py`, `backtest.py`, `reconstruction.py` y `report.py`; expone una API
  propia en `windmcp/api.py`.
- [`templates/ingenieria.html`](templates/ingenieria.html): página de
  ingeniería/reproducibilidad servida en `/ingenieria`; es principalmente una
  referencia de diseño, no el frontend operativo del flujo principal.
- [`dendoclust/`](dendoclust): utilidades independientes para comparar curvas
  de turbinas y agruparlas jerárquicamente; incluye una app Flask y un script
  de análisis/exportación para Orange.
- [`DATOS DE METEREOLOGICOS DE parques/`](DATOS%20DE%20METEREOLOGICOS%20DE%20parques):
  126 archivos meteorológicos incluidos para ubicaciones de parques.
  [`aerogeneradores/`](aerogeneradores) contiene curvas CSV de potencia.
  [`mercado_demanda/`](mercado_demanda), [`mercado_pml/`](mercado_pml),
  [`mercado_fx/`](mercado_fx) y [`mercado_tasas/`](mercado_tasas) contienen
  series/presets comerciales.
- [`checar/`](checar): datos, mapas e informes de trabajo/validación.
  [`exports/`](exports) conserva exportaciones de ejemplo.
- [`docs/`](docs): metodología y limitaciones; son especialmente relevantes
  `economic-lifecycle.md`, `layout_pipeline.md`, `ahp-topsis.md` y
  `recurso-periodo-certe.md`.
- [`saved_models/`](saved_models), [`uploads/`](uploads) y `runs/`: ubicaciones
  para artefactos generados. `saved_models/` solo contiene `.gitkeep` en este
  checkout.

## 4. Modelos de ML: entradas, salidas y métricas

### Reconstrucción MCP (WindMCP)

El paquete [`windmcp/`](windmcp) compara modelos para reconstruir velocidad
local a partir de reanálisis:

- **Modelos estadísticos MCP**: regresiones/correcciones y mapeo de cuantiles
  (global, estacional y por sector/dirección), implementados en `windmcp/mcp.py`.
- **Regresión ML** en [`windmcp/ml.py`](windmcp/ml.py): Random Forest,
  Gradient Boosting y Ridge son candidatos predeterminados; MLPRegressor está
  disponible como opción no predeterminada. Las entradas incluyen velocidad
  de referencia, sus transformaciones cuadrática y raíz, hora/día cíclicos y,
  cuando están disponibles, dirección, temperatura y presión. El objetivo es
  velocidad observada CERTE. Los valores predichos negativos se limitan a cero.
- **Dirección**: correcciones circulares globales, sectoriales o
  mes-sectoriales; se validan por separado de la velocidad.
- **Reconstrucción**: [`windmcp/reconstruction.py`](windmcp/reconstruction.py)
  aplica el modelo a la historia del reanálisis y conserva las observaciones
  CERTE válidas, rellenando sus huecos con predicciones. Produce series
  horarias de velocidad/dirección, procedencia y bandas de incertidumbre si
  están disponibles.

`windmcp/backtest.py` realiza selección con holdout cronológico y backtesting
walk-forward. Las métricas incluyen N, R, R², RMSE, MAE, sesgo (Bias), distancia
KS entre distribuciones, parámetros Weibull `k`/`lambda` y sus diferencias,
percentiles y métricas angulares para dirección. Se generan resultados JSON,
CSV, HTML e informes bajo el directorio de cada ejecución. El flujo de
validación y las métricas están en [`windmcp/mcp.py`](windmcp/mcp.py),
[`windmcp/backtest.py`](windmcp/backtest.py) y
[`windmcp/service.py`](windmcp/service.py).

### Otros usos de ML

- [`core/ml_validation.py`](core/ml_validation.py) calcula importancia por
  permutación **fuera de muestra** con Random Forest y la compara con una
  regresión lineal/logística; informa R² en regresión o balanced accuracy en
  clasificación. Devuelve `None` si no hay datos/clases suficientes para una
  validación honesta.
- [`core/multicriteria.py`](core/multicriteria.py) y `core/farm_mcda.py` usan
  TOPSIS/AHP para decisión multicriterio; no son modelos predictivos.
- [`dendoclust/`](dendoclust) convierte curvas de potencia en series de
  producción y calcula distancias/correlaciones y clustering jerárquico.

No se encontró ningún modelo preentrenado en `saved_models/`: los modelos MCP
se ajustan durante las ejecuciones. Entre las métricas energéticas no
predictivas están AEP P50/P75/P90, factor de capacidad, horas equivalentes y
pérdidas.

## 5. Cálculos tecnoeconómicos

La lógica común de flujos de caja está en
[`core/project_economics.py`](core/project_economics.py) y los supuestos se
documentan en [`docs/economic-lifecycle.md`](docs/economic-lifecycle.md):

- **CAPEX**: costo de turbinas dividido por su participación en el CAPEX, más
  componentes BOS; diseño añade proxy de cimentación geométrica y costo de
  cable colector. El escenario editable de [`core/cost_model.py`](core/cost_model.py)
  usa 945 USD/kW de turbina, 70% de participación y OPEX anual de 3.72% del
  CAPEX. Diseño usa aparte el benchmark global IRENA onshore 2024 de
  1,041 USD/kW.
- **OPEX**: se escala con inflación anual. La energía parte del AEP neto del año
  1 y se degrada 0.3% anual por defecto; el desmantelamiento es 3% del CAPEX
  inicial y se paga al final, sin valor de rescate.
- **VAN/VPN (NPV)**: `-CAPEX + Σ flujo_neto_t / (1 + tasa)^t`.
- **LCOE**: valor presente de CAPEX, OPEX y desmantelamiento dividido entre el
  valor presente de la energía degradada.
- **TIR (IRR)**: bisección sobre flujos que incluyen CAPEX en año 0 y costo
  terminal; puede no estar disponible si no se encuentra cambio de signo.
- También calcula payback simple/descontado, B/C, ROI, valor presente de
  ingresos/costos/energía, tablas de flujo anual y sensibilidad. El endpoint
  `/api/energy/leveraged` contempla financiamiento y servicio de deuda.
- La energía puede valorarse con un precio constante, serie anual o, para el
  primer año, ingreso horario explícito. Diseño agrega escenarios de demanda,
  límites de red y comercialización.

Todos los importes de un cálculo deben usar la misma moneda. Las vidas, tasa y
supuestos son configurables en los flujos que los exponen.

## 6. Estado: funciona, incompleto y limitaciones/errores conocidos

### Implementado

- La app Flask principal, interfaz, endpoints REST, persistencia/importación de
  proyectos y tareas asíncronas.
- Lectura y análisis de datos de viento, estadísticas, curvas de potencia,
  simulación de energía, comparación de turbinas, Kriging/calibración espacial,
  generación de layouts y ranking AHP/TOPSIS.
- Flujo MCP con selección/validación de velocidad y dirección, reconstrucción
  histórica y reportes reproducibles.
- Flujos económicos compartidos y pruebas de consistencia.

### Pendiente o acotado explícitamente

- [`docs/ahp-topsis.md`](docs/ahp-topsis.md) indica que los juicios AHP actuales
  provienen de un solo investigador y aún requieren validación; la revisión de
  pertinencia, redundancia y sensibilidad de criterios permanece abierta.
- [`docs/recurso-periodo-certe.md`](docs/recurso-periodo-certe.md) aclara que
  el mapa espacial no reconstruye Kriging temporal por instante para cada
  turbina. En el flujo de parque se usan factores locales/estadísticas o la
  serie histórica seleccionada; no equivale a una simulación cronológica
  meteorológica de varias décadas.
- Para AEP con un solo año parcial, `core/energy.py` estima P75/P90 como 95% y
  90% de P50; con varios años usa percentiles empíricos. La salida parcial es
  una aproximación, no una distribución de incertidumbre observada.
- En [`templates/ingenieria.html`](templates/ingenieria.html) aparecen como
  blueprint endpoints de SARIMA, NWP/MOS, XGBoost/LSTM, PV detallado, PDF, etc.
  que no corresponden a rutas de la API principal de [`api.py`](api.py); el
  propio endpoint `/ingenieria` la describe como página de referencia. No
  asumir que esos ejemplos están conectados o implementados.
- El estado de sesiones y tareas del backend principal vive en memoria de
  proceso y expira tras 4 horas; reiniciar el servidor lo pierde. No hay base
  de datos configurada.
- [`config.py`](config.py) permite cambiar rutas con variables `AERO_*`, pero
  `CERTE_FOLDER` y `MARKET_DATA_FOLDER` tienen por defecto rutas fuera del
  repositorio. Algunas fuentes/presets pueden no estar disponibles en una
  instalación limpia; verificar y configurar esas rutas localmente.
- `dendoclust/app_orange.py` y `dendoclust/orange_ml_certe_real.py` importan
  matplotlib, que no está declarado en [`requirements.txt`](requirements.txt);
  instalar solo los requisitos base no garantiza que esas herramientas
  independientes arranquen.
- No hay evidencia de modelos persistidos en `saved_models/`; tampoco hay
  manifiesto del frontend o guía raíz previa de instalación.

### Errores reproducidos

No se reprodujo un fallo en la validación enfocada ejecutada para este análisis:
`python3 -m pytest -q tests/test_project_economics.py tests/test_lcoe_consistency.py tests/test_ml_validation.py tests/test_layout_pipeline.py`
terminó con **32 pruebas aprobadas**. No se ejecutó toda la suite ni el smoke
test de API, que requiere que el servidor esté iniciado. Los puntos anteriores
son limitaciones/configuración pendiente verificadas en el código y la
documentación, no afirmaciones de que toda la aplicación esté libre de errores.

## 7. Ejecución local

Desde la raíz del repositorio, con Python 3.10 o posterior:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Abrir <http://localhost:5051>. `PORT` permite cambiar el puerto, por ejemplo:
`PORT=8080 python run.py`. Flask sirve la interfaz desde `templates/` y
`static/`; no se necesita un servidor/build de frontend.

Opcionalmente, `python run_with_warmup.py` inicia la misma aplicación y
precalienta en segundo plano el cache de archivos meteorológicos para acelerar
las primeras consultas espaciales. WindMCP puede ejecutarse como servicio
independiente con `python -m windmcp.api` (puerto 5052); revisar sus entradas
CERTE/reanálisis y configuración en [`windmcp/config.py`](windmcp/config.py).

Para validar lógica sin levantar el servidor:

```bash
python -m pytest -q
```

El smoke test manual es `python tests/smoke_test.py` y necesita el servidor
principal activo. Para las herramientas `dendoclust`, instalar matplotlib
explícitamente y revisar los datos requeridos por cada script.
