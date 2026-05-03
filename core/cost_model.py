"""
Modelo económico central de WindPro Platform.

Toda la plataforma (Tecno-Económico, Kriging clásico, Model Arena, Forecast Studio,
Parque) lee de aquí los valores base de CAPEX y OPEX. Cada parámetro lleva su rango
[min, max] para análisis de sensibilidad posterior — los rangos NO se usan en los
cálculos directos, solo el `value`.

Fórmulas:
    turbine_cost_usd = P_nom_kW · turbine_usd_per_kw · n_aeros
    CAPEX_total      = turbine_cost_usd / turbines_pct
    component_i_usd  = CAPEX_total · pct_i        (para cada sub-componente BOS)
    OPEX_anual_usd   = CAPEX_total · opex_pct_capex
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, Any


# ── Defaults centrales del estudio (valores y rangos para sensibilidad) ──────
DEFAULT_COST_MODEL: Dict[str, Any] = {
    # Precio del aerogenerador
    'turbine_usd_per_kw':  {'value': 1800.0, 'min': 1600.0, 'max': 2000.0},

    # Aerogenerador como % del CAPEX total (turbines + BOS = 100%)
    'turbines_pct':        {'value': 0.66,   'min': 0.60,   'max': 0.80},

    # Desglose del 34% BOS (sub-componentes como % del CAPEX total)
    'civil_works_pct':     {'value': 0.12,   'min': 0.10,   'max': 0.15},
    'electrical_pct':      {'value': 0.08,   'min': 0.05,   'max': 0.10},
    'installation_pct':    {'value': 0.05,   'min': 0.01,   'max': 0.05},
    'engineering_pct':     {'value': 0.06,   'min': 0.02,   'max': 0.10},
    'miscellaneous_pct':   {'value': 0.03,   'min': 0.01,   'max': 0.05},

    # OPEX anual como % del CAPEX total
    'opex_pct_capex':      {'value': 0.0372, 'min': 0.02,   'max': 0.04},
}


# Sub-componentes BOS en orden de presentación
BOS_COMPONENTS = (
    ('civil_works_pct',   'civil_works_usd',   'Obras civiles (caminos + cimentaciones)'),
    ('electrical_pct',    'electrical_usd',    'Sistema eléctrico y conexión a red'),
    ('installation_pct',  'installation_usd',  'Instalación y puesta en marcha'),
    ('engineering_pct',   'engineering_usd',   'Ingeniería, factibilidad y permisos'),
    ('miscellaneous_pct', 'miscellaneous_usd', 'Misceláneos y contingencias'),
)


@dataclass
class CostModel:
    """Valores efectivos del modelo (sin rangos). Todas las pct en fracción [0,1]."""
    turbine_usd_per_kw: float = 1800.0
    turbines_pct:       float = 0.66
    civil_works_pct:    float = 0.12
    electrical_pct:     float = 0.08
    installation_pct:   float = 0.05
    engineering_pct:    float = 0.06
    miscellaneous_pct:  float = 0.03
    opex_pct_capex:     float = 0.0372

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> 'CostModel':
        """Construye un CostModel desde un dict del frontend o desde DEFAULT_COST_MODEL."""
        if not data:
            return cls.from_defaults()
        kwargs = {}
        for f in cls.__dataclass_fields__:
            v = data.get(f)
            if isinstance(v, dict):           # formato {'value': x, 'min':..., 'max':...}
                v = v.get('value')
            if v is None:
                v = DEFAULT_COST_MODEL[f]['value']
            kwargs[f] = float(v)
        return cls(**kwargs)

    @classmethod
    def from_defaults(cls) -> 'CostModel':
        return cls(**{k: v['value'] for k, v in DEFAULT_COST_MODEL.items()})

    def to_dict(self) -> Dict[str, float]:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}

    # ── core math ─────────────────────────────────────────────────────────────
    def total_pct_check(self) -> float:
        """Suma de turbinas + BOS. Idealmente 1.0; se reporta para validación UI."""
        return (self.turbines_pct + self.civil_works_pct + self.electrical_pct +
                self.installation_pct + self.engineering_pct + self.miscellaneous_pct)

    def turbine_unit_cost_usd(self, rated_kw: float) -> float:
        return float(rated_kw) * self.turbine_usd_per_kw

    def capex_breakdown(self, rated_kw: float, n_turbines: int) -> Dict[str, float]:
        """CAPEX total + desglose en USD."""
        n = max(int(n_turbines), 0)
        unit  = self.turbine_unit_cost_usd(rated_kw)
        turbines_total = unit * n
        # CAPEX se escala desde el costo de aeros usando turbines_pct
        capex_total = turbines_total / self.turbines_pct if self.turbines_pct > 0 else 0.0

        out = {
            'turbine_unit_usd':   round(unit),
            'turbines_total_usd': round(turbines_total),
            'turbines_pct':       self.turbines_pct,
            'total_capex_usd':    round(capex_total),
            'pct_total_check':    round(self.total_pct_check(), 4),
        }
        for pct_field, usd_field, _label in BOS_COMPONENTS:
            pct = getattr(self, pct_field)
            out[usd_field] = round(capex_total * pct)
            out[pct_field] = pct
        return out

    def opex_annual_usd(self, capex_total_usd: float) -> float:
        return float(capex_total_usd) * self.opex_pct_capex


def get_default_model_dict() -> Dict[str, Any]:
    """Devuelve el dict completo (con rangos) para que el frontend renderice los inputs."""
    return {k: dict(v) for k, v in DEFAULT_COST_MODEL.items()}
