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

* **Fase 2 no iniciada:** No se ha calculado el precio capturado horario ni se ha realizado el cruce con las series de viento observado o predicho.
* **Economía intacta:** No se modificó `core/project_economics.py` ni `core/farm.py`.
* **API sin modificar:** No se alteraron endpoints de `api.py`.
* **Caso de estudio no modificado:** No se ha alterado la configuración guardada dentro de `PARQEU1.aeroproj`.

---

## 7. Aspectos sin verificar

* **[sin verificar]:** La convención exacta de etiquetado del remuestreo de WindMCP de 10 min a 1 h: ¿cada hora rotula el inicio $[h, h+1)$ o el final $(h−1, h]$? Esto queda pendiente y es lo primero que se debe auditar en `windmcp/data.py::resample_hourly` al iniciar la Fase 2 antes de alinear con el PML.

---

## Decisiones del usuario:

Nodo base para pruebas: 02JUD115 (mercado_pml/PML_2017-2024 02JUD115.txt). El PML oficial de IPO (PML_2017-2024_02IPO115.txt) queda para sensibilidad. PML_usd.txt es IPO 2022 escalado con dólar fijo y ya no se usa.
Zona horaria: CERTE está en UTC−6 fijo y el MCP lo convierte a UTC. El PML se convierte a UTC con America/Mexico_City (horario de verano hasta 2022).
CERTE existe solo de nov-2017 a ago-2020. 2018 es el único año completo y es el año de entrenamiento del MCP activo. Todo lo posterior a sep-2020 es reconstrucción del Random Forest.
El viento reconstruido de PARQEU1 termina el 31-ago-2024, así que 2024 queda fuera de los escenarios (solo parcial).
Periodo de estudio: 2017–2023 completos.
Escenarios bajo/base/alto: min, mediana y max de los precios capturados anuales (por confirmar).
Sin decidir: tipo de cambio histórico vs fijo, y qué hacer después de 2024.
Siguiente paso, Fase 2: validar el precio capturado con viento observado vs predicho en 2019 y 2020. El modelo (entrenado con 2018) debe predecir esas horas desde el reanálisis, sin usar historical_reconstruction.csv, porque conserva las observaciones. Usar solo horas comunes con el PML en UTC; revisar si el remuestreo del MCP rotula cada hora con el inicio [h, h+1) o el final (h−1, h]; reportar precio capturado, razón de captura, correlación y sesgo por hora del día, con bootstrap por bloques de días. 2018 solo como referencia dentro de muestra.
