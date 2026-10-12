"""
AERO by OPEN CIM — Wind-to-Power Lab & Energy Simulation.
Physical + ML hybrid engine.  All assumptions documented and configurable.
Techno-economic analysis based on PhD research (ajusteMultiplesArchivosConMediaMovil.m).
"""
import os
import re
import warnings
from typing import Optional
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from scipy import stats
from config import Config
from core.project_economics import (project_cashflows, irr_bisection, DEFAULT_DEGRADATION_PCT,
                                    DEFAULT_DECOMMISSIONING_PCT)
from core.cost_model import CostModel, BOS_COMPONENTS   
warnings.filterwarnings('ignore')

# ─── AIR DENSITY ──────────────────────────────────────────────────────────────

def air_density(temperature_c=15.0, pressure_pa=101325.0, humidity_pct=0.0):
    """
    Ideal gas law for moist air.  Returns rho in kg/m³.
    Acepta escalares o arrays numpy (ej. series horarias de MERRA-2/ERA5).
    humidity_pct: relative humidity 0-100.
    """
    T_K = np.asarray(temperature_c, dtype=float) + 273.15
    e_sat = 6.112 * np.exp(17.67 * np.asarray(temperature_c, dtype=float) /
                           (np.asarray(temperature_c, dtype=float) + 243.5)) * 100  # Pa
    e = np.asarray(humidity_pct, dtype=float) / 100.0 * e_sat
    rho = (np.asarray(pressure_pa, dtype=float) - 0.378 * e) / (Config.GAS_CONSTANT_DRY_AIR * T_K)
    return rho


def density_from_altitude(altitude_m, temperature_c=15.0):
    """Standard atmosphere approximation."""
    T_K = temperature_c + 273.15
    P = 101325 * (1 - 2.2557e-5 * altitude_m) ** 5.2559
    rho = P / (Config.GAS_CONSTANT_DRY_AIR * T_K)
    return float(rho)


# ─── WIND PROFILE EXTRAPOLATION ───────────────────────────────────────────────

def extrapolate_wind_hellmann(v_ref, z_ref, z_target, alpha=None):
    """
    Power-law (Hellmann) extrapolation.
    α defaults to open terrain (0.143) if not provided.
    """
    if alpha is None:
        alpha = Config.HELLMANN_EXPONENT_OPEN
    return v_ref * (z_target / z_ref) ** alpha
def hellmann_alpha_by_zone(zone: str = 'default') -> float:
    """
    Coeficiente de Hellmann calibrado por zona geográfica de Oaxaca.
    Fuente: Hernández-Escobedo et al. (2014), datos MERRA-2 CIIDIR.
      istmo        : terreno plano costero, alta velocidad  α ≈ 0.11
      costa        : franja costera con vegetación baja     α ≈ 0.14
      valles       : valles centrales, terreno mixto        α ≈ 0.20
      sierra_norte : terreno montañoso, alta rugosidad      α ≈ 0.25
    """
    _ALPHAS = {
        'istmo':        0.11,
        'costa':        0.14,
        'valles':       0.20,
        'sierra_norte': 0.25,
        'sonora':       0.12,
        'chihuahua':    0.13,
        'semiarido':    0.13,
        'desierto':     0.11,
        'default':      0.143,
    }
    return _ALPHAS.get(zone.lower(), _ALPHAS['default'])


def compute_site_cp_fit(ws_series, curve_ws, curve_power_kw,
                        rotor_diameter_m, rho=1.225, dt_hours=1.0,
                        max_w=25.0, near_cp_fraction=0.95):
    """Measure turbine/resource fit over every valid record of a wind series.

    Hour counts are annualised to 8,760 h so multi-year histories remain
    comparable. Raw totals and data coverage are returned separately.
    """
    raw_ws = np.asarray(ws_series, dtype=float)
    valid_ws = np.isfinite(raw_ws) & (raw_ws >= 0.0)
    ws = raw_ws[valid_ws]
    curve_v = np.asarray(curve_ws, dtype=float)
    curve_p = np.asarray(curve_power_kw, dtype=float)
    valid_curve = np.isfinite(curve_v) & np.isfinite(curve_p)
    curve_v = curve_v[valid_curve]
    curve_p = np.maximum(curve_p[valid_curve], 0.0)
    if not len(ws) or len(curve_v) < 2 or float(rotor_diameter_m) <= 0:
        return {
            'cp_at_site_mean': 0.0, 'cp_max_turbine': 0.0,
            'ws_at_cp_max_ms': 0.0, 'cpmax_speed_mismatch_ms': None,
            'hours_near_cpmax': 0.0, 'near_cpmax_pct': 0.0,
            'cp_effective_full_series': 0.0,
            'cp_effective_operating': 0.0,
            'cp_max_reached_at_site': 0.0,
            'cp_peak_access_ratio': 0.0,
            'resource_operational_availability_pct': 0.0,
            'resource_operational_hours': 0.0,
            'resource_non_generation_hours': 0.0,
            'hours_at_or_above_90pct_rated': 0.0,
            'at_or_above_90pct_rated_pct': 0.0,
            'site_series_samples_total': int(raw_ws.size),
            'site_series_samples_valid': int(valid_ws.sum()),
            'site_series_samples_missing': int(raw_ws.size - valid_ws.sum()),
            'site_series_valid_hours_observed_total': 0.0,
            'site_series_annualization_factor': 0.0,
            'site_series_dt_hours': float(dt_hours),
            'near_cp_fraction': float(near_cp_fraction),
        }
    order = np.argsort(curve_v)
    curve_v, curve_p = curve_v[order], curve_p[order]
    area = np.pi * (float(rotor_diameter_m) / 2.0) ** 2
    # The catalogue curves are specified at the standard reference density.
    # Applying the platform's linear density correction to power and using the
    # site's density in 0.5*rho*A*v^3 cancels exactly, so Cp(v) is evaluated at
    # the same 1.225 kg/m3 reference without distorting turbine comparisons.
    density = 1.225

    def cp_for_speed(speed):
        speed = np.asarray(speed, dtype=float)
        power_kw = np.interp(speed, curve_v, curve_p, left=0.0, right=0.0)
        power_kw = np.where(speed <= float(max_w), power_kw, 0.0)
        kinetic_kw = 0.5 * density * area * np.power(np.maximum(speed, 0.01), 3) / 1000.0
        return np.clip(np.divide(
            power_kw, kinetic_kw, out=np.zeros_like(power_kw), where=kinetic_kw > 0,
        ), 0.0, 16.0 / 27.0)

    positive_curve = curve_v[curve_p > 0.0]
    search_low = max(0.5, float(positive_curve.min()) if len(positive_curve) else 0.5)
    search_high = min(float(max_w), float(curve_v.max()))
    search_ws = np.arange(search_low, search_high + 0.025, 0.05)
    search_cp = cp_for_speed(search_ws)
    cp_max = float(search_cp.max()) if len(search_cp) else 0.0
    ws_at_cpmax = float(search_ws[int(np.argmax(search_cp))]) if cp_max > 0 else 0.0
    site_mean = float(np.mean(ws))
    cp_at_mean = float(cp_for_speed(np.asarray([site_mean]))[0])
    hourly_cp = cp_for_speed(ws)
    hourly_power = np.interp(ws, curve_v, curve_p, left=0.0, right=0.0)
    hourly_power = np.where(ws <= float(max_w), hourly_power, 0.0)
    operating_mask = hourly_power > 0.0
    rated_power_kw = float(curve_p.max()) if len(curve_p) else 0.0
    near_rated_mask = (
        hourly_power >= 0.90 * rated_power_kw
        if rated_power_kw > 0 else np.zeros(len(ws), dtype=bool)
    )
    near_mask = (hourly_cp >= cp_max * float(near_cp_fraction)
                 if cp_max > 0 else np.zeros(len(ws), dtype=bool))
    sample_hours = float(dt_hours)
    total_hours = float(len(ws) * sample_hours)
    annualization = 8760.0 / total_hours if total_hours > 0 else 0.0
    hours_near_raw = float(np.count_nonzero(near_mask) * sample_hours)
    operating_hours_raw = float(np.count_nonzero(operating_mask) * sample_hours)
    near_rated_hours_raw = float(np.count_nonzero(near_rated_mask) * sample_hours)
    non_generation_hours_raw = max(0.0, total_hours - operating_hours_raw)
    cut_in = float(positive_curve.min()) if len(positive_curve) else 0.0
    below_cut_in_raw = float(np.count_nonzero(ws < cut_in) * sample_hours)
    above_cut_out_raw = float(np.count_nonzero(ws > float(max_w)) * sample_hours)
    cp_max_site = float(hourly_cp.max()) if len(hourly_cp) else 0.0
    return {
        'cp_at_site_mean': cp_at_mean,
        'cp_max_turbine': cp_max,
        'cp_max_reached_at_site': cp_max_site,
        'cp_peak_access_ratio': cp_max_site / cp_max if cp_max > 0 else 0.0,
        'cp_effective_full_series': float(np.mean(hourly_cp)),
        'cp_effective_operating': (
            float(np.mean(hourly_cp[operating_mask])) if np.any(operating_mask) else 0.0
        ),
        'ws_at_cp_max_ms': ws_at_cpmax,
        'cpmax_speed_mismatch_ms': abs(site_mean - ws_at_cpmax),
        'hours_near_cpmax': hours_near_raw * annualization,
        'hours_near_cpmax_observed_total': hours_near_raw,
        'near_cpmax_pct': hours_near_raw / total_hours * 100.0 if total_hours > 0 else 0.0,
        'resource_operational_availability_pct': (
            operating_hours_raw / total_hours * 100.0 if total_hours > 0 else 0.0
        ),
        'resource_operational_hours': operating_hours_raw * annualization,
        'resource_non_generation_hours': non_generation_hours_raw * annualization,
        'resource_non_generation_hours_observed_total': non_generation_hours_raw,
        'hours_at_or_above_90pct_rated': near_rated_hours_raw * annualization,
        'hours_at_or_above_90pct_rated_observed_total': near_rated_hours_raw,
        'at_or_above_90pct_rated_pct': (
            near_rated_hours_raw / total_hours * 100.0 if total_hours > 0 else 0.0
        ),
        'hours_below_cut_in_observed_total': below_cut_in_raw,
        'hours_above_cut_out_observed_total': above_cut_out_raw,
        'site_series_samples_total': int(raw_ws.size),
        'site_series_samples_valid': int(valid_ws.sum()),
        'site_series_samples_missing': int(raw_ws.size - valid_ws.sum()),
        'site_series_valid_hours_observed_total': total_hours,
        'site_series_annualization_factor': annualization,
        'site_series_dt_hours': sample_hours,
        'near_cp_fraction': float(near_cp_fraction),
        'site_mean_speed_ms': site_mean,
        'cp_reference_density_kg_m3': density,
    }


def extrapolate_wind_log(v_ref, z_ref, z_target, z0=0.03):
    """
    Logarithmic wind profile.
    z0: roughness length in metres. Default 0.03 (campo abierto) para
    consistencia con core.kriging.Z0 y core.wind_stats — pasar z0 explícito
    para otros supuestos (p. ej. 0.001 mar abierto/costa, 0.1-0.5 Sierra
    Norte Oaxaca).
    """
    if z0 <= 0:
        z0 = 1e-4
    num   = np.log(z_target / z0)
    denom = np.log(z_ref / z0)
    if denom == 0:
        return v_ref
    return v_ref * num / denom


# ─── POWER CURVE ──────────────────────────────────────────────────────────────

def _generic_sound_power_lwa_db(rated_kw: float) -> float:
    """Estimación genérica del nivel de potencia sonora (Lwa, dB(A)) por
    clase de potencia, usada solo cuando el archivo de turbina no declara un
    valor explícito (sufijo `_lw<dB>` en el nombre de archivo). Valores
    medios aproximados reportados en fichas técnicas de fabricantes
    ensayadas bajo IEC 61400-11:2012 (no son un valor certificado de ningún
    modelo concreto -- ver PowerCurve.sound_power_source para trazabilidad).
    """
    if rated_kw < 1000:
        return 97.0
    if rated_kw < 2500:
        return 103.0
    if rated_kw < 4000:
        return 105.0
    return 107.0


class PowerCurve:
    """
    Represents a wind turbine power curve with physical constraints.
    Supports: CSV load, manual point entry, interpolation, density correction.
    """

    # Built-in reference curves (ws_ms, power_kw)
    BUILTIN_CURVES = {
        'SG_8.0_167': {
            'rated_power_kw': 8000,
            'cut_in': 3.0, 'rated_speed': 13.0, 'cut_out': 25.0,
            'hub_height_m': 110, 'rotor_diameter_m': 167,
            'curve': [
                (0,0),(1,0),(2,0),(3,0),(3.5,80),(4,185),(4.5,320),(5,500),
                (5.5,740),(6,1040),(6.5,1420),(7,1870),(7.5,2400),(8,3030),
                (8.5,3750),(9,4570),(9.5,5430),(10,6200),(10.5,6850),(11,7340),
                (11.5,7680),(12,7900),(12.5,7980),(13,8000),(14,8000),(15,8000),
                (16,8000),(17,8000),(18,8000),(19,8000),(20,8000),(21,8000),
                (22,8000),(23,8000),(24,8000),(25,8000),(25.1,0)
            ]
        },
        'V150_4.5': {
            'rated_power_kw': 4500,
            'cut_in': 3.0, 'rated_speed': 12.0, 'cut_out': 22.5,
            'hub_height_m': 105, 'rotor_diameter_m': 150,
            'curve': [
                (0,0),(1,0),(2,0),(3,50),(4,200),(5,430),(6,790),(7,1250),
                (8,1840),(9,2540),(10,3230),(11,3850),(12,4400),(12.5,4480),
                (13,4500),(14,4500),(15,4500),(16,4500),(17,4500),(18,4500),
                (19,4500),(20,4500),(21,4500),(22,4500),(22.5,4500),(22.6,0)
            ]
        },
        'E126_7.58': {
            'rated_power_kw': 7580,
            'cut_in': 2.0, 'rated_speed': 12.0, 'cut_out': 28.0,
            'hub_height_m': 135, 'rotor_diameter_m': 126,
            'curve': [
                (0,0),(1,0),(2,30),(3,150),(4,380),(5,760),(6,1300),(7,1980),
                (8,2850),(9,3820),(10,4900),(11,6050),(12,7300),(12.5,7500),
                (13,7580),(14,7580),(15,7580),(16,7580),(17,7580),(18,7580),
                (20,7580),(22,7580),(24,7580),(26,7580),(28,7580),(28.1,0)
            ]
        },
    }

    def __init__(self, name='custom', rated_power_kw=2000.0,
                 cut_in=3.0, rated_speed=12.0, cut_out=25.0,
                 hub_height_m=80.0, rotor_diameter_m=80.0,
                 curve_points=None):
        self.name = name
        self.rated_power_kw = float(rated_power_kw)
        self.cut_in = float(cut_in)
        self.rated_speed = float(rated_speed)
        self.cut_out = float(cut_out)
        self.hub_height_m = float(hub_height_m)
        self.rotor_diameter_m = float(rotor_diameter_m)
        self.rotor_area_m2 = np.pi * (rotor_diameter_m / 2) ** 2

        if curve_points is None:
            curve_points = self._default_curve()
        self._raw_points = curve_points
        self._build_interpolator(curve_points)
        # Nivel de potencia sonora (Lwa, dB(A)) para el módulo de ruido
        # (core/acoustic_noise.py) -- None hasta que from_file() lo declare
        # (explícito en el nombre de archivo) o lo estime genéricamente.
        self.sound_power_lwa_db = None
        self.sound_power_source = None

    @classmethod
    def from_builtin(cls, model_key):
        if model_key not in cls.BUILTIN_CURVES:
            raise ValueError(f"Unknown turbine model '{model_key}'. "
                             f"Available: {list(cls.BUILTIN_CURVES)}")
        spec = cls.BUILTIN_CURVES[model_key]
        return cls(name=model_key,
                   rated_power_kw=spec['rated_power_kw'],
                   cut_in=spec['cut_in'],
                   rated_speed=spec['rated_speed'],
                   cut_out=spec['cut_out'],
                   hub_height_m=spec['hub_height_m'],
                   rotor_diameter_m=spec['rotor_diameter_m'],
                   curve_points=spec['curve'])

    @classmethod
    def from_csv_text(cls, csv_text, **kwargs):
        """Parse a CSV/TSV with columns: wind_speed, power_kw."""
        import io
        df = pd.read_csv(io.StringIO(csv_text), sep=None, engine='python',
                         names=['ws', 'power'], header=None, skiprows=1,
                         on_bad_lines='skip')
        pts = [(float(r.ws), float(r.power)) for _, r in df.iterrows()
               if pd.notna(r.ws) and pd.notna(r.power)]
        return cls(curve_points=pts, **kwargs)

    @classmethod
    def from_file(cls, filepath):
        """
        Load a turbine power curve from aerogeneradores folder CSV.
        File format: no header, two columns → wind_speed_m/s, power_kw
        Filename convention: {brand}{id}_p{rated_kW}_r{rotor_radius_m}[_h{hub_m}].csv
          _p  = rated power in kW
          _r  = rotor RADIUS in metres (diameter = 2 × r)
          _h  = hub height in metres (optional, overrides heuristic when present)
        """
        import os, re
        fname = os.path.basename(filepath)
        stem  = fname.replace('.csv', '')

        # parse rated power, rotor radius and optional explicit hub height
        p_match = re.search(r'_p(\d+)',       stem, re.IGNORECASE)
        r_match = re.search(r'_r([\d.]+)',    stem, re.IGNORECASE)
        h_match = re.search(r'_h(\d+(?:\.\d+)?)', stem, re.IGNORECASE)
        lw_match = re.search(r'_lw([\d.]+)',  stem, re.IGNORECASE)
        rated_kw  = float(p_match.group(1)) if p_match else None
        rotor_r_m = float(r_match.group(1)) if r_match else None
        rotor_m   = (rotor_r_m * 2.0) if rotor_r_m else None  # diameter
        hub_explicit = float(h_match.group(1)) if h_match else None

        # pretty name: capitalise brand prefix
        name_clean = re.sub(r'_p\d+.*', '', stem)   # e.g. "vestas30"
        name_clean = name_clean.capitalize()          # "Vestas30"

        # read curve — format: ws,power with no header
        df_c = pd.read_csv(filepath, header=None, names=['ws', 'power_kw'])
        df_c = df_c.dropna()
        pts  = [(float(r['ws']), float(r['power_kw'])) for _, r in df_c.iterrows()]

        # derive operating limits from curve data
        cut_in   = next((ws for ws, pw in pts if pw > 1), 3.0)
        rated_kw = rated_kw or max(pw for _, pw in pts)
        rated_spd = next((ws for ws, pw in pts if pw >= rated_kw * 0.98), 12.0)
        cut_out   = max(ws for ws, pw in pts if pw > 0)

        # Hub height: use explicit _h suffix when present, else estimate from rotor diameter.
        # Heuristic updated from real machine datasheets (ranges are approximate mid-values):
        #   rotor < 60 m  → small turbines (Acciona AW-70, etc.)         ≈ 55 m
        #   rotor 60-89 m → G80 / V80 / E70 class (2 MW)                ≈ 78 m
        #   rotor 90-129 m→ G87 / V90 / Alstom / Clipper / SWT-3.6 class≈ 80 m
        #   rotor 130-169 m→ SWT-4.0-128 / V100 / E-101                  ≈ 100 m
        #   rotor ≥ 170 m → 7–8 MW+ modern machines                      ≈ 120 m
        if hub_explicit is not None:
            hub_h = hub_explicit
        elif rotor_m:
            if   rotor_m < 60:   hub_h = 55.0
            elif rotor_m < 90:   hub_h = 78.0   # G80 (80m), V80 (80m), E70 (70m)
            elif rotor_m < 130:  hub_h = 80.0   # G87, V90, Alstom 3MW, Clipper 2.5MW
            elif rotor_m < 170:  hub_h = 100.0  # SWT-4.0-128, V100, V110, E-101
            else:                hub_h = 120.0  # 7-8 MW+
        else:
            hub_h = 80.0

        turbine = cls(name=name_clean,
                     rated_power_kw=rated_kw,
                     cut_in=cut_in, rated_speed=rated_spd, cut_out=cut_out,
                     hub_height_m=hub_h,
                     rotor_diameter_m=rotor_m or 80.0,
                     curve_points=pts)
        if lw_match:
            turbine.sound_power_lwa_db = float(lw_match.group(1))
            turbine.sound_power_source = 'declared_in_filename'
        else:
            turbine.sound_power_lwa_db = _generic_sound_power_lwa_db(rated_kw)
            turbine.sound_power_source = 'generic_class_estimate_iec61400_11'
        return turbine

    def _default_curve(self):
        """Simple generic curve based on rated power and speed."""
        ws = np.linspace(0, self.cut_out + 1, 60)
        p = np.zeros_like(ws)
        for i, v in enumerate(ws):
            if v < self.cut_in or v > self.cut_out:
                p[i] = 0
            elif v >= self.rated_speed:
                p[i] = self.rated_power_kw
            else:
                p[i] = self.rated_power_kw * ((v - self.cut_in) /
                       (self.rated_speed - self.cut_in)) ** 3
        return list(zip(ws.tolist(), p.tolist()))

    def _build_interpolator(self, pts):
        pts_sorted = sorted(pts, key=lambda x: x[0])
        ws_arr = np.array([p[0] for p in pts_sorted])
        pw_arr = np.array([p[1] for p in pts_sorted])
        self._interp = interp1d(ws_arr, pw_arr, kind='linear',
                                bounds_error=False, fill_value=(0, 0))
        self._ws_arr = ws_arr
        self._pw_arr = pw_arr

    def power_kw(self, wind_speed_ms):
        """Return power in kW for scalar or array of wind speeds."""
        ws = np.asarray(wind_speed_ms, dtype=float)
        p = self._interp(ws)
        p = np.where(ws < self.cut_in, 0.0, p)
        p = np.where(ws > self.cut_out, 0.0, p)
        p = np.maximum(p, 0.0)
        p = np.minimum(p, self.rated_power_kw)
        return p

    def power_kw_density_corrected(self, wind_speed_ms, rho, rho_ref=1.225):
        """Apply air density correction: P_corr = P_curve * (ρ/ρ_ref)."""
        p = self.power_kw(wind_speed_ms)
        factor = np.asarray(rho) / rho_ref
        return np.minimum(p * factor, self.rated_power_kw)

    def cp_curve(self):
        """Compute Cp = P / (0.5 * rho * A * v³) for plotting."""
        ws = self._ws_arr[self._ws_arr > 0.5]
        p_w = self._interp(ws) * 1000  # W
        rho = 1.225
        kinetic = 0.5 * rho * self.rotor_area_m2 * ws**3
        cp = np.where(kinetic > 0, p_w / kinetic, 0)
        cp = np.minimum(cp, 0.593)  # Betz limit
        return ws.tolist(), cp.tolist()

    def to_dict(self):
        return {
            'name': self.name,
            'rated_power_kw': self.rated_power_kw,
            'cut_in': self.cut_in,
            'rated_speed': self.rated_speed,
            'cut_out': self.cut_out,
            'hub_height_m': self.hub_height_m,
            'rotor_diameter_m': self.rotor_diameter_m,
            'rotor_area_m2': round(self.rotor_area_m2, 1),
            'curve_ws': self._ws_arr.tolist(),
            'curve_pw': self._pw_arr.tolist(),
        }


# ─── WIND-TO-POWER ENGINE ─────────────────────────────────────────────────────

class WindToPowerEngine:
    """
    Converts a wind time series to power/energy using physical simulation,
    optionally with density correction and hub height extrapolation.
    """

    def __init__(self, turbine: PowerCurve, n_turbines=1,
                 availability=1.0, electrical_losses=0.0,
                 wake_losses=0.0, curtailment_losses=0.0):
        self.turbine = turbine
        self.n_turbines = int(n_turbines)
        self.availability = float(availability)
        self.electrical_losses = float(electrical_losses)
        self.wake_losses = float(wake_losses)
        self.curtailment_losses = float(curtailment_losses)

    def run(self, df, ws_col='wind_speed', ws_height_m=50.0,
            temp_col=None, pressure_col=None, altitude_m=None,
            extrapolation='hellmann', roughness_z0=0.03, hellmann_alpha=None):
        """
        Main simulation method.  Returns enriched DataFrame + summary dict.
        """
        df = df.copy()
        ws = df[ws_col].fillna(df[ws_col].median()).values

        # ── hub height extrapolation ──
        if ws_height_m != self.turbine.hub_height_m:
            if extrapolation == 'log':
                ws_hub = extrapolate_wind_log(ws, ws_height_m,
                                              self.turbine.hub_height_m,
                                              z0=roughness_z0)
            else:
                alpha = hellmann_alpha or Config.HELLMANN_EXPONENT_OPEN
                ws_hub = extrapolate_wind_hellmann(ws, ws_height_m,
                                                   self.turbine.hub_height_m,
                                                   alpha=alpha)
        else:
            ws_hub = ws.copy()

        df['ws_hub'] = ws_hub

        # ── air density ──
        if temp_col and temp_col in df.columns and pressure_col and pressure_col in df.columns:
            rho_arr = air_density(df[temp_col].values, df[pressure_col].values)
        elif altitude_m is not None:
            t = df[temp_col].values if temp_col and temp_col in df.columns else 15.0
            rho_arr = density_from_altitude(altitude_m, t)
        else:
            rho_arr = Config.AIR_DENSITY_SEA_LEVEL

        df['air_density'] = rho_arr

        # ── power conversion ──
        if np.isscalar(rho_arr):
            p_kw = self.turbine.power_kw_density_corrected(ws_hub, rho_arr)
        else:
            p_kw = self.turbine.power_kw_density_corrected(ws_hub, rho_arr)

        # ── losses ──
        # gross power (n turbines, no losses)
        p_gross_kw = p_kw * self.n_turbines

        # net power after losses
        loss_factor = (self.availability *
                       (1 - self.electrical_losses) *
                       (1 - self.wake_losses) *
                       (1 - self.curtailment_losses))
        p_net_kw = p_gross_kw * loss_factor

        df['power_gross_kw'] = p_gross_kw
        df['power_net_kw']   = p_net_kw

        # ── energy per interval ──
        if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1:
            dt_h = (df.index.to_series().diff().mode()[0].total_seconds() / 3600)
        else:
            dt_h = 1.0

        df['energy_gross_kwh'] = df['power_gross_kw'] * dt_h
        df['energy_net_kwh']   = df['power_net_kw'] * dt_h

        # ── zone classification ──
        df['zone'] = 'below_cut_in'
        df.loc[df['ws_hub'] >= self.turbine.cut_in, 'zone'] = 'below_rated'
        df.loc[df['ws_hub'] >= self.turbine.rated_speed, 'zone'] = 'rated'
        df.loc[df['ws_hub'] > self.turbine.cut_out, 'zone'] = 'cut_out'

        summary = self._compute_summary(df, dt_h)
        return df, summary

    def _compute_summary(self, df, dt_h):
        n_total = len(df)
        e_gross = df['energy_gross_kwh'].sum()
        e_net   = df['energy_net_kwh'].sum()
        hours_total = n_total * dt_h
        cap_kw = self.turbine.rated_power_kw * self.n_turbines
        cf = e_gross / (cap_kw * hours_total) if cap_kw * hours_total > 0 else 0
        flh = e_gross / cap_kw if cap_kw > 0 else 0

        zone_counts = df['zone'].value_counts().to_dict()

        return {
            'n_hours': round(hours_total, 1),
            'energy_gross_mwh': round(e_gross / 1000, 2),
            'energy_net_mwh':   round(e_net   / 1000, 2),
            'capacity_factor_pct': round(cf * 100, 2),
            'full_load_hours': round(flh, 1),
            'rated_power_total_kw': cap_kw,
            'mean_power_kw': round(float(df['power_net_kw'].mean()), 2),
            'zone_hours': {z: round(v * dt_h, 1) for z, v in zone_counts.items()},
            'loss_factor': round(1 - (self.availability
                                      * (1 - self.electrical_losses)
                                      * (1 - self.wake_losses)
                                      * (1 - self.curtailment_losses)), 4),
        }


# ─── AEP CALCULATOR ───────────────────────────────────────────────────────────

class AEPCalculator:
    """
    Annual Energy Production with P-values, Weibull-based and time-series-based.
    """

    def compute_from_timeseries(self, df, energy_col='energy_net_kwh',
                                 n_years_available=None):
        """
        Compute AEP statistics from available time series.
        P50/P75/P90 via bootstrapped annual totals.
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DatetimeIndex required.")

        # Annual totals. Partial boundary years are retained for audit, but
        # excluded from long-term percentiles when at least two complete years
        # are available; otherwise they would bias P75/P90 downward.
        energy = pd.to_numeric(df[energy_col], errors='coerce')
        annual = energy.resample('YE').sum(min_count=1) / 1000  # MWh
        annual_hours = energy.resample('YE').count()
        expected_hours = pd.Series(
            [8784 if timestamp.is_leap_year else 8760 for timestamp in annual.index],
            index=annual.index,
            dtype=float,
        )
        coverage = annual_hours / expected_hours
        complete_annual = annual.loc[coverage >= 0.90].dropna()
        percentile_sample = complete_annual if len(complete_annual) >= 2 else annual.dropna()

        if len(annual) < 2:
            # Only one partial year: scale to full year
            total_h = (df.index[-1] - df.index[0]).total_seconds() / 3600
            energy_mwh = df[energy_col].sum() / 1000
            aep_p50 = energy_mwh * (8760 / total_h) if total_h > 0 else energy_mwh

            return {
                'aep_p50_mwh': round(aep_p50, 1),
                'aep_p75_mwh': round(aep_p50 * 0.95, 1),
                'aep_p90_mwh': round(aep_p50 * 0.90, 1),
                'n_years': round(total_h / 8760, 2),
                'note': 'Scaled from partial record; P75/P90 estimated via ±5/10% rule.',
            }

        # Thesis convention: P50=median annual AEP, P75=25th percentile and
        # P90=10th percentile of the annual distribution.
        annual_vals = percentile_sample.values
        annual_rows = [{
            'year': int(timestamp.year),
            'aep_mwh': round(float(value), 1),
            'hours': int(annual_hours.loc[timestamp]),
            'coverage_pct': round(float(coverage.loc[timestamp] * 100.0), 2),
            'used_for_percentiles': bool(timestamp in percentile_sample.index),
        } for timestamp, value in annual.dropna().items()]

        return {
            'aep_p50_mwh':  round(float(np.percentile(annual_vals, 50)), 1),
            'aep_p75_mwh':  round(float(np.percentile(annual_vals, 25)), 1),
            'aep_p90_mwh':  round(float(np.percentile(annual_vals, 10)), 1),
            'aep_p10_mwh':  round(float(np.percentile(annual_vals, 90)), 1),
            'aep_mean_mwh': round(float(np.mean(annual_vals)), 1),
            'aep_std_mwh':  round(float(np.std(annual_vals)), 1),
            'n_years': len(annual_vals),
            'annual_values_mwh': [round(v, 1) for v in annual_vals.tolist()],
            'annual': annual_rows,
            'percentile_definition': 'P50=percentil 50; P75=percentil 25; P90=percentil 10',
        }

    def compute_from_weibull(self, turbine: PowerCurve, weibull_k, weibull_lambda,
                              rho=1.225, n_turbines=1, losses=0.0):
        """
        Analytical AEP from Weibull wind distribution and power curve.
        """
        ws = np.linspace(0, turbine.cut_out + 2, 1000)
        # Weibull PDF
        pdf = stats.weibull_min.pdf(ws, c=weibull_k, scale=weibull_lambda)
        p_kw = turbine.power_kw_density_corrected(ws, rho)
        # Expected power = integral(P(v) * f(v) dv)
        dv = ws[1] - ws[0]
        expected_power_kw = np.sum(p_kw * pdf) * dv * n_turbines
        aep_gross_mwh = expected_power_kw * 8760 / 1000
        aep_net_mwh = aep_gross_mwh * (1 - losses)
        cap_kw = turbine.rated_power_kw * n_turbines
        cf = expected_power_kw / cap_kw if cap_kw > 0 else 0

        return {
            'aep_gross_mwh': round(aep_gross_mwh, 1),
            'aep_net_mwh':   round(aep_net_mwh, 1),
            'capacity_factor_pct': round(cf * 100, 2),
            'full_load_hours': round(aep_gross_mwh * 1000 / cap_kw, 0) if cap_kw > 0 else 0,
            'expected_power_kw': round(expected_power_kw, 1),
            'method': 'weibull_analytical',
        }

    def monthly_distribution(self, df, energy_col='energy_net_kwh'):
        """Mean monthly energy in MWh."""
        monthly = df[energy_col].resample('ME').sum() / 1000
        by_month = monthly.groupby(monthly.index.month).mean()
        return {
            'months': list(range(1, 13)),
            'labels': ['Ene','Feb','Mar','Abr','May','Jun',
                       'Jul','Ago','Sep','Oct','Nov','Dic'],
            'energy_mwh': [round(float(by_month.get(m, 0)), 2) for m in range(1, 13)],
        }

    def sensitivity_table(self, aep_p50, items=None):
        """
        Sensitivity of AEP to key loss assumptions (±ΔP50).
        """
        if items is None:
            items = [
                ('Wind speed +1 m/s', +0.12),
                ('Wind speed -1 m/s', -0.12),
                ('Hub height +10 m', +0.04),
                ('Hub height -10 m', -0.04),
                ('Density +5%', +0.05),
                ('Density -5%', -0.05),
                ('Availability -2%', -0.02),
                ('Wake losses +2%', -0.02),
                ('Electrical losses +1%', -0.01),
            ]
        rows = []
        for label, delta in items:
            rows.append({
                'assumption': label,
                'delta_pct': round(delta * 100, 1),
                'aep_low_mwh':  round(aep_p50 * (1 + min(delta, 0)), 1),
                'aep_high_mwh': round(aep_p50 * (1 + max(delta, 0)), 1),
            })
        return rows


# ─── TURBINE COMPARATOR ───────────────────────────────────────────────────────

class TurbineComparator:
    """Compare multiple turbines at the same site."""

    def compare(self, turbines: list, ws_series: np.ndarray,
                 rho=1.225, dt_h=1.0, losses=0.0):
        """
        turbines: list of PowerCurve objects.
        Returns ranked comparison.
        """
        results = []
        for t in turbines:
            p_kw = t.power_kw_density_corrected(ws_series, rho)
            e_kwh = p_kw * dt_h
            e_total_mwh = e_kwh.sum() / 1000
            hours = len(ws_series) * dt_h
            cap = t.rated_power_kw
            cf = e_total_mwh * 1000 / (cap * hours) if cap * hours > 0 else 0
            flh = e_total_mwh * 1000 / cap if cap > 0 else 0

            # fraction of hours in each zone
            frac_below_ci = (ws_series < t.cut_in).mean()
            frac_rated    = (ws_series >= t.rated_speed).mean()
            frac_cut_out  = (ws_series > t.cut_out).mean()

            results.append({
                'name': t.name,
                'rated_power_kw': t.rated_power_kw,
                'rotor_diameter_m': t.rotor_diameter_m,
                'hub_height_m': t.hub_height_m,
                'aep_gross_mwh': round(e_total_mwh * (8760 / hours)
                                       if hours > 0 else 0, 1),
                'capacity_factor_pct': round(cf * 100, 2),
                'full_load_hours': round(flh, 0),
                'mean_power_kw': round(float(p_kw.mean()), 1),
                'frac_below_cut_in': round(frac_below_ci * 100, 1),
                'frac_at_rated': round(frac_rated * 100, 1),
                'frac_above_cut_out': round(frac_cut_out * 100, 1),
            })

        results.sort(key=lambda x: -x['capacity_factor_pct'])
        for i, r in enumerate(results):
            r['rank'] = i + 1
        return results


# ─── TECHNO-ECONOMIC ANALYZER ─────────────────────────────────────────────────

class TechnoEconomicAnalyzer:
    """
    Techno-economic analysis for wind farm projects.
    Methodology from PhD research (ajusteMultiplesArchivosConMediaMovil.m).

    Cost model (modelo central WindPro — core/cost_model.py):
      - Turbine cost:       rated_kW × turbine_usd_per_kw   (default 945 USD/kW)
      - CAPEX total:        turbine_cost / turbines_pct     (default 70% → ×1.429)
      - BOS (30%):          civil + electrical + installation + engineering + misc
      - OPEX anual:         CAPEX_total × opex_pct_capex    (default 3.72%)
      Nota: esta ruta (panel de una sola turbina) no incluye el costo de
      cimentación dependiente de geometría que sí suma farm_mcda._economics()
      — ver docs/ahp-topsis.md §5.
    Financial model:
      - NPV / VAN over project_years at discount_rate
      - LCOE = PV(CAPEX + OPEX + decommissioning) / PV(degraded energy)
      - VAE  = NPV × r / (1 − (1+r)^-n)
    """

    def __init__(self,
                 cost_model: Optional['CostModel'] = None,
                 turbine_spacing_multiplier: float = 7.0):
        from core.cost_model import CostModel
        self.cost_model   = cost_model or CostModel.from_defaults()
        self.spacing_mult = turbine_spacing_multiplier

    # ── park sizing ──────────────────────────────────────────────────────────

    def turbines_for_demand(self, annual_energy_kwh_per_turbine: float,
                             demand_mwh: float) -> int:
        """Number of turbines needed to meet annual energy demand."""
        if annual_energy_kwh_per_turbine <= 0:
            return 0
        return int(np.ceil(demand_mwh * 1000 / annual_energy_kwh_per_turbine))

    # ── cost breakdown ────────────────────────────────────────────────────────

    def capex_breakdown(self, turbine: 'PowerCurve', n_turbines: int) -> dict:
        """
        Desglose CAPEX en USD usando el modelo central WindPro.
        turbine: PowerCurve con rated_power_kw y rotor_diameter_m.
        """
        rated_kw  = turbine.rated_power_kw
        spacing_m = self.spacing_mult * turbine.rotor_diameter_m  # 7·D
        area_m2   = (spacing_m ** 2) * n_turbines

        breakdown = self.cost_model.capex_breakdown(rated_kw, n_turbines)
        breakdown.update({
            'n_turbines':   n_turbines,
            'area_m2':      round(area_m2),
            'area_ha':      round(area_m2 / 10000, 1),
            'installed_mw': round(rated_kw * n_turbines / 1000, 2),
        })
        return breakdown

    # ── financial metrics ─────────────────────────────────────────────────────

    def analyze(self, turbine: 'PowerCurve',
                n_turbines: int,
                annual_energy_kwh: float,
                energy_price_usd_kwh: float = None,
                discount_rate_pct: float = 8.0,
                project_years: int = None,
                usd_to_mxn: float = 1.0,
                energy_price_series_usd_kwh: list = None,
                year1_revenue_override_usd: float = None,
                annual_inflation_pct: float = 0.0,
                degradation_pct: float = DEFAULT_DEGRADATION_PCT,
                decommissioning_pct: float = DEFAULT_DECOMMISSIONING_PCT,
                capex_usd_override: float = None,
                opex_usd_annual_override: float = None) -> dict:
        """
        Full techno-economic analysis.
        Returns NPV/VAN, LCOE, VAE, payback, cash flow table.

        annual_energy_kwh: year-1 net park energy; degraded from year 2 onward
        energy_price_series_usd_kwh: optional per-year price array used by the
            fixed PML profile. Discount rate and FX remain scalar inputs.
        year1_revenue_override_usd: optional override for year-1 revenue (already
            in USD, FX-adjusted). Used when revenue is computed via hourly
            weighting (8760 PML × FX × dispatched energy). Bypasses
            energy×price×fx for year 1 only.
        capex_usd_override: when set, skips capex_breakdown and uses this value
            directly (e.g. to reproduce TOPSIS economics that include cable +
            foundation costs).
        opex_usd_annual_override: when set, skips opex_annual_usd and uses this
            value directly. Ignored when capex_usd_override is None.
        """
        if energy_price_usd_kwh is None or project_years is None:
            raise ValueError(
                'energy_price_usd_kwh y project_years deben venir de la configuración del proyecto')
        energy_price_usd_kwh = float(energy_price_usd_kwh)
        project_years = int(project_years)
        if not np.isfinite(energy_price_usd_kwh) or energy_price_usd_kwh < 0:
            raise ValueError('energy_price_usd_kwh debe ser un número no negativo')
        if not 5 <= project_years <= 40:
            raise ValueError('project_years debe estar entre 5 y 40')

        if capex_usd_override is not None and np.isfinite(float(capex_usd_override)) and float(capex_usd_override) > 0:
            total_capex = float(capex_usd_override)
            capex = self.capex_breakdown(turbine, n_turbines)
            capex['total_capex_usd'] = round(total_capex)
        else:
            capex = self.capex_breakdown(turbine, n_turbines)
            total_capex = capex['total_capex_usd']
        investment_cost = total_capex * usd_to_mxn
        annual_inflation_pct = float(annual_inflation_pct or 0.0)

        # OPEX anual = % del CAPEX total (modelo central WindPro), or override
        if (capex_usd_override is not None and opex_usd_annual_override is not None
                and np.isfinite(float(opex_usd_annual_override)) and float(opex_usd_annual_override) >= 0):
            annual_maintenance = float(opex_usd_annual_override)
        else:
            annual_maintenance = self.cost_model.opex_annual_usd(total_capex)
        annual_opex        = annual_maintenance * usd_to_mxn

        lifecycle = project_cashflows(
            investment_cost, annual_opex, annual_energy_kwh,
            energy_price_usd_kwh * usd_to_mxn, discount_rate_pct / 100.0,
            project_years, degradation_pct=degradation_pct,
            decommissioning_pct=decommissioning_pct,
            annual_inflation_pct=annual_inflation_pct,
            price_series=([float(p) * usd_to_mxn if p is not None else None
                           for p in energy_price_series_usd_kwh]
                          if energy_price_series_usd_kwh else None),
            year1_revenue_override=(float(year1_revenue_override_usd) * usd_to_mxn
                                   if year1_revenue_override_usd is not None else None))
        npv = lifecycle['van_usd']
        pv_costs_lcoe = lifecycle['pv_costs']
        pv_energy_lcoe = lifecycle['pv_energy_kwh']
        pv_revenue = lifecycle['pv_revenue']
        payback_years = lifecycle['payback_years']
        cashflow_table = [{
            'year': row['year'],
            'energy_mwh': row['energy_mwh'],
            'price_usd_kwh': row['price_usd_kwh'] / usd_to_mxn if usd_to_mxn else 0.0,
            'discount_rate_pct': discount_rate_pct,
            'fx_mxn_per_usd': usd_to_mxn,
            'revenue': row['revenue_usd'],
            'opex': row['opex_usd'],
            'decommissioning': row['decommissioning_usd'],
            'cashflow': row['net_cashflow_usd'],
            'pv_cashflow': row['pv_net_cashflow_usd'],
            'cumulative': row['cumulative_undiscounted_usd'],
        } for row in lifecycle['cashflow_table']]
        annual_revenue = cashflow_table[0]['revenue']
        annual_cashflow = annual_revenue - annual_opex

        # TODO (tesis §Metodología tecno-económica): este npv es el VAN del PROYECTO (sin deuda).
        # El VAN equity apalancado requiere: restar PMT anual (interés + principal al 7%) del
        # cashflow para obtener FC_equity, descontar FC_equity a la tasa equity (9.5%) y
        # restar solo el capital_equity (30% CAPEX) como inversión inicial — NO el CAPEX total.
        # El valor acumulado simple (sum FC_equity - equity_inicial) NO es el VAN; es el
        # retorno acumulado sin descontar. Ver corrección en chapter_aero.tex tabs kpis/post_apal.

        r_mean = discount_rate_pct / 100.0
        # VAE — annual equivalent value
        vae = (npv * r_mean / (1 - (1 + r_mean) ** -project_years)
               if r_mean > 0 else npv / project_years)

        total_opex_project_usd = lifecycle['opex_lifetime_usd']
        # LCOE includes discounted OPEX, terminal cost and degraded energy.
        lcoe_usd_kwh = pv_costs_lcoe / pv_energy_lcoe if pv_energy_lcoe > 0 else 0

        capacity_factor = (annual_energy_kwh /
                           (turbine.rated_power_kw * n_turbines * 8760)
                           if turbine.rated_power_kw * n_turbines > 0 else 0)

        irr_fraction = irr_bisection(lifecycle['cashflows'])
        irr_pct = round(irr_fraction * 100, 2) if irr_fraction is not None else None
        b_c_ratio = round(pv_revenue / pv_costs_lcoe, 4) if pv_costs_lcoe > 0 else None
        van_capex_ratio = round(npv / total_capex, 4) if total_capex > 0 else None

        # Indicates whether series-based variable params were used
        used_variable_params = bool(
            (energy_price_series_usd_kwh and len(energy_price_series_usd_kwh) > 0) or
            (year1_revenue_override_usd is not None) or
            (annual_inflation_pct != 0)
        )

        return {
            'capex': capex,
            'annual_energy_kwh':    round(annual_energy_kwh),
            'annual_energy_mwh':    round(annual_energy_kwh / 1000, 1),
            'annual_revenue_usd':   round(annual_revenue),
            'annual_opex_usd':      round(annual_opex),
            'total_opex_project_usd': round(total_opex_project_usd),
            'annual_cashflow_usd':  round(annual_cashflow),
            'npv_usd':              round(npv),
            'vae_usd':              round(vae),
            'lcoe_usd_kwh':         round(lcoe_usd_kwh, 4),
            'payback_years':        round(payback_years, 1) if payback_years is not None else None,
            'project_years':        project_years,
            'discount_rate_pct':    discount_rate_pct,
            'discount_rate_mean_pct': round(r_mean * 100, 3),
            'annual_inflation_pct': round(annual_inflation_pct, 3),
            'energy_price_usd_kwh': energy_price_usd_kwh,
            'capacity_factor_pct':  round(capacity_factor * 100, 2),
            'cashflow_table':       cashflow_table,
            'degradation_pct_per_year': degradation_pct,
            'decommissioning_pct_capex': decommissioning_pct,
            'decommissioning_usd': lifecycle['decommissioning_usd'],
            'decommissioning_present_value_usd': lifecycle['decommissioning_present_value_usd'],
            'is_viable':            npv > 0,
            'variable_params_used': used_variable_params,
            'b_c_ratio':            b_c_ratio,
            'van_capex_ratio':      van_capex_ratio,
            'irr_pct':              irr_pct,
        }

    # ── Cp analysis ───────────────────────────────────────────────────────────

    def cp_analysis(self, turbine: 'PowerCurve',
                    ws_series: np.ndarray,
                    rho: float = 1.225) -> dict:
        """
        Compute Cp for each wind speed in the series.
        Based on PhD code: cp_sitio = P_turbine / P_kinetic.
        Returns Cp statistics and fraction near Cp_max.
        """
        rotor_area = turbine.rotor_area_m2
        ws = np.asarray(ws_series, dtype=float)
        ws = ws[~np.isnan(ws)]

        p_turbine = turbine.power_kw(ws) * 1000  # W
        p_kinetic = 0.5 * rho * rotor_area * np.maximum(ws, 0.01) ** 3

        cp = np.where(p_kinetic > 0, p_turbine / p_kinetic, 0)
        cp = np.minimum(cp, 0.593)  # Betz limit

        cp_max   = float(cp.max()) if len(cp) > 0 else 0
        cp_mean  = float(cp.mean()) if len(cp) > 0 else 0
        cp_betz_utilization = cp_mean / 0.593 * 100

        # fraction operating near Cp_max (within 5% — from PhD code)
        threshold_pct = 0.05
        near_max_mask = cp >= cp_max * (1 - threshold_pct)
        near_max_frac = float(near_max_mask.mean() * 100)

        # ws at Cp_max
        if len(ws) > 0 and len(cp) > 0:
            idx_cpmax = int(np.argmax(cp))
            ws_at_cpmax = float(ws[idx_cpmax])
        else:
            ws_at_cpmax = 0.0

        # Cp vs ws curve (binned)
        ws_bins = np.arange(0, 26, 1.0)
        cp_curve = []
        for lo in ws_bins:
            hi = lo + 1.0
            mask = (ws >= lo) & (ws < hi)
            if mask.sum() > 0:
                cp_curve.append({'ws': lo + 0.5, 'cp': round(float(cp[mask].mean()), 4)})

        return {
            'cp_max':               round(cp_max, 4),
            'cp_mean':              round(cp_mean, 4),
            'cp_betz_utilization_pct': round(cp_betz_utilization, 1),
            'ws_at_cp_max_ms':      round(ws_at_cpmax, 2),
            'near_cpmax_pct':       round(near_max_frac, 1),
            'cp_curve':             cp_curve,
        }


# ─── FOLDER HELPERS ───────────────────────────────────────────────────────────

def load_turbines_from_folder(folder_path):
    """
    Scan folder_path for *.csv files in aerogeneradores format and return
    a list of PowerCurve objects.  Silently skips unreadable files.
    """
    import os
    turbines = []
    if not os.path.isdir(folder_path):
        return turbines
    for fname in sorted(os.listdir(folder_path)):
        if not fname.lower().endswith('.csv'):
            continue
        fpath = os.path.join(folder_path, fname)
        try:
            t = PowerCurve.from_file(fpath)
            turbines.append(t)
        except Exception:
            pass
    return turbines


def list_turbine_info(folder_path):
    """
    Return a lightweight list of turbine metadata dicts from the folder,
    without loading full curve interpolators (fast for UI listing).
    Includes hub_height_m estimated by the same heuristic as from_file().
    """
    import os, re
    result = []
    if not os.path.isdir(folder_path):
        return result
    for fname in sorted(os.listdir(folder_path)):
        if not fname.lower().endswith('.csv'):
            continue
        stem = fname.replace('.csv', '')
        p_match = re.search(r'_p(\d+)',              stem, re.IGNORECASE)
        r_match = re.search(r'_r([\d.]+)',           stem, re.IGNORECASE)
        h_match = re.search(r'_h(\d+(?:\.\d+)?)',    stem, re.IGNORECASE)
        name_clean   = re.sub(r'_p\d+.*', '', stem).capitalize()
        rated_kw     = int(p_match.group(1)) if p_match else None
        rotor_r_m    = float(r_match.group(1)) if r_match else None
        rotor_m      = (rotor_r_m * 2.0) if rotor_r_m else None
        hub_explicit = float(h_match.group(1)) if h_match else None
        if hub_explicit is not None:
            hub_h = hub_explicit
        elif rotor_m:
            if   rotor_m < 60:  hub_h = 55.0
            elif rotor_m < 90:  hub_h = 78.0
            elif rotor_m < 130: hub_h = 80.0
            elif rotor_m < 170: hub_h = 100.0
            else:               hub_h = 120.0
        else:
            hub_h = 80.0
        result.append({
            'filename':         fname,
            'name':             name_clean,
            'rated_power_kw':   rated_kw,
            'rotor_diameter_m': rotor_m,
            'hub_height_m':     hub_h,
            'hub_source':       'explicit' if hub_explicit else 'heuristic',
        })
    return result
