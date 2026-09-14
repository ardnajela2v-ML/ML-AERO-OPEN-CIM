# AHP–TOPSIS con fundamento bibliográfico y análisis de robustez

Se excluye Delphi por decisión del investigador. No se afirma consenso de un
panel. El método adoptado es: evidencia científica → juicios AHP individuales
→ TOPSIS → robustez. No se han introducido juicios reales en esta implementación.

## Objetivo y alcance

La primera versión opera sobre los ocho criterios actuales del ranking de
parques. El autor debe declarar el objetivo de la decisión y fundamentar sus
comparaciones. Es posible identificar como autor al investigador; registrar
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

Matriz inicial del ranking de parques: los ocho criterios se obtuvieron de
`LAYOUT_WEIGHTS` en `core/multicriteria.py`. Los porcentajes de referencia
sirven para auditar el estado del programa; no constituyen un resultado AHP.
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

Se usa una jerarquía plana de ocho criterios: 28 comparaciones independientes.
Cada respuesta expresa importancia respecto al objetivo, no preferencia por
una turbina. No se fija a priori una división 60/40 entre áreas. La escala de
entrada es 1–9 y sus recíprocos; las comparaciones comienzan sin responder.
El valor 1 debe elegirse explícitamente para expresar igualdad.

La matriz debe ser positiva, finita, completa, recíproca y tener diagonal 1.
Los pesos son el vector propio principal normalizado. Se informa λmax,
CI = (λmax − n)/(n − 1), RI y CR = CI/RI. La tabla adoptada usa RI=1.41 para
n=8. Fuente: [R. W. Saaty, 1987](https://doi.org/10.1016/0270-0255(87)90473-8).
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

## 6. Verificación del software

`tests/test_ahp.py` verifica prioridades conocidas, reciprocidad, datos faltantes,
inconsistencia, trazabilidad y uso de pesos recalculados en ranking y robustez.
`tests/ahp.test.cjs` cubre precisión, cambio a escenario manual y persistencia.
Los datos de prueba son sintéticos y no representan juicios del estudio.
