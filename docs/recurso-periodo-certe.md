# Fuente temporal, periodo y cálculo espacial

## Periodo y sensores

- Recurso Eólico conserva «Todo el registro» por defecto y permite elegir un año
  según el reloj de la estación (CERTE: UTC−6 fijo). Cambia la vista de la fuente;
  conserva la tabla completa para restaurar años y guardar el proyecto.
- CERTE se lee desde `Config.CERTE_FOLDER`. Se prefiere el consolidado
  `M04_CERTE_CONSOLIDADO_2017-2020.txt` para no duplicar las exportaciones anuales.
- La velocidad es el promedio aritmético A/B por fecha y altura cuando existen
  ambos canales. Se requieren ambos valores finitos y no negativos; si falta
  uno queda un hueco. Se conservan los originales para diagnóstico.
- En el archivo actual hay A/B a 80 m y una medición a 20, 40 y 60 m. No se
  promedian alturas distintas ni desviaciones estándar. La turbulencia A/B usa
  las velocidades y desviaciones estándar de los sensores originales.
- Las presiones canónicas son Pa y la presentación es hPa. Los conteos de horas
  usan la cadencia del registro, no el número de muestras.
- Las rosas ofrecen 16 y 36 sectores centrados en la dirección indicada, con
  norte arriba y sentido horario. No se giran las mediciones.

## Cálculo espacial actualmente implementado

La fuente elegida puede alimentar la cadena de anomalías espaciales antes
restringida a fuentes MCP. Mantiene los nodos ERA5/MERRA2, interpolación,
componentes circulares, rugosidad y cálculos espaciales. No publica MCP ni rellena
la fuente temporal. El ancla es la coordenada real de la estación elegida.

En estación única o año seleccionado se usan las horas válidas del periodo:
mínimo 24 y 90% de coincidencia por nodo, con tres nodos por proveedor oficial.
El MCP completo conserva su política de climatología de diez años. Sin validación
observada independiente se usan pesos iguales entre proveedores participantes.

Parámetros físicos conservados: referencia espacial 100 m, z0=0.03 m; anomalías
logarítmicas con desplazamiento 0.5 m/s y límites P01/P99; perfiles de rugosidad por
sector WorldCover con respaldo por pendiente DEM. El ajuste adicional de altura
usa Hellmann con el exponente configurado. La calibración conserva densidad de
respaldo 1.225 kg/m³ en el ancla; los nodos pueden usar presión y temperatura.
Resultados de otras fuentes, periodos o revisiones requieren recalcular.

## Auditoría del proyecto adjunto y límite pendiente

El proyecto del 11 de septiembre usaba MCP 1980–2024 y una comparación CERTE de
2018. Antes del cambio A/B, el lector reproducía esa comparación original:
52,461 registros, 9.357056316883018 m/s y sector dominante 340°. La rosa de 36
sectores difería como máximo 0.004989 puntos porcentuales por redondeo. Ese
baseline corresponde al sensor A; no debe exigirse igualdad después del cambio
al promedio A/B.

**El mapa actual no es una reconstrucción temporal por turbina.** La calibración
reduce las series de los nodos a estadísticas y mapas sectoriales. Antes de esta actualización, la optimización
podía aplicar multiplicadores de velocidad media local a una serie común.

La aclaración del usuario exige series históricas propias para cada ubicación
candidata TOPSIS y cada turbina, y que energía/evaluación consuman esas series.
La reconstrucción por Kriging en cada instante no está implementada; el escalado
conservado y la conexión del histórico se detallan en la actualización siguiente.

## Producción histórica conectada a TOPSIS (actualización)

La producción de los candidatos individuales y de los layouts usa ahora la
fuente histórica seleccionada cuando existe. Se conservan los pares de velocidad
y dirección; los inválidos se excluyen y se informa cuántos. Sin dirección no se
sustituye silenciosamente el histórico por Weibull para calcular estelas.

- Cada turbina recibe el factor media local del mapa / media histórica a la
  misma altura. No se modifica la fuente temporal registrada.
- La producción anual se estima sobre 8,760 horas a partir de las muestras
  válidas. La energía del registro y su duración se distinguen de esa AEP.
- El motor de estelas conserva la integración mediante frecuencias empíricas
  conjuntas: 36 sectores y intervalos de velocidad de 1 m/s. Usa la distribución
  observada, no una Weibull sintética; no es una simulación cronológica dinámica.
- El horizonte económico consume esa AEP y conserva los supuestos existentes
  de degradación, pérdidas y costos. No genera 25 años meteorológicos nuevos.
- Comparación y refinamiento de layouts guardan la misma base histórica y mapa;
  al mover turbinas recalculan sus factores locales.
- Resultados y auditoría incluyen periodo, archivos, altura, exclusiones de
  muestras, método y factores por turbina. Cambiar la fuente/año descarta tareas
  TOPSIS pendientes. Las exportaciones de proyectos conservan el contexto.

Prueba con CERTE 2018: 52,461 pares A/B válidos a 80 m; media A=9.3570563,
B=9.4116229 y promedio A/B=9.3843396 m/s. Las medias a 20, 40 y 60 m son
7.2593352, 8.3700823 y 8.9211043 m/s. Dirección dominante de 36 sectores: 340°.

La comparación cuantitativa contra un Kriging temporal por instante sigue siendo
una etapa posterior. No se ha sustituido el escalado espacial por ese método.

Validación de esta etapa: 85 pruebas Python y 20 pruebas JavaScript aprobadas.
Comprobación en navegador: Globe → CERTE observado → todo el registro → 2018;
media A/B 9.384 m/s y densidad de potencia 1017.5 W/m² en 2018.
