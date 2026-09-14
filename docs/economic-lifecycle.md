# Ciclo de vida económico compartido

El panel tecnoeconómico, la sensibilidad Monte Carlo, TOPSIS de parques y la
valoración de sitios de Kriging usan `core/project_economics.py` para construir
los flujos del proyecto. El AEP recibido representa la energía neta del año 1.

## Supuestos del estudio

- Degradación: 0.3% anual, aplicada desde el año 2: `E_t = E_1 × 0.997^(t−1)`.
- Desmantelamiento: 3% del CAPEX inicial, pagado una sola vez al final de la vida
  del proyecto. No se infla este importe ni se descuenta un valor de rescate.
- OPEX: el importe inicial se multiplica por `(1 + inflación)^(t−1)`.
- Precio: constante salvo una serie anual explícita. El ingreso horario del
  primer año, cuando existe, sustituye únicamente el ingreso de ese año.

0.3% y 3% conservan los supuestos que ya utilizaba TOPSIS. Esta unificación no
constituye una validación empírica de esos valores. Sus constantes se definen
en el módulo compartido; las funciones de cálculo admiten otros valores para
comparaciones explícitas, incluido cero.

## Cálculo

`FC_t = E_t × precio_t − OPEX_t − desmantelamiento_t`

`VAN = −CAPEX + Σ FC_t / (1 + tasa)^t`

`LCOE = [CAPEX + Σ (OPEX_t + desmantelamiento_t) / (1 + tasa)^t]
        / [Σ E_t / (1 + tasa)^t]`

B/C divide ingresos descontados entre costos descontados. La TIR usa el vector
completo de flujos, incluido CAPEX en el año 0 y desmantelamiento final. Se
conserva el método de bisección: puede devolver un resultado no disponible si
no encuentra cambio de signo en su intervalo de búsqueda; no garantiza una
TIR única para flujos con varios cambios de signo.

Los ajustes de pérdidas, escalamiento de precio e inflación del navegador
preservan la energía degradada y el costo final. El financiamiento iniciado
desde el panel recibe los flujos del proyecto por año y resta el servicio de
deuda; el endpoint conserva compatibilidad con clientes antiguos que solo
envían ingresos y OPEX constantes.

## Comparación e interpretación

La igualdad entre motores requiere el mismo AEP, CAPEX, OPEX, precio, tasa,
vida útil y supuestos. Diseño mantiene su modelo de costos de referencia y
el costo adicional de cable colector. El panel mantiene el escenario de
costos editable. Por ello, esta corrección no implica igualdad de resultados
si se comparan configuraciones de costos distintas.

Los resultados guardados no se recalculan automáticamente. Es necesario
volver a ejecutar el análisis para aplicar el ciclo de vida actualizado; el
panel identifica resultados anteriores sin estos supuestos.

## Verificación

`tests/test_project_economics.py` compara los motores para vidas de 5, 25 y
40 años y tasas de 0% y 8%, verifica fórmulas independientes de valor presente,
precios variables, inflación, ingreso horario, cable colector y financiamiento.
`tests/test_lcoe_consistency.py` conserva la regresión contra LCOE sin descuento.
`tests/economic_lifecycle.test.cjs` comprueba los ajustes del navegador.
