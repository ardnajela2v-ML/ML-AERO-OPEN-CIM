# HANDOFF — Fase 1: Integración y Lectura de PML

Este documento registra los cambios, resultados empíricos verificados, estado de los datos observados/reconstruidos, caso de estudio actual, instrucciones de reproducción y decisiones para la continuación del proyecto. Basado estrictamente en la inspección del repositorio, `git log`, `git diff` y las pruebas ejecutadas en esta sesión.

---

## 1. Qué se cambió

### Archivos modificados y creados

1. **`core/forecasting.py`**:
   * **Funciones agregadas:**
     * `_is_cenace_tabulated(path)`: Detecta automáticamente si el archivo tiene separadores por tabulador y las columnas clave `fecha`, `hora` y `pml_usd_kwh`.
     * `_read_cenace_tabulated(path)`: Parser especializado para el formato de exportación horaria de CENACE.
   * **Función extendida:**
     * `read_pml(path)`: Se agregó la delegación inicial hacia `_read_cenace_tabulated(path)` si el archivo cumple la condición tabulada. Si no, conserva intacto el flujo legado por coma.
   * **Motivo:**
     * Permitir la lectura del archivo multi-año 2017–2024 con fechas, respetando precios en USD/kWh (`pml_usd_kwh`), convirtiendo las horas locales a UTC continuo y descartando la fila de resumen final, sin romper los archivos antiguos sin fecha (`PML_usd.txt`, `PML_IPO.txt`, `PML_02JUD-115_2018_usd.txt`).

2. **`tests/test_pml_reader.py`**:
   * **Archivo nuevo** con 11 pruebas unitarias (`pytest`):
     * `TestPmlLegacyReaders`: verifica que los archivos antiguos se identifiquen como no tabulados y lean sus filas esperadas (8,759 y 8,760).
     * `TestPmlCenaceTabulatedNew`: verifica lectura de 70,128 filas, igualdad compra=venta, monotonía de timestamps UTC y saltos DST.
     * `TestSyntheticCenaceParser`: verifica el parser con un archivo tabulado sintético con fila de resumen malformada y hora 25.
     * `TestProfileNormalizationWithNewPml`: verifica que `normalize_fixed_hourly_profile` conserve las 70,128 horas.

3. **`.gitignore`**:
   * Se agregó `Sin título/` para evitar advertencias de carpetas temporales anidadas.

4. **Archivos de datos del repositorio (Commit `232063d`)**:
   * `mercado_pml/PML_2017-2024 02JUD115.txt`: renombrado desde `PML_2017 2024 02JUD115.txt` (70,128 registros horarios).
   * `mercado_pml/PML_2017-2024_02IPO115.txt`: agregado a la carpeta de presets (70,128 registros horarios).
   * `checar/Datos Anual 2.txt`: agregado a la carpeta de trabajo.
   * `DATOS DE METEREOLOGICOS DE parques/`: reorganización eliminando subcarpeta temporal `123/`.

---

## 2. Resultados verificados con números

* **Filas y registros horarios:**
  * Total de registros en el archivo: 70,130 líneas (1 cabecera + 70,128 filas horarias + 1 fila de resumen final).
  * Filas horarias válidas procesadas: **70,128** exactas ($8 \times 365 + 2 \text{ bisiestos} = 70,128$).
  * Fila de resumen final: descartada automáticamente por no tener fecha ni hora numéricas.
  * Pares `(fecha, hora)` duplicados: **0** (unicidad estricta).

* **Distribución de horas por día y Horario de Verano (DST):**
  * Total de días entre 2017 y 2024: **2,922 días**.
  * Días con exactamente 24 horas: **2,910 días**.
  * Días con 23 horas: **exactamente 6 días** (primer domingo de abril 2017–2022, entrada en vigor de DST en México donde CENACE omite la hora 24):
    * `02/04/17`, `01/04/18`, `07/04/19`, `05/04/20`, `04/04/21`, `03/04/22`.
  * Días con 25 horas: **exactamente 6 días** (último domingo de octubre 2017–2022, término de DST donde CENACE añade la hora 25):
    * `29/10/17`, `28/10/18`, `27/10/19`, `25/10/20`, `31/10/21`, `30/10/22`.
  * Días con horas distintas de 23, 24 o 25: **0**.
  * Años 2023 y 2024: 100% de los días con exactamente 24 horas (DST eliminado en México).

* **Construcción de timestamps UTC:**
  * Fórmula: $\text{timestamp\_utc} = \text{medianoche\_utc}(\text{fecha}) + (\text{hora} - 1)\text{ horas}$.
  * Medianoche local (`America/Mexico_City`) convertida a UTC: `06:00 UTC` en invierno / `05:00 UTC` en verano.
  * Hora 25: calculada como `medianoche_utc + 24 horas`, enlazando de forma continua a 1 hora de diferencia con la hora 1 del día siguiente.
  * Monotonía temporal: **estrictamente monótona creciente** (`is_monotonic_increasing == True`).
  * Duplicados de timestamps UTC: **0**.
  * Rango temporal UTC: `2017-01-01 06:00:00+00:00` a `2025-01-01 05:00:00+00:00`.

* **Niveles de precios y comparación con archivos legados:**
  * Rango de precios en el nuevo archivo: mínimo **0.0 USD/kWh** (61 horas con PML cero por condiciones de mercado/congestión), máximo **1.629115 USD/kWh**, media global **0.058940 USD/kWh**.
  * Comparativa `PML_usd.txt` (8,759 h) vs corte 2018 nuevo (8,760 h sobre primeras 8,759 h coincidentes):
    * Correlación de Pearson: **$r = 0.2365$** (no coinciden en perfil horario).
    * Correlación de Spearman: **$\rho = 0.2917$**.
    * Media `PML_usd.txt`: **0.110344 USD/kWh** vs corte 2018 nuevo: **0.082102 USD/kWh** (**+34.4%** inflado en el archivo legado).
    * Hallazgo: `PML_usd.txt` tiene correlación $r = 0.9827$ con el año **2022** del nuevo archivo, no con 2018.
    * El archivo preexistente `mercado_pml/PML_02JUD-115_2018_usd.txt` tiene correlación **$r = 0.9957$** con el corte 2018 nuevo (media 0.078993 USD/kWh).

* **Pruebas (`pytest`):**
  * `tests/test_pml_reader.py`: **11 pasadas, 0 fallos** en 0.97s.
  * Suite completa (`python -m pytest -q`): **335 pasadas, 47 advertencias informativas, 0 fallos** en 202.46s (commit `232063d`).

---

## 3. Datos CERTE y MCP

* **Ubicación y archivo:** `CERTE_FOLDER` (en `config.py`) apunta a `/Users/ale/Desktop/2222dose/copiaunoMCPSOFT/certe`, fuera del repo. Archivo preferido: `M04_CERTE_CONSOLIDADO_2017-2020.txt`. Datos a 10 min, del 2017-11-21 11:10 al 2020-09-01 00:00 (hora local UTC−6). No hay observaciones de 2021 a 2024.
* **Horas válidas de velocidad a 80 m:**
  * 2017: 931 h (10.6 %).
  * 2018: 8,746 h (99.84 %).
  * 2019: 6,925 h (79.05 %, hueco del 16-abr al 22-jun).
  * 2020: 5,114 h (58.22 %, mayo casi vacío y termina el 1-sep).
* **Zona horaria:** `windmcp/data.py::_localize_observation_timestamps` usa `Etc/GMT+6` y convierte a UTC; los reanálisis ya vienen en UTC.
* **Control de calidad (`windmcp/qc.py`):** máximo 70 m/s, salto máximo 15 m/s, persistencia ≥6 muestras idénticas. 516 de 130,244 registros (0.40 %) quedan como sospechosos y se excluyen del entrenamiento.
* **MCP activo `20260926T185148Z_f23fc01b`:** `ml_random_forest`, entrenado con 2018 (8,746 h; R² 0.717, RMSE 2.99 m/s); validado en 2019 (6,872 h; R² 0.792, RMSE 2.42 m/s). Serie reconstruida 1980-01-01 a 2024-08-31 UTC: 21,641 h observadas conservadas y 369,919 h sintetizadas.

---

## 4. PARQEU1.aeroproj (estado actual)

* **Parámetros del proyecto:**
  * Velocidad con `UnionWindnavigator_MERRA2_N16,500_W95,000.txt`.
  * Dirección con `Dosarbolitos_ERA5_N16,500_W95,000.txt`.
  * Altura de buje: 80 m.
  * Nodo PML: `JUD334` (físicamente `02JUD-115`).
  * Archivo PML activo: `PML_usd.txt` en modo `fixed_2018_profile` con precio 0.1103 USD/kWh.
  * Horizonte económico: 25 años.
  * Tasa de descuento: 9.5 %.
  * Moneda: USD.
  * Tipo de cambio base: 18 MXN/USD.
  * Esquema comercial: `spot_pml/total_sale`.
* **Nota de consistencia económica:** Ese PML no es realmente 2018, así que los resultados económicos guardados en el caso usan un precio por encima del 2018 real del nodo (0.1103 USD/kWh en el caso frente a 0.0821 USD/kWh verificado en el nodo).

---

## 5. Cómo reproducir

Desde la raíz del repositorio (`/Users/ale/Desktop/2222dose/base/WindProPlatform`):

```bash
# 1. Activar el entorno virtual
source .venv/bin/activate

# 2. Ejecutar las pruebas del lector de PML (Fase 1)
python -m pytest -q tests/test_pml_reader.py

# 3. Ejecutar la suite completa de pruebas del proyecto
python -m pytest -q

# 4. Levantar la aplicación local
python run.py
# Abrir en el navegador: http://localhost:5051
```

Rutas clave de los datos utilizados:
* Archivo nuevo JUD: `mercado_pml/PML_2017-2024 02JUD115.txt`
* Archivo nuevo IPO: `mercado_pml/PML_2017-2024_02IPO115.txt`
* Lector implementado: `core/forecasting.py`
* Tests: `tests/test_pml_reader.py`

---

## 6. Lo que NO se ha hecho

* **Validación de captura (antes "Fase 2"): completada.** Ver sección 9 — precio capturado 2019 y 2020 calculado y cruzado contra viento predicho, con r, razón de captura, AEP y bootstrap por bloques de día para ambos años; incluye el hallazgo y la corrección (versión "(ii)") del sensor WS_80mB_mean en 2020. 2018 es el año de entrenamiento (dentro de muestra), no se valida fuera de muestra.
* **Economía intacta:** No se modificó `core/project_economics.py` ni `core/farm.py`.
* **API sin modificar:** No se alteraron endpoints de `api.py`.
* **Caso de estudio no modificado:** No se ha alterado la configuración guardada dentro de `PARQEU1.aeroproj`.

---

## 7. Aspectos sin verificar

* **[resuelto parcialmente, ver sección 8]:** La convención exacta de etiquetado del remuestreo de WindMCP de 10 min a 1 h se auditó con `tools/audit_timing.py`. No se encontró evidencia suficiente de un desfase de hora entera; sí se documentó un desfase menor y específico de ERA5 que queda como limitación conocida, no como corrección aplicada.

---

## 8. Auditoría de tiempos

Auditoría de solo lectura ejecutada con `tools/audit_timing.py` (no modifica `windmcp/` ni `core/`). Compara la velocidad observada a 80 m (CERTE M04, reloj UTC−6 fijo) contra MERRA-2 (`UnionWindnavigator_MERRA2_N16,500_W95,000.txt`) y ERA5 (`Dosarbolitos_ERA5_N16,500_W95,000.txt`) — verificado programáticamente que son los mismos dos archivos de referencia del run MCP activo `20260926T185148Z_f23fc01b`. Salidas completas en `tools/audit_timing_output/` (`lag_sweep.csv`, `subhour_sweep.csv`, `solar_peak_by_month.csv`, `direction_lag_sweep.csv`, `bootstrap_lag_diff.csv`, `summary.txt`).

**Resultados por prueba:**

1. **Barrido de lag entero (−3h a +3h), por año × fuente × filtro de completitud × temporada.** ERA5 da mejor ajuste en lag=−1h de forma consistente (2019: r=0.8865 vs r=0.8836 en lag=0; 2020: r=0.6867 vs r=0.6842), en ambas temporadas (abr–oct y nov–mar) y en las 4 variantes de completitud (sin filtro, ≥4/6, ≥5/6, ==6/6 — el umbral no cambia la conclusión). MERRA-2 **no** replica el patrón: mejor lag=0h en 2019, mejor lag=+1h en 2020. Si el problema fuera el reloj del logger CERTE, debería afectar a ambas referencias por igual; que solo ERA5 lo muestre apunta a algo específico de esa fuente, no a la torre.
2. **Fracciones de hora (interpolación fina ±60 min, solo ERA5).** El pico de ajuste cae en +30 a +40 min, no en +60 min — consistente con el efecto esperable de comparar un promedio horario (caja) contra una serie instantánea (el punto medio del intervalo es la mejor aproximación puntual de un promedio de caja de una señal suave). El promedio trapezoidal `(ERA5(T)+ERA5(T+1h))/2` — la comparación correcta caja-vs-instantáneo — sigue preferiendo lag=−1h (r=0.8874) sobre lag=0 (r=0.8802): el efecto de promediado no explica todo el desfase, pero tampoco hay una hora entera "perdida" de forma limpia.
3. **Hora local del pico de radiación solar, por mes.** Pico a las 12:00 local en los 12 meses del año sin excepción — **descarta que el reloj de la torre siguiera horario de verano**; se confirma reloj UTC−6 fijo todo el año.
4. **Dirección (78 m obs vs dirección de referencia, estadística circular).** La señal es mucho más débil que en velocidad: ERA5 2019 agreement=0.8173 (lag=−1) vs 0.8151 (lag=0), diferencia de 0.002 sobre una escala de ruido circular con RMSE de 40–52°. MERRA-2 2020 no se mueve entre lags (0.7169 en ambos). Solo ERA5 arrastra el patrón a dirección; MERRA-2 no.
5. **Significancia (bootstrap por bloques de día, n=1000, seed=12345).** ERA5 2019: diff observado r(−1h)−r(0h)=+0.0029, IC95%=[+0.0003, +0.0056] → **significativo**. ERA5 2020: diff=+0.0026, IC95%=[−0.0005, +0.0057] → no significativo (potencia estadística menor: 215 días completos vs 291 en 2019, coherente con la completitud horaria más baja de 2020: 58.3% vs 79.0% de horas 6/6). MERRA-2 no significativo en ningún año y con signo de diff negativo en ambos (buen control negativo).

**Conclusión:** Sin evidencia suficiente para corregir ERA5: mejora de r ≈ 0.003, significativa solo en 2019, el ajuste fino no apunta a 1 h entera, y ERA5 solo afecta la dirección. Queda como limitación documentada. Reloj de torre UTC−6 fijo confirmado.

No se modificó ningún archivo de `windmcp/` ni `core/` como resultado de esta auditoría.

---

## 9. Sensor WS_80mB_mean trabado en 2020 y validación de captura

Al intentar validar el precio capturado (ver sección "Decisiones del usuario" más abajo) con `tools/capture_validation.py` — observado vs predicho MERRA-2, reutilizando `core/farm.py::chronological_revenue`, solo horas CERTE 6/6, modelo real de producción `ml_random_forest` (verificado contra `result.json::speed_model_selection`, no confundir con el archivo `_ml_forward.csv`, que es de una comparación ML-only distinta que elige `ml_gradient_boosting` y no se usa para reconstrucción) — 2020 mostró un AEP predicho más del doble del observado (7787 vs 3802 MWh/año) y una correlación mucho más baja que 2019 (0.673 vs 0.874).

**Causa encontrada:** `WS_80mB_mean` (uno de los dos sensores redundantes a 80 m) quedó dañado desde inicios de enero hasta inicios de junio de 2020 (ver ventana exacta abajo). `WS_80mA_mean`, `WS_60m_mean`, `WS_40m_mean` y `WS_20m_mean` están sanos durante toda la ventana. `windmcp/data.py::_combine_height_sensors` promedia A y B asumiendo que ambos son mediciones válidas — su único filtro es "no NaN, no negativo" — y un sensor dañado pero numéricamente "válido" pasa ese filtro y se promedia con el bueno, contaminando `WS_80m_mean` (el target real de entrenamiento/validación del MCP). **No se modificó `windmcp/data.py`** — esto queda señalado como una limitación de `_combine_height_sensors` (no detecta sensores "pegados" ni desviados, solo NaN/negativos), no corregida (propuesta de arreglo al final de esta sección).

Se verificó con datos, no se asumió, que `WS_80mA_mean` sigue siendo confiable: (a) el exponente de cizalladura (ley de potencia, 20/40/60/80mA) es estable entre años — 2018: α=0.183, 2019: α=0.192, 2020: α=0.183 — sin distorsión en 2020 a pesar del sensor B dañado; (b) el cociente `A/((A+B)/2)` mensual en periodos limpios de 2018-2019 promedia 1.00057 con una desviación entre meses de 0.0054 — **sin sesgo sistemático**, A no lee ni más alto ni más bajo que el promedio combinado cuando ambos sensores están sanos (`tools/sensor_qc_80m.py`).

**Corrección de esta sección (ver nota debajo de la tabla):** la primera versión de este hallazgo (`tools/capture_validation_2020_sensor_fix.py`) usaba un detector de persistencia (B repite el mismo valor ≥6 muestras de 10 min seguidas) que solo atrapa un sensor *literalmente congelado* en un número exacto. Al revisar qué horas de enero-febrero sobrevivían a ese filtro, se encontró que dejaba pasar 105 horas con razón A/B de hasta 111x — B seguía mal, solo que no bit-a-bit idéntico. Se reemplazó por un **detector diario** (`tools/sensor_qc_80m.py`): un día se marca si `|promedio_diario(A)/promedio_diario(B) − 1| > 15%`. Ese umbral se eligió comparando contra el ruido de turbulencia normal entre A y B: por hora, incluso en 2018 (limpio) hay ~0.5% de horas con >10% de desviación solo por ruido; a nivel diario ese ruido se cancela y 2018 da 0/365 días marcados, 2019 da 1/291 (ver abajo). La sensibilidad al umbral (10%/15%/25%) no cambia la conclusión: 125/123/120 días marcados en 2020 respectivamente.

**Ventana detectada (umbral 15%, por días, no por calendario fijo):** 123 días entre **2020-01-03 y 2020-06-05**. No es un bloque limpio ene-may: incluye además 3 días aislados de junio (03, 04 y 05-jun) con desviación de 15-23% — una cola de recuperación más larga de lo que se había estimado antes (se había dicho "se recupera en junio" sin precisar que los primeros días de junio aún estaban parcialmente afectados).

**Hallazgo adicional (no corregido, fuera de alcance de esta sección):** 2019 tiene **un día aislado con la misma firma de falla**: 2019-12-25, razón A/B = 2.14 (B lee menos de la mitad por un solo día, se recupera al día siguiente). Es ~250x más corto que la ventana de 2020 y su impacto en las métricas anuales de 2019 es despreciable (1/291 días), pero es la misma clase de defecto y convendría revisarlo si se reentrena o se audita de nuevo 2019.

**Impacto en `historical_reconstruction.csv` (run activo `20260926T185148Z_f23fc01b`):** de los 8,784 valores horarios de 2020 en la reconstrucción larga (1980-2024), 2,952 caen en los 123 días marcados, y 2,922 de esos están etiquetados `data_source=CERTE_observed` (es decir, el valor contaminado quedó grabado tal cual en la serie oficial, no fue descartado por QC ni sustituido por el modelo). Sustituyendo esos 2,922 valores por `WS_80mA_mean` (dejando el resto del año igual, incluidas las horas que ya venían de `MCP_reconstructed`):

| | Energía anual 2020 (turbina Clipper 2.5 MW, solo para comparación relativa) |
|---|---|
| `historical_reconstruction.csv` original | 6,115.8 MWh |
| Sustituyendo 80mA en los 123 días marcados | 8,242.4 MWh |
| **Diferencia** | **+2,126.6 MWh (+34.8%)** |

**Validación de captura "(ii)" recalculada con los días exactos del detector** (`tools/capture_validation_2020_corrected.py`, reutiliza `chronological_revenue` y el PML JUD, solo horas 6/6):

| | n horas | meses | r(ws obs,pred) | Δ precio capturado | AEP obs | AEP pred |
|---|---|---|---|---|---|---|
| Sin corregir | 5105 | ene-sep | 0.673 | -1.13% | 3802 | 7787 |
| **(ii) corregida** | 5105 | ene-sep | **0.878** | **+0.35%** | **7448.7** | 7787.1 |

(La versión "(i) excluir horas trabadas" de la primera versión de esta sección se descarta — sus números estaban calculados sobre un filtro que, como se explicó arriba, no excluía todo lo contaminado.)

La versión corregida recupera una correlación casi igual a la de 2019 (0.874) y reduce el sesgo de AEP de +105% a prácticamente nada (AEP observado corregido y predicho quedan a 4.5% uno del otro) — confirma que el problema era de calidad de dato en la referencia observada, no una falla real de generalización del modelo MCP a 2020. Bootstrap por bloques de día (n=1000): la diferencia de precio capturado sigue sin ser significativa (IC95%=[-0.00022, +0.00040]); la correlación ws_obs-ws_pred sí lo es (IC95%=[+0.851, +0.899]).

**Validación de captura 2019 (año de referencia, sensor sano — no requiere corrección salvo el día aislado 2019-12-25, impacto despreciable en el año completo)** (`tools/capture_validation.py`, salida en `tools/capture_validation_output/`): usa las mismas horas CERTE 6/6 y el mismo PML JUD que la versión 2020.

| | n horas | r(ws obs,pred) | Δ precio capturado | AEP obs (MWh) | AEP pred (MWh) | razón de captura obs | razón de captura pred |
|---|---|---|---|---|---|---|---|
| 2019 | 6870 | 0.8739 | +0.015% | 9171.3 | 9331.7 | 0.763 | 0.7631 |

Precio promedio realizado: observado 0.059347 USD/kWh vs predicho 0.059356 USD/kWh (precio plano de referencia 0.077787 USD/kWh). Bootstrap por bloques de día (`tools/capture_validation_output/bootstrap_capture_diff.csv`, n=1000, n_days=290): diferencia de precio capturado (pred−obs) = +0.000009 USD/kWh, IC95%=[−0.001072, +0.001125] → **no significativa**. Correlación ws_obs-ws_pred = 0.8739, boot_mean=0.8735, IC95%=[+0.8535, +0.8928] → significativamente distinta de cero. 2019 no se recalculó con el detector de sensor porque solo tiene 1/291 días marcados (2019-12-25) y su efecto en las métricas anuales es despreciable (ver sección "Hallazgo adicional" arriba).

**Aclaración sobre los R² de 2019 que aparecían en la sección 3 de este documento:** "R² 0.792" no es de `ml_random_forest` (el modelo real de producción) — es de `ml_gradient_boosting`, el ganador de una comparación ML-only interna (`ml_method_selection`) que **no** es la que se usa para reconstrucción. El R² de `ml_random_forest` (el modelo realmente activo, seleccionado por `speed_model_selection` con un criterio distinto — penalización de distribución, no solo RMSE) en 2019 completo es **0.7625** (recalculado y verificado directamente desde `<ref>_forward.csv`, no desde `<ref>_ml_forward.csv`). El 0.717 de la sección 3 sí es de `ml_random_forest`, pero es la métrica de un holdout cronológico *dentro* de 2018 (N=2187), no la validación externa en 2019 — son dos números de `ml_random_forest`, ninguno es "0.792".

**Propuesta de arreglo para `_combine_height_sensors` (NO aplicada):** detectar, con el mismo criterio de desviación diaria sostenida (no una hora aislada, por el ruido normal medido arriba), cuál de los dos sensores A/B deja de ser consistente con `WS_60m_mean` (sensor independiente, nunca falló en este episodio) en vez de asumir que A siempre es el sano. Si solo uno es consistente con 60 m: usar ese solo y marcar la hora con una nueva columna de proveniencia (`"A_only"`/`"B_only"` en vez de `"AB_mean"`). Si ninguno lo es (falla de toda la torre): dejar hueco (NaN), no adivinar. Tests a agregar (`tests/test_combine_height_sensors.py`): (1) A≈B normal → sigue promediando, regresión; (2) B dañado sostenido, A consistente con 60 m → cambia a `A_only`; (3) desacuerdo de 1-2 horas aisladas → NO dispara (protege del ruido de turbulencia, ~0.5%/hora en años limpios); (4) A y B fallan juntos → hueco, no se arriesga una suposición; (5) reproduce la ventana real de 2020 (2020-01-03 a 2020-06-05) como regresión; (6) corre sobre 2018 y 2019 completos y confirma `AB_mean` en ~100% de las horas (±1 día, el 25-dic-2019) — es la prueba que conecta con el punto de que el año de entrenamiento no se ve afectado. Qué cambiaría si se aplica: nada en el entrenamiento (2018 sale limpio con este criterio, no haría falta reentrenar `ml_random_forest`); sí cambiaría `historical_reconstruction.csv` para los 2,922 valores de 2020 y el día de 2019, y cualquier AEP/economía de WindPro que dependa de esa serie para esos periodos.

No se modificó `windmcp/`, `core/` ni `runs/mcp/` (el run oficial) en esta sección; todo vive en `tools/capture_validation.py`, `tools/capture_validation_2020_sensor_fix.py` (primer intento, conservado tal cual), `tools/sensor_qc_80m.py`, `tools/capture_validation_2020_corrected.py` y sus carpetas `_output/`.

---

## 10. Diseño del caso de estudio (en definición)

Sección de solo lectura/análisis (no se tocó código): mapeo del flujo real de recurso→layouts→TOPSIS→economía en `core/farm_mcda.py`, `core/layout_generation.py`, `core/layout_refinement.py`, `core/multicriteria.py`, `core/ahp.py` y los endpoints de `api.py`, hecho para preparar el diseño del motor de precio capturado. Ningún archivo de `windmcp/`, `core/`, `runs/mcp/` ni `api.py` se modificó.

### Mapa del flujo real

* **Recurso de viento por layout:** sin histórico, `_joint_synthetic_wind()` (`core/farm_mcda.py`) genera un Weibull sintético (v̄, k, ρ promedio de los sitios interpolados) común a todas las tecnologías. Con histórico (MCP/CERTE/reanálisis cargado en sesión), `prepare_historical_wind()` (`core/historical_resource.py`), llamado dentro del closure `_process_turbine` de `generate_park_layouts`.
* **`price_hourly_usd_kwh`:** se construye una sola vez por corrida en el endpoint `mcda_spatial`/`farm_layouts_generate` (`api.py`, función `_run`/handler, bloque que lee `market_models.pml.hourly_profile_2018` de la sesión) y se pasa igual a todas las tecnologías de esa misma llamada a `generate_park_layouts`.
* **`chronological_revenue`:** se invoca dentro de `_process_turbine` (`core/farm_mcda.py`) para **cada candidato factible del pool** (no solo el campeón final) — pero solo si hay recurso histórico con `hour_of_year` **y** `price_hourly_usd_kwh` no es `None`. Si falta cualquiera de las dos condiciones, no se calcula ingreso real y `_economics()` cae al escalar `aep_p50_mwh × 1000 × price_usd_kwh`.
* **`_economics()`:** recibe `revenue_year1_override` (el ingreso real cruzado hora por hora, cuando existe) y lo usa **solo para el año 1** del flujo de caja (`project_cashflows`); los años 2 en adelante siguen degradando/escalando desde ese punto de partida, no vuelven a cruzar contra PML horario de otros años.
* **7 criterios de TOPSIS activos (`LAYOUT_WEIGHTS`, `core/multicriteria.py`):** `hours_near_cpmax` 0.2311, `van_usd` 0.2047, `aep_p90_mwh` 0.1881, `capex_usd_per_installed_kw` 0.1516, `factor_capacidad` 0.1073, `cp_effective_full_series` 0.0813, `lcoe_usd_kwh` 0.0360. De estos, **solo `van_usd` depende del precio de energía** — `lcoe_usd_kwh` es PV(costos)/PV(energía) y no se mueve con el precio; `capex_usd_per_installed_kw`, `factor_capacidad`, `aep_p90_mwh`, `hours_near_cpmax` y `cp_effective_full_series` son puramente físicos.
* **Selección de campeón por tecnología:** `select_technology_champion()` (`core/layout_generation.py`) elige, dentro de cada tecnología, el candidato de **máximo VAN** entre los factibles — un argmax de una sola variable, no un mini-TOPSIS. El TOPSIS real (`rank_layout_alternatives`) solo compara después los campeones ya elegidos de cada tecnología.
* **P90:** paramétrico, no empírico — `aep_p90 = max(0.0, aep_p50 × (1 − 1.28155 × incertidumbre_pct/100))` (`core/farm_mcda.py`), con `incertidumbre_pct` = raíz de suma de cuadrados de componentes (`resource`, `mcp`, `wake`, `power_curve`, `losses`). Es incertidumbre de medición/modelo alrededor de un año/promedio climatológico, no variabilidad interanual real.
* **PML guardado como `hourly_profile_2018`:** perfil horario fijo de 8,760 horas (`_store_fixed_pml_profile`, `api.py`), replicado igual todos los años del horizonte del proyecto — no hay hoy un PML por año distinto dentro de un mismo escenario.
* **Tres puntos de ingesta de `read_pml`** (`core/forecasting.py::read_pml`) en `api.py`: carga normal de mercado, un segundo endpoint de mercado, y un tercer punto adicional — los tres leen el mismo formato (`compra`/`venta`, con o sin `timestamps`).
* **Qué cachea cada paso hoy:** `optimize_layout_structured` cachea orientaciones/fases entre N (`grid_cache`); `generate_feasible_layouts_grid` cachea factibilidad por N (`n_feasible_cache`, evita revisitar la misma N); el campo espacial Kriging tiene caché explícito por sesión (`sess['_topsis_field_cache']`, con firma que excluye pesos TOPSIS y turbinas pero incluye precio/tasa/horizonte). **No cacheado:** el pool completo de candidatos de `generate_feasible_layouts_grid` no se persiste aparte del campeón final — cambiar solo el escenario de precio y volver a llamar `generate_park_layouts` repite toda la búsqueda de estela aunque la geometría no cambiaría.

### Inconsistencias encontradas (sin corregir)

* **Conteo de criterios:** el docstring de `core/multicriteria.py` y el objetivo de `docs/ahp-topsis.md` dicen "ocho criterios", pero `LAYOUT_WEIGHTS` tiene **7** entradas desde el retiro de `discounted_payback_years` (2026-09-17). Ningún archivo actualizó ese número.
* **Dos tablas de pesos paralelas:** `DEFAULT_WEIGHTS` (para `topsis()`/ranking de sitios-turbinas) y `LAYOUT_WEIGHTS` (para `rank_layout_alternatives()`, ranking de parques) usan nombres de criterio parcialmente distintos para conceptos relacionados (`cp_max` vs `hours_near_cpmax`, `aep_mwh` vs `aep_p90_mwh`) — fácil confundir cuál aplica a qué endpoint.
* **`iec_suitability_score` fijo en 0.5** para toda alternativa (`core/farm_mcda.py`) — nunca discrimina entre layouts, documentado como dato faltante del fabricante (`bankable_missing`) pero sigue expuesto en `LAYOUT_AUDIT_ONLY` como si fuera un valor calculado.
* **Ingreso real cruzado con precio solo en el año 1:** `chronological_revenue`/`revenue_year1_override` no cubre el resto del horizonte del proyecto — es una limitación de datos (no hay PML horario de 25 años), no de diseño, pero no está declarada explícitamente donde se cita "ingreso real".

### Propuestas sin confirmar

> Las siguientes son propuestas para discutir, **no decisiones tomadas**:

* Escenario de mercado como un año de PML común a todos los layouts de una corrida (formalizar lo que `price_hourly_usd_kwh` ya hace implícitamente, pero como parámetro explícito de la corrida en vez de "lo último cargado en la sesión").
* Precio capturado emparejado por año, 2017–2023.
* Energía de largo plazo usando los 44 años de la serie reconstruida.
* P50, P75 y P90 separando dos fuentes de riesgo distintas: variabilidad interanual real (entre los 44 años) e incertidumbre de modelo/medición (la que ya calcula el P90 paramétrico actual) — hoy están fusionadas en un solo número.
* Año 0 = 2024, operación 2025–2049.

### Decisiones abiertas del usuario

* Qué horizonte de P90 debe alimentar a TOPSIS: el paramétrico actual, el empírico interanual, o ambos reportados por separado.
* Definición final de los escenarios de mercado (bajo/base/alto): confirmar si siguen siendo min/mediana/max de precios capturados anuales o cambia el criterio.
* Cobertura mínima de un año completo y calendario: qué cuenta como "año completo" para entrar al cálculo (p. ej. umbral de completitud horaria) y cómo se alinea el calendario de 8,760 horas fijas con años bisiestos.

### Preguntas de lectura pendientes

* ¿El conteo "ocho criterios" en `core/multicriteria.py` y `docs/ahp-topsis.md` se corrige para decir siete, o se vuelve a promover un octavo criterio (p. ej. `capex_usd` bruto, IEC, u otro de `LAYOUT_AUDIT_ONLY`) antes de corregir el número?
* ¿Las dos tablas de pesos paralelas (`DEFAULT_WEIGHTS` vs `LAYOUT_WEIGHTS`) deben unificarse, o se documenta explícitamente que son independientes por diseño (sitios-turbinas vs parques)?
* ¿`iec_suitability_score` se retira de `LAYOUT_AUDIT_ONLY` mientras no haya datos reales del fabricante, o se deja como placeholder declarado?
* ¿El caché del pool de `generate_feasible_layouts_grid` (hoy inexistente entre corridas con distinto precio) vale la pena implementarlo antes de correr varios escenarios de mercado, dado el costo de repetir la búsqueda de estela solo para recalcular economía?

---

## Decisiones del usuario:

Nodo base para pruebas: 02JUD115 (mercado_pml/PML_2017-2024 02JUD115.txt). El PML oficial de IPO (PML_2017-2024_02IPO115.txt) queda para sensibilidad. PML_usd.txt es IPO 2022 escalado con dólar fijo y ya no se usa.
Zona horaria: CERTE está en UTC−6 fijo y el MCP lo convierte a UTC. El PML se convierte a UTC con America/Mexico_City (horario de verano hasta 2022).
CERTE existe solo de nov-2017 a ago-2020. 2018 es el único año completo y es el año de entrenamiento del MCP activo. Todo lo posterior a sep-2020 es reconstrucción del Random Forest.
El viento reconstruido de PARQEU1 termina el 31-ago-2024, así que 2024 queda fuera de los escenarios (solo parcial).
Periodo de estudio: 2017–2023 completos.
Escenarios bajo/base/alto: min, mediana y max de los precios capturados anuales (por confirmar).
Sin decidir: tipo de cambio histórico vs fijo, y qué hacer después de 2024.
Validación de captura (2019 y 2020): completada, ver sección 9 (r, razón de captura, AEP y bootstrap para ambos años, corrección de sensor aplicada en 2020).
Siguiente paso: cerrar la especificación del caso de estudio (lectura de código y decisiones) antes de implementar.
