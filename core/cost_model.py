"""
Modelo económico central de WindPro Platform.

Toda la plataforma (Tecno-Económico, Kriging clásico,
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
# Recalibrado 2026-09 con evidencia 2018-2024 (el valor previo, 1800+2727 USD/kW
# total, correspondía a costos ~2008). Anclas usadas:
#   - Key, Roberts & Eberle (2022) "Scaling trends for balance-of-system costs
#     at land-based wind power plants", NREL/OSTI 1837954: planta de referencia
#     2018 = TCC 881 USD/kW + BOS 386 USD/kW = 1267 USD/kW total (2.5 MW,
#     Ø117 m, buje 90 m). TCC/total ≈ 69.5%.
#   - DOE Wind Energy Technologies Office / LBNL, Land-Based Wind Market
#     Report: rango reciente (~2022-2023) 1200-1800 USD/kW instalado; O&M
#     > 40 USD/kW/año en 2023 (confianza media: síntesis de reportes de
#     mercado, no cifra única verificada en tabla).
#   - IRENA (2025) "Renewable Power Generation Costs in 2024": promedio
#     global ponderado 1041 USD/kW (2024), turbina ≈75% del costo instalado
#     (ver DESIGN_COST_BENCHMARK abajo, que ya usaba esta fuente).
# Punto central elegido para México (entre el ancla 2018 y el rango 2022-23
# de EE.UU., por encima del promedio global 2024 que incluye mercados con
# mano de obra más barata): ~1350 USD/kW turbina+BOS-porcentual, turbina 70%.
# Ver docs/ahp-topsis.md para el detalle y el nivel de confianza de cada cifra.
DEFAULT_COST_MODEL: Dict[str, Any] = {
    # Precio del aerogenerador
    'turbine_usd_per_kw':  {'value': 945.0,  'min':  700.0, 'max': 1100.0},

    # Aerogenerador como % del CAPEX total (turbines + BOS = 100%)
    'turbines_pct':        {'value': 0.70,   'min': 0.65,   'max': 0.78},

    # Desglose del 30% BOS (sub-componentes como % del CAPEX total).
    # civil_works_pct cubre SOLO obras civiles no relacionadas con cimentación
    # (caminos, preparación de sitio) — el costo de cimentación ahora se
    # calcula aparte, dependiente de geometría (ver foundation_usd_per_m3eq).
    'civil_works_pct':     {'value': 0.04,   'min': 0.02,   'max': 0.06},
    'electrical_pct':      {'value': 0.09,   'min': 0.06,   'max': 0.12},
    'installation_pct':    {'value': 0.06,   'min': 0.03,   'max': 0.08},
    'engineering_pct':     {'value': 0.07,   'min': 0.04,   'max': 0.10},
    'miscellaneous_pct':   {'value': 0.04,   'min': 0.02,   'max': 0.06},

    # OPEX anual como % del CAPEX total (validado: da ~50 USD/kW/año sobre el
    # CAPEX recalibrado, consistente con el supuesto de 52 USD/kW/año de Key
    # et al. 2022 y con el >40 USD/kW/año de DOE/LBNL 2023).
    'opex_pct_capex':      {'value': 0.0372, 'min': 0.02,   'max': 0.04},

    # Costo de cimentación: proxy físico del momento de volteo (empuje
    # aerodinámico × brazo de palanca de la altura de buje), USD por
    # (diámetro_rotor_m² · altura_buje_m). Mecanismo y geometría de
    # referencia citados en Key, Roberts & Eberle (2022) — la magnitud en
    # USD es una estimación de orden de magnitud (no hay tabla pública con
    # el desglose de cimentación en USD; el detalle vive en el software
    # LandBOSSE de NREL, no en el manuscrito). Calibrado para dar ≈130
    # USD/kW de cimentación en la turbina de referencia del mismo estudio
    # (2.5 MW, Ø117 m, buje 90 m): 0.264 = 130·2500 / (117² · 90).
    'foundation_usd_per_m3eq': {'value': 0.264, 'min': 0.15, 'max': 0.40},
}


# Benchmark exclusivo para Diseño y optimización. IRENA reporta para proyectos
# onshore comisionados en 2024 un costo total instalado medio global de
# 1 041 USD/kW (USD de 2024). El mismo informe indica que los aerogeneradores
# representan aproximadamente 75% del costo instalado. Por tanto, el parámetro
# interno de suministro se fija en 1 041 × 0.75 = 780.75 USD/kW y el BOS ocupa
# el 25% restante. La evaluación tecnoeconómica conserva DEFAULT_COST_MODEL.
DESIGN_COST_BENCHMARK: Dict[str, Any] = {
    'id': 'irena_onshore_2024_global_weighted_average',
    'label': 'IRENA onshore 2024 · promedio global ponderado',
    'data_year': 2024,
    'currency_year': 2024,
    'total_installed_usd_per_kw': 1041.0,
    'source_url': (
        'https://www.irena.org/Publications/2025/Jun/'
        'Renewable-Power-Generation-Costs-in-2024'),
    'cost_model': {
        'turbine_usd_per_kw': 780.75,
        'turbines_pct': 0.75,
        # El 25% BOS conserva las proporciones relativas 12:8:5:6:3 del
        # modelo original, evitando alterar qué rubros se representan.
        'civil_works_pct': 0.08823529411764706,
        'electrical_pct': 0.058823529411764705,
        'installation_pct': 0.03676470588235294,
        'engineering_pct': 0.04411764705882353,
        'miscellaneous_pct': 0.022058823529411766,
        'opex_pct_capex': 0.0372,
        # No se fija aquí a propósito: hereda el valor por defecto de
        # DEFAULT_COST_MODEL vía CostModel.from_dict (mismo proxy físico,
        # independiente del benchmark de precio usado).
    },
}


# Benchmark específico para México (Istmo de Tehuantepec y sitios maduros).
# Anclas bibliográficas:
#   - IRENA (2023) "Renewable Power Generation Costs in 2022", tabla de costos
#     de instalación onshore por región: América Latina y el Caribe promedio
#     ponderado ≈ 1 198 USD/kW (USD de 2022), turbina ≈ 73-74 % del total.
#     DOI/URL: https://www.irena.org/Publications/2023/Aug/
#     Renewable-Power-Generation-Costs-in-2022
#   - SENER/CRE — Subastas eléctricas de largo plazo 1ª-3ª (2016-2019):
#     precios de adjudicación eólica en Oaxaca ~18-22 USD/MWh implican CAPEX
#     estimado de 1 000-1 350 USD/kW (análisis regresivo publicado en
#     Elizondo-Azuela et al., "Performance of renewable energy auctions:
#     experience in Brazil, Mexico and Peru", World Bank, 2017).
#   - Key, Roberts & Eberle (2022), NREL/OSTI 1837954 (ya citado en
#     DEFAULT_COST_MODEL): la estructura porcentual de BOS se conserva;
#     se re-escala al nivel de precio mexicano.
# Punto central: 1 200 USD/kW total instalado (USD de 2024), consistente con
# el rango 1 050-1 350 reportado para proyectos mexicanos recientes.
# OPEX ≈ 4.0 % CAPEX (≈ 48 USD/kW·año), ligeramente por encima del benchmark
# global (3.72 %) para reflejar costos de O&M en zonas remotas del Istmo.
MEXICO_COST_BENCHMARK: Dict[str, Any] = {
    'id': 'irena_latam_2022_mexico_onshore',
    'label': 'IRENA LatAm 2022 · México onshore (Istmo, sitios maduros)',
    'data_year': 2022,
    'currency_year': 2024,
    'total_installed_usd_per_kw': 1200.0,
    'source_url': (
        'https://www.irena.org/Publications/2023/Aug/'
        'Renewable-Power-Generation-Costs-in-2022'),
    'cost_model': {
        # 1 200 × 0.74 = 888 USD/kW turbina
        'turbine_usd_per_kw': 888.0,
        'turbines_pct': 0.74,
        # BOS = 26 %, mismas proporciones relativas 12:8:5:6:3 del modelo base
        # pero re-escaladas a 0.26 (mayor logística Istmo frente al 0.25 IRENA).
        'civil_works_pct':     0.09230769230769231,    # 12/34 * 0.26
        'electrical_pct':      0.06153846153846154,    # 8/34 * 0.26
        'installation_pct':    0.038461538461538464,   # 5/34 * 0.26
        'engineering_pct':     0.04615384615384615,    # 6/34 * 0.26
        'miscellaneous_pct':   0.023076923076923078,   # 3/34 * 0.26
        'opex_pct_capex': 0.04,
        # foundation_usd_per_m3eq: hereda DEFAULT_COST_MODEL (misma física).
    },
}


# Sub-componentes BOS en orden de presentación
BOS_COMPONENTS = (
    ('civil_works_pct',   'civil_works_usd',   'Obras civiles (caminos, preparación de sitio)'),
    ('electrical_pct',    'electrical_usd',    'Sistema eléctrico y conexión a red'),
    ('installation_pct',  'installation_usd',  'Instalación y puesta en marcha'),
    ('engineering_pct',   'engineering_usd',   'Ingeniería, factibilidad y permisos'),
    ('miscellaneous_pct', 'miscellaneous_usd', 'Misceláneos y contingencias'),
)


@dataclass
class CostModel:
    """Valores efectivos del modelo (sin rangos). Todas las pct en fracción [0,1]."""
    turbine_usd_per_kw: float = 945.0
    turbines_pct:       float = 0.70
    civil_works_pct:    float = 0.04
    electrical_pct:     float = 0.09
    installation_pct:   float = 0.06
    engineering_pct:    float = 0.07
    miscellaneous_pct:  float = 0.04
    opex_pct_capex:     float = 0.0372
    foundation_usd_per_m3eq: float = 0.264

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

    @classmethod
    def from_design_benchmark(cls) -> 'CostModel':
        """Modelo IRENA global 2024 para comparar alternativas en Diseño/TOPSIS."""
        return cls(**DESIGN_COST_BENCHMARK['cost_model'])

    @classmethod
    def from_mexico_benchmark(cls) -> 'CostModel':
        """Modelo específico México (IRENA LatAm 2022, Istmo/sitios maduros)."""
        return cls(**MEXICO_COST_BENCHMARK['cost_model'])

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

    def foundation_cost_usd(self, rotor_diameter_m: Optional[float],
                             hub_height_m: Optional[float],
                             n_turbines: int) -> float:
        """Costo de cimentación, proxy del momento de volteo (empuje ∝ área de
        rotor, brazo de palanca = altura de buje): diámetro² × altura_buje.
        Mecanismo citado en Key, Roberts & Eberle (2022, NREL/OSTI 1837954);
        magnitud calibrada a orden de magnitud (ver DEFAULT_COST_MODEL).
        Devuelve 0 si falta geometría (compatibilidad con módulos que no la
        rastrean, p.ej. Kriging clásico — ver docs/ahp-topsis.md)."""
        if not rotor_diameter_m or not hub_height_m or n_turbines <= 0:
            return 0.0
        return (self.foundation_usd_per_m3eq * float(rotor_diameter_m) ** 2
                * float(hub_height_m) * int(n_turbines))


def electrical_loss_estimate(
    collection_length_m: float,
    installed_mw: float,
    n_turbines: int,
    substation_distance_km: float = 0.0,
    voltage_mv_kv: float = 34.5,
    voltage_hv_kv: float = 115.0,
    pf: float = 0.95,
) -> Dict[str, float]:
    """Estimación de pérdidas eléctricas en tres componentes, pre-factibilidad.

    Referencias:
      - MV colección: fórmula de alimentador radial balanceado, factor 1/3 para
        distribución de carga. Parámetros de cable XLPE 95 mm² a 34.5 kV
        (r ≈ 0.35 Ω/km). Stehly & Beiter, NREL/TP-5000-77450 (2020), §4.2.
      - Transformador elevador 34.5/115 kV: pérdidas típicas 0.3–0.5 %,
        se usa 0.4 % (media de rango CIGRÉ 261, 2004).
      - Línea HV: Stehly & Beiter (2020); cable ACSR 397.5 kcmil 115 kV
        r ≈ 0.065 Ω/km. Para 230 kV usar voltage_hv_kv=230.

    Supuestos:
      - Factor de potencia 0.95.
      - Alimentador radial con N turbinas uniformemente distribuidas a lo largo
        del tendido MST: factor de pérdida ≈ 1/3 del escenario concentrado.
      - No incluye filtros de armónicos ni compensación reactiva.

    Returns dict con claves:
      mv_collection_loss_pct, transformer_loss_pct, hv_transmission_loss_pct,
      total_electrical_loss_pct, substation_distance_km, voltage_mv_kv,
      voltage_hv_kv, inputs_valid.
    """
    r_mv_ohm_km = 0.35   # XLPE 95 mm², 34.5 kV — Stehly & Beiter 2020
    r_hv_ohm_km = 0.065  # ACSR 397.5 kcmil, 115 kV
    transformer_loss_pct = 0.4  # CIGRÉ 261 (2004), media del rango 0.3–0.5 %

    inputs_valid = (
        installed_mw > 0
        and n_turbines > 0
        and collection_length_m >= 0
        and voltage_mv_kv > 0
        and voltage_hv_kv > 0
        and 0 < pf <= 1
    )
    if not inputs_valid:
        return {
            'mv_collection_loss_pct': 0.0,
            'transformer_loss_pct': transformer_loss_pct,
            'hv_transmission_loss_pct': 0.0,
            'total_electrical_loss_pct': transformer_loss_pct,
            'substation_distance_km': float(substation_distance_km or 0.0),
            'voltage_mv_kv': voltage_mv_kv,
            'voltage_hv_kv': voltage_hv_kv,
            'inputs_valid': False,
        }

    L_mv_km = collection_length_m / 1000.0
    pf2 = pf ** 2
    # Radial feeder: factor 1/3 por distribución de carga a lo largo del MST
    mv_loss_pct = (
        100.0 * installed_mw * r_mv_ohm_km * L_mv_km
        / (3.0 * n_turbines * voltage_mv_kv ** 2 * pf2)
    )

    d_hv_km = max(0.0, float(substation_distance_km or 0.0))
    hv_loss_pct = (
        100.0 * installed_mw * r_hv_ohm_km * d_hv_km
        / (voltage_hv_kv ** 2 * pf2)
        if d_hv_km > 0 else 0.0
    )

    total = mv_loss_pct + transformer_loss_pct + hv_loss_pct
    return {
        'mv_collection_loss_pct':    round(mv_loss_pct, 4),
        'transformer_loss_pct':      round(transformer_loss_pct, 4),
        'hv_transmission_loss_pct':  round(hv_loss_pct, 4),
        'total_electrical_loss_pct': round(total, 4),
        'substation_distance_km':    d_hv_km,
        'voltage_mv_kv':             voltage_mv_kv,
        'voltage_hv_kv':             voltage_hv_kv,
        'inputs_valid':              True,
    }


def get_default_model_dict() -> Dict[str, Any]:
    """Devuelve el dict completo (con rangos) para que el frontend renderice los inputs."""
    return {k: dict(v) for k, v in DEFAULT_COST_MODEL.items()}


def get_design_cost_benchmark() -> Dict[str, Any]:
    """Benchmark IRENA global 2024 + benchmark México, para que el frontend
    pueda ofrecer tres opciones: ESTUDIO, IRENA, MEXICO."""
    return {
        **{k: v for k, v in DESIGN_COST_BENCHMARK.items() if k != 'cost_model'},
        'cost_model': dict(DESIGN_COST_BENCHMARK['cost_model']),
        'mexico': {
            **{k: v for k, v in MEXICO_COST_BENCHMARK.items() if k != 'cost_model'},
            'cost_model': dict(MEXICO_COST_BENCHMARK['cost_model']),
        },
    }
