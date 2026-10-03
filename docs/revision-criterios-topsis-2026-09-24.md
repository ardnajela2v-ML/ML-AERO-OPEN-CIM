# Revisión de criterios TOPSIS — 24 de septiembre de 2026

Revisión del expediente `ahp-topsis.md` contra el código del árbol de trabajo actual. No sustituye las decisiones anteriores ni modifica pesos o cálculos. El árbol contiene cambios locales en curso; los hallazgos describen lo leído en esta fecha, no necesariamente una versión publicada. Las recomendaciones son propuestas del análisis, no decisiones ya aprobadas por el investigador.

## 1. Qué decisión toma actualmente el programa

`core/farm_mcda.py` genera candidatos por tecnología y selecciona un representante mediante `select_technology_champion(..., require_feasibility_keys=('wake_loss_ok',))`, maximizando VAN. Después `rank_layout_alternatives` conserva una alternativa por modelo de turbina y aplica TOPSIS.

Por tanto, el resultado es una comparación multicriterio de representantes de tecnologías preseleccionados por VAN. No es una clasificación de todos los layouts factibles. Esta selección previa puede eliminar un layout de menor VAN que habría obtenido mejor TOPSIS por otros atributos. El peso de VAN en TOPSIS no expresa toda su influencia en el proceso.

El código devuelve `feasible=True` para esos representantes y reporta factibilidad ambiental por separado. No debe describirse este flujo como una garantía de cumplimiento conjunto de ruido, sombras, pendiente y estelas. El comportamiento está comentado como deliberado; aquí se identifica la diferencia con el contexto metodológico, sin cambiarlo.

## 2. Los siete criterios conservados

| Criterio | Cálculo observado | Aporte y límite | Dictamen provisional |
|---|---|---|---|
| Horas cerca de Cp máximo | 8760 × fracción de registros válidos con Cp(v) ≥ 0.95 Cp máximo de la propia turbina | Mide frecuencia de proximidad al máximo propio, no magnitud absoluta de eficiencia ni energía. Una turbina con un máximo menor puede superar a otra en este indicador. No incorpora velocidades individuales afectadas por estelas. | Conservar como candidato de adaptación turbina–recurso; su inclusión ponderada requiere explicitar por qué importa esa proximidad y probar sensibilidad al umbral. |
| Cp efectivo de la serie | Media aritmética de Cp(v) sobre registros válidos, incluyendo ceros | Da igual peso temporal a registros de bajo y alto contenido energético. No es el cociente entre energía eléctrica total y energía cinética total. Tampoco incorpora el layout. | Conservar como diagnóstico y candidato; denominarlo con precisión «Cp medio temporal derivado de la curva de potencia». Justificar su utilidad adicional antes de ratificar peso. |
| Factor de capacidad neto | AEP denominada P50 / (MW instalados × 8760) | Producción relativa a capacidad instalada; distingue utilización de tamaño. Si la capacidad es igual para todas las alternativas, es proporcional a AEP. | Mantener como candidato principal cuando varíe capacidad instalada; revisar redundancia por conjunto de alternativas. |
| AEP P90 absoluto | max(0, AEP denominada P50 × (1 − 1.28155 σ)); σ es raíz de suma de cuadrados de porcentajes comunes | Mide producción absoluta ajustada por incertidumbre supuesta. Con σ común y factor positivo, la columna normalizada es idéntica a la de P50. No diferencia riesgo entre alternativas. | Mantener una sola columna de energía absoluta. Reportar P90 con sus supuestos; no atribuirle diferenciación de riesgo que el modelo no calcula. |
| VAN absoluto | Suma descontada de flujos, con CAPEX inicial y costos e ingresos del ciclo de vida | Valor total; responde al objetivo de aprovechar un terreno. Depende del escenario comercial y ya interviene en la preselección. | Mantener como candidato principal; hacer explícita su intervención en ambas etapas. |
| CAPEX/kW | (CAPEX base + cableado + cimentación) / kW instalados | Intensidad de inversión. No equivale a inversión total, restricción de presupuesto ni retorno. Su costo está incorporado también en VAN/LCOE. | Mantener si se desea valorar explícitamente intensidad de inversión; documentar ese objetivo y la incertidumbre de costos. |
| LCOE | Valor presente de costos / valor presente de energía | Costo por energía; no incluye el precio de venta en esta implementación. Puede complementar VAN, que sí depende de ingresos. | Mantener como candidato principal; no justificar automáticamente la exclusión de otras métricas por una equivalencia histórica. |

Detalles de Cp: `compute_site_cp_fit` usa densidad de referencia 1.225 kg/m³, interpola potencia, aplica corte y recorta Cp a [0,16/27]. El recorte puede ocultar inconsistencias de curva/diámetro: debe auditarse cuántos valores serían recortados. Al derivarse de una curva de potencia eléctrica, no debe identificarse automáticamente con eficiencia aerodinámica pura del rotor. La anualización representa la distribución de registros válidos; no corrige sesgos estacionales de datos faltantes ni pondera intervalos irregulares.

Con paso uniforme, el Cp medio temporal es media[P(v)/(0.5ρAv³)]. Un cociente de energías sería suma[P(v)Δt]/suma[0.5ρAv³Δt]. Son definiciones distintas; cambiar de una a otra exige una decisión metodológica, no solo renombrar una variable.

El cálculo P90 supone una aproximación normal y combinación cuadrática sin covarianzas. Debe justificarse qué representan sus componentes, la base llamada P50 y el horizonte de incertidumbre. La documentación oficial SAM distingue análisis multianual P50/P90 del tratamiento específico de incertidumbres eólicas: https://samrepo.nlr.gov/help/p50p90.html y https://sam.nrel.gov/images/web_page_files/sam-help-2025-4-16.pdf.

## 3. Reevaluación de los descartes

| Criterios | Estado propuesto de la justificación |
|---|---|
| B/C, ROI, TIR y recuperaciones | Reabrir la afirmación de redundancia universal. El ingreso horario específico puede romper la equivalencia con LCOE. Esto no implica reincorporarlos todos; exige evaluar aporte y redundancia con el modelo actual. Recuperaciones pueden no existir dentro del horizonte: no tratarlas como cero. |
| DSCR | Conservar como candidato a diagnóstico/restricción de financiamiento cuando exista modelo de deuda integrado; no afirmar equivalencia general fuera de sus supuestos. |
| AEP/MW y VAN/MW | Su exclusión es una elección entre resultados absolutos e intensivos, no una invalidez general. AEP/MW es proporcional al factor de capacidad si comparte base energética; VAN/MW no es constante en general cuando cambian costos, tecnología o estelas. |
| AEP/área, VAN/área, densidad energética | Mantener exclusión como columnas adicionales cuando el denominador sea común. Comparar sitios de áreas distintas cambia el argumento. |
| CAPEX absoluto | Puede actuar como presupuesto máximo o indicador de inversión total. Preferir menos inversión no es por sí mismo un error de TOPSIS: el intercambio costo–beneficio es una decisión legítima. CAPEX/kW representa otro objetivo. |
| OPEX/kW | Mantener fuera si es exactamente un porcentaje común de CAPEX/kW. |
| OPEX acumulado | Con porcentaje y horizonte fijos es proporcional a CAPEX absoluto, no necesariamente a CAPEX/kW cuando cambia capacidad instalada. Corregir la confusión de denominadores del expediente. |
| Estelas | Mantener como restricción y diagnóstico si ese es el objetivo, pero no etiquetar como constante por compartir un límite. Su efecto energético se incorpora a AEP neta. |
| Incertidumbre y recorte | Excluir como columnas si realmente son constantes. Si pasan a estimarse por alternativa, reevaluar; hoy σ común no aporta diferenciación de riesgo. |
| Aptitud IEC | Dato/evaluación pendiente, no criterio válido con puntaje fijo 0.5. Separar «sin evaluar» de «cumple». |
| VAN apalancado | Mantener pendiente de definición e integración de deuda; no equipararlo al VAN sin deuda. |
| Cp a velocidad media y desajuste de velocidades | Mantener como diagnósticos provisionales; su redundancia no queda demostrada por la prueba de los otros cuatro criterios técnicos. |
| Disponibilidad operativa del recurso | Diagnóstico de fracción de registros con potencia positiva; no confundir con disponibilidad mecánica de la máquina. Reevaluar aporte con datos actuales. |
| Horas ≥90% de potencia nominal | Aplicar el mismo examen que a horas ≥95% Cp máximo: definición, pertinencia y sensibilidad al umbral. Falta de umbral universal no invalida por sí sola una métrica definida para el estudio. |
| Cableado y complejidad civil | Efecto económico parcialmente incorporado en CAPEX; conservar como diagnósticos, salvo que se defina una dimensión técnica distinta que se quiera valorar. |

## 4. Comprobaciones de esta revisión

Se ejecutó `project_cashflows` con valores sintéticos ilustrativos, no datos del estudio: CAPEX 5 millones USD, OPEX 100 mil USD/año, energía 10 millones kWh/año, precio 0.06 USD/kWh, descuento 8%, 25 años y los valores por defecto de degradación/desmantelamiento.

| Caso | LCOE USD/kWh | B/C | VAN USD |
|---|---:|---:|---:|
| Ingreso por energía × precio | 0.058460 | 1.026335 | 160365.07 |
| Ingreso del año 1 sustituido por 800 mil USD | 0.058460 | 1.056746 | 345550.25 |

Contraejemplo: mismo LCOE y distinto B/C. Basta para refutar una equivalencia universal en el código actual; no mide la correlación empírica entre los parques del estudio.

Además, los comentarios de `_economics` afirman que el ingreso sustituido sirve como base para los años siguientes, pero `project_cashflows` solo lo aplica cuando `year == 1`. En ambos casos el ingreso del año 2 fue 598200 USD. La proyección horaria de ingresos requiere resolver esa discrepancia antes de fundamentar con ella resultados a 25 años.

Se verificó numéricamente la invariancia de normalización vectorial entre AEP=[100,200,300] y P90=k×AEP, con componentes de incertidumbre [6,4,3,2,2]%. k=0.8935464619 y diferencia máxima de columnas normalizadas 1.11×10⁻¹⁶. El resultado supone k positivo y común; si P90 se recorta a cero para todas las alternativas, pasa a ser constante.

Las correlaciones del expediente anterior no se recalcularon aquí. Su prueba usa un sitio, 13 turbinas, conteo aproximado por área, P90 proxy y sin estelas; no sustituye un análisis de las alternativas actuales. Spearman perfecto evidencia equivalencia de orden en una muestra; una transformación no lineal puede modificar distancias en TOPSIS aunque preserve dicho orden.

## 5. Decisiones y verificaciones pendientes

1. Declarar si la unidad de decisión será cada layout factible o cada tecnología representada por su layout de mayor VAN. Documentar separadamente el alcance ambiental evaluado.
2. Exportar la matriz real de alternativas, con fuentes del recurso y precio, escenarios, unidades, supuestos de costos y factibilidad. Separar corridas por sitio/escenario para evitar correlaciones inducidas al mezclarlas.
3. Examinar constantes, relaciones algebraicas y correlaciones; añadir comparaciones de ranking al retirar criterios, manteniendo explícita la renormalización de pesos.
4. Para Cp, revisar recortes, cobertura temporal y sensibilidad de horas cerca del máximo a 90%, 95% y 98%; contrastar escenarios con y sin cada indicador de Cp sin cambiar por ello el cálculo físico de energía.
5. Revalidar la familia económica con ingreso plano y horario, resolviendo primero el tratamiento de ingresos después del año 1.
6. Ratificar criterios por su objetivo decisorio y evidencia, y después revisar las intensidades AHP. El orden ordinal registrado no determina por sí solo razones de importancia; la consistencia de la matriz no valida el supuesto lineal usado para generarlas.

La propuesta inicial mantiene VAN, una medida de energía absoluta y LCOE como candidatos centrales; FC y CAPEX/kW responden a objetivos intensivos explícitos; los dos indicadores de Cp quedan sujetos a justificación y sensibilidad. No se asignan nuevos pesos ni se presenta este conjunto como definitivo.

Fuentes de implementación: `core/energy.py:compute_site_cp_fit`; `core/farm_mcda.py:_economics` y `analyze_park_layouts`; `core/project_economics.py:project_cashflows`; `core/multicriteria.py:rank_layout_alternatives`; `core/layout_generation.py:select_technology_champion`; `scripts/verify_technical_criteria_correlation.py`. Definición de LCOE contrastada con documentación oficial SAM: https://samrepo.nlr.gov/help/mtf_lcoe.html.
