"""
Pruebas del camino dated de chronological_revenue (§7 paso 3).

5a – Turbina única vs. referencia tools/capture_dated_vs_hourofyear.py (2017-2023)
5b – Serie sintética calculable a mano: revenue exacto, floor('h'), convención horaria
5c – Año bisiesto 2020 (expected_hours=8784)
5d – Año incompleto marcado complete=False y excluido de totales
5e – Duplicados en PML: mismo valor → pml_duplicates_dropped=1; distinto → ValueError
5f – ratio_by_sector precalculado produce resultado bit-idéntico
5g – Regresión camino legado: retorno sin by_year ni ratio_by_sector, método igual
GOLDEN – Legacy path con 3 turbinas y precio no plano: constantes de df10ace
DST  – Convención UTC naive, floor('h'), y timestamps de hora 25 (otoño) via read_pml
TEMP – Camino dated con temperature_c/pressure_pa: rho_mean sobre muestras valid∩has_price
SHIFT – Capture ratio 2018 con Δt=−1h, 0h, +1h (informativo)
"""
import calendar
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from core.energy import PowerCurve
from core.farm import chronological_revenue

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_pml_series(year: int, price_fn):
    """PML sintético completo para `year`, resolución horaria, UTC naive."""
    n_h = 8784 if calendar.isleap(year) else 8760
    idx = pd.date_range(f'{year}-01-01', periods=n_h, freq='h')
    return idx.values.astype('datetime64[ns]'), np.array([float(price_fn(i)) for i in range(n_h)])


def _wind_ts(year: int, delta_hours: int = 0):
    """Timestamps UTC naive para año completo, opcionalmente desplazados."""
    n_h = 8784 if calendar.isleap(year) else 8760
    start = pd.Timestamp(year=year, month=1, day=1) + pd.Timedelta(hours=delta_hours)
    return pd.date_range(start, periods=n_h, freq='h').values.astype('datetime64[ns]')


def _turbine_gamesa():
    return PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')


def _P(turbine, ws_ms):
    """Potencia kW para velocidad escalar a rho=1.225."""
    return float(turbine.power_kw_density_corrected(np.array([float(ws_ms)]), 1.225)[0])


# ── GOLDEN: camino legado, constantes de commit df10ace ──────────────────────


class TestLegacyGolden:
    """Verifica que el retorno del camino legado (hour_of_year) es bit-idéntico
    al valor calculado en df10ace antes de cualquier cambio de este paso.

    Generado con:
        turbine = gamesa21_p2000_r45
        ws = rng(99).uniform(2,18,500), wd = rng(99).uniform(0,360,500)
        hoy = arange(500)%8760, price = |rng(1).normal(0.07,0.02,8760)|
        layout = [[0,0],[600,0],[300,520]]
    """
    GOLDEN = {
        'revenue_usd_year1':          2329649.5,
        'aep_mwh':                    33514.78,
        'avg_realized_price_usd_kwh': 0.069511,
        'avg_flat_price_usd_kwh':     0.069856,
        'capture_ratio':              0.9951,
        'n_hours':                    500,
        'rho_mean':                   1.225,
        'method':                     'chronological_hourly_generation_times_hourly_price',
    }

    def test_legacy_golden_values_unchanged(self):
        turbine = _turbine_gamesa()
        rng   = np.random.default_rng(99)
        ws    = rng.uniform(2, 18, 500)
        wd    = rng.uniform(0, 360, 500)
        hoy   = np.arange(500) % 8760
        rng2  = np.random.default_rng(1)
        price = np.abs(rng2.normal(0.07, 0.02, 8760))
        result = chronological_revenue(
            [[0, 0], [600, 0], [300, 520]], turbine, ws, wd, hoy, price)
        for key, expected in self.GOLDEN.items():
            if isinstance(expected, float):
                assert result[key] == pytest.approx(expected, rel=1e-12), (
                    f'{key}: esperado {expected}, obtenido {result[key]}')
            else:
                assert result[key] == expected


# ── 5b: Serie sintética calculable a mano ────────────────────────────────────


class TestDatedSyntheticManual:
    """Respuestas calculables a papel y lápiz."""

    def setup_method(self):
        self.turbine = _turbine_gamesa()

    def test_two_hours_exact_revenue(self):
        """revenue = P(10)*0.05 + P(8)*0.10 exactamente."""
        ts = np.array(['2021-06-15T12:00', '2021-06-15T13:00'], dtype='datetime64[ns]')
        ws = np.array([10.0, 8.0])
        wd = np.zeros(2)
        result = chronological_revenue(
            [[0, 0]], self.turbine, ws, wd,
            timestamps_utc=ts,
            pml_series_values=np.array([0.05, 0.10]),
            pml_series_timestamps_utc=ts.copy(),
            min_coverage=0,
        )
        P10 = _P(self.turbine, 10.0)
        P8  = _P(self.turbine, 8.0)
        expected = P10 * 0.05 + P8 * 0.10
        assert result['by_year'][2021]['revenue_usd'] == pytest.approx(expected, abs=0.011)
        assert result['by_year'][2021]['avg_flat_price_usd_kwh'] == pytest.approx(0.075, rel=1e-9)

    def test_floor_12_30_matches_12_00_price(self):
        """Muestra a las 12:30 UTC hace floor('h') → 12:00 UTC."""
        ts_wind = np.array(['2021-06-15T12:30'], dtype='datetime64[ns]')
        pml_ts  = np.array(['2021-06-15T12:00'], dtype='datetime64[ns]')
        result = chronological_revenue(
            [[0, 0]], self.turbine, np.array([8.0]), np.zeros(1),
            timestamps_utc=ts_wind,
            pml_series_values=np.array([0.07]),
            pml_series_timestamps_utc=pml_ts,
            min_coverage=0,
        )
        assert result['by_year'][2021]['n_hours'] == 1
        assert result['by_year'][2021]['avg_flat_price_usd_kwh'] == pytest.approx(0.07, rel=1e-6)

    def test_on_the_hour_convention(self):
        """Timestamp exactamente en la hora → floor es no-op, precio emparejado."""
        ts = np.array(['2021-03-15T18:00'], dtype='datetime64[ns]')
        result = chronological_revenue(
            [[0, 0]], self.turbine, np.array([9.0]), np.zeros(1),
            timestamps_utc=ts,
            pml_series_values=np.array([0.055]),
            pml_series_timestamps_utc=ts.copy(),
            min_coverage=0,
        )
        P9 = _P(self.turbine, 9.0)
        assert result['by_year'][2021]['revenue_usd'] == pytest.approx(P9 * 0.055, abs=0.011)

    def test_capture_ratio_one_when_price_equals_avg_flat(self):
        """Con precio plano → capture_ratio = 1.0 exacto."""
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.06)
        ts_wind = _wind_ts(2021)
        ws = np.random.default_rng(11).uniform(4, 14, 8760)
        result = chronological_revenue(
            [[0, 0]], self.turbine, ws, np.zeros(8760),
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
        )
        assert result['by_year'][2021]['capture_ratio'] == pytest.approx(1.0, abs=1e-6)

    def test_by_year_keys_are_python_int(self):
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        ts_wind = _wind_ts(2021)
        result = chronological_revenue(
            [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
        )
        for k in result['by_year']:
            assert isinstance(k, int)

    def test_no_price_match_raises(self):
        ts_wind = _wind_ts(2021)
        ts_pml, pml_vals = _make_pml_series(2022, lambda _: 0.05)
        with pytest.raises(ValueError, match='Ninguna muestra'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
                timestamps_utc=ts_wind,
                pml_series_values=pml_vals,
                pml_series_timestamps_utc=ts_pml,
            )

    def test_no_complete_year_raises(self):
        ts = np.array(['2021-06-01T12:00'], dtype='datetime64[ns]')
        with pytest.raises(ValueError, match='cobertura'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.array([8.0]), np.zeros(1),
                timestamps_utc=ts,
                pml_series_values=np.array([0.05]),
                pml_series_timestamps_utc=ts.copy(),
            )

    def test_partial_params_raises(self):
        """Sólo uno de los tres parámetros dated → ValueError."""
        ts_wind = _wind_ts(2021)
        with pytest.raises(ValueError, match='todos o ninguno'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
                timestamps_utc=ts_wind,
            )

    def test_dated_with_price_hourly_raises(self):
        """price_hourly_usd_kwh por nombre (no posicional = hour_of_year) → ValueError."""
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        ts_wind = _wind_ts(2021)
        with pytest.raises(ValueError, match='price_hourly_usd_kwh'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
                price_hourly_usd_kwh=np.full(8760, 0.05),
                timestamps_utc=ts_wind,
                pml_series_values=pml_vals,
                pml_series_timestamps_utc=ts,
            )

    def test_dated_with_hour_of_year_raises(self):
        """hour_of_year no None con use_dated → ValueError."""
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        ts_wind = _wind_ts(2021)
        with pytest.raises(ValueError, match='hour_of_year'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
                np.arange(8760),   # 5.º posicional = hour_of_year (no None)
                timestamps_utc=ts_wind,
                pml_series_values=pml_vals,
                pml_series_timestamps_utc=ts,
            )

    def test_subhourly_sample_duration_raises(self):
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        ts_wind = _wind_ts(2021)
        with pytest.raises(ValueError, match='sample_duration_hours=1'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
                timestamps_utc=ts_wind,
                pml_series_values=pml_vals,
                pml_series_timestamps_utc=ts,
                sample_duration_hours=1/6,
            )

    def test_duplicate_wind_timestamps_raises(self):
        ts_wind = np.array([
            '2021-06-01T12:00', '2021-06-01T12:00',
            '2021-06-01T13:00',
        ], dtype='datetime64[ns]')
        ts_pml = np.array(['2021-06-01T12:00', '2021-06-01T13:00'], dtype='datetime64[ns]')
        with pytest.raises(ValueError, match='misma hora UTC'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(3, 8.0), np.zeros(3),
                timestamps_utc=ts_wind,
                pml_series_values=np.array([0.05, 0.06]),
                pml_series_timestamps_utc=ts_pml,
                min_coverage=0,
            )


# ── 5c: Año bisiesto 2020 ─────────────────────────────────────────────────────


class TestLeapYear2020:

    def setup_method(self):
        self.turbine = _turbine_gamesa()

    def test_leap_year_expected_hours_8784(self):
        ts, pml_vals = _make_pml_series(2020, lambda _: 0.05)
        ts_wind = _wind_ts(2020)
        assert len(ts_wind) == 8784
        result = chronological_revenue(
            [[0, 0]], self.turbine, np.full(8784, 8.0), np.zeros(8784),
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
        )
        assert result['by_year'][2020]['n_hours'] == 8784
        assert result['by_year'][2020]['expected_hours'] == 8784
        assert result['by_year'][2020]['coverage'] == pytest.approx(1.0, abs=1e-6)
        assert result['by_year'][2020]['complete'] is True

    def test_feb29_contributes_to_energy(self):
        """Quitar las 24h del 29-feb reduce la energía exactamente P(8)*24/1000 MWh."""
        ts, pml_vals = _make_pml_series(2020, lambda _: 0.05)
        ts_wind = _wind_ts(2020)
        ws = np.full(8784, 8.0)

        result_full = chronological_revenue(
            [[0, 0]], self.turbine, ws, np.zeros(8784),
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
        )
        pml_idx = pd.DatetimeIndex(ts)
        mask29  = (pml_idx.month == 2) & (pml_idx.day == 29)
        ts_no29  = ts[~mask29]
        pml_no29 = pml_vals[~mask29]
        result_no29 = chronological_revenue(
            [[0, 0]], self.turbine, ws, np.zeros(8784),
            timestamps_utc=ts_wind,
            pml_series_values=pml_no29,
            pml_series_timestamps_utc=ts_no29,
        )
        P8 = _P(self.turbine, 8.0)
        diff = (result_full['by_year'][2020]['energy_mwh']
                - result_no29['by_year'][2020]['energy_mwh'])
        assert diff == pytest.approx(24 * P8 / 1000.0, rel=1e-4)


# ── 5d: Año incompleto ────────────────────────────────────────────────────────


class TestIncompleteYear:

    def setup_method(self):
        self.turbine = _turbine_gamesa()

    def test_partial_year_marked_incomplete(self):
        # 2021 completo (coverage=1.0 ≥ 0.90) + 2024 parcial (744h/8784 ≈ 8.5% < 0.90)
        # El 2021 completo evita ValueError del umbral de cobertura.
        ts_2021, p_2021  = _make_pml_series(2021, lambda _: 0.05)
        ts_2024_p, p_2024 = _make_pml_series(2024, lambda _: 0.05)
        ts_combined = np.concatenate([ts_2021, ts_2024_p[:744]])
        p_combined  = np.concatenate([p_2021, p_2024[:744]])
        ts_wind = np.concatenate([_wind_ts(2021), _wind_ts(2024)[:744]])
        ws = np.full(8760 + 744, 8.0)
        result = chronological_revenue(
            [[0, 0]], self.turbine, ws, np.zeros(8760 + 744),
            timestamps_utc=ts_wind,
            pml_series_values=p_combined,
            pml_series_timestamps_utc=ts_combined,
        )
        assert result['by_year'][2024]['complete'] is False
        assert result['by_year'][2024]['n_hours'] == 744
        assert result['by_year'][2024]['coverage'] < 0.09

    def test_incomplete_year_excluded_from_totals(self):
        ts_2021, p_2021 = _make_pml_series(2021, lambda _: 0.05)
        ts_2022_one = np.array(['2022-06-01T00:00'], dtype='datetime64[ns]')
        ts_combined = np.concatenate([ts_2021, ts_2022_one])
        p_combined  = np.concatenate([p_2021, np.array([0.10])])
        ts_wind = np.concatenate([
            _wind_ts(2021),
            np.array(['2022-06-01T00:00'], dtype='datetime64[ns]'),
        ])
        ws = np.full(8761, 8.0)
        result = chronological_revenue(
            [[0, 0]], self.turbine, ws, np.zeros(8761),
            timestamps_utc=ts_wind,
            pml_series_values=p_combined,
            pml_series_timestamps_utc=ts_combined,
        )
        assert result['by_year'][2021]['complete'] is True
        assert result['by_year'][2022]['complete'] is False
        assert result['years_complete'] == [2021]
        assert result['n_years_complete'] == 1


# ── 5e: Duplicados en PML ─────────────────────────────────────────────────────


class TestPmlDuplicates:

    def setup_method(self):
        self.turbine = _turbine_gamesa()

    def test_duplicate_same_value_drops_silently(self):
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        dup_ts  = np.array(['2021-06-01T00:00'], dtype='datetime64[ns]')
        ts_dup  = np.concatenate([ts, dup_ts])
        pml_dup = np.concatenate([pml_vals, np.array([0.05])])
        ts_wind = _wind_ts(2021)
        ws = np.full(8760, 8.0)
        result = chronological_revenue(
            [[0, 0]], self.turbine, ws, np.zeros(8760),
            timestamps_utc=ts_wind,
            pml_series_values=pml_dup,
            pml_series_timestamps_utc=ts_dup,
        )
        assert result['pml_duplicates_dropped'] == 1
        result_clean = chronological_revenue(
            [[0, 0]], self.turbine, ws, np.zeros(8760),
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
        )
        assert result['by_year'][2021]['revenue_usd'] == pytest.approx(
            result_clean['by_year'][2021]['revenue_usd'], rel=1e-6)

    def test_duplicate_different_value_raises(self):
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        dup_ts  = np.array(['2021-06-01T00:00'], dtype='datetime64[ns]')
        ts_dup  = np.concatenate([ts, dup_ts])
        pml_dup = np.concatenate([pml_vals, np.array([0.99])])
        with pytest.raises(ValueError, match='valores distintos'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
                timestamps_utc=_wind_ts(2021),
                pml_series_values=pml_dup,
                pml_series_timestamps_utc=ts_dup,
            )


# ── 5f: ratio_by_sector precalculado ─────────────────────────────────────────


class TestRatioBySectorPassthrough:

    def setup_method(self):
        self.turbine = _turbine_gamesa()

    def test_precomputed_gives_identical_result(self):
        ts, pml_vals = _make_pml_series(2021, lambda i: 0.05 + 0.02 * (i % 24 / 24))
        ts_wind = _wind_ts(2021)
        rng = np.random.default_rng(42)
        ws = rng.uniform(5, 12, 8760)
        wd = rng.uniform(0, 360, 8760)
        layout = [[0, 0], [500, 0], [250, 433]]

        result_a = chronological_revenue(
            layout, self.turbine, ws, wd,
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
        )
        rbs = np.array(result_a['ratio_by_sector'])
        result_b = chronological_revenue(
            layout, self.turbine, ws, wd,
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
            ratio_by_sector=rbs,
        )
        assert result_b['ratio_by_sector_params']['precomputed'] is True
        assert result_b['by_year'][2021]['revenue_usd'] == pytest.approx(
            result_a['by_year'][2021]['revenue_usd'], rel=1e-9)
        assert result_b['by_year'][2021]['capture_ratio'] == pytest.approx(
            result_a['by_year'][2021]['capture_ratio'], abs=1e-7)

    def test_wrong_shape_raises(self):
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        with pytest.raises(ValueError, match='ratio_by_sector'):
            chronological_revenue(
                [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
                timestamps_utc=_wind_ts(2021),
                pml_series_values=pml_vals,
                pml_series_timestamps_utc=ts,
                ratio_by_sector=np.ones((36, 5)),
            )


# ── 5g: Regresión camino legado ───────────────────────────────────────────────


class TestLegacyPathRegression:

    def setup_method(self):
        self.turbine = _turbine_gamesa()
        rng = np.random.default_rng(7)
        self.ws    = rng.uniform(3, 15, 200)
        self.wd    = rng.uniform(0, 360, 200)
        self.hoy   = np.arange(200)
        self.price = np.full(8760, 0.075)

    def test_flat_price_capture_ratio_is_one(self):
        r = chronological_revenue([[0, 0], [500, 0]], self.turbine,
                                  self.ws, self.wd, self.hoy, self.price)
        assert r['capture_ratio'] == pytest.approx(1.0, abs=1e-6)

    def test_no_by_year_no_ratio_by_sector_in_return(self):
        r = chronological_revenue([[0, 0]], self.turbine,
                                  self.ws, self.wd, self.hoy, self.price)
        assert 'by_year' not in r
        assert 'ratio_by_sector' not in r
        assert r['method'] == 'chronological_hourly_generation_times_hourly_price'

    def test_dated_return_keys_and_no_legacy_revenue_key(self):
        """Camino dated: claves correctas; 'revenue_usd_year1' ausente."""
        ts, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        ts_wind = _wind_ts(2021)
        r = chronological_revenue(
            [[0, 0]], self.turbine, np.full(8760, 8.0), np.zeros(8760),
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts,
        )
        assert r['method'] == 'chronological_dated_generation_times_dated_price'
        assert 'revenue_usd_mean_complete_years' in r
        assert 'aep_mwh_overlap_mean' in r
        assert 'revenue_usd_year1' not in r

    def test_legacy_requires_price_8760(self):
        with pytest.raises(ValueError, match='8760'):
            chronological_revenue([[0, 0]], self.turbine,
                                  self.ws, self.wd, self.hoy, np.full(100, 0.05))

    def test_legacy_requires_hour_of_year(self):
        with pytest.raises(ValueError, match='hour_of_year'):
            chronological_revenue([[0, 0]], self.turbine, self.ws, self.wd)

    def test_legacy_requires_price_hourly(self):
        with pytest.raises(ValueError, match='price_hourly_usd_kwh'):
            chronological_revenue([[0, 0]], self.turbine,
                                  self.ws, self.wd, self.hoy)


# ── TEMP: Camino dated con temperature_c / pressure_pa ───────────────────────


class TestDatedPathTemperaturePressure:
    """Camino dated con temperature_c y pressure_pa: verifica que _valid_hp
    aplica correctamente la máscara (valid ∩ has_price) a la densidad del aire."""

    def setup_method(self):
        self.turbine = _turbine_gamesa()

    def test_rho_mean_uses_valid_and_priced_samples(self):
        """Con 2 ws=NaN y PML completo, rho sobre las 8758 muestras restantes."""
        from core.energy import air_density

        ts_pml, pml_vals = _make_pml_series(2021, lambda _: 0.05)
        ts_wind = _wind_ts(2021)
        ws = np.full(8760, 8.0)
        wd = np.zeros(8760)
        ws[[0, 100]] = np.nan

        temp  = np.full(8760, 7.0)
        press = np.full(8760, 95000.0)
        temp[[0, 100]]  = 999.0
        press[[0, 100]] = 999.0

        result = chronological_revenue(
            [[0, 0]], self.turbine, ws, wd,
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts_pml,
            temperature_c=temp,
            pressure_pa=press,
        )
        valid_mask = np.isfinite(ws) & (ws >= 0)
        rho_expected = float(np.mean(air_density(temp[valid_mask], press[valid_mask], 0.0)))
        assert result['n_hours'] == 8758
        assert result['rho_mean'] == pytest.approx(rho_expected, abs=5e-5)

    def test_rho_mean_excludes_priceless_samples(self):
        """Con PML solo para el primer semestre, rho sobre las primeras 4380 muestras."""
        from core.energy import air_density

        ts_pml_full, pml_vals_full = _make_pml_series(2021, lambda _: 0.05)
        ts_pml  = ts_pml_full[:4380]
        pml_vals = pml_vals_full[:4380]
        ts_wind = _wind_ts(2021)
        ws = np.full(8760, 8.0)
        wd = np.zeros(8760)
        temp  = np.where(np.arange(8760) < 4380, 5.0,  30.0).astype(float)
        press = np.where(np.arange(8760) < 4380, 95000.0, 102000.0).astype(float)

        result = chronological_revenue(
            [[0, 0]], self.turbine, ws, wd,
            timestamps_utc=ts_wind,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=ts_pml,
            temperature_c=temp,
            pressure_pa=press,
            min_coverage=0,
        )
        rho_expected = float(np.mean(air_density(temp[:4380], press[:4380], 0.0)))
        assert result['n_hours'] == 4380
        assert result['rho_mean'] == pytest.approx(rho_expected, abs=5e-5)


# ── DST: convención UTC via read_pml ─────────────────────────────────────────


class TestDstConventionViaReadPml:
    """
    Construye archivos CENACE sintéticos con días de cambio de horario y
    verifica timestamps UTC de read_pml + integración con chronological_revenue.

    TestSyntheticCenaceParser (test_pml_reader.py) ya cubre hora 25 para 4 filas
    del evento de otoño 2018. Esta clase amplía a 3 días contiguos y añade la
    transición de primavera.

    TestSyntheticCenaceParser no es un @pytest.fixture reutilizable (es un
    método de clase sin decorator). Se reutiliza su formato de archivo exacto:
    7 columnas TSV + fila de resumen al final.

    Convenciones del archivo real PML_2017-2024 02JUD115.txt (verificadas con S1):
    - Spring: CENACE usa horas 1-23 secuenciales (hora 24 omitida; sin hueco en UTC).
    - Autumn: CENACE usa horas 1-25 (hora extra). 2022 SÍ tiene hora 25 (30/10/22).
    - Toda la serie UTC tiene 0 duplicados y 0 pasos ≠ 1h.
    """

    def setup_method(self):
        self.turbine = _turbine_gamesa()

    @staticmethod
    def _build_cenace_file(rows):
        """
        Formato idéntico a TestSyntheticCenaceParser (test_pml_reader.py):
        7 cols TSV + fila resumen al final.
        rows: [(fecha 'DD/MM/YY', hora int, precio_usd_kwh float), ...]
        """
        FX = 20.0
        hdr = "nodo\tfecha\thora\tpml\ttipo_cambio_fix\tpml_usd_mwh\tpml_usd_kwh\n"
        body = ''.join(
            f"02JUD-115\t{f}\t{h}\t{p * FX * 1000:.1f}\t{FX}\t{p * 1000:.4f}\t{p:.6f}\n"
            for f, h, p in rows
        )
        body += f"\t\t\t\t{FX}\t0.0\t0.0\n"
        tf = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8')
        tf.write(hdr + body)
        tf.close()
        return tf.name

    def test_autumn_3days_73_timestamps_all_1h_steps(self):
        """
        28-oct-2018 (otoño, 25 h). 3 días: 27-oct + 28-oct + 29-oct = 73 timestamps.
        Referencia de hora 25: TestSyntheticCenaceParser (test_pml_reader.py).
        Verifica además contigüidad multi-día: Δt=1h en toda la serie.
        """
        from core.forecasting import read_pml

        rows = (
            [('27/10/18', h, 0.05) for h in range(1, 25)] +
            [('28/10/18', h, 0.06) for h in range(1, 26)] +
            [('29/10/18', h, 0.04) for h in range(1, 25)]
        )
        path = self._build_cenace_file(rows)
        try:
            res = read_pml(path)
            ts  = res['timestamps_utc']
            assert len(ts) == 73
            assert ts.is_monotonic_increasing
            assert ts.duplicated().sum() == 0
            diffs = ts[1:] - ts[:-1]
            assert (diffs == pd.Timedelta(hours=1)).all(), f'Pasos ≠ 1h: {diffs[diffs != pd.Timedelta(hours=1)]}'
            # hora 25 de 28-oct: índice 48; midnight(28-oct, UTC-5)=05:00Z +24h = T05:00Z Oct 29
            assert ts[48] == pd.Timestamp('2018-10-29T05:00Z')
            # hora 1 de 29-oct: índice 49; midnight(29-oct, UTC-6)=06:00Z
            assert ts[49] == pd.Timestamp('2018-10-29T06:00Z')
        finally:
            os.remove(path)

    def test_spring_3days_71_timestamps_all_1h_steps(self):
        """
        01-abr-2018 (primavera, 23 h). CENACE usa horas 1-23 secuenciales;
        la hora faltante (02:00-03:00 local) no aparece → sin hueco en UTC.
        Verificado con el archivo real: 0 pasos ≠ 1h en toda la serie.
        """
        from core.forecasting import read_pml

        rows = (
            [('31/03/18', h, 0.05) for h in range(1, 25)] +
            [('01/04/18', h, 0.06) for h in range(1, 24)] +
            [('02/04/18', h, 0.04) for h in range(1, 25)]
        )
        path = self._build_cenace_file(rows)
        try:
            res = read_pml(path)
            ts  = res['timestamps_utc']
            assert len(ts) == 71
            assert ts.is_monotonic_increasing
            assert ts.duplicated().sum() == 0
            diffs = ts[1:] - ts[:-1]
            assert (diffs == pd.Timedelta(hours=1)).all(), f'Pasos ≠ 1h en primavera: {diffs[diffs != pd.Timedelta(hours=1)]}'
            # hora 3 del día de primavera: midnight(01-abr, UTC-6)=06:00Z +2h = 08:00Z
            # = 03:00 UTC-5 (primera hora post-spring-forward) ✓; índice = 24+2 = 26
            assert ts[26] == pd.Timestamp('2018-04-01T08:00Z'), (
                f'hora 3 primavera: esperaba T08:00Z, obtuvo {ts[26]}')
        finally:
            os.remove(path)

    def test_autumn_pml_feeds_chronological_revenue_correctly(self):
        """
        Integración: PML (3 días otoño) → chronological_revenue.
        ts_wind = rango UTC explícito (no copia de pml_ts → no circular).
        Revenue afirmado = Σ P(ws_i) × precio_i calculado a mano.
        res['venta'] está en USD/kWh (mean≈0.059 en archivo real).
        """
        from core.forecasting import read_pml

        rows = (
            [('27/10/18', h, 0.05) for h in range(1, 25)] +
            [('28/10/18', h, 0.06) for h in range(1, 26)] +
            [('29/10/18', h, 0.04) for h in range(1, 25)]
        )
        path = self._build_cenace_file(rows)
        try:
            res = read_pml(path)
            ts_idx = res['timestamps_utc']
            if hasattr(ts_idx, 'tz') and ts_idx.tz is not None:
                ts_idx = ts_idx.tz_localize(None)
            pml_ts   = ts_idx.values.astype('datetime64[ns]')
            pml_vals = np.asarray(res['venta'], float)  # USD/kWh confirmado

            # Wind: rango UTC explícito (Oct 27 05:00 → Oct 30 05:00, 73 h, 1h/muestra)
            ts_wind = pd.date_range(
                '2018-10-27 05:00:00', periods=73, freq='h'
            ).values.astype('datetime64[ns]')

            rng = np.random.default_rng(42)
            ws  = rng.uniform(5, 13, 73)
            wd  = np.zeros(73)

            result = chronological_revenue(
                [[0, 0]], self.turbine, ws, wd,
                timestamps_utc=ts_wind,
                pml_series_values=pml_vals,
                pml_series_timestamps_utc=pml_ts.copy(),
                min_coverage=0,
            )
            assert result['n_hours'] == 73

            # Revenue manual: P(ws_i) × precio_i; hora 25 (idx 48) precio 0.06; no estela
            P = self.turbine.power_kw_density_corrected(
                ws.reshape(-1, 1), 1.225).flatten()
            prices = np.concatenate([
                np.full(24, 0.05), np.full(25, 0.06), np.full(24, 0.04)])
            expected_rev = float(np.sum(P * prices))
            # revenue_usd redondeado a 2 decimales → abs=0.011
            assert result['by_year'][2018]['revenue_usd'] == pytest.approx(
                expected_rev, abs=0.011)
        finally:
            os.remove(path)


# ── 5a: Turbina única vs. tools/capture_dated_vs_hourofyear.py ───────────────

# Confirmaciones antes de TOL=0.001:
# - Misma turbina: clipper25_p2500_r44.5 (capture_validation.py::POWER_CURVE_FILE)
# - wd=0 (turbina única, sin estela; wd irrelevante para n=1)
# - Pérdidas default (availability=1.0, electrical_losses=0.0)
# - avg_flat = mean(precio sobre horas de viento del año) ← mismo que capture_metrics
# - res['venta'] en USD/kWh (mean=0.058940, verificado)

PML_PATH = 'mercado_pml/PML_2017-2024 02JUD115.txt'


@pytest.mark.skipif(
    not os.path.isfile(PML_PATH),
    reason='PML real no disponible en este entorno',
)
class TestSingleTurbineVsReference:
    """
    Referencia (tools/capture_dated_vs_hourofyear_output/capture_by_year.csv):
      2017: 0.8413  2018: 0.8780  2019: 0.7883
      2020: 0.8734  2021: 0.8688  2022: 0.8520  2023: 0.8185
      media: 0.8458
    """

    REFERENCE = {
        2017: 0.8413, 2018: 0.8780, 2019: 0.7883,
        2020: 0.8734, 2021: 0.8688, 2022: 0.8520, 2023: 0.8185,
    }
    TOL = 0.001

    @pytest.fixture(scope='class')
    def result(self):
        from core.forecasting import read_pml
        from core.energy import extrapolate_wind_hellmann
        import sys
        sys.path.insert(0, 'tools')
        from corrected_reconstruction import load_corrected_reconstruction

        pml = read_pml(PML_PATH)
        pml_ts = pml['timestamps_utc']
        if hasattr(pml_ts, 'tz') and pml_ts.tz is not None:
            pml_ts = pml_ts.tz_localize(None)
        pml_ts_naive = np.asarray(pml_ts, dtype='datetime64[ns]')
        pml_vals     = np.asarray(pml['venta'], float)  # USD/kWh

        turbine = PowerCurve.from_file('aerogeneradores/clipper25_p2500_r44.5.csv')

        df, _, _ = load_corrected_reconstruction()
        col  = 'long_term_wind_speed_corrected'
        ws_s = df[col]
        good = np.isfinite(ws_s) & (ws_s >= 0)
        ws_s = ws_s[good]
        ws_hub = extrapolate_wind_hellmann(ws_s.to_numpy(), 80.0, 80.0)
        ts_all = ws_s.index.values.astype('datetime64[ns]')
        T = len(ws_hub)

        return chronological_revenue(
            [[0, 0]], turbine, ws_hub, np.zeros(T),
            timestamps_utc=ts_all,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=pml_ts_naive,
        )

    def test_pml_no_duplicates(self, result):
        assert result['pml_duplicates_dropped'] == 0

    def test_n_years_complete_and_list(self, result):
        assert result['n_years_complete'] == 7
        assert result['years_complete'] == list(range(2017, 2024))

    def test_seven_years_present(self, result):
        for y in self.REFERENCE:
            assert y in result['by_year'], f'Año {y} ausente en by_year'

    def test_mean_capture_ratio_2017_2023(self, result):
        vals = [result['by_year'][y]['capture_ratio'] for y in range(2017, 2024)]
        mean = float(np.mean(vals))
        assert mean == pytest.approx(0.8458, abs=0.001), (
            f'Media capture_ratio 2017-2023: {mean:.4f}, esperada 0.8458 ± 0.001')

    def test_capture_ratio_per_year_within_tolerance(self, result):
        """
        avg_flat = media del PML sobre horas de viento del año (igual que
        capture_dated_vs_hourofyear.py::capture_metrics).
        2017: 6 horas sin precio → n_hours≈8754, coverage≈0.9993 (complete=True).
        """
        print('\n[5a] capture_ratio, n_hours y coverage por año:')
        print(f'  {"año":>4}  {"calc":>8}  {"ref":>8}  {"Δ":>8}  {"n_hours":>8}  {"coverage":>9}  {"complete":>8}')
        failed = []
        for y, ref in sorted(self.REFERENCE.items()):
            yr    = result['by_year'][y]
            calc  = yr['capture_ratio']
            cov   = yr['coverage']
            comp  = yr['complete']
            n_h   = yr['n_hours']
            delta = abs(calc - ref) if calc is not None else float('inf')
            ok    = delta <= self.TOL
            print(f'  {y}  {calc:8.4f}  {ref:8.4f}  {delta:8.4f}  {n_h:8d}  {cov:9.4f}  {str(comp):>8}')
            if not ok:
                failed.append((y, calc, ref, delta))
            assert comp is True, (
                f'Año {y}: complete=False (coverage={cov:.4f}). '
                'Si coverage < 0.90 investigar fuente de viento o PML.')

        # Imprimir 2024 y 2025 (informativos; wind series termina en 2023)
        print('\n  Años fuera del rango principal (informativos):')
        for y in [2024, 2025]:
            if y in result['by_year']:
                yr = result['by_year'][y]
                print(f'  {y}: n_hours={yr["n_hours"]}, coverage={yr["coverage"]:.4f}, '
                      f'complete={yr["complete"]}')
            else:
                print(f'  {y}: ausente de by_year (sin muestras de viento)')

        assert not failed, (
            'Años fuera de TOL=0.001 (investigar antes de ampliar): ' +
            ', '.join(f'{y}: calc={c:.4f} ref={r:.4f} Δ={d:.4f}'
                      for y, c, r, d in failed))


# ── SHIFT: sensibilidad a desplazamiento de +/- 1 h (informativo) ────────────


@pytest.mark.skipif(
    not os.path.isfile(PML_PATH),
    reason='PML real no disponible en este entorno',
)
class TestTimestampShiftSensitivity:
    """Capture ratio de 2018 con Δt = −1h, 0h, +1h aplicado al viento.

    INFORMATIVO: cuantifica cuánto cambia el capture_ratio si los timestamps del
    viento se tratan como fin de intervalo (−1h) vs. inicio de intervalo (0h,
    convención de chronological_revenue). +1h es referencia simétrica.

    Este test NO cubre ni verifica la convención horaria del camino dated;
    solo mide la sensibilidad empírica para 2018.
    Los tres valores se reportan en stdout y se afirma únicamente que son
    finitos y en rango razonable (0.5–1.5).
    """

    @pytest.fixture(scope='class')
    def data_2018(self):
        from core.forecasting import read_pml
        from core.energy import extrapolate_wind_hellmann
        import sys
        sys.path.insert(0, 'tools')
        from corrected_reconstruction import load_corrected_reconstruction

        pml = read_pml(PML_PATH)
        pml_ts = pml['timestamps_utc']
        if hasattr(pml_ts, 'tz') and pml_ts.tz is not None:
            pml_ts = pml_ts.tz_localize(None)
        pml_ts_naive = np.asarray(pml_ts, dtype='datetime64[ns]')
        pml_vals     = np.asarray(pml['venta'], float)

        turbine = PowerCurve.from_file('aerogeneradores/clipper25_p2500_r44.5.csv')

        df, _, _ = load_corrected_reconstruction()
        col  = 'long_term_wind_speed_corrected'
        ws_s = df.loc['2018-01-01':'2018-12-31 23:00', col]
        good = np.isfinite(ws_s) & (ws_s >= 0)
        ws_s = ws_s[good]
        ws_hub = extrapolate_wind_hellmann(ws_s.to_numpy(), 80.0, 80.0)
        ts_2018 = ws_s.index.values.astype('datetime64[ns]')
        return turbine, ws_hub, ts_2018, pml_ts_naive, pml_vals

    def _cr_shift(self, data, dh):
        turbine, ws_hub, ts, pml_ts, pml_vals = data
        ts_shifted = (pd.DatetimeIndex(ts) + pd.Timedelta(hours=dh)
                      ).values.astype('datetime64[ns]')
        T = len(ws_hub)
        r = chronological_revenue(
            [[0, 0]], turbine, ws_hub, np.zeros(T),
            timestamps_utc=ts_shifted,
            pml_series_values=pml_vals,
            pml_series_timestamps_utc=pml_ts,
            min_coverage=0,
        )
        return r['by_year'].get(2018, {}).get('capture_ratio')

    def test_shift_values_are_finite_and_reasonable(self, data_2018):
        cr_m1 = self._cr_shift(data_2018, -1)
        cr_0  = self._cr_shift(data_2018,  0)
        cr_p1 = self._cr_shift(data_2018, +1)
        print(f'\n[SHIFT] capture_ratio 2018: -1h={cr_m1:.4f}  0h={cr_0:.4f}  +1h={cr_p1:.4f}')
        for label, cr in [('-1h', cr_m1), ('0h', cr_0), ('+1h', cr_p1)]:
            assert cr is not None and 0.5 < cr < 1.5, f'Shift {label}: {cr}'
