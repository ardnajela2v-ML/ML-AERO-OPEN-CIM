import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'windpro-platform-2025-secret')
    MAX_CONTENT_LENGTH = 256 * 1024 * 1024  # 256 MB upload limit
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    EXPORT_FOLDER = os.path.join(BASE_DIR, 'exports')
    MODEL_FOLDER = os.path.join(BASE_DIR, 'saved_models')

    # Scientific defaults
    DEFAULT_HORIZON_H = [1, 6, 24, 72]          # forecast horizons in hours
    DEFAULT_TEST_FRACTION = 0.2                   # chronological hold-out
    DEFAULT_CV_FOLDS = 5                          # time-series CV folds
    DEFAULT_LAG_HOURS = [1, 2, 3, 6, 12, 24, 48]
    DEFAULT_ROLLING_WINDOWS = [3, 6, 12, 24, 48]
    QUANTILE_LEVELS = [0.10, 0.25, 0.50, 0.75, 0.90]

    # Physical constants
    AIR_DENSITY_SEA_LEVEL = 1.225   # kg/m³ at 15°C, 1013.25 hPa
    GAS_CONSTANT_DRY_AIR = 287.05   # J/(kg·K)
    GRAVITY = 9.81                  # m/s²
    HELLMANN_EXPONENT_OPEN = 0.143  # open terrain (IEC roughness class 1)
    HELLMANN_EXPONENT_COASTAL = 0.11

    # External data folders (override via env vars if needed)
    DATA_FOLDER = os.environ.get(
        'WINDPRO_DATA_FOLDER',
        '/Users/ale/Documents/suso/WindProPlatform/DATOS DE METEREOLOGICOS DE parques')
    TURBINES_FOLDER = os.environ.get(
        'WINDPRO_TURBINES_FOLDER',
        '/Users/ale/Documents/suso/WindProPlatform/aerogeneradores')
    MARKET_DATA_FOLDER = os.environ.get(
        'WINDPRO_MARKET_FOLDER',
        '/Users/ale/Documents/suso/datos carga y precios pml compra y venta ')

    # Pronóstico a 25 años — escenarios de crecimiento histórico (CAGR)
    DEMAND_GROWTH_DEFAULT = 0.025   # 2.5 % anual (referencia PRODESEN)
    PML_GROWTH_DEFAULT    = 0.030   # 3.0 % anual nominal
    FORECAST_HORIZON_YEARS = 25
    FORECAST_HISTORY_YEARS = 10     # historia sintética para ajustar tendencias

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
        for d in [cls.UPLOAD_FOLDER, cls.EXPORT_FOLDER, cls.MODEL_FOLDER]:
            os.makedirs(d, exist_ok=True)

Config.ensure_dirs()
