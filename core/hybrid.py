"""
WindPro Platform — Hybrid Energy System Module (HOMER Pro-style)
Wind + Solar PV + Battery + Diesel generator + Load profile
Hourly dispatch simulation, NPC/LCOE, CO2 savings.
"""
import numpy as np
import pandas as pd
from typing import Optional, Dict, List
import warnings

warnings.filterwarnings('ignore')


# ─── SOLAR PV MODEL ───────────────────────────────────────────────────────────

def estimate_clearsky_ghi(timestamps: pd.DatetimeIndex,
                           lat: float = 20.0,
                           cloud_factor: float = 0.65) -> np.ndarray:
    """
    Estimate hourly GHI [W/m²] from solar geometry × cloud/clearsky factor.
    Simple isotropic clear-sky model (Hottel-like).
    """
    doy  = timestamps.dayofyear.values.astype(float)
    hour = timestamps.hour.values + timestamps.minute.values / 60.0
    lat_r = np.radians(lat)

    decl = np.radians(23.45 * np.sin(np.radians(360 / 365 * (doy - 81))))
    ha   = np.radians((hour - 12) * 15)

    sin_elev = (np.sin(lat_r) * np.sin(decl) +
                np.cos(lat_r) * np.cos(decl) * np.cos(ha))
    sin_elev = np.maximum(sin_elev, 0.0)

    Io  = 1367.0 * (1 + 0.033 * np.cos(np.radians(360 * doy / 365)))
    ghi = Io * sin_elev * 0.84 * cloud_factor
    return ghi.astype(float)


class SolarPVModel:
    """GHI → AC power using the Performance Ratio method."""

    def __init__(self, capacity_kwp: float, pr: float = 0.80,
                 temp_coeff: float = -0.004, noct: float = 45.0):
        self.capacity_kwp = capacity_kwp
        self.pr           = pr
        self.temp_coeff   = temp_coeff   # /°C power loss
        self.noct         = noct         # nominal operating cell temp °C

    def power_kw(self, ghi: np.ndarray,
                 tamb: Optional[np.ndarray] = None) -> np.ndarray:
        """
        P_AC = GHI/1000 * capacity_kWp * PR
        Temperature derating optional if ambient temp provided.
        """
        g = np.maximum(ghi, 0.0)
        p = g / 1000.0 * self.capacity_kwp * self.pr
        if tamb is not None:
            tcell = tamb + g * (self.noct - 20) / 800
            p *= (1 + self.temp_coeff * (tcell - 25))
        return np.maximum(p, 0.0)


# ─── BATTERY STORAGE ──────────────────────────────────────────────────────────

class BatteryStorage:
    """
    1-state SoC battery model with round-trip efficiency and C-rate limits.
    """
    CYCLE_DEPTH_FACTOR = 0.80  # usable SoC band

    def __init__(self, capacity_kwh: float,
                 c_rate: float = 0.5,
                 eta_rt: float = 0.92,
                 soc_min: float = 0.10,
                 soc_max: float = 0.90,
                 soc_init: float = 0.50):
        self.capacity_kwh   = capacity_kwh
        self.max_kw         = capacity_kwh * c_rate
        self.eta_c          = np.sqrt(eta_rt)   # charge efficiency
        self.eta_d          = np.sqrt(eta_rt)   # discharge efficiency
        self.soc_min        = soc_min
        self.soc_max        = soc_max
        self.soc            = soc_init
        self.cycles         = 0.0
        self._prev_soc      = soc_init

    def step(self, surplus_kw: float, dt_h: float = 1.0) -> float:
        """
        surplus_kw > 0 → charge from surplus; < 0 → discharge to cover deficit.
        Returns actual power exchange (+ charged, – discharged from grid perspective).
        """
        if surplus_kw >= 0:
            avail_kwh  = (self.soc_max - self.soc) * self.capacity_kwh / self.eta_c
            p = min(surplus_kw, self.max_kw, avail_kwh / dt_h)
            self.soc  += p * self.eta_c * dt_h / self.capacity_kwh
            exchange   = p
        else:
            avail_kwh  = (self.soc - self.soc_min) * self.capacity_kwh * self.eta_d
            p = min(-surplus_kw, self.max_kw, avail_kwh / dt_h)
            self.soc  -= p / self.eta_d * dt_h / self.capacity_kwh
            exchange   = -p

        # Approximate cycle counting (half-cycle = DoD change)
        delta = abs(self.soc - self._prev_soc)
        self.cycles += delta / (2 * (self.soc_max - self.soc_min))
        self._prev_soc = self.soc
        return exchange

    def reset(self, soc: float = 0.50):
        self.soc    = soc
        self.cycles = 0.0
        self._prev_soc = soc


# ─── DIESEL GENERATOR ─────────────────────────────────────────────────────────

class DieselGenerator:
    """
    Linearised fuel consumption: L/h = F0*P_rated + F1*P_output
    Default coefficients from Homer Energy standard values.
    """
    CO2_KG_PER_L = 2.68
    DEFAULT_FUEL_PRICE = 0.90   # USD/L

    def __init__(self, capacity_kw: float,
                 min_loading: float = 0.30,
                 f0: float = 0.08,
                 f1: float = 0.246,
                 fuel_price_usd_l: float = None):
        self.capacity_kw = capacity_kw
        self.min_load    = min_loading
        self.f0          = f0
        self.f1          = f1
        self.fuel_price  = fuel_price_usd_l or self.DEFAULT_FUEL_PRICE

    def dispatch(self, deficit_kw: float) -> tuple:
        """Returns (power_kw, fuel_L_h)."""
        if deficit_kw <= 0:
            return 0.0, 0.0
        p = float(np.clip(deficit_kw, self.min_load * self.capacity_kw, self.capacity_kw))
        fuel = self.f0 * self.capacity_kw + self.f1 * p
        return p, fuel


# ─── LOAD PROFILE GENERATOR ───────────────────────────────────────────────────

def build_load_profile(annual_kwh: float,
                       peak_kw: float,
                       profile_type: str = 'residential',
                       n_hours: int = 8760) -> np.ndarray:
    """
    Generate synthetic hourly load profile.
    profile_type: 'residential' | 'commercial' | 'industrial' | 'flat'
    """
    hours = np.arange(n_hours)
    hour_of_day = hours % 24
    month_of_year = (hours // 730) % 12  # approximate

    if profile_type == 'flat':
        shape = np.ones(n_hours)
    elif profile_type == 'residential':
        # Morning + evening peaks
        morning = np.exp(-0.5 * ((hour_of_day - 7) / 1.5) ** 2)
        evening = np.exp(-0.5 * ((hour_of_day - 20) / 2.0) ** 2)
        shape   = 0.3 + morning * 0.7 + evening * 1.0
        # Seasonal: higher in summer (cooling) and winter (heating)
        seasonal = 1.0 + 0.15 * np.cos(2 * np.pi * (month_of_year - 6) / 12)
        shape   *= seasonal
    elif profile_type == 'commercial':
        # Business hours 8-18
        business = np.where((hour_of_day >= 8) & (hour_of_day <= 18), 1.0, 0.2)
        shape    = business
    elif profile_type == 'industrial':
        # Two shifts 6-22
        shift = np.where((hour_of_day >= 6) & (hour_of_day <= 22), 1.0, 0.5)
        shape = shift
    else:
        shape = np.ones(n_hours)

    # Scale to annual energy and peak
    shape = np.maximum(shape, 0.05)
    shape = shape / shape.mean() * (annual_kwh / n_hours)
    shape = np.minimum(shape, peak_kw)
    return shape.astype(float)


# ─── HYBRID DISPATCH SIMULATION ───────────────────────────────────────────────

class HybridDispatch:
    """
    Simulate Wind + Solar + Battery + Diesel hourly dispatch.
    Strategy: Renewables first → Battery → Diesel → Unmet load.
    """

    def simulate(self,
                 load_kw: np.ndarray,
                 wind_kw: np.ndarray,
                 solar_kw: Optional[np.ndarray] = None,
                 battery: Optional[BatteryStorage] = None,
                 diesel: Optional[DieselGenerator] = None,
                 dt_h: float = 1.0) -> Dict:

        n = len(load_kw)
        solar_kw  = solar_kw if solar_kw is not None else np.zeros(n)

        p_wind_u  = np.zeros(n)
        p_solar_u = np.zeros(n)
        p_batt    = np.zeros(n)
        p_diesel  = np.zeros(n)
        p_curtail = np.zeros(n)
        p_unmet   = np.zeros(n)
        soc_trace = np.zeros(n)
        fuel_lh   = np.zeros(n)

        if battery:
            battery.reset(0.50)

        for t in range(n):
            load   = float(load_kw[t])
            wind   = float(wind_kw[t])
            solar  = float(solar_kw[t])
            renew  = wind + solar

            # Step 1: Renewables → load
            used      = min(renew, load)
            surplus   = renew - used
            deficit   = load  - used
            p_wind_u[t]  = min(wind,  load)
            p_solar_u[t] = min(solar, max(0.0, load - wind))

            # Step 2: Surplus → charge battery
            if battery and surplus > 0:
                charged       = battery.step(surplus, dt_h)
                p_curtail[t]  = surplus - charged
            elif surplus > 0:
                p_curtail[t]  = surplus

            # Step 3: Deficit → discharge battery
            if battery and deficit > 0:
                discharge  = -battery.step(-deficit, dt_h)
                deficit   -= discharge
                p_batt[t]  = discharge

            # Step 4: Remaining deficit → diesel
            if diesel and deficit > 0:
                p_d, f_l    = diesel.dispatch(deficit)
                deficit    -= p_d
                p_diesel[t] = p_d
                fuel_lh[t]  = f_l

            p_unmet[t]  = max(0.0, deficit)
            if battery:
                soc_trace[t] = battery.soc

        # ── KPIs ──
        total_load_kwh  = float(load_kw.sum() * dt_h)
        renewable_kwh   = float((p_wind_u + p_solar_u).sum() * dt_h)
        wind_kwh        = float(p_wind_u.sum() * dt_h)
        solar_kwh       = float(p_solar_u.sum() * dt_h)
        diesel_kwh      = float(p_diesel.sum() * dt_h)
        unmet_kwh       = float(p_unmet.sum() * dt_h)
        curtailed_kwh   = float(p_curtail.sum() * dt_h)
        fuel_total_l    = float(fuel_lh.sum() * dt_h)
        co2_kg          = fuel_total_l * DieselGenerator.CO2_KG_PER_L
        fuel_cost_usd   = fuel_total_l * (diesel.fuel_price if diesel else DieselGenerator.DEFAULT_FUEL_PRICE)
        rf              = renewable_kwh / total_load_kwh if total_load_kwh > 0 else 0.0
        lpsp            = unmet_kwh / total_load_kwh    if total_load_kwh > 0 else 0.0
        batt_cycles     = battery.cycles if battery else 0.0

        # Downsampled traces for chart (max 720 pts)
        step_ds = max(1, n // 720)
        idx_ds  = np.arange(0, n, step_ds)

        return {
            'total_load_kwh':      round(total_load_kwh, 1),
            'renewable_kwh':       round(renewable_kwh, 1),
            'wind_kwh':            round(wind_kwh, 1),
            'solar_kwh':           round(solar_kwh, 1),
            'diesel_kwh':          round(diesel_kwh, 1),
            'unmet_kwh':           round(unmet_kwh, 1),
            'curtailed_kwh':       round(curtailed_kwh, 1),
            'fuel_liters':         round(fuel_total_l, 1),
            'co2_kg':              round(co2_kg, 1),
            'fuel_cost_usd':       round(fuel_cost_usd, 2),
            'renewable_fraction':  round(rf * 100, 2),
            'lpsp_pct':            round(lpsp * 100, 4),
            'battery_cycles':      round(batt_cycles, 1),
            'soc_trace':           [round(float(v), 3) for v in soc_trace[idx_ds]]
                                   if battery else None,
            'traces': {
                'load':       [round(float(v), 2) for v in load_kw[idx_ds]],
                'renewables': [round(float(v), 2) for v in (p_wind_u + p_solar_u)[idx_ds]],
                'wind':       [round(float(v), 2) for v in p_wind_u[idx_ds]],
                'solar':      [round(float(v), 2) for v in p_solar_u[idx_ds]],
                'battery':    [round(float(v), 2) for v in p_batt[idx_ds]],
                'diesel':     [round(float(v), 2) for v in p_diesel[idx_ds]],
                'unmet':      [round(float(v), 2) for v in p_unmet[idx_ds]],
                'step':       int(step_ds),
            },
        }


# ─── NPC / LCOE ECONOMICS ─────────────────────────────────────────────────────

class HybridEconomics:
    """Net Present Cost and LCOE (HOMER methodology)."""

    UNIT_COSTS = {
        'wind_kw':    1300,   # USD/kW
        'solar_kw':    750,   # USD/kWp
        'batt_kwh':    320,   # USD/kWh
        'diesel_kw':   200,   # USD/kW
    }
    OM_ANNUAL = {
        'wind_kw':  25,   # USD/kW/yr
        'solar_kw': 12,
        'batt_kwh':  8,
        'diesel_kw': 35,
    }
    LIFETIME    = 25   # years
    DISCOUNT    = 0.08

    def compute(self,
                wind_kw: float, solar_kw: float,
                battery_kwh: float, diesel_kw: float,
                dispatch: Dict,
                costs: Optional[Dict] = None,
                discount_rate: float = None,
                lifetime: int = None) -> Dict:

        r = discount_rate or self.DISCOUNT
        N = lifetime      or self.LIFETIME
        c = costs         or {}

        capex = {
            'wind':    wind_kw     * c.get('wind_kw',   self.UNIT_COSTS['wind_kw']),
            'solar':   solar_kw    * c.get('solar_kw',  self.UNIT_COSTS['solar_kw']),
            'battery': battery_kwh * c.get('batt_kwh',  self.UNIT_COSTS['batt_kwh']),
            'diesel':  diesel_kw   * c.get('diesel_kw', self.UNIT_COSTS['diesel_kw']),
        }
        capex['total'] = sum(capex.values())

        om_yr = (wind_kw     * self.OM_ANNUAL['wind_kw'] +
                 solar_kw    * self.OM_ANNUAL['solar_kw'] +
                 battery_kwh * self.OM_ANNUAL['batt_kwh'] +
                 diesel_kw   * self.OM_ANNUAL['diesel_kw'] +
                 dispatch.get('fuel_cost_usd', 0))

        # Present value of O&M stream
        pv_om = om_yr * (1 - (1+r)**(-N)) / r

        npc = capex['total'] + pv_om
        crf = r * (1+r)**N / ((1+r)**N - 1)
        ann = npc * crf

        served_kwh = (dispatch.get('total_load_kwh', 0)
                      - dispatch.get('unmet_kwh', 0))
        lcoe = ann / served_kwh if served_kwh > 0 else 0.0

        # CO2 savings vs 100% diesel baseline
        eta_diesel  = 0.30   # 30% generator efficiency
        co2_baseline = served_kwh / eta_diesel * DieselGenerator.CO2_KG_PER_L
        co2_saved    = co2_baseline - dispatch.get('co2_kg', 0)

        return {
            'capex':              {k: round(v) for k, v in capex.items()},
            'om_annual':          round(om_yr),
            'pv_om':              round(pv_om),
            'npc':                round(npc),
            'annualized_cost':    round(ann),
            'lcoe_usd_kwh':       round(lcoe, 4),
            'crf':                round(crf, 5),
            'discount_rate':      r,
            'lifetime_yr':        N,
            'co2_saved_kg_yr':    round(co2_saved),
            'co2_saved_t_yr':     round(co2_saved / 1000, 1),
            'renewable_fraction': dispatch.get('renewable_fraction', 0),
        }
