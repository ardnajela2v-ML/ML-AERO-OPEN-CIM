"""
Verifica si cp_effective_full_series / hours_near_cpmax (LAYOUT_WEIGHTS,
core/multicriteria.py) son redundantes con aep_p90_mwh / factor_capacidad,
usando el mismo método (correlación de Spearman entre alternativas) ya
aplicado a la familia económica LCOE/B-C/TIR/recuperación descontada
(docs/ahp-topsis.md §1/§6), que sí resultó ρ=±1.0000 exacto.

Contexto: docs/ahp-topsis.md §1 deja abierta la pregunta de si Cp efectivo
es una dimensión independiente de AEP/factor de capacidad — "no está
resuelto". §6 "Cuarta actividad" (2026-09-14) señala que esa sospecha nunca
se verificó con código, a diferencia del bloque económico. Este script es
esa verificación.

Corrida de referencia (2026-09-14), documentada en docs/ahp-topsis.md
§6 "Cuarta actividad" — resolución: sitio Chimalapas (Istmo de Oaxaca, ERA5
1979-2023, 100 m), las 13 turbinas reales de aerogeneradores/, α de
Hellmann de zona 'istmo' (0.11, Hernández-Escobedo et al. 2014).
Correlaciones obtenidas, todas moderadas (0.22-0.63) y lejos de ±1.0000 —
el bloque técnico NO tiene la misma redundancia probada del bloque
económico. Ver el documento para la lectura completa y las limitaciones
(un solo sitio, n=13 turbinas, AEP bruto aproximado sin pérdidas de estela).

Uso: reejecutar contra otros sitios/zonas del estudio (Tamaulipas, Yucatán,
Baja California, Coahuila) para confirmar si la conclusión generaliza —
ese paso NO se ha hecho todavía.

    python3 scripts/verify_technical_criteria_correlation.py \
        --file "DATOS DE METEREOLOGICOS DE parques/OaxacaIII_ERA5_N16,750_W94,750chimalapas.txt" \
        --zone istmo
"""
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from scipy.stats import spearmanr

from config import Config
from core.data import DataLoader
from core.energy import (
    AEPCalculator, PowerCurve, WindToPowerEngine,
    compute_site_cp_fit, hellmann_alpha_by_zone, list_turbine_info,
)

AREA_KM2 = 4.0
MIN_SPACING_D = 3.0
NEAR_CP_FRACTION = 0.95  # same convention as core/farm_mcda.py


def compute_rows(wind_path, zone):
    df, meta = DataLoader().load_file(wind_path, source_hint='auto')
    ws_height_m = float(meta['wind_height_m'])
    alpha = hellmann_alpha_by_zone(zone)
    area_m2 = AREA_KM2 * 1e6

    aep_calc = AEPCalculator()
    rows = []
    for meta_t in list_turbine_info(Config.TURBINES_FOLDER):
        fpath = os.path.join(Config.TURBINES_FOLDER, meta_t['filename'])
        turbine = PowerCurve.from_file(fpath)
        engine = WindToPowerEngine(turbine, n_turbines=1, availability=1.0,
                                    electrical_losses=0.0, wake_losses=0.0,
                                    curtailment_losses=0.0)
        df_sim, summary = engine.run(df, ws_col='wind_speed', ws_height_m=ws_height_m,
                                      extrapolation='hellmann', hellmann_alpha=alpha)
        aep = aep_calc.compute_from_timeseries(df_sim, 'energy_net_kwh')
        aep_p50_single = aep.get('aep_p50_mwh', summary.get('energy_net_mwh'))

        curve_pts = list(turbine._raw_points)
        cp_fit = compute_site_cp_fit(
            df_sim['ws_hub'].to_numpy(),
            [p[0] for p in curve_pts], [p[1] for p in curve_pts],
            turbine.rotor_diameter_m, dt_hours=1.0, max_w=turbine.cut_out,
            near_cp_fraction=NEAR_CP_FRACTION,
        )

        rated_mw = turbine.rated_power_kw / 1000.0
        factor_capacidad = aep_p50_single * 1000.0 / (rated_mw * 1000.0 * 8760.0)

        # Proxy for the real layout packer (core/farm.py:_build_structured_grid):
        # a square-grid density at a fixed spacing, no wake losses. Directionally
        # right, not the real optimizer's output — see script docstring.
        physical_max_n = max(1, int(area_m2 // (MIN_SPACING_D * turbine.rotor_diameter_m) ** 2))
        aep_p90_bruto_mwh = aep_p50_single * 0.85 * physical_max_n

        rows.append({
            'turbine': meta_t['filename'],
            'cp_effective_full_series': cp_fit['cp_effective_full_series'],
            'hours_near_cpmax': cp_fit['hours_near_cpmax'],
            'factor_capacidad': factor_capacidad,
            'aep_p90_mwh_bruto': aep_p90_bruto_mwh,
        })
    return rows


def report(rows):
    cp_eff = np.array([r['cp_effective_full_series'] for r in rows])
    hours_near = np.array([r['hours_near_cpmax'] for r in rows])
    fc = np.array([r['factor_capacidad'] for r in rows])
    aep90 = np.array([r['aep_p90_mwh_bruto'] for r in rows])

    pairs = [
        ('cp_effective_full_series', 'factor_capacidad', cp_eff, fc),
        ('cp_effective_full_series', 'hours_near_cpmax', cp_eff, hours_near),
        ('cp_effective_full_series', 'aep_p90_mwh_bruto', cp_eff, aep90),
        ('hours_near_cpmax', 'factor_capacidad', hours_near, fc),
        ('hours_near_cpmax', 'aep_p90_mwh_bruto', hours_near, aep90),
        ('factor_capacidad', 'aep_p90_mwh_bruto', fc, aep90),
    ]
    print(f'\nn = {len(rows)} turbinas\n')
    for name_a, name_b, a, b in pairs:
        rho = spearmanr(a, b).correlation
        print(f'  rho({name_a}, {name_b}) = {rho:.3f}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', required=True, help='Ruta a un archivo de viento (Config.DATA_FOLDER u otra).')
    parser.add_argument('--zone', default='istmo', help="Zona para hellmann_alpha_by_zone (default: 'istmo').")
    args = parser.parse_args()

    computed_rows = compute_rows(args.file, args.zone)
    for row in computed_rows:
        print(row)
    report(computed_rows)
