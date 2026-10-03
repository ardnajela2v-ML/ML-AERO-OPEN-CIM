"""Assert WindPro's exact circular-overlap Jensen model against PyWake NOJ.

Constant Ct, momentum induction, area-overlap rotor averaging, free-stream
normalization and squared-sum superposition isolate the wake calculation.
Power is checked using the same WindPro curve for both sets of effective speeds.
This validates these controlled configurations, not measured farm performance.

Run from any directory with Python; see validation/README_jensen.md.
"""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault('MPLCONFIGDIR', '/tmp/windpro-mpl')

import numpy as np

from core.energy import PowerCurve
from core.farm import effective_wind_per_turbine, jensen_deficit

K_WAKE = 0.075
CT_CONST = 0.8
V_FREE = 9.0
WIND_DIR_MET = 270.0  # From west, downstream along +x.
SPACING_D = 5.0
# Absolute tolerances cover floating-point geometry/coordinate rotation only.
SPEED_ATOL = 1e-10  # m/s
POWER_ATOL = 1e-7  # kW
CASES = ('aligned', 'partial_overlap', 'outside_wake')


def build_control():
    """Load the reference implementation only when explicitly running validation."""
    from py_wake.wind_turbines import WindTurbine
    from py_wake.wind_turbines.power_ct_functions import PowerCtTabular
    from py_wake.deficit_models.noj import NOJDeficit
    from py_wake.deficit_models.utils import ct2a_mom1d
    from py_wake.rotor_avg_models import AreaOverlapAvgModel
    from py_wake.superposition_models import SquaredSum
    from py_wake.wind_farm_models import PropagateDownwind
    from py_wake.site import UniformSite

    turbine = PowerCurve.from_file(str(ROOT / 'aerogeneradores/gamesa21_p2000_r45.csv'))
    power_ct = PowerCtTabular(
        [0., 3., 25., 30.], [0., 0., turbine.rated_power_kw * 1000.,
                                     turbine.rated_power_kw * 1000.],
        'w', [CT_CONST] * 4)
    wt = WindTurbine('control', turbine.rotor_diameter_m, turbine.hub_height_m,
                     powerCtFunction=power_ct)
    # Select momentum induction explicitly instead of PyWake's Madsen default.
    deficit = NOJDeficit(k=K_WAKE, ct2a=ct2a_mom1d,
                         rotorAvgModel=AreaOverlapAvgModel())
    model = PropagateDownwind(UniformSite(p_wd=[1.], ti=.1), wt,
                             wake_deficitModel=deficit, superpositionModel=SquaredSum())
    return turbine, model


def validate_case(case, turbine, model):
    """Raise AssertionError on a geometry, scalar, vector or power mismatch."""
    diameter = float(turbine.rotor_diameter_m)
    spacing = SPACING_D * diameter
    rotor = diameter / 2.
    wake = rotor + K_WAKE * spacing
    full_deficit = (1. - np.sqrt(1. - CT_CONST)) * (rotor / wake) ** 2
    if case == 'aligned':
        offsets = [0.]
    elif case == 'partial_overlap':
        # Include the formerly lost band wake < |dy| < wake + rotor,
        # both signs, and points near the inner and outer tangencies.
        offsets = [sign * dy for sign in (-1., 1.) for dy in
                   (wake - rotor + 1., wake, wake + rotor / 2., wake + rotor - 1.)]
    elif case == 'outside_wake':
        offsets = [sign * dy for sign in (-1., 1.) for dy in
                   (wake + rotor, wake + rotor + 1., wake + 2. * rotor)]
    else:
        raise ValueError(f'Unknown control case: {case}')

    rows = []
    for dy in offsets:
        layout = np.array([[0., 0.], [spacing, dy]])
        if case == 'aligned':
            layout = np.array([[0., 0.], [spacing, 0.], [2. * spacing, 0.]])
        scalar = jensen_deficit(spacing, dy, diameter, k=K_WAKE, ct=CT_CONST)
        actual = effective_wind_per_turbine(
            layout, V_FREE, WIND_DIR_MET, diameter, k=K_WAKE,
            ct=CT_CONST, wake_model='jensen')
        expected = model(layout[:, 0], layout[:, 1], wd=[WIND_DIR_MET],
                         ws=[V_FREE]).WS_eff.values.reshape(-1)
        label = f'{case}, dy={dy:g} m'
        np.testing.assert_allclose(actual, expected, rtol=0, atol=SPEED_ATOL, err_msg=label)
        np.testing.assert_allclose(V_FREE * (1. - scalar), expected[1],
                                   rtol=0, atol=SPEED_ATOL, err_msg=label + ' scalar')
        np.testing.assert_allclose(actual[0], V_FREE, rtol=0, atol=SPEED_ATOL)
        if case == 'aligned':
            farther = (1. - np.sqrt(1. - CT_CONST)) * (rotor / (rotor + K_WAKE * 2. * spacing)) ** 2
            analytic = V_FREE * (1. - np.array([0., full_deficit,
                                                np.hypot(full_deficit, farther)]))
            np.testing.assert_allclose(actual, analytic, rtol=0, atol=SPEED_ATOL)
        elif case == 'partial_overlap':
            assert 0. < scalar < full_deficit, label
            assert V_FREE * (1. - full_deficit) < actual[1] < V_FREE, label
        else:
            assert scalar == 0., label
            np.testing.assert_allclose(actual, V_FREE, rtol=0, atol=SPEED_ATOL)
        power_actual = turbine.power_kw(actual)
        power_expected = turbine.power_kw(expected)
        np.testing.assert_allclose(power_actual, power_expected, rtol=0,
                                   atol=POWER_ATOL, err_msg=label + ' power')
        rows.append({'case': case, 'dy_m': dy,
                     'max_speed_error_ms': float(np.max(np.abs(actual - expected))),
                     'max_power_error_kw': float(np.max(np.abs(power_actual - power_expected)))})
    return rows


def main():
    import py_wake
    turbine, model = build_control()
    rows = [row for case in CASES for row in validate_case(case, turbine, model)]
    print(json.dumps({'status': 'passed', 'pywake_version': py_wake.__version__,
                      'numpy_version': np.__version__, 'speed_atol_ms': SPEED_ATOL,
                      'power_atol_kw': POWER_ATOL, 'results': rows}, indent=2))


if __name__ == '__main__':
    main()
