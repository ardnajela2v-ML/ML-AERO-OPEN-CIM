# Auditoría de cantidad de turbinas — proyecto_aero_2026-09-26.aeroproj

Se leyó el proyecto adjunto como datos. La corrida guardada usa 384 600 pares
válidos de viento MCP (1980–2024), estela gaussiana, `k=0.04`, `Ct=0.8`,
separación mínima `2.5D` y límite de pérdida por estela de 5 %.

| Modelo | Rotor | Capacidad física guardada | Seleccionadas | Estela |
|---|---:|---:|---:|---:|
| Gamesa20 | 80 m | 17 | 12 | 4.59 % |
| Gamesa21 | 90 m | 15 | 13 | 4.97 % |
| Vestas20 | 80 m | 17 | 12 | 4.57 % |
| Vestas30 | 90 m | 15 | 11 | 4.88 % |

La capacidad física respeta el orden esperado. El fallo aparece en la búsqueda
de un representante factible: en Gamesa20, los patrones de 13 turbinas
explorados reportaron al menos 5.95 % de estela. Sin embargo, las **mismas
13 coordenadas** del ganador Gamesa21 son válidas para Gamesa20: su distancia
mínima es 225 m, mayor que los 200 m exigidos a Gamesa20. Recalculadas con
la serie MCP y los multiplicadores espaciales guardados, esas coordenadas dan
4.97 % de estela con Gamesa21 (reproduce el resultado guardado) y **4.57 %
con Gamesa20**. Por tanto, el descarte de 13 turbinas Gamesa20 fue un falso
negativo de la búsqueda geométrica, no una restricción física.

Aplicando a ese layout el cálculo horario de ingresos contra el PML del
proyecto y los costos/25 años/tasa guardados, el VAN Gamesa20 de 13 turbinas
es aproximadamente **80 557 454 USD**, frente a **78 444 873 USD** del ganador
guardado de 12 turbinas. También cambia la decisión económica, por unos
**2.11 millones USD**. El cálculo reproduce la misma fuente MCP, curva de
potencia y configuración económica; el valor definitivo debe salir de una
nueva corrida completa del proyecto con el código corregido.

Causa: cada turbina generaba solo rejillas con separaciones expresadas en
múltiplos de *su propio* diámetro. El patrón ganador de la de 90 m usa
225 m = 2.5D; para la de 80 m equivale a 2.8125D, valor ausente de sus
separaciones configuradas. La optimización local no encontró ese patrón.
La corrección reutiliza las coordenadas seleccionadas por rotores mayores
como candidatos de los menores, y vuelve a comprobar geometría, exclusiones,
separación, estela y VAN con la turbina destino. Los resultados previos dentro
del archivo `.aeroproj` no se modificaron: hay que generar de nuevo los
layouts para actualizar el ranking.

Esta reutilización corrige el falso negativo observado y mejora la comparación
entre modelos analizados juntos. La búsqueda sigue siendo aproximada: no prueba
que se haya encontrado el óptimo global ni descubre automáticamente diseños que
ningún modelo haya propuesto.

Al importar este `.aeroproj`, la interfaz restaura `lastSpatial` y `lastRanking`.
Reejecutar solo TOPSIS vuelve a ordenar esas alternativas antiguas; no genera
posiciones. El código corregido marca nuevas corridas con
`layout_search_revision=shared_geometry_v1`. Los resultados anteriores se
muestran con un aviso y requieren «Regenerar layouts ahora» (con el servidor
Python reiniciado para cargar el módulo nuevo) antes de recalcular TOPSIS.

## Por qué apareció ahora

Se comparó con `exports/benchmark_horario_20260924/deferred_pipeline.json`.
Aquel resultado usaba 52 461 registros observados a 10 minutos (~un año) y un
polígono de 0.709182 km²; el proyecto adjunto usa 384 600 horas válidas del
hindcast MCP 1980–2024 y un polígono de 0.760639 km² (+7.26 % de área,
39.5 m de distancia máxima entre los contornos). La separación mínima 2.5D,
las listas de separaciones, el retiro de 45 m para Gamesa21 y el límite de
estela 5 % no cambiaron. En la corrida anterior Gamesa21 tenía capacidad
física 13 y se seleccionaron 11; en la nueva tiene capacidad 15 y se
seleccionaron 13. El espacio adicional hizo visible un patrón de 13 que la
rejilla expresada en diámetros de Gamesa20 no proponía.

El historial de Git muestra que las rejillas escaladas por diámetro y la
selección de una cantidad exacta preceden a esta corrida. El commit
`3ab3039` (22-09-2026) agregó la comparación explícita de tres familias y
selección por VAN con `candidate_limit_per_n=0` por defecto. Cambios locales
posteriores aceleraron la búsqueda de N, pero la corrida guardada ya muestra
la omisión geométrica; el problema no proviene del ajuste Jensen, pues usa
el modelo de estela gaussiano. El error es de cobertura del espacio de
layouts, no del cálculo de capacidad física ni de la fórmula de estela.
