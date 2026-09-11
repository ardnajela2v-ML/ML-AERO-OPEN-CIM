"""Reproducible energy basis and layout refinement after park TOPSIS."""
from copy import deepcopy

import numpy as np

from core.farm import simulate_wind_farm


def build_energy_context(sites, turbine, *, wake_model, k_wake, ct,
                         availability, electrical_losses, curtailment_loss=0.0,
                         environmental_loss=0.0):
    # Keep the inputs to the deterministic joint distribution, not a second
    # independently fitted Weibull or an extrapolation of hub-height wind.
    return {
        'version': 1,
        'wind_method': 'topsis_joint_weibull_8760',
        'wind_height_m': float(turbine.hub_height_m),
        'sites': [{key: deepcopy(site[key]) for key in
                   ('v_mean', 'weibull_K', 'rho_mean', 'sectorial') if key in site}
                  for site in sites],
        'wake_model': wake_model, 'k_wake': k_wake, 'ct': ct,
        'n_sectors': 36, 'availability': availability,
        'electrical_losses': electrical_losses,
        'curtailment_loss': curtailment_loss,
        'environmental_loss': environmental_loss,
        'local_wind_policy': 'uniform_park_resource_as_in_topsis',
    }


def energy_evaluator(context, turbine):
    from core.farm_mcda import _joint_synthetic_wind

    if context.get('version') != 1 or not context.get('sites'):
        raise ValueError('Ejecuta TOPSIS nuevamente para guardar su base energética.')
    if not np.isclose(float(context['wind_height_m']), turbine.hub_height_m):
        raise ValueError('La altura cambió; ejecuta TOPSIS nuevamente para comparar layouts.')
    ws, wd, mean_speed, _, rho = _joint_synthetic_wind(context['sites'])
    survival = ((1.0 - float(context.get('curtailment_loss', 0.0))) *
                (1.0 - float(context.get('environmental_loss', 0.0))))

    def evaluate(points):
        sim = simulate_wind_farm(
            points, turbine, ws, wd, rho_override=rho,
            **{key: context[key] for key in ('wake_model', 'k_wake', 'ct',
               'n_sectors', 'availability', 'electrical_losses')})
        if sim.get('error'):
            return sim
        sim['aep_after_wake_operational_mwh'] = sim['aep_mwh']
        # Operational losses already applied by simulate_wind_farm; apply
        # curtailment/environment once and retain gross/no-wake diagnostics.
        for row in [sim, *sim.get('turbines', [])]:
            row['aep_mwh'] *= survival
            for key in ('cf_pct', 'flh'):
                if key in row:
                    row[key] *= survival
        sim['aep_p50_mwh'] = sim['aep_mwh']
        sim['aep_per_turbine_mwh'] = sim['aep_mwh'] / len(points)
        sim['mean_power_per_turbine_kw'] = sim['aep_per_turbine_mwh'] * 1000 / 8760
        sim['wind_source'] = context['wind_method']
        return sim

    return evaluate, ws, wd, mean_speed, rho
