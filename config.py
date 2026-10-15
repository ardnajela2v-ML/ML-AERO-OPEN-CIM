import os
import warnings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_DEFAULT_SECRET = 'aero-opencim-2025-secret'  # nosec B105

class Config:
    _raw_secret = os.environ.get('SECRET_KEY', '')
    if not _raw_secret:
        warnings.warn(
            'SECRET_KEY no está definida en el entorno. '
            'Se usa la clave de desarrollo por defecto — '
            'NUNCA usar en producción. '
            'Establece SECRET_KEY=<valor_aleatorio_largo> en las variables de entorno.',
            stacklevel=2,
        )
    SECRET_KEY = _raw_secret or _DEFAULT_SECRET
    MAX_CONTENT_LENGTH = 256 * 1024 * 1024  # 256 MB upload limit
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    EXPORT_FOLDER = os.path.join(BASE_DIR, 'exports')
    MCP_RUNS_FOLDER = os.environ.get(
        'AERO_MCP_RUNS_FOLDER',
        os.path.join(BASE_DIR, 'runs', 'mcp'))
    MCP_UPLOAD_FOLDER = os.environ.get(
        'AERO_MCP_UPLOAD_FOLDER',
        os.path.join(BASE_DIR, 'uploads', 'mcp'))
    KRIGING_RUNS_FOLDER = os.environ.get(
        'AERO_KRIGING_RUNS_FOLDER',
        os.path.join(BASE_DIR, 'runs', 'kriging'))
    CERTE_FOLDER = os.environ.get(
        'AERO_CERTE_FOLDER',
        os.path.abspath(os.path.join(BASE_DIR, '..', '..', 'copiaunoMCPSOFT', 'certe')))
    CACHE_FOLDER = os.environ.get(
        'AERO_CACHE_FOLDER',
        os.path.join(BASE_DIR, '.cache', 'aero'))

    # Physical constants
    AIR_DENSITY_SEA_LEVEL = 1.225   # kg/m³ at 15°C, 1013.25 hPa
    GAS_CONSTANT_DRY_AIR = 287.05   # J/(kg·K)
    GRAVITY = 9.81                  # m/s²
    HELLMANN_EXPONENT_OPEN = 0.143  # open terrain (IEC roughness class 1)
    HELLMANN_EXPONENT_COASTAL = 0.11

    # External data folders (override via env vars if needed)
    DATA_FOLDER = os.environ.get(
        'AERO_DATA_FOLDER',
        os.path.join(BASE_DIR, 'DATOS DE METEREOLOGICOS DE parques'))
    TURBINES_FOLDER = os.environ.get(
        'AERO_TURBINES_FOLDER',
        os.path.join(BASE_DIR, 'aerogeneradores'))
    # Carpeta legado (compatibilidad). Por defecto apunta a la carpeta original
    # externa para retro-compatibilidad; los flujos nuevos usan las carpetas
    # dedicadas por categoría definidas más abajo.
    MARKET_DATA_FOLDER = os.environ.get(
        'AERO_MARKET_FOLDER',
        os.path.join(BASE_DIR, 'mercado_pml'))

    # Carpetas dedicadas por categoría (dentro del proyecto). Cualquier archivo
    # .txt/.csv/.tsv/.dat que se deposite en estas carpetas se descubre
    # automáticamente como preset en su lista correspondiente.
    DEMAND_FOLDER = os.environ.get(
        'AERO_DEMAND_FOLDER',
        os.path.join(BASE_DIR, 'mercado_demanda'))
    PML_FOLDER = os.environ.get(
        'AERO_PML_FOLDER',
        os.path.join(BASE_DIR, 'mercado_pml'))
    FX_FOLDER = os.environ.get(
        'AERO_FX_FOLDER',
        os.path.join(BASE_DIR, 'mercado_fx'))
    INTEREST_FOLDER = os.environ.get(
        'AERO_INTEREST_FOLDER',
        os.path.join(BASE_DIR, 'mercado_tasas'))

    # CORS — lista de orígenes permitidos separada por comas en ALLOWED_ORIGINS.
    # En producción: ALLOWED_ORIGINS=https://windpro.ipn.mx,https://app.windpro.ipn.mx
    ALLOWED_ORIGINS = [
        o.strip()
        for o in os.environ.get('ALLOWED_ORIGINS', 'http://localhost:5051').split(',')
        if o.strip()
    ]

    # Auth opcional por API key. Si API_KEY está definida en el entorno, todos
    # los endpoints (excepto GET /, /api/version y assets estáticos) requieren
    # el header  X-API-Key: <valor>. Si no está definida, la auth está inactiva
    # (modo desarrollo / CI).
    API_KEY = os.environ.get('API_KEY', '')

    # Allowed upload extensions
    ALLOWED_EXTENSIONS = {'csv', 'txt', 'tsv', 'dat', 'xlsx'}

    # MERRA-2 standard variable names (from GES DISC exports)
    MERRA2_WIND_COLS = ['U50M', 'V50M', 'U10M', 'V10M', 'U2M', 'V2M',
                        'T2M', 'PS', 'T10M', 'DISPH']

    # ERA5 standard variable names
    ERA5_WIND_COLS = ['u10', 'v10', 'u100', 'v100', 't2m', 'sp', 'd2m']

    # Data quality thresholds
    QC_MAX_CONSECUTIVE_GAPS = 24    # flag if gap > 24 h
    QC_MAX_MISSING_PCT = 0.15       # flag if > 15% missing
    QC_WIND_MAX_PHYSICAL = 70.0     # m/s — physical upper bound
    QC_WIND_MIN_PHYSICAL = 0.0
    QC_MAX_SPIKE_SIGMA = 5.0        # z-score threshold for spike detection

    @classmethod
    def ensure_dirs(cls):
        for d in [cls.UPLOAD_FOLDER, cls.EXPORT_FOLDER, cls.CACHE_FOLDER,
                  cls.MCP_RUNS_FOLDER, cls.MCP_UPLOAD_FOLDER, cls.KRIGING_RUNS_FOLDER]:
            os.makedirs(d, exist_ok=True)

Config.ensure_dirs()
