"""Compare the project's active Gaussian wake calculation with matched PyWake settings."""
import json
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/tmp/windpro-mpl')
import numpy as np

from core.energy import PowerCurve
from core.farm import effective_wind_per_turbine, bin_wind_to_sectors, simulate_wind_farm
from py_wake.wind_turbines import WindTurbine
from py_wake.wind_turbines.power_ct_functions import PowerCtTabular
from py_wake.literature.gaussian_models import Bastankhah_PorteAgel_2014
from py_wake.superposition_models import SquaredSum
from py_wake.rotor_avg_models import GQGridRotorAvg
from py_wake.site import UniformSite

ROOT = Path(__file__).resolve().parents[1]
rows = json.loads((ROOT / 'exports/benchmark_horario_20260924/production_validation.json').read_text())['alternatives']
reference = ROOT / 'exports/benchmark_temporal_20260923/benchmark.py'
scope = {'__file__': str(reference)}
exec(reference.read_text().split('rows=[]\n')[0], scope)
directions, speed_bins, frequencies = bin_wind_to_sectors(scope['ws'], scope['wd'], 36)
results = []
annual = []
for row in rows:
    turbine = PowerCurve.from_file(str(ROOT / 'aerogeneradores' / row['turbina']))
    d = float(turbine.rotor_diameter_m)
    ct = .8
    function = PowerCtTabular([0., 3., 25., 30.], [0., 0., turbine.rated_power_kw * 1000.,
                                                 turbine.rated_power_kw * 1000.], 'w', [ct] * 4)
    py_turbine = WindTurbine(row['turbina'], d, 80., powerCtFunction=function)
    model = Bastankhah_PorteAgel_2014(
        UniformSite(p_wd=[1.]), py_turbine, k=.04, ceps=.2,
        use_effective_ws=False, rotorAvgModel=None, superpositionModel=SquaredSum())
    rotor_model = Bastankhah_PorteAgel_2014(
        UniformSite(p_wd=[1.]), py_turbine, k=.04, ceps=.2,
        use_effective_ws=False, rotorAvgModel=GQGridRotorAvg(4, 4),
        superpositionModel=SquaredSum())
    points = np.asarray(row['layout_xy_m'], dtype=float)
    for direction in (0., 45., 90., 180., 270., 315.):
        try:
            local = effective_wind_per_turbine(points, 9., direction, d,
                                               k=.04, ct=ct, wake_model='gaussian')
            external = model(points[:, 0], points[:, 1], wd=[direction], ws=[9.]).WS_eff.values.reshape(-1)
            rotor_averaged = rotor_model(points[:, 0], points[:, 1], wd=[direction], ws=[9.]).WS_eff.values.reshape(-1)
            diff = local - external
            centre_power = float(np.sum(turbine.power_kw(external)))
            rotor_power = float(np.sum(turbine.power_kw(rotor_averaged)))
            results.append({'turbine': row['turbina'], 'direction': direction,
                            'max_abs_speed_difference_ms': float(np.max(np.abs(diff))),
                            'mean_abs_speed_difference_ms': float(np.mean(np.abs(diff))),
                            'max_relative_difference_pct': float(np.max(np.abs(diff) / external) * 100.),
                            'rotor_average_max_abs_speed_change_ms': float(np.max(np.abs(rotor_averaged - external))),
                            'rotor_average_total_power_change_pct':
                                100. * (rotor_power / centre_power - 1.) if centre_power else None})
        except Exception as exc:
            results.append({'turbine': row['turbina'], 'direction': direction, 'error': str(exc)})

    core = simulate_wind_farm(points, turbine, scope['ws'], scope['wd'],
                              sample_duration_hours=1/6, **scope['opts'])
    centre_speeds = model(points[:, 0], points[:, 1], wd=directions,
                          ws=speed_bins).WS_eff.values
    rotor_speeds = rotor_model(points[:, 0], points[:, 1], wd=directions,
                               ws=speed_bins).WS_eff.values
    survival = scope['opts']['availability'] * (1 - scope['opts']['electrical_losses'])
    def annual_energy(speeds):
        power = turbine.power_kw_density_corrected(speeds, scope['rho'])
        return float(np.sum(power * frequencies[None, :, :]) * 8760 * survival / 1000)
    centre_aep = annual_energy(centre_speeds)
    rotor_aep = annual_energy(rotor_speeds)
    free_aep = core['aep_no_wake_mwh']
    rotor_wake = 100 * (1 - rotor_aep / free_aep) if free_aep else None
    annual.append({'turbine': row['turbina'], 'core_aep_mwh_uniform_site': core['aep_mwh'],
                   'pywake_centre_aep_mwh_uniform_site': centre_aep,
                   'pywake_rotor_avg_aep_mwh_uniform_site': rotor_aep,
                   'core_vs_pywake_centre_pct': 100 * (core['aep_mwh'] / centre_aep - 1),
                   'rotor_avg_vs_centre_pct': 100 * (rotor_aep / centre_aep - 1),
                   'core_wake_loss_pct_uniform_site': core['wake_loss_pct'],
                   'rotor_avg_wake_loss_pct_uniform_site': rotor_wake,
                   'rotor_avg_wake_change_pp': rotor_wake - core['wake_loss_pct']})

out = {'comparison': 'PyWake Bastankhah-PorteAgel 2014, same k/Ct, rotor center, squared-sum, free-stream normalization',
       'wind_speed_ms': 9., 'rows': results, 'annual_uniform_site': annual}
(ROOT / 'validation/control_case_gaussian_pywake.json').write_text(json.dumps(out, indent=2))
valid = [r for r in results if 'error' not in r]
print('cases', len(results), 'valid', len(valid), 'errors', len(results) - len(valid))
print('max absolute speed difference', max(r['max_abs_speed_difference_ms'] for r in valid))
print('max relative difference pct', max(r['max_relative_difference_pct'] for r in valid))
print('rotor-average max speed change', max(r['rotor_average_max_abs_speed_change_ms'] for r in valid))
print('rotor-average total power change pct range',
      min(r['rotor_average_total_power_change_pct'] for r in valid),
      max(r['rotor_average_total_power_change_pct'] for r in valid))
print('largest cases', sorted(valid, key=lambda r: -r['max_abs_speed_difference_ms'])[:5])
print('annual core vs centre pct', [round(r['core_vs_pywake_centre_pct'], 4) for r in annual])
print('annual rotor avg vs centre pct', [round(r['rotor_avg_vs_centre_pct'], 4) for r in annual])
print('rotor avg wake change pp', [round(r['rotor_avg_wake_change_pp'], 4) for r in annual])
