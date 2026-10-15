# AERO OPEN CIM

**Plataforma de prefactibilidad eólica de ciclo completo**  
José Antonio Rojas Escobar · CIIDIR Oaxaca, IPN · DoCITTE · 2026

---

## Descripción

AERO OPEN CIM es una plataforma web de análisis y prefactibilidad para proyectos de energía eólica, desarrollada como soporte metodológico para tesis doctoral en el IPN CIIDIR Oaxaca. Integra cinco módulos científicos desacoplados:

1. **Recurso Eólico** — Adquisición (MERRA-2/ERA5/CERTE), control de calidad, MCP, Weibull por sector, recurso espacial por Kriging ordinario.
2. **Diseño de Parque** — Generación y simulación de layouts con modelos de estela Jensen NOJ y Bastankhah-Porté-Agel (Gaussiano). Selección del campeón tecnológico por VAN máximo.
3. **Selección MCDA** — TOPSIS con pesos AHP (CR < 0.10), análisis de robustez SMAA (500 muestras Dirichlet), perfiles de pesos intercambiables.
4. **Evaluación Tecnoeconómica** — Cashflows a 25 años, LCOE nominal/real (Fisher), VAN, TIR, payback descontado, apalancamiento equity, cruce cronológico generación × PML/CENACE.
5. **Validación y Exportación** — Métricas de validación del recurso, benchmarking de pronóstico, exportación JSON/CSV/KML.

## Casos de uso

- Evaluación de múltiples sitios y tecnologías de turbina para un mismo predio
- Análisis de sensibilidad tecnoeconómica (AEP ±10%, WACC ±3 pp, inflación)
- Rankeo multicriterio documentado con expediente AHP exportable
- Cruce de generación con precios de mercado spot (México MEM/CENACE)
- Soporte a decisiones de prefactibilidad en el Istmo de Tehuantepec

## Instalación

```bash
git clone <repo-url> aero-open-cim
cd aero-open-cim
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                  # escucha en http://localhost:5051
```

### Variables de entorno (opcionales)

| Variable | Descripción | Default |
|---|---|---|
| `SECRET_KEY` | Clave de sesión Flask | valor de desarrollo (cambiar en producción) |
| `ALLOWED_ORIGINS` | Orígenes CORS permitidos (separados por coma) | `http://localhost:5051` |
| `API_KEY` | API key para autenticación (si se define, activa auth) | desactivado |
| `AERO_DATA_FOLDER` | Carpeta de datos meteorológicos | `./DATOS DE METEREOLOGICOS DE parques` |
| `AERO_TURBINES_FOLDER` | Carpeta de curvas de potencia | `./aerogeneradores` |
| `AERO_PML_FOLDER` | Carpeta de archivos PML/CENACE | `./mercado_pml` |

## Datos requeridos

- **Series de viento**: archivos `.txt`/`.csv` exportados de NASA MERRA-2 (GES DISC) o ECMWF ERA5, con columnas de velocidad U/V en múltiples alturas, temperatura y presión. Uno o más archivos por sitio.
- **Curvas de potencia**: archivos `.csv` con columnas `ws_ms,power_kw[,ct]` por modelo de turbina.
- **PML/CENACE** (opcional): precios marginales locales horarios en USD/kWh para el nodo del proyecto.

## Estructura del proyecto

```
aero-open-cim/
├── api.py               # Factory Flask + todos los endpoints REST
├── config.py            # Configuración y rutas (sobrescribibles por env vars)
├── run.py               # Punto de entrada
├── core/                # Motor científico (privado)
├── templates/
│   ├── index.html       # SPA principal
│   └── ingenieria.html  # Manual de ingeniería y fundamentos matemáticos
├── static/
│   ├── css/aero.css
│   └── js/aero.js
├── tests/               # Pytest
├── aerogeneradores/     # Curvas de potencia de ejemplo
├── mercado_pml/         # Datos PML (no incluidos en repo)
└── requirements.txt
```

## Citar este software

Si usas AERO OPEN CIM en tu investigación, cita usando el archivo `CITATION.cff` o:

> Velázquez Velázquez, A. & Rojas Escobar, J.A. (2026). *AERO OPEN CIM — Plataforma de prefactibilidad eólica* (v1.0.0). Universidad del Istmo. doi: [pendiente Zenodo]

## Licencia

MIT License — ver `LICENSE` para los términos completos.

El motor científico (`core/`) se distribuye en forma compilada y no se incluye en esta licencia abierta. Los derechos sobre los algoritmos científicos y la metodología son del autor.

## Autor

**Alejandra Velázquez Velázquez** *(autora principal)*  
Maestría en Ciencias en Energía Eólica  
Universidad del Istmo, Oaxaca, México

**José Antonio Rojas Escobar** *(coautor)*

## Agradecimientos

NASA GES DISC (MERRA-2) · ECMWF (ERA5) · CENACE MEM México ·  
Saaty (1987) AHP · Hwang & Yoon (1981) TOPSIS ·  
Bastankhah & Porté-Agel (2014) · Jensen (1983) · Matheron (1963) Kriging
