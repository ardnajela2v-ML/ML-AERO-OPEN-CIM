# AHP–TOPSIS con fundamento bibliográfico y análisis de robustez

Se excluye Delphi por decisión del investigador. No se afirma consenso de un
panel. El método adoptado es: evidencia científica → juicios AHP individuales
→ TOPSIS → robustez. Desde 2026-09-14, `LAYOUT_WEIGHTS` contiene un primer
juicio AHP real (28 comparaciones, un solo investigador, ver §6 "Tercera
actividad") en vez del placeholder 60/40 anterior; sigue siendo la
construcción de un único evaluador, no un panel, y sigue pendiente de
validación por el investigador antes de citarse en la tesis como definitivo.

## Objetivo y alcance

La versión actual opera sobre **ocho** criterios del ranking de parques —
el mismo número que antes del 2026-09-13, pero no el mismo conjunto: ese día
se retiró `benefit_cost_ratio` y se cambió el rol de otros tres criterios
(ver §5 y §6 para el detalle completo). El autor debe declarar
el objetivo de la decisión y fundamentar sus comparaciones. Es posible identificar como autor al investigador; registrar
un nombre no acredita experiencia ni valida científicamente los criterios.
La revisión bibliográfica que sigue permanece abierta. AHP no resuelve por
sí solo las preguntas de pertinencia y redundancia.

## 1. Expediente científico de cada criterio

Cada ficha debe registrar definición, ecuación, unidad, sentido de preferencia,
fuente de datos, incertidumbre, referencia bibliográfica verificable, pasaje
que sustenta su uso, límites de transferencia a México y relación con otros
indicadores. Distinguir la existencia física de una variable de la justificación
para darle prioridad decisoria. No reconstruir una matriz AHP desde los pesos
actuales para presentarla como si procediera de expertos.

**Distinción importante que esta revisión sostiene en todo momento:** la
literatura puede fundamentar que un criterio es *pertinente* (existe, se mide,
se usa en estudios comparables) y puede documentar *cómo otros estudios lo
ponderaron*. No puede fundamentar que un **puntaje numérico específico**
(p. ej. 22.11% en este expediente) sea el correcto para este estudio — eso
sigue siendo un juicio del investigador, y así debe describirse en la tesis.
Donde la revisión de literatura muestre que otros estudios reportan pesos muy
distintos entre sí para tipos de criterio comparables, eso es evidencia de que
**no existe un peso "verdadero"** — es un argumento a favor de ejecutar
robustez (SMAA), no en contra de haber usado AHP.

Matriz inicial del ranking de parques: los criterios se obtuvieron de
`LAYOUT_WEIGHTS` en `core/multicriteria.py`. El conjunto cambió el
2026-09-13 (ver §6); la tabla que sigue documenta los ocho originales de
antes de ese cambio, por trazabilidad histórica — no son los mismos ocho
criterios que hay hoy, aunque coincida el número.
Los porcentajes de referencia sirven para auditar el estado del programa; no
constituyen un resultado AHP.
Esta revisión (2026-09) usó búsquedas en Google Scholar/motores generales vía
herramientas de este entorno; no es una revisión sistemática con protocolo
PRISMA. La columna "Verificación" indica si se leyó el texto completo del
artículo (alta confianza) o solo metadatos/resúmenes de múltiples buscadores
independientes (confianza media) — antes de citarlas en la tesis, confirmar
cada una abriendo el DOI.

| Criterio actual | Peso de referencia | Fundamento localizado en literatura | Verificación | Qué sigue sin resolver |
|---|---:|---|---|---|
| Cp efectivo de la serie | 22.1%¹ | El coeficiente de potencia (Cp) y el límite de Betz (16/27 ≈ 59.3%) son la definición estándar de eficiencia aerodinámica de un aerogenerador. [Manwell, McGowan & Rogers, *Wind Energy Explained*, 2ª ed., Wiley, 2009/2010, cap. 3](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119994367). | Metadatos (texto estándar, no releído completo aquí) | Que Cp promediado sobre toda la serie temporal sea una dimensión *independiente* de AEP y factor de capacidad —matemáticamente están correlacionados— no está resuelto; requiere análisis de correlación con los datos propios del estudio. |
| Horas cerca de Cp máximo | 21.6%¹ | El tiempo que la turbina opera cerca de su Cp máximo (no solo su Cp promedio) afecta materialmente la AEP: Lanzafame & Messina muestran que operar continuamente en el punto de Cp máximo cambia la energía anual producida. Sedaghat et al. muestran que elegir la velocidad nominal correcta —que determina cuánto tiempo se opera cerca de Cp máximo— puede mejorar la AEP hasta 43%. [Lanzafame & Messina, *Renewable Energy* 35(1):301-306, 2010, DOI 10.1016/j.renene.2009.06.020](https://doi.org/10.1016/j.renene.2009.06.020); [Sedaghat et al., *Applied Energy* 205:781-789, 2017](https://doi.org/10.1016/j.apenergy.2017.08.081). | Texto completo (Sedaghat, por resumen extendido); metadatos (Lanzafame) | El umbral específico **≥95% de Cp máximo** usado en WindPro no aparece en ninguna de estas fuentes — es una convención del programa, no un valor de la literatura. Falta justificar ese percentil concreto o probar sensibilidad del ranking a moverlo (90%, 98%, etc.). |
| Factor de capacidad neto | 3.4%¹ | El factor de capacidad es un criterio de selección de turbina usado como único criterio decisorio en literatura reciente. [Paraschiv & Paraschiv, *Scientific Reports* 14, 2024, DOI 10.1038/s41598-024-61350-6](https://doi.org/10.1038/s41598-024-61350-6) (texto completo verificado: *"The capacity factor allows us to determine the most suitable turbine for installation at the analyzed location"*, CF = Eer/Ei). | Texto completo | Ese estudio usa CF como único criterio; no discute su peso relativo frente a los otros 7 criterios de este expediente — la pertinencia está fundamentada, la magnitud del peso (3.4%, la más baja del set) no. |
| AEP P90 (bruto) | 7.9%¹ | El AEP P90 es el estándar de la industria para dimensionar y comparar la energía entregada por una configuración, con incertidumbre incorporada al percentil de excedencia. Ver, p. ej., el uso de P90 en evaluación bancable de proyectos eólicos discutido en la documentación de SAM/NREL ya citada en la sección de metodología del proyecto. | Metadatos | **Cambio 2026-09-13**: este criterio dejó de normalizarse por área efectiva — se confirmó en código (`core/farm_mcda.py:24-56`) que el área es la misma constante para todas las alternativas de un ranking, por lo que dividir entre ella no cambiaba el resultado de TOPSIS (ver observación matemática más abajo). Ahora es el AEP P90 bruto, en MWh. Consecuencia directa: premia configuraciones con mayor energía absoluta — el propio investigador debe decidir si eso es deseable (p. ej., para cumplir un tamaño de contrato de compra de energía) o si prefiere que el factor de capacidad sea el único criterio de "energía normalizada por tamaño" del set. |
| Recuperación descontada | 19.8%¹ | Es una de las métricas "tradicionales" (TPEM) más usadas en evaluación financiera de proyectos de energía renovable, con fórmula formal DiscountedPBP = I / Σ(CFᵢ/(1+k)ⁱ). [Delapedra-Silva, Ferreira, Cunha & Kimura, *Processes* 10(2):184, 2022, DOI 10.3390/pr10020184](https://doi.org/10.3390/pr10020184) (texto completo verificado). La misma revisión cita textualmente su limitación: *"PBP disregards existing cash flows after the investment is recovered, and is a very limited tool for decision making"*. | Texto completo | Es el criterio con mayor peso individual después de los dos de Cp/horas; la revisión no ofrece un argumento para que el payback pese casi tanto como VAN, B/C y LCOE juntos (19.8% vs. 4.7%+15.0%+5.5%=25.2% combinados) — es coherente con la limitación citada arriba, que sugiere lo contrario. |
| VAN por MW instalado | 4.7%¹ | El VAN (NPV) es la métrica financiera más usada individualmente en evaluación de proyectos renovables, con fórmula formal NPV = Σ(CFᵢ/(1+k)ⁱ). [Delapedra-Silva et al., *Processes* 10(2):184, 2022](https://doi.org/10.3390/pr10020184) (texto completo verificado). Normalizar VAN por capacidad instalada (en vez de por área) es una forma estándar de comparar "creación de valor por unidad de capital/hardware comprometido" entre alternativas de distinto tamaño — el equivalente financiero de lo que el factor de capacidad hace con la energía. | Texto completo (NPV); razonamiento propio para la normalización | **Cambio 2026-09-13**: reemplazó a VAN/área efectiva por el mismo motivo matemático que el AEP (división entre una constante no afecta TOPSIS). `van_usd_per_installed_mw` ya estaba calculado en el código (`core/farm_mcda.py:152-154`) pero excluido del ranking; ahora sí varía por alternativa porque el MW instalado depende de cuántas turbinas y de qué modelo — este SÍ logra el objetivo original de "no premiar solo por ser una turbina más grande/costosa". |
| Beneficio/costo | 15.0%¹ | El B/C se usa junto con VAN, payback y TIR para comparar tecnologías/sitios de parques eólicos específicamente (no solo energía renovable en general). [Kamel, El Badawi & Alanzi, *Journal of Engineering Research* 12(4):840-858, 2024, DOI 10.1016/j.jer.2023.10.028](https://doi.org/10.1016/j.jer.2023.10.028); metodología general de B/C en evaluación de proyectos energéticos: [IRENA, *Smart Grids and Renewables: A Cost-Benefit Analysis Guide for Developing Countries*, 2015](https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2015/IRENA_PST_Smart_Grids_CBA_Guide_2015.pdf) (aplica a redes inteligentes, no a parques eólicos — se cita solo por la definición metodológica de B/C, no como respaldo de su uso en este contexto). | Metadatos (Kamel); texto completo pero fuera de dominio (IRENA) | Sigue sin resolverse la observación matemática ya documentada más abajo: si el precio de energía es constante entre alternativas, B/C es una transformación monótona de 1/LCOE y no aporta información independiente. Ningún artículo encontrado discute esta redundancia. |
| LCOE | 5.5%¹ | El LCOE es, junto con VAN/IRR/payback, uno de los cuatro grupos metodológicos dominantes en evaluación financiera de renovables, con fórmula formal LCOE = Σ(Capitalᵢ+O&Mᵢ+Fuelᵢ+Carbonᵢ+Dᵢ) / Σ(MWhᵢ·(1+k)⁻ⁱ). [Delapedra-Silva et al., *Processes* 10(2):184, 2022](https://doi.org/10.3390/pr10020184) (texto completo verificado). Esa misma revisión encuentra que *"there has been no consensus about the best one to use in practice [...] practitioners tend to use more than one evaluation technique as complements"* — respaldo directo para usar LCOE junto con VAN/B-C/payback en vez de uno solo. | Texto completo | Es el criterio económico "puro" con menor peso (5.5%) pese a ser, según la misma revisión, el método que más ha crecido en importancia relativa frente a NPV/IRR tradicionales en la última década — el peso actual va en sentido contrario a esa tendencia documentada, lo cual no es necesariamente un error, pero merece una nota explícita en la tesis. |

¹ Peso del expediente AHP exportado el 2026-09-13 (`windpro-ahp.json`, autor="J",
objetivo="J", justificación="K" — valores de una prueba de la calculadora, no
juicios reales documentados). Estos pesos **no** deben citarse en la tesis
como resultado de una elicitación real hasta que el expediente se vuelva a
calcular con autor, objetivo y justificación verdaderos que referencien esta
tabla. **Además**, ese expediente comparó "AEP/área efectiva" y "VAN/área
efectiva" contra los otros 6 criterios — esos dos criterios cambiaron de
definición el mismo día (ver filas de arriba). Las 13 comparaciones por pares
que involucraban a esos dos criterios deben rehacerse pensando en su nuevo
significado (AEP bruto en MWh; VAN por MW instalado), no reutilizarse tal
cual asumiendo que el número seguía midiendo lo mismo.

Los pesos de referencia (`LAYOUT_WEIGHTS`, columna que compara contra este AHP
de ejemplo) agrupan 60% técnico y 40% económico. Esa división también debe
someterse a juicio; no se fija de antemano como conclusión.

Observaciones matemáticas del proyecto: si todas las alternativas usan la
misma área positiva, dividir una columna por esa área no cambia su
normalización vectorial TOPSIS — **esto ya se verificó como cierto para el
área efectiva de este programa** (`effective_park_area` calcula un único
valor compartido por todas las alternativas de un ranking) y motivó el cambio
del 2026-09-13 descrito arriba: AEP y VAN dejaron de normalizarse por área y
pasaron a AEP bruto y VAN/MW instalado (este último sí varía por alternativa).
Asimismo, si todas comparten precio constante y se calcula PV(ingresos) =
precio × PV(energía), B/C = precio/LCOE. En ese escenario B/C y LCOE son
transformaciones monótonas del mismo cociente. **Verificado en código**
(`rank_layout_alternatives`, no `topsis` — la primera prueba con la función
equivocada arrojó puntajes TOPSIS idénticos por error, ver corrección abajo)
con 5 alternativas sintéticas: ponderar solo LCOE o solo B/C produce el mismo
orden y los mismos extremos, pero **no** el mismo puntaje normalizado
intermedio (p. ej., una alternativa intermedia obtuvo 0.977 bajo LCOE y 0.849
bajo B/C) — la normalización vectorial L2 no preserva distancias relativas
bajo una transformación no lineal (recíproca) como B/C=precio/LCOE, aunque sí
preserva el orden. Es decir, B/C y LCOE **no son numéricamente idénticos**
en TOPSIS, pero eso no los hace independientes: dado precio constante, B/C
tiene cero grados de libertad adicionales respecto a LCOE (se calcula
exactamente a partir de él, sin dato nuevo del proyecto), así que ponderar
ambos no suma dos juicios distintos — amplía uno solo de forma arbitraria y
dependiente de qué transformación no lineal se use. Es el mismo problema que
formaliza el método CRITIC para pesos objetivos en MCDM: [Diakoulaki, Mavrotas
& Papayannakis, "Determining objective weights in multiple criteria problems:
The CRITIC method", *Computers & Operations Research* 22(7):763-770,
1995](https://doi.org/10.1016/0305-0548(94)00059-H) — criterios
correlacionados/redundantes, ponderados por separado, "cuentan" la misma
información más de una vez y distorsionan la influencia real de esa
dimensión. **Esta redundancia sigue sin resolverse** y debe abordarse antes de
someter la tesis (verificar con los datos reales del estudio si el precio de
energía varía entre alternativas; si no varía, considerar retirar uno de los
cuatro criterios de la familia LCOE/B-C/TIR/recuperación descontada —todos
rank-correlacionados a ρ=±1.0000 bajo precio y parámetros de costo
compartidos, ver prueba numérica de 2026-09-13— o sustituir el peso liberado
por un criterio genuinamente independiente). La revisión de criterios debe
considerar estas relaciones, junto con correlaciones observadas; no basta
contar referencias.

## 2. Obtención individual de pesos AHP

Se usa una jerarquía plana de criterios: n(n−1)/2 comparaciones
independientes (28 para los ocho criterios actuales — ver §6 para el
historial de cambios de este set, que pasó de 8 a 7 y de vuelta a 8 el
2026-09-13 por razones distintas cada vez). Cada respuesta expresa
importancia respecto al objetivo, no preferencia por una turbina. No se fija
a priori una división 60/40 entre áreas. La escala de entrada es 1–9 y sus
recíprocos; las comparaciones comienzan sin responder. El valor 1 debe
elegirse explícitamente para expresar igualdad.

La matriz debe ser positiva, finita, completa, recíproca y tener diagonal 1.
Los pesos son el vector propio principal normalizado. Se informa λmax,
CI = (λmax − n)/(n − 1), RI y CR = CI/RI. `core/ahp.py` calcula RI según el
número de criterios de la matriz (RI=1.41 para n=8).
Fuente: [R. W. Saaty, 1987](https://doi.org/10.1016/0270-0255(87)90473-8).
Se adopta CR ≤0.10 como regla de aplicación de este estudio; no equivale a una
prueba estadística, a consenso ni a validación científica.

Una matriz inconsistente se puede calcular y exportar para revisar sus juicios,
pero no aplicar desde el flujo AHP. Se muestran tríadas con mayor desviación
logarítmica respecto a la transitividad multiplicativa; son orientaciones de
revisión, no instrucciones para cambiar preferencias. No se corrigen respuestas
automáticamente ni se deducen matrices desde los pesos anteriores.

AHP formaliza juicios y permite examinar su coherencia. Referencia metodológica:
[T. L. Saaty, 2008](https://www.rafikulislam.com/uploads/resourses/197245512559a37aadea6d.pdf).
La implementación inicial es individual. La agregación de varios participantes
y las jerarquías de grupos no están implementadas.

## 3. Uso en la plataforma

En TOPSIS del parque, abrir **Obtener pesos con AHP — comparaciones por pares**.

1. Completar autor, objetivo y justificación, con referencias cuando corresponda.
2. Responder las 28 comparaciones y pulsar **Calcular pesos y consistencia**.
3. Revisar los resultados. Si CR supera 0.10, reconsiderar los juicios y volver
   a calcular, sin perseguir un ganador concreto.
4. Pulsar **Aplicar a TOPSIS**. Si ya existen alternativas de parque, se vuelve
   a calcular el ranking; de lo contrario, ejecutar el análisis de parques.
5. Ejecutar robustez desde ese escenario para explorar variaciones de los pesos.
6. Exportar el expediente AHP y guardar el proyecto.

La matriz, pesos completos, autor, objetivo, justificación, fecha de cálculo,
CR y referencia RI se conservan en el expediente. El borrador y el expediente
aplicado se guardan con el estado del proyecto. Cambiar el borrador no sustituye
los pesos aplicados hasta pulsar Aplicar. Mover un control de peso cambia el
escenario a manual. Restaurar valores por defecto vuelve al escenario de
referencia. Ninguno de esos escenarios se presenta como AHP.

El servidor recalcula el expediente cuando se usa en ranking, análisis espacial
o robustez y rechaza matrices inconsistentes; no confía en pesos o marcas de
consistencia adjuntos por el cliente. TOPSIS puede omitir columnas constantes
o no disponibles según sus reglas existentes y renormalizar las restantes:
se conservan los pesos AHP propuestos y los efectivos devueltos por el ranking.

## 4. Robustez e interpretación

El análisis actual mantiene la matriz de alternativas fija y muestrea pesos
Dirichlet alrededor del vector AHP, con concentración explícita. Se reportan
el centro utilizado y el expediente. Esa concentración es un supuesto de
sensibilidad, no una dispersión estimada de expertos ni una confianza Delphi.
La interpretación es condicional a los datos, alternativas y distribución.
[Lahdelma, Hokkanen y Salminen, 1998](https://www.sciencedirect.com/science/article/pii/S037722179700163X).

Quedan como extensiones: análisis de redundancia/exclusión de criterios,
variación de umbrales y muestreo conjunto de incertidumbre de datos y pesos.
El Monte Carlo económico no equivale a ese análisis conjunto. Random Forest
es una explicación auxiliar; su validación fuera de muestra (K-fold, con
comparación contra un modelo lineal entrenado en los mismos folds) ya está
implementada en `core/ml_validation.py` y cubierta por
`tests/test_ml_validation.py`, `tests/test_weight_robustness.py` y
`tests/test_global_sensitivity.py` — no se sustituye por AHP, son capas
independientes de la auditoría.

La robustez puede mostrar estabilidad o revelar que no existe un ganador
concluyente. No certifica la pertinencia de los criterios ni la exactitud de
los modelos físicos. Antes de usar resultados en la tesis, completar las
fichas de evidencia y documentar por qué se conserva cada criterio.

## 5. CAPEX/MW instalado como posible criterio adicional (2026-09-13)

Se evaluó agregar CAPEX específico (USD/kW instalado, ya calculado en
`core/farm_mcda.py` y expuesto en `LAYOUT_AUDIT_ONLY` pero sin peso) como
noveno criterio o reemplazo dentro de la familia LCOE/B-C/TIR/recuperación
descontada. **Hallazgo previo (código verificado)**: con el modelo de costos
original, `capex_usd_per_installed_kw` era una **constante matemática**
(2 727.27 USD/kW) independiente de la turbina o del número de unidades,
porque `turbine_usd_per_kw` era un único precio fijo por kW para cualquier
turbina — mismo problema estructural que el área efectiva. Verificado con
tres combinaciones turbina/cantidad de potencia total similar: las tres
dieron exactamente 2 727.2727 USD/kW.

**Causa raíz y corrección**: el costo de cimentación de un aerogenerador
escala con la altura de buje y el diámetro de rotor a través del momento de
volteo (empuje aerodinámico × brazo de palanca de la altura), no es una
fracción fija del costo de turbina. Mecanismo y geometría de referencia
citados en [Key, Roberts & Eberle, "Scaling trends for balance-of-system
costs at land-based wind power plants: opportunities for innovations in
foundation and erection", National Renewable Energy Laboratory, OSTI ID
1837954, 2022](https://www.osti.gov/biblio/1837954) (texto completo
verificado vía PDF): *"Foundation cost increases are a result of the larger
total tower mass and larger overturning moments that are generated by taller
towers... turbine operational load—such as peak thrust at rated wind
speed—is the primary driver of foundation cost."* El mismo estudio confirma
que el costo de colección/cableado (ya modelado en `_collection_length_m`,
proxy de árbol de expansión mínima) escala con la huella de la planta y **no**
con la altura de buje — consistente con lo ya implementado, sin cambios ahí.

Se implementó `CostModel.foundation_cost_usd()`
(`core/cost_model.py`): `costo = coeficiente × diámetro_rotor_m² ×
altura_buje_m × n_turbinas`, sumado al CAPEX en `farm_mcda._economics()`
igual que el cable de colección. **Nivel de confianza distinto por
componente**: el mecanismo físico (momento de volteo ∝ D²×H) y la geometría
de la turbina de referencia (2.5 MW, Ø117 m, buje 90 m) están citados y
verificados en texto completo; la magnitud en dólares (coeficiente calibrado
a ≈130 USD/kW de cimentación en esa turbina de referencia) es una
**estimación de orden de magnitud**, no una cifra leída de una tabla
publicada — el desglose de cimentación en USD del estudio citado vive en su
software LandBOSSE (no público en forma de ecuación cerrada), solo sus
tendencias relativas están en el texto. Debe refinarse si se dispone de una
corrida de LandBOSSE o de cotizaciones reales antes de citar la cifra exacta
en la tesis.

También se recalibró el precio base `turbine_usd_per_kw` (antes 1800 USD/kW,
totalizando ~2727 USD/kW con BOS — nivel ~2008) a 945 USD/kW (~1350 USD/kW
total con BOS porcentual, antes de cimentación/cable), usando como ancla:
planta de referencia 2018 del mismo estudio Key/Roberts/Eberle (TCC 881 +
BOS 386 = 1267 USD/kW); rango reciente 1200-1800 USD/kW citado por reportes
de mercado DOE/LBNL (confianza media — síntesis de búsqueda, no tabla única
verificada); IRENA, *Renewable Power Generation Costs in 2024* (2025),
promedio global ponderado onshore 1041 USD/kW, turbina ≈75% del costo (ya
usado en `DESIGN_COST_BENCHMARK`). El punto elegido para México queda por
encima del promedio global 2024 (que incluye mercados de mano de obra más
barata) y cerca del rango de EE.UU. reportado por DOE/LBNL. Verificado con
OPEX resultante (~50 USD/kW/año) contra el supuesto de 52 USD/kW/año del
mismo estudio NREL y el >40 USD/kW/año reportado por DOE/LBNL para 2023.

**Resultado verificado**: con las tres turbinas de referencia de la
plataforma, `capex_usd_per_installed_kw` ahora varía genuinamente entre
1428.6 y 1495.3 USD/kW (dirección físicamente sensata: mayor diámetro/altura
sube el costo por kW, pero mayor potencia nominal lo diluye), y el LCOE
resultante (~0.061-0.064 USD/kWh) es creíble para eólica terrestre. El
criterio queda disponible en `LAYOUT_AUDIT_ONLY` para promoverse a
`LAYOUT_WEIGHTS` — pendiente de decidir junto con la redundancia
LCOE/B-C/TIR/recuperación descontada de la sección anterior, ya que ambas
decisiones compiten por el mismo espacio de peso económico.

## 6. Restructuración de criterios económicos (2026-09-13)

Resolviendo lo pendiente de las secciones 1 y 5, el set de `LAYOUT_WEIGHTS`
pasó primero de 8 a 7 criterios, y luego (segunda decisión, mismo día — ver
al final de esta sección) de vuelta a 8, por una razón distinta a la
original.

**Se retiran de los pesos** (bajan a `LAYOUT_AUDIT_ONLY`, se siguen
calculando y mostrando en el detalle de cada alternativa):
- `benefit_cost_ratio` — confirmado en código
  (`rank_layout_alternatives`, precio/tasa/OPEX% constantes entre
  alternativas de una corrida) que junto con LCOE y TIR están perfectamente
  rank-correlacionados (ρ=±1.0000): son la misma información bajo
  transformaciones no lineales distintas. LCOE queda como único
  representante — estándar de industria con respaldo de importancia
  creciente frente a payback/TIR (Delapedra-Silva et al. 2022, ya citado).
  El mismo ρ=±1.0000 se confirmó también para ROI (VAN/CAPEX): es la misma
  familia, no un criterio nuevo independiente.

**Cambia la forma de un criterio ya ponderado**:
- `van_usd_per_installed_mw` → `van_usd` (bruto). Verificado en código: con
  factor de capacidad fijo y variando solo la escala (n turbinas de un mismo
  modelo), VAN/MW instalado se queda prácticamente constante (29 191.76 USD/MW
  en todo el rango, en una prueba con `_economics()`), mientras que VAN bruto
  escala con la capacidad instalada. Dado que el área del sitio es fija entre
  alternativas (mismo argumento ya aceptado para AEP P90 bruto), el bruto es
  la métrica correcta: mide cuánto valor total se extrae de ese terreno fijo,
  no una eficiencia por unidad abstracta de capacidad.
- **Asimetría deliberada con CAPEX**: a diferencia de VAN/AEP (criterios de
  beneficio, "más es mejor"), `capex_usd_per_installed_kw` se queda
  normalizado por kW, NO se vuelve bruto. Con CAPEX bruto ("menos es mejor")
  y área de sitio fija, TOPSIS premiaría trivialmente instalar menos
  capacidad — la operación opuesta y sin sentido a lo que premian VAN/AEP.
  Solo un criterio de costo normalizado por unidad de capacidad evita esa
  distorsión.

**Se agrega**:
- `capex_usd_per_installed_kw` — ver §5: antes era una constante matemática
  (mismo problema que el área efectiva), corregido con el costo de
  cimentación dependiente de geometría. Verificado que no es redundante con
  OPEX/kW (proporcional exacto por `opex_pct_capex` fijo), con CAPEX bruto
  (correlacionado con escala, no con eficiencia de costo) ni con ROI (misma
  familia que LCOE).

**Otros candidatos evaluados y descartados para esta ronda**:
- `wake_loss_pct`: descartado — no es objetivo respecto al diseñador. Es el
  resultado de una búsqueda de layout acotada por `max_wake_loss_pct`, un
  parámetro que fija el analista igual para todas las alternativas de una
  corrida, no una propiedad intrínseca de la turbina (`core/farm_mcda.py`,
  `optimize_layout_structured`).
- `aep_uncertainty_pct`, `curtailment_loss_pct`, `iec_suitability_score`:
  verificado en código que son constantes entre alternativas de una misma
  corrida (parámetros de proyecto o, en el caso de IEC, un valor
  hardcodeado `0.5` sin implementar — ver `bankable_missing` en
  `core/farm_mcda.py`, que ya señala la aptitud de clase IEC como dato
  faltante del fabricante: `ct_curve`, `iec_class`, `extreme_wind`,
  `ambient_turbulence`). Aptitud de clase IEC 61400-1 (turbulencia y ráfaga
  extrema de 50 años del sitio vs. clase declarada de la turbina) es un
  criterio real y estandarizado (IEC 61400-1) que queda como extensión
  futura una vez se disponga de esos datos del fabricante — no encontrado
  combinado explícitamente con AHP/TOPSIS en la búsqueda hecha para este
  expediente.
- Endpoint `/api/energy/leveraged` (VAN apalancado con deuda): existe pero
  está desconectado del loop de alternativas de `farm_mcda.py`; integrarlo
  exigiría decidir de dónde sale `debt_ratio_pct` por alternativa — una
  decisión de modelado nueva, no un ajuste de esta ronda.

**Estructura intermedia (7 criterios)** que resultó de lo anterior: los
cuatro técnicos sin cambio (0.60), más VAN bruto (0.10), LCOE (0.20) y
CAPEX/kW (0.10) como único bloque económico (0.40).

### Segunda decisión, mismo día: se reincorpora recuperación descontada

El investigador decidió **reincorporar `discounted_payback_years` junto a
LCOE, a propósito**, no por descuido — por ejemplo, por la expectativa de
que un comité de tesis vea el PRI reportado explícitamente, una
consideración de exposición/comunicación del trabajo, no un hallazgo nuevo
de independencia estadística. **Queda anotado sin ambigüedad**: LCOE y
recuperación descontada **siguen siendo ρ=±1.0000** en este modelo (misma
información bajo dos transformaciones distintas del mismo cociente
PV(costos)/PV(energía) dado precio y tasa constantes). Ponderar ambos no le
da a TOPSIS dos juicios económicos independientes — es el mismo juicio
contado dos veces, con una amplificación cuya magnitud exacta depende de la
forma de cada transformación (ver CRITIC, Diakoulaki et al. 1995, y MAUT,
Keeney & Raiffa 1976, ambos citados en §1). Esta es una decisión informada
y documentada del investigador, aceptando esa redundancia parcial de forma
consciente — no una corrección del análisis anterior.

**Estructura final** (60% técnico / 40% económico, pesos provisionales — ver
siguiente párrafo):

| Criterio | Bloque | Peso |
|---|---|---:|
| Cp efectivo de la serie completa | Técnico | 0.20 |
| Horas cerca de Cp máximo | Técnico | 0.20 |
| Factor de capacidad neto | Técnico | 0.10 |
| AEP P90 (bruto) | Técnico | 0.10 |
| VAN (bruto) | Económico | 0.10 |
| LCOE | Económico | 0.10 |
| CAPEX por kW instalado | Económico | 0.10 |
| Recuperación descontada | Económico | 0.10 |

Los pesos de LCOE, CAPEX/kW y recuperación descontada (0.10 cada uno) fueron,
hasta 2026-09-14, un placeholder razonado, no un resultado de comparaciones
AHP reales. Esa fecha se reemplazaron por el expediente descrito a
continuación.

### Tercera actividad, 2026-09-14: expediente AHP real de los 8 criterios

A petición explícita del investigador ("tu tarea, no la mía"), se construyó
un primer expediente AHP real —28 comparaciones, matriz completa,
verificación de consistencia con `core/ahp.calculate_ahp`— para reemplazar
el placeholder anterior. **Naturaleza de este juicio**: es la construcción
de un único evaluador (el método adoptado en este documento excluye Delphi;
ver encabezado), realizada por el investigador con asistencia de Claude
Sonnet 5 para sistematizar 28 juicios de forma consistente a partir de la
evidencia ya reunida en §1, §5 y §6 — no es un panel de expertos, no es un
resultado estadístico, y no sustituye la validación del investigador antes
de citarse en la tesis como AHP definitivo. Si el investigador no está de
acuerdo con algún juicio individual, debe recalcularse desde la calculadora
de la plataforma con su propio autor/objetivo/justificación.

**Método de construcción** (auditable, no ajustado a un resultado buscado):
cada criterio recibió un nivel de prioridad relativo (tabla siguiente),
fundamentado en la evidencia ya citada. Cada una de las 28 comparaciones por
pares se obtuvo del cociente entre los dos niveles involucrados, redondeado
al valor más cercano de la escala verbal estándar de Saaty (1, 2, 3, ..., 9
y sus recíprocos — nunca valores intermedios como 1.5, que no forman parte
de esa escala). Cuando el cociente cae a medio camino entre dos valores de
la escala, el redondeo son juicios de "importancia igual", nunca hacia el
valor que favorecería a un criterio en particular. Esto es intencional: el
propósito no es maximizar la diferenciación entre criterios, sino declarar
solo la diferencia que la evidencia sostiene y dejar el resto en empate
explícito.

| Criterio | Bloque | Nivel de prioridad | Razonamiento (evidencia ya citada en este documento) |
|---|---|---:|---|
| Factor de capacidad neto | Técnico | 10 (el más alto) | Único estudio revisado en texto completo que usa un criterio de este set como decisorio único (Paraschiv & Paraschiv 2024, §1) — la pertinencia individual más fuerte del set, aunque ese estudio no compara su peso relativo frente a los otros 7 (limitación ya anotada en §1). |
| AEP P90 (bruto) | Técnico | 9 | Estándar de industria para evaluación bancable (P90/SAM-NREL, §1); tras el cambio de 2026-09-13 mide energía absoluta extraída del sitio, igual base de comparación que factor de capacidad — nivel casi igual por diseño, ver M[factor_cap, aep]=1 (empate) más abajo. |
| LCOE | Económico | 6 | Único representante retenido de la familia LCOE/B-C/TIR/recuperación (§6); la revisión citada documenta explícitamente su "importancia creciente" relativa a NPV/IRR en la última década (Delapedra-Silva et al. 2022, §1) — el criterio económico "puro" mejor respaldado del set. |
| Horas cerca de Cp máximo | Técnico | 6 | Literatura cuantifica impacto directo en AEP (hasta 43%, Sedaghat et al. 2017; Lanzafame & Messina 2010, §1), pero el umbral concreto (≥95%) es convención del programa, no de la literatura — mismo nivel que LCOE, no más alto, por esa reserva. |
| VAN (bruto) | Económico | 5.5 | "Métrica financiera más usada individualmente" (Delapedra-Silva et al. 2022, §1); nivel comparable a horas-Cp-máximo y ligeramente por debajo de LCOE porque la misma fuente no ofrece un criterio para preferir VAN sobre LCOE — se usan como complementos, no hay jerarquía documentada entre ambos. |
| Cp efectivo de la serie completa | Técnico | 5 | Definición estándar de eficiencia aerodinámica (Manwell et al., §1), pero §1 deja abierta su posible correlación con AEP/factor de capacidad — mismo nivel que CAPEX/kW, ambos con una reserva de redundancia/incertidumbre documentada sin resolver. |
| CAPEX por kW instalado | Económico | 4 | Mecanismo físico y geometría de la turbina de referencia verificados en texto completo (Key, Roberts & Eberle 2022, §5); la magnitud en USD/kW es una estimación de orden de magnitud, no una cifra de tabla publicada (§5) — confianza intermedia, ligeramente por debajo de los criterios financieros ya consolidados. |
| Recuperación descontada | Económico | 2 (el más bajo, con margen claro) | Documentado en este mismo archivo como ρ=±1.0000 con LCOE (misma información, §6) y la fuente que lo respalda lo critica explícitamente: *"PBP disregards existing cash flows after the investment is recovered, and is a very limited tool for decision making"* (Delapedra-Silva et al. 2022, §1). Se mantiene en el set por decisión consciente del investigador (§6, "Segunda decisión"), no por mérito informativo propio — de ahí el nivel más bajo del set, con una separación clara del resto en vez de un empate. |

**Matriz completa** (fila vs. columna; valor = cuántas veces más importante
es la fila que la columna; orden de filas/columnas igual al de la tabla
`LAYOUT_WEIGHTS`: Cp efectivo, horas Cp máx., factor de capacidad, AEP P90,
VAN, LCOE, CAPEX/kW, recuperación descontada):

```
[[1,   1,   1/2, 1/2, 1,   1,   1,   2  ],
 [1,   1,   1/2, 1/2, 1,   1,   1,   3  ],
 [2,   2,   1,   1,   2,   2,   2,   5  ],
 [2,   2,   1,   1,   2,   1,   2,   4  ],
 [1,   1,   1/2, 1/2, 1,   1,   1,   3  ],
 [1,   1,   1/2, 1,   1,   1,   1,   3  ],
 [1,   1,   1/2, 1/2, 1,   1,   1,   2  ],
 [1/2, 1/3, 1/5, 1/4, 1/3, 1/3, 1/2, 1  ]]
```

**Resultado de `calculate_ahp`**: λmax=8.06416, CI=0.00917, RI=1.41 (n=8),
**CR=0.0065** (muy por debajo del umbral 0.10 adoptado — matriz consistente,
sin tríadas problemáticas que revisar). Pesos resultantes (vector propio
principal normalizado, no manualmente redondeados para que sumen 1 con
precisión de punto flotante, ver `core/multicriteria.py`):

| Criterio | Bloque | Peso AHP |
|---|---|---:|
| Factor de capacidad neto | Técnico | 0.2138 |
| AEP P90 (bruto) | Técnico | 0.1933 |
| LCOE | Económico | 0.1216 |
| Horas cerca de Cp máximo | Técnico | 0.1096 |
| VAN (bruto) | Económico | 0.1096 |
| Cp efectivo de la serie completa | Técnico | 0.1042 |
| CAPEX por kW instalado | Económico | 0.1042 |
| Recuperación descontada | Económico | 0.0437 |

Bloque técnico: 62.1%; bloque económico: 37.9% — cercano al 60/40 de
referencia previo, pero ahora **derivado** de 28 juicios individuales, no
asumido a priori (§2: "no se fija a priori una división 60/40"). Esa
cercanía es una coincidencia razonable dado que los niveles de prioridad se
basaron en la misma revisión bibliográfica que ya sugería ese orden de
magnitud — no es evidencia adicional de que 60/40 sea "correcto", ni excusa
para dejar de someter este expediente a revisión por el investigador.

Nota importante que se mantiene sin cambio: la redundancia ρ=±1.0000 entre
LCOE y recuperación descontada (§6, CRITIC/Diakoulaki 1995, MAUT/Keeney &
Raiffa 1976) **no se resuelve** por tener ahora un AHP real — un peso AHP
bajo (0.0437) para recuperación descontada reduce la distorsión frente al
placeholder anterior (0.10), pero sigue sumando una fracción de información
ya contada en LCOE. Queda como limitación documentada, no corregida, tal
como se advirtió en §6.

## 7. Verificación del software

`tests/test_ahp.py` verifica prioridades conocidas, reciprocidad, datos faltantes,
inconsistencia, trazabilidad y uso de pesos recalculados en ranking y robustez.
`tests/ahp.test.cjs` cubre precisión, cambio a escenario manual y persistencia.
Los datos de prueba son sintéticos y no representan juicios del estudio.
`tests/test_turbine_clustering.py` (2026-09-14) cubre el endpoint
`/api/mcda/turbine_clustering` (agrupamiento jerárquico de turbinas del
catálogo por correlación de sus series de potencia — preselección de
candidatos antes de TOPSIS): agrupamiento por defecto sobre todo el catálogo,
filtrado por `turbine_filenames` con orden preservado del catálogo, los
cuatro métodos de enlace soportados, método no soportado, menos de dos
turbinas tras filtrar, nombres de archivo desconocidos ignorados sin error,
sesión sin datos, `session_id` faltante y series de potencia constantes
(viento en calma, sin variación que correlacionar).
