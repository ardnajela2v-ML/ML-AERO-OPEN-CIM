# Caso de estudio PARQEU1: especificación (cerrada)

> Estado: especificación cerrada, sin cambios de código aún. Las referencias a archivos y líneas provienen de las revisiones contra el código hechas en Claude Code; verificar al implementar.

## 1. Parámetros financieros

Los parámetros son **editables** y se guardan en la configuración financiera del proyecto (`park_configuration.financial_config`, mismo objeto que `base_fx_mxn_usd`), junto con el PML. Los valores de abajo son los **defaults del caso PARQEU1**, no constantes del código.

| Parámetro | Default PARQEU1 | Nota |
|---|---|---|
| `discount_rate` | 9.5 % | Su base la define `discount_rate_basis` |
| `discount_rate_basis` | `nominal` | `nominal` o `real`. Si falta en un proyecto viejo: se asume `nominal` y se avisa |
| `annual_inflation_pct` | 3.65 % | Escala el OPEX (`core/project_economics.py:52`) |
| `price_escalation_pct` | = inflación | Campo ya existente (`api.py:3489`; en `pml.projection_policy`, `api.py:1948`, hoy fijo en 0.0). Construye `price_series` |
| Tasa real | derivada | (1 + nominal) / (1 + inflación) − 1 → 5.64 % con los defaults. Si la base es `real`, la nominal se deriva al revés |
| Año 0 | 2024 | CAPEX en año 0, USD de 2024 |
| Operación | 2025–2049 | 25 años, sin periodo de construcción |
| Degradación | la ya implementada | Años 2–25 |

### Convención de escalamiento

Patrón existente (`api.py:3504-3520` → `TechnoEconomicAnalyzer.analyze`, `core/energy.py:869-878` → `project_cashflows`):

- `price_series[0]` = precio capturado del escenario, **sin escalar** (año 1, 2025).
- `price_series[t]` = precio × (1 + escalamiento/100)^t para t ≥ 1.
- El OPEX sigue la misma convención: año 1 sin escalar.
- `annual_inflation_pct` y `price_series` se construyen **juntos** desde el mismo `fin_cfg` y se actualizan juntos, nunca uno sin el otro.

### Dos corridas, misma configuración

| | Nominal | Real |
|---|---|---|
| Inflación (OPEX) | `annual_inflation_pct` | 0 |
| Escalamiento de precio | `price_escalation_pct` | (1 + escalamiento) / (1 + inflación) − 1 |
| Tasa de descuento | nominal | real derivada |

Con los defaults (escalamiento = inflación), el escalamiento real es 0 y el precio queda plano. La verificación cruzada **van_usd_nominal = van_usd_real** vale con cualquier combinación de escalamiento e inflación, siempre que ambas corridas lean el mismo `fin_cfg`.

### Reporte

- VAN, TIR y LCOE se reportan en ambas bases, rotulados (`lcoe_nominal`, `lcoe_real`). **No son iguales**: están en dólares distintos. Lo que debe coincidir es el VAN.
- Alcance: VAN de proyecto, **sin apalancar y antes de impuestos**, sin depreciación (`project_cashflows` no los modela).

### Validaciones

- Aviso si `annual_inflation_pct` = 0 con `discount_rate_basis` = `nominal`.
- Aviso si la tasa real derivada ≤ 0.
- Aviso si falta `discount_rate_basis` (se asume `nominal`).

### Gap de código

`core/farm_mcda.py::_economics()` (línea 150), usada por layouts y TOPSIS, no pasa `annual_inflation_pct` ni `price_series` a `project_cashflows`: quedan en 0.0 y `None` sin importar `fin_cfg`. El patrón a replicar ya existe en `api.py:3504-3536`.

## 2. Energía

- Fuente: serie corregida de 44 años (1980–2023, años completos). El viento reconstruido termina el 31-ago-2024; 2024 queda fuera por parcial.
- Entrenamiento del modelo solo con 2018, único año completo de observación CERTE (existe nov-2017 a ago-2020). Lo posterior a sep-2020 es reconstrucción del Random Forest.
- `aep_p50` = promedio climatológico anualizado (≈ 9,205 MWh en el layout de referencia). No es la mediana empírica (≈ 9,300 MWh); se declara.
- Cobertura mínima para "año completo": **90 %**, alineada a `core/energy.py:615` (`AEPCalculator.compute_from_timeseries`). Los 44 años tienen 100 %; solo 2024 (66.67 %) queda fuera. "Cobertura" mide completitud calendario de la serie reconstruida, no fracción de datos observados.
- Sensibilidad: P50 con los últimos 20 años frente a los 44.

## 3. Precio y escenarios

- PML nodo JUD, fechas en UTC: hora local → UTC respetando el horario de verano real (hasta 2022). Cada marca de tiempo = medianoche local del día (a UTC) + (hora − 1) horas; la hora 25 no se trata como hora 24 del día siguiente.
- Validar unicidad de (fecha, hora) y que cada día tenga 24 horas, salvo seis de 23 y seis de 25 en 2017–2022.
- Para cada layout y cada año 2017–2023: `chronological_revenue` con el viento de ese año y el PML de ese año (emparejado por fecha). Precio capturado = ingreso / energía de la misma pasada horaria.
- Se reportan por separado, por año: precio promedio del PML y razón de captura.
- Cada escenario usa la razón de captura de su propio año (rango observado 0.79–0.88, desviación 0.033).

### Escenarios

- **Base**: media de los siete años emparejados (2017–2023). Decisión confirmada; reemplaza la "mediana" de versiones anteriores.
- **Bajo / Alto**: mínimo y máximo observados. No son percentiles ni probabilidades.
- Tabla obligatoria con el valor de cada año (2022, crisis de precios, puede dominar un extremo).
- Los PML están en USD corrientes de cada año; el precio del escenario se toma como nivel de 2024 y escala desde ahí (limitación #10).

## 4. Ingresos y OPEX

- Año 1 (2025): `aep_p50` × precio capturado del escenario, sin escalar.
- Años 2–25: degradación de energía; precio y OPEX escalan según §1.
- Después de 2024 no se inventa perfil horario: se usa precio capturado constante en términos reales.

## 5. Layouts, TOPSIS y robustez

- Layouts y TOPSIS con el escenario base; TOPSIS repetido por escenario, reportando si el ganador cambia.
- TOPSIS mantiene el P90 paramétrico y los pesos AHP sin tocar (juicios de un solo investigador; se declara).
- Pool de candidatos en sesión; por escenario solo se recalcula la economía. Datos por candidato: `layout_xy_m`, `ratio_by_sector` (36 × n_turbinas), `aep_p50`, `collection_length_m`, `n_turbinas`, referencia a turbina. El tamaño (~1.2 MB) no está medido: medir con candidatos reales al implementar.
- **Matriz de robustez** para las finalistas: energía (P50, P75, P90 empíricos entre años) × precio (bajo, base, alto), con VAN, TIR y LCOE, vía `AEPCalculator.compute_from_timeseries`.
- Horizontes del P90: **1 año** y **25 años**.
  - Variabilidad entre años: 6.4 % a 1 año; ≈ 1.3 % a 25 años (sin autocorrelación).
  - Incertidumbre de modelo: 8.3 % = √(6² + 4² + 3² + 2² + 2²) (`core/farm_mcda.py:578-586`).
  - Combinación por raíz de suma de cuadrados: ≈ 10.5 % a 1 año y ≈ 8.4 % a 25 años. Este paso no existe en el código; se agrega junto a `aep_p90`.
  - **No es doble conteo**: el componente `resource` (6.0 %) es incertidumbre de medición/modelo alrededor de un año o promedio climatológico; la variabilidad entre años es dispersión real del clima. Son fuentes distintas aunque sus magnitudes se parezcan.
- **SMAA** (`/api/multicriteria/weight_robustness`, `api.py:5474-5522`): perturba pesos sobre una tabla de métricas ya calculada. Se corre **3 veces**, una por escenario de precio, no por celda de la matriz.

## 6. Contrato en la app

### Campo nuevo `pml_series`

Dentro de `park_configuration.market_models.pml`, junto a `hourly_profile_2018`:

```
pml_series: {
  schema_version: 1,
  start_utc: "2017-01-01T06:00:00Z",
  n_hours: 70128,
  values: [...],               // USD/kWh, longitud = n_hours
  years_selected: [2017, ..., 2023],
  years_complete: [2017, ..., 2023]
}
```

- `years_complete` se calcula con la cobertura del **viento reconstruido**, no la del PML (que llega a diciembre de 2024). Umbral 90 %.
- No existe hoy ningún campo de versión en el `.aeroproj`; `schema_version` es nuevo.

### `hourly_profile_2018`

Se mantiene como corte heredado de exactamente 8,760 valores, sin fecha, derivado de 2018 (año no bisiesto).

### Estado actual (defecto existente)

`_store_fixed_pml_profile` (`api.py:1908-1949`) guarda en `hourly_profile_2018` lo que traiga el perfil, y `normalize_fixed_hourly_profile` (`core/forecasting.py:321-352`) conserva las series multi-año con fechas. Cargar `mercado_pml/PML_2017-2024 02JUD115.txt` (70,128 h) produce hoy un `hourly_profile_2018` de 70,128 valores, con `hourly_timestamps` poblado.

Consecuencia: la compuerta de `/api/multicriteria/spatial` (`api.py:5666`, `len(pml_hourly) == 8760`) falla en silencio: `price_hourly_usd_kwh` queda `None` y TOPSIS usa precio escalar sin avisar.

### Compuerta de `/api/multicriteria/spatial`

- `hourly_profile_2018` de exactamente 8,760: pasa como hoy.
- Cualquier otra longitud: **400** vía `_err()` (`api.py:206`), con mensaje claro.
- `pml_series`: **400** ("PML multi-año aún no soportado en esta ruta") hasta que existan los pasos 2 y 3 de §7. No se acepta ni se descarta.
- Sin perfil: comportamiento actual (precio escalar), sin error.
- El mismo guard aplica a cualquier punto que lea `hourly_profile_2018` esperando 8,760.

### Migración de proyectos guardados

- `hourly_profile_2018` con longitud ≠ 8,760 **y** `hourly_timestamps`: se migra a `pml_series` en memoria, sin reescribir el archivo hasta que el usuario guarde. El corte de 8,760 h sale de 2018.
- Longitud ≠ 8,760 sin timestamps: error visible.
- Proyecto sin `pml_series` ni los campos financieros nuevos: abre como hoy, con los defaults de §1.

### Carga multi-año

`/api/pml/load` y la descarga CENACE guardan en `pml_series` y derivan el corte de 8,760 h, para que no se pisen entre sí.

## 7. Orden de implementación

1. Guard de 8,760 y rechazo de `pml_series` en `/api/multicriteria/spatial` (`api.py:5666`) y en todo punto que lea `hourly_profile_2018`, con pruebas (70,128 → 400; 8,760 → pasa; sin perfil → precio escalar).
2. Timestamp real en `prepare_historical_wind` (`core/historical_resource.py:51-54`): hoy solo pasa `hour_of_year` y pierde el año calendario. Bloquea los pasos 3 y 4.
3. `chronological_revenue` con fechas reales (`core/farm.py:691-803`): energía e ingreso por año calendario, y `ratio_by_sector` opcional como entrada (hoy se recalcula siempre, líneas 766-771). Hoy solo implementa `hour_of_year`; el método con fechas existe únicamente en `tools/capture_dated_vs_hourofyear.py`, sin estela.
4. Economía: inflación y `price_series` en `farm_mcda._economics()`, y las dos corridas (nominal y real).
5. Pool en sesión, matriz de robustez, escenarios y las 3 corridas de SMAA.
6. Clustering jerárquico de campeones: después de construir `alternatives` (1 campeón por
   tecnología) y antes de `rank_layout_alternatives`, calcular similitud entre tecnologías con
   linkage Ward sobre 6 variables Z-normalizadas (`aep_p50_mwh`, `aep_p90_mwh`, `van_usd`,
   `lcoe_usd_kwh`, `factor_capacidad`, `capex_usd_per_installed_kw`). Resultado en
   `tech_clustering` (dendrograma + asignaciones k=2/k=3). Informacional puro — `alternatives`
   y `rank_layout_alternatives` sin cambios.

Antes de cada paso: commit, y `python -m pytest -q` completo antes y después.

## 8. Resultados ya medidos

- Energía: `simulate_wind_farm` vs `chronological_revenue`, diferencia −0.13 % a +0.10 % en los 8 campeones, sin signo consistente.
- Tendencia (Mann-Kendall, 44 años): p = 0.11, pendiente −0.008 m/s/año (≈ −0.36 m/s en el periodo). Poca potencia estadística.
- Ventana 2017–2023: 8,467 MWh frente a 9,205 MWh de los 44 años (−8 %). Por eso la energía sale de la serie larga y solo el precio del emparejamiento.
- Captura: razón media 0.846 con fechas reales frente a 0.938 con `hour_of_year` y perfil de 2018 (≈ +11 % de ingreso inflado). Correlación viento-precio negativa cada año (−0.3 a −0.57 en promedios diarios). Medido en turbina única sin estela; verificar con los 8 campeones.
- Entre años (1 año): P50 = 9,300, P75 = 8,788, P90 = 8,290 MWh; desviación 6.4 %.
- Los VAN guardados en PARQEU1 están sobreestimados por dos causas independientes: PML mal etiquetado y emparejamiento por `hour_of_year`.

## 9. Limitaciones a declarar

1. Entrenamiento con un solo año (2018).
2. Viento sintético en parte de 2017–2023.
3. Sesgo de AEP de 2–6 %.
4. ERA5 sin corregir.
5. 2024 parcial (no define escenarios).
6. Escenarios = años observados, no probabilidades.
7. P90 de TOPSIS sin variabilidad entre años.
8. Densidad constante en la simulación.
9. Sin periodo de construcción.
10. PML 2017–2023 en USD corrientes de cada año, no desinflados a 2024.
11. Pesos AHP de un solo investigador.
12. VAN anteriores de PARQEU1 sobreestimados (PML mal etiquetado y emparejamiento por `hour_of_year`).
13. VAN de proyecto sin apalancar, antes de impuestos y sin depreciación; capa apalancada fuera del caso.
14. Tasa de interés de la capa apalancada fija nominal (`interest_rate_pct`, default 8 %), sin equivalente real consistente.
15. Layout de referencia de una sola turbina, sin estela, en las mediciones de captura.
16. Sin tendencia significativa en 44 años, pero con poca potencia y posibles cambios artificiales del reanálisis.
