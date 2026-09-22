# Flujo de diseño y selección

La aplicación sigue **configuración → recurso espacial → Parque/layouts → AHP/TOPSIS → evaluación tecnoeconómica**.

## Ruta principal

1. En **③ Parque**, se seleccionan tecnologías y restricciones: geometría, exclusiones/OSM, separación, estela, pendiente opcional, límite de conexión y receptores de ruido/sombra.
2. `POST /api/farm/layouts/generate` prepara el recurso y llama a `core.farm_mcda.generate_park_layouts`. No usa pesos ni ejecuta TOPSIS. Los muestreadores espaciales reciben `resource_only=True`: no calculan economía independiente por turbina/punto.
3. El motor existente `generate_feasible_layouts_grid` explora cantidades y geometrías y evalúa estela, energía y economía **por cada tecnología, antes de clasificar** — tres familias explícitas y siempre auditables (`family_summary` en cada alternativa): Alineado y Tresbolillo (`optimize_layout_structured`, rejilla regular rose-aligned) y Greedy-vértice (`optimize_layout_greedy_vertex`, crecimiento irregular sembrado en el vértice más a barlovento, packing-maximising). Ninguna familia se descarta implícitamente por ranking de score; `select_technology_champion` conserva el mayor VAN factible **en el conjunto explorado** entre las tres, por tecnología — no certifica un óptimo global. La política ambiental anterior se conserva: se reportan violaciones y alternativas de mitigación explícitamente.
4. Los layouts y su diagnóstico se revisan en Parque. Cada alternativa puede mostrarse en el mapa. Aplicar mitigación regenera las alternativas y requiere una nueva clasificación.
5. **④ AHP/TOPSIS** envía las alternativas evaluadas a `POST /api/multicriteria/rank`. Cambiar pesos no ejecuta Kriging ni modifica posiciones o AEP. La evaluación financiera y su vista de posiciones reutilizan la alternativa del ranking elegida (ganadora por defecto).

Cambiar controles de generación invalida los candidatos y el ranking anteriores. Las respuestas de clasificación pendientes se descartan si ya cambió el conjunto de alternativas. Los proyectos guardados admiten tanto `farm_layout_candidates_v1` como el formato anterior `farm_layout_mcda_v2`.

## Revisión de rutas paralelas

| Ruta o motor | Uso actual |
| --- | --- |
| `/api/farm/layouts/generate` → `generate_park_layouts` | Generación principal previa a TOPSIS. |
| `/api/multicriteria/rank` → `rank_layout_alternatives` | Clasificación del conjunto preparado; una alternativa seleccionada por tecnología. |
| `/api/multicriteria/spatial`, scope park → `analyze_park_layouts` | Adaptador de compatibilidad para clientes antiguos; genera y clasifica. La interfaz principal ya no lo llama para parques. |
| `/api/multicriteria/spatial`, scope exploratory → `rank_sites_turbines` | Exploración explícita de puntos/turbinas aisladas; no representa layouts físicos. |
| `/api/farm/optimize` → `optimize_layout_structured` | Simulación manual de una tecnología en herramientas avanzadas. El encabezado antiguo lo describía incorrectamente como voraz. |
| `/api/farm/refine_topsis` → `refine_topsis_layout` | Comparación manual de variantes de una tecnología clasificada; no sustituye las alternativas del ranking. |
| `/api/farm/recommend` → `recommend_n_turbines` | Compatibilidad/estimación de demanda; abrir Parque ya no invoca esta estimación independiente. |
| `optimize_layout_greedy_vertex` (`core.farm`) | Tercera familia de `generate_feasible_layouts_grid` (paso 6): crecimiento greedy sembrado en el vértice más a barlovento, ejes alineados a la dirección dominante real del viento (rosa energética, no un valor fijo). Una sola pasada por tecnología produce una secuencia anidada válida para todo N. |
| `optimize_layout_greedy` (`core.farm`) | Función heredada sin llamadas en el flujo actual (dirección de estela fija a 270°, sin siembra en vértice); no confundir con `optimize_layout_greedy_vertex`. No se borró su implementación pública. |
| Diseño rápido | Usa la generación principal; espera revisión y TOPSIS antes de continuar al informe financiero. |

Las herramientas avanzadas conservan simulación, comparación y edición manual para inspección explícita. Sus resultados no reemplazan silenciosamente las alternativas generadas ni el ranking. Un cambio destinado a la decisión debe pasar por la generación y clasificación principales.

## Validación

`tests/test_layout_pipeline.py` verifica generación real sin TOPSIS, conservación de geometría/energía al clasificar y despacho independiente de la API. `tests/layout_pipeline.test.cjs` comprueba separación de acciones, invalidación, controles enviados y continuidad financiera. Las suites existentes cubren geometría, estelas, restricciones ambientales, terreno, OSM, MCP y persistencia.
