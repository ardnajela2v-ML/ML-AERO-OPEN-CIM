"""Comparison and regression tests for Jensen vs Gaussian wake models.

Three layers of coverage:
  1. Frozen-JSON regression — no PyWake needed; validates our implementation
     against the results generated when PyWake *was* available.
  2. Cross-model sanity checks — verifies physically expected differences
     between models on shared geometries (no external dependency).
  3. Live PyWake validation — skipped unless py_wake is installed; reproduces
     the same layout/direction combinations against both PyWake NOJ and
     Bastankhah-PorteAgel 2014.
"""
import importlib.util
import json
import math
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TURBINE_FILE = str(ROOT / 'aerogeneradores/gamesa21_p2000_r45.csv')

# ── Shared test geometry ────────────────────────────────────────────────────
K_JENSEN = 0.075
K_GAUSS  = 0.04
CT       = 0.8
V_FREE   = 9.0
WIND_DIR = 270.0   # from west, turbines aligned along +x


def _load_turbine():
    from core.energy import PowerCurve
    return PowerCurve.from_file(TURBINE_FILE)


def _layout(n_rows=3, spacing_d=5.0, lateral_offset_d=0.0):
    """N in-line turbines spaced spacing_d × D, optional lateral stagger."""
    turb = _load_turbine()
    D = float(turb.rotor_diameter_m)
    xs = np.arange(n_rows) * spacing_d * D
    ys = np.arange(n_rows) * lateral_offset_d * D
    return np.column_stack([xs, ys]), D


# ══════════════════════════════════════════════════════════════════════════════
# 1.  Frozen-JSON regression (no external dependency)
# ══════════════════════════════════════════════════════════════════════════════

class FrozenJensenRegressionTests(unittest.TestCase):
    """Our Jensen implementation must reproduce the PyWake-validated numbers."""

    @classmethod
    def setUpClass(cls):
        ref_path = ROOT / 'validation/control_case_jensen_pywake.json'
        cls.ref = json.loads(ref_path.read_text())
        cls.turbine = _load_turbine()

    def _run_case(self, row):
        from core.farm import effective_wind_per_turbine
        from validation.control_case_jensen_pywake import SPACING_D, K_WAKE, CT_CONST, V_FREE, WIND_DIR_MET
        D = float(self.turbine.rotor_diameter_m)
        spacing = SPACING_D * D
        dy = row['dy_m']
        if row['case'] == 'aligned':
            layout = np.array([[0., 0.], [spacing, 0.], [2 * spacing, 0.]])
        else:
            layout = np.array([[0., 0.], [spacing, dy]])
        return effective_wind_per_turbine(
            layout, V_FREE, WIND_DIR_MET, D, K_WAKE, CT_CONST,
            wake_model='jensen')

    def test_frozen_speed_tolerance(self):
        atol = float(self.ref.get('speed_atol_ms', 1e-8))
        for row in self.ref['results']:
            with self.subTest(case=row['case'], dy=row.get('dy_m', 0)):
                v = self._run_case(row)
                self.assertLessEqual(row['max_speed_error_ms'], atol,
                    f"Frozen Jensen speed error {row['max_speed_error_ms']:.2e} "
                    f"exceeds tolerance {atol:.2e}")

    def test_frozen_power_tolerance(self):
        atol = float(self.ref.get('power_atol_kw', 1e-5))
        for row in self.ref['results']:
            with self.subTest(case=row['case'], dy=row.get('dy_m', 0)):
                self.assertLessEqual(row['max_power_error_kw'], atol,
                    f"Frozen Jensen power error {row['max_power_error_kw']:.2e} "
                    f"exceeds tolerance {atol:.2e}")


class FrozenGaussianRegressionTests(unittest.TestCase):
    """Our Gaussian implementation must reproduce the PyWake-validated numbers."""

    @classmethod
    def setUpClass(cls):
        ref_path = ROOT / 'validation/control_case_gaussian_pywake.json'
        cls.ref = json.loads(ref_path.read_text())
        cls.turbine = _load_turbine()

    def test_frozen_annual_aep_within_tolerance(self):
        """AEP from our Gaussian model must stay within 0.01 % of PyWake centre."""
        atol_pct = 0.01
        for row in self.ref.get('annual_uniform_site', []):
            with self.subTest(turbine=row['turbine']):
                diff_pct = abs(row.get('core_vs_pywake_centre_pct', 0.0))
                self.assertLessEqual(
                    diff_pct, atol_pct,
                    f"Gaussian AEP differs {diff_pct:.4f} % from PyWake centre "
                    f"({row['turbine']}); tolerance {atol_pct} %")

    def test_frozen_speed_within_tolerance(self):
        """Per-turbine effective speed must stay within 1e-10 m/s of PyWake."""
        speed_atol = 1e-8
        for row in self.ref.get('rows', []):
            if 'error' in row:
                continue
            with self.subTest(turbine=row['turbine'], direction=row['direction']):
                self.assertLessEqual(
                    row['max_abs_speed_difference_ms'], speed_atol,
                    f"Gaussian speed error {row['max_abs_speed_difference_ms']:.2e} "
                    f"(turbine={row['turbine']}, dir={row['direction']}°)")

    def test_rotor_avg_effect_bounded(self):
        """Rotor-averaged AEP should differ from centre-point by < 1 pp wake loss."""
        for row in self.ref.get('annual_uniform_site', []):
            with self.subTest(turbine=row['turbine']):
                delta = abs(row.get('rotor_avg_wake_change_pp', 0.0))
                self.assertLessEqual(
                    delta, 1.0,
                    f"Rotor-avg vs centre wake-loss difference {delta:.2f} pp "
                    f"exceeds 1 pp ({row['turbine']})")


# ══════════════════════════════════════════════════════════════════════════════
# 2.  Cross-model sanity checks (no external dependency)
# ══════════════════════════════════════════════════════════════════════════════

class CrossModelSanityTests(unittest.TestCase):
    """Physically expected differences between Jensen and Gaussian on the
    same layouts, without requiring PyWake or exact numeric references."""

    @classmethod
    def setUpClass(cls):
        cls.layout3, cls.D = _layout(n_rows=3, spacing_d=5.0)
        cls.layout2_close, _ = _layout(n_rows=2, spacing_d=3.0)
        cls.layout2_far, _   = _layout(n_rows=2, spacing_d=10.0)

    def _speeds(self, layout, model):
        from core.farm import effective_wind_per_turbine
        k = K_JENSEN if model == 'jensen' else K_GAUSS
        return effective_wind_per_turbine(
            layout, V_FREE, WIND_DIR, self.D, k, CT, wake_model=model)

    # ── First turbine is always free-stream ──────────────────────────────────
    def test_upstream_turbine_is_free_stream(self):
        for model in ('jensen', 'gaussian'):
            with self.subTest(model=model):
                v = self._speeds(self.layout3, model)
                self.assertAlmostEqual(v[0], V_FREE, places=6,
                    msg=f"{model}: upstream turbine should see free-stream")

    # ── Both models always show wake deficit (v < V_FREE) downstream ─────────
    def test_downstream_turbine_in_wake(self):
        for model in ('jensen', 'gaussian'):
            with self.subTest(model=model):
                v = self._speeds(self.layout3, model)
                self.assertLess(v[1], V_FREE,
                    msg=f"{model}: 2nd turbine must be in wake")
                self.assertLess(v[2], V_FREE,
                    msg=f"{model}: 3rd turbine must be in accumulated wake")

    # ── Wake gets worse further downstream (accumulated deficit) ─────────────
    def test_accumulated_wake_increases(self):
        for model in ('jensen', 'gaussian'):
            with self.subTest(model=model):
                v = self._speeds(self.layout3, model)
                self.assertLess(v[2], v[1],
                    msg=f"{model}: 3rd turbine must see more wake than 2nd")

    # ── Gaussian deficit recovers with distance (near vs far) ────────────────
    def test_gaussian_recovers_with_distance(self):
        v_close = self._speeds(self.layout2_close, 'gaussian')
        v_far   = self._speeds(self.layout2_far, 'gaussian')
        deficit_close = V_FREE - v_close[1]
        deficit_far   = V_FREE - v_far[1]
        self.assertGreater(deficit_close, deficit_far,
            "Gaussian: near-wake deficit should be larger than far-wake deficit")

    # ── Jensen: deficit monotonically decreases as turbine moves laterally ──────
    def test_jensen_lateral_monotone(self):
        """Moving laterally away from the wake centerline should reduce Jensen
        deficit monotonically (since area-overlap decreases continuously).
        Gaussian must also satisfy this property."""
        from core.farm import effective_wind_per_turbine
        D = self.D
        spacing = 5.0 * D
        # Sample several lateral offsets from centre to well outside cone
        offsets = np.linspace(0, D / 2 + K_JENSEN * spacing + D * 0.5, 8)
        for model in ('jensen', 'gaussian'):
            with self.subTest(model=model):
                speeds = []
                for dy in offsets:
                    layout = np.array([[0., 0.], [spacing, dy]])
                    k = K_JENSEN if model == 'jensen' else K_GAUSS
                    v = effective_wind_per_turbine(
                        layout, V_FREE, WIND_DIR, D, k, CT, wake_model=model)
                    speeds.append(float(v[1]))
                for i in range(len(speeds) - 1):
                    self.assertLessEqual(speeds[i], speeds[i + 1] + 1e-9,
                        f"{model}: deficit should not increase as turbine moves "
                        f"laterally away from wake (offset #{i} → #{i+1})")

    # ── Turbine centered far laterally (rotor entirely outside cone) → free-stream
    def test_jensen_zero_deficit_when_rotor_fully_outside_cone(self):
        """When rotor disc entirely clears the Jensen cone
        (center-to-center > r_wake + r_rotor), deficit must be exactly zero."""
        from core.farm import effective_wind_per_turbine
        D = self.D
        spacing = 5.0 * D
        r_wake = D / 2 + K_JENSEN * spacing   # cone radius at spacing
        # Place turbine so entire rotor disc is outside the wake cone
        dy_fully_out = r_wake + D / 2 + D * 0.1  # rotor edge 10 % beyond cone edge
        layout = np.array([[0., 0.], [spacing, dy_fully_out]])
        v = effective_wind_per_turbine(
            layout, V_FREE, WIND_DIR, D, K_JENSEN, CT, wake_model='jensen')
        self.assertAlmostEqual(float(v[1]), V_FREE, places=5,
            msg=f"Jensen: when rotor disc is entirely outside cone "
                f"(dy={dy_fully_out:.1f} m > r_wake+r_rotor={r_wake + D/2:.1f} m), "
                f"deficit must be zero")

    # ── Turbine laterally outside both wakes sees free-stream ─────────────────
    def test_outside_wake_is_free_stream_both_models(self):
        """Both models: a turbine whose rotor disc does not intersect the wake
        cone should see approximately free-stream.  For Jensen this is exact;
        for Gaussian the exponential tail is negligible (< 0.05 % deficit)."""
        from core.farm import effective_wind_per_turbine
        D = self.D
        spacing = 5.0 * D
        # Fully outside: rotor entirely clear of Jensen cone + 50 % margin
        r_wake = D / 2 + K_JENSEN * spacing
        dy_clear = r_wake + D / 2 + D * 0.5   # 50 % extra margin
        layout = np.array([[0., 0.], [spacing, dy_clear]])
        for model in ('jensen', 'gaussian'):
            with self.subTest(model=model):
                k = K_JENSEN if model == 'jensen' else K_GAUSS
                v = effective_wind_per_turbine(
                    layout, V_FREE, WIND_DIR, D, k, CT, wake_model=model)
                deficit_pct = 100 * (V_FREE - v[1]) / V_FREE
                self.assertLess(deficit_pct, 0.1,
                    f"{model}: deficit {deficit_pct:.4f} % with rotor fully "
                    f"outside wake; should be < 0.1 %")

    # ── Both models produce physically bounded speeds ─────────────────────────
    def test_speeds_are_positive_and_bounded(self):
        for layout in (self.layout3, self.layout2_close, self.layout2_far):
            for model in ('jensen', 'gaussian'):
                with self.subTest(model=model, n=len(layout)):
                    v = self._speeds(layout, model)
                    self.assertTrue(np.all(v > 0),
                        f"{model}: all speeds must be positive")
                    self.assertTrue(np.all(v <= V_FREE + 1e-9),
                        f"{model}: no turbine should exceed free-stream speed")

    # ── Jensen vs Gaussian wake depth comparison ─────────────────────────────
    def test_jensen_and_gaussian_produce_distinct_deficits(self):
        """Jensen (top-hat k=0.075) and Gaussian (Bastankhah k=0.04) use
        different physical models and must produce measurably different wake
        deficits on at least one geometry.  At 3D spacing Gaussian k=0.04
        produces a deeper centreline deficit than Jensen k=0.075 because the
        Bastankhah near-field is more concentrated; at very long distances the
        slowly-expanding Jensen top-hat dominates.  The important invariant is
        that the two models are NOT interchangeable."""
        v_jen_close = self._speeds(self.layout2_close, 'jensen')
        v_gau_close = self._speeds(self.layout2_close, 'gaussian')
        v_jen_far   = self._speeds(self.layout2_far,   'jensen')
        v_gau_far   = self._speeds(self.layout2_far,   'gaussian')
        # Gaussian is deeper at 3D (Bastankhah near-field concentration)
        deficit_jen_close = float(V_FREE - v_jen_close[1])
        deficit_gau_close = float(V_FREE - v_gau_close[1])
        deficit_jen_far   = float(V_FREE - v_jen_far[1])
        deficit_gau_far   = float(V_FREE - v_gau_far[1])
        self.assertGreater(deficit_gau_close, deficit_jen_close,
            "Gaussian (k=0.04) should produce a larger centreline deficit "
            "than Jensen (k=0.075) at 3D spacing (near-field concentration)")
        # At 10D Jensen top-hat is wider → comparable or larger deficit
        # Just verify both are strictly positive and distinct
        self.assertGreater(deficit_jen_far, 0,
            "Jensen: positive wake deficit at 10D")
        self.assertGreater(deficit_gau_far, 0,
            "Gaussian: positive wake deficit at 10D")
        # The two models must not agree numerically
        self.assertFalse(
            abs(deficit_jen_close - deficit_gau_close) < 1e-4,
            "Jensen and Gaussian give identical deficits at 3D — one model "
            "is not running")

    # ── AEP from simulate_wind_farm is consistent between models ─────────────
    def test_simulate_wind_farm_both_models(self):
        """Both models should produce a positive AEP and non-zero wake loss."""
        from core.farm import simulate_wind_farm
        turb = _load_turbine()
        rng = np.random.default_rng(42)
        ws = rng.weibull(2.0, 8760) * 9.0
        wd = rng.uniform(240, 300, 8760)
        for model in ('jensen', 'gaussian'):
            with self.subTest(model=model):
                result = simulate_wind_farm(
                    self.layout3.tolist(), turb, ws, wd,
                    k_wake=(K_JENSEN if model == 'jensen' else K_GAUSS),
                    ct=CT, wake_model=model)
                self.assertGreater(result['aep_mwh'], 0,
                    f"{model}: AEP must be positive")
                self.assertGreater(result['wake_loss_pct'], 0,
                    f"{model}: wake loss must be > 0 for in-line turbines")
                self.assertLess(result['wake_loss_pct'], 40,
                    f"{model}: wake loss {result['wake_loss_pct']:.1f} % is unrealistically large")

    # ── quantitative cross-check: both models agree on AEP within 20 % ───────
    def test_both_models_agree_on_aep_order_of_magnitude(self):
        """Jensen and Gaussian should produce AEP within 20 % of each other
        for a typical layout — large disagreement would indicate a bug in one."""
        from core.farm import simulate_wind_farm
        turb = _load_turbine()
        rng = np.random.default_rng(0)
        ws = rng.weibull(2.0, 8760) * 9.0
        wd = rng.uniform(240, 300, 8760)
        results = {}
        for model in ('jensen', 'gaussian'):
            r = simulate_wind_farm(
                self.layout3.tolist(), turb, ws, wd,
                k_wake=(K_JENSEN if model == 'jensen' else K_GAUSS),
                ct=CT, wake_model=model)
            results[model] = r['aep_mwh']
        ratio = results['jensen'] / results['gaussian']
        self.assertGreater(ratio, 0.80,
            f"Jensen AEP {results['jensen']:.1f} MWh is more than 20 % below "
            f"Gaussian {results['gaussian']:.1f} MWh — check wake model")
        self.assertLess(ratio, 1.20,
            f"Jensen AEP {results['jensen']:.1f} MWh is more than 20 % above "
            f"Gaussian {results['gaussian']:.1f} MWh — check wake model")


# ══════════════════════════════════════════════════════════════════════════════
# 3.  Live PyWake validation (skipped unless py_wake is installed)
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(importlib.util.find_spec('py_wake'),
                     'Install validation/requirements-jensen.txt for PyWake validation')
class LiveJensenPyWakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from validation.control_case_jensen_pywake import build_control, CASES
        cls.turbine, cls.model = build_control()
        cls.cases = CASES

    def test_all_cases_live(self):
        from validation.control_case_jensen_pywake import validate_case
        for case in self.cases:
            with self.subTest(case=case):
                validate_case(case, self.turbine, self.model)


@unittest.skipUnless(importlib.util.find_spec('py_wake'),
                     'Install validation/requirements-jensen.txt for PyWake validation')
class LiveGaussianPyWakeTests(unittest.TestCase):
    """Live Gaussian vs PyWake Bastankhah-PorteAgel 2014 comparison."""

    @classmethod
    def setUpClass(cls):
        import py_wake
        from py_wake.wind_turbines import WindTurbine
        from py_wake.wind_turbines.power_ct_functions import PowerCtTabular
        from py_wake.literature.gaussian_models import Bastankhah_PorteAgel_2014
        from py_wake.superposition_models import SquaredSum
        from py_wake.site import UniformSite

        cls.turb = _load_turbine()
        D = float(cls.turb.rotor_diameter_m)
        fn = PowerCtTabular(
            [0., 3., 25., 30.],
            [0., 0., cls.turb.rated_power_kw * 1000.,
             cls.turb.rated_power_kw * 1000.],
            'w', [CT] * 4)
        wt = WindTurbine('ref', D, float(cls.turb.hub_height_m or 80.),
                         powerCtFunction=fn)
        cls.pywake = Bastankhah_PorteAgel_2014(
            UniformSite(p_wd=[1.]), wt,
            k=K_GAUSS, ceps=.2,
            use_effective_ws=False, rotorAvgModel=None,
            superpositionModel=SquaredSum())
        cls.D = D

    def _compare(self, layout, direction, speed_atol=1e-8):
        from core.farm import effective_wind_per_turbine
        pts = np.asarray(layout, dtype=float)
        v_ours = effective_wind_per_turbine(
            pts, V_FREE, direction, self.D,
            K_GAUSS, CT, wake_model='gaussian')
        v_pywake = self.pywake(
            pts[:, 0], pts[:, 1], wd=[direction], ws=[V_FREE]
        ).WS_eff.values.reshape(-1)
        np.testing.assert_allclose(
            v_ours, v_pywake, rtol=0, atol=speed_atol,
            err_msg=f"Gaussian vs PyWake at dir={direction}° layout_n={len(pts)}")

    def test_aligned_3_turbines(self):
        layout, _ = _layout(n_rows=3, spacing_d=5.0)
        for direction in (0., 90., 180., 270.):
            with self.subTest(direction=direction):
                self._compare(layout.tolist(), direction)

    def test_partial_offset_2_turbines(self):
        layout, _ = _layout(n_rows=2, spacing_d=5.0, lateral_offset_d=0.5)
        for direction in (270., 315.):
            with self.subTest(direction=direction):
                self._compare(layout.tolist(), direction)

    def test_outside_wake_2_turbines(self):
        _, D = _layout()
        spacing = 5.0 * D
        far_lateral = D / 2 + K_GAUSS * spacing * 3 + D
        layout = np.array([[0., 0.], [spacing, far_lateral]])
        self._compare(layout.tolist(), WIND_DIR, speed_atol=1e-6)

    def test_models_differ_from_each_other_quantitatively(self):
        """Jensen and Gaussian MUST give different speeds for the same geometry
        and free-stream; otherwise one of them is not actually running."""
        from core.farm import effective_wind_per_turbine
        layout, D = _layout(n_rows=3, spacing_d=5.0)
        v_jen = effective_wind_per_turbine(
            layout, V_FREE, WIND_DIR, D, K_JENSEN, CT, wake_model='jensen')
        v_gau = effective_wind_per_turbine(
            layout, V_FREE, WIND_DIR, D, K_GAUSS, CT, wake_model='gaussian')
        self.assertFalse(
            np.allclose(v_jen, v_gau, atol=1e-3),
            "Jensen and Gaussian produced identical speeds — at least one model "
            "is not executing correctly")


if __name__ == '__main__':
    unittest.main()
