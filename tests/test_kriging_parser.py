from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from core.kriging import (
    analyze_sites, clear_site_stats_cache, fit_power_curve_gauss4,
    parallel_read_sites, parse_turbine_filename, read_wind_file,
    site_wind_stats, turbine_site_economics,
)

_ERA5_HEADER = (
    "Date/time [UTC]\tSpeed_100m [m/s]\tDirection_100m [degrees]\t"
    "Temperature_2m [degrees C]\tPressure_0m [kPa]"
)
_MERRA2_HEADER = (
    "Date/time [UTC]\tSpeed_50m [m/s]\tDirection_50m [degrees]\t"
    "Temperature_10m [degrees C]\tTemperature_2m [degrees C]\tPressure_0m [kPa]"
)


def _write_windnavigator(path: str, header: str, rows: list) -> None:
    """Reproduce el layout real de un export Windnavigator: ~14 líneas de
    metadatos libres, luego la fila de encabezado, luego los datos."""
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write("Site info\n")
        for _ in range(12):
            fh.write("Some metadata line\n")
        fh.write("\n")
        fh.write(header + "\n")
        for row in rows:
            fh.write("\t".join(str(v) for v in row) + "\n")


class KrigingParserTests(unittest.TestCase):
    """Regresión para el bug de columnas: ERA5 (5 cols) y MERRA-2 (6 cols)
    comparten un mismo parser posicional que confundía Pressure_0m con
    Temperature_2m en ERA5, y siempre asumía z_ref=100 m aunque MERRA-2 mide
    a 50 m. Ver core/kriging.py:read_wind_file / _parse_header_columns."""

    def test_era5_columns_are_not_shifted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "SiteERA5_N16,500_W95,000.txt")
            rows = [
                ("2020-01-01T00:00:00", 8.0, 180.0, 25.0, 101.3),
                ("2020-01-01T01:00:00", 9.0, 190.0, 26.0, 101.4),
            ]
            _write_windnavigator(path, _ERA5_HEADER, rows)
            site = read_wind_file(path)

            self.assertEqual(site['z_ref'], 100.0)
            # Antes del fix, t2m tomaba los valores de presión (~101 kPa
            # leídos como °C) y p0m quedaba fijo en la constante 101.3.
            self.assertAlmostEqual(float(site['t2m'][0]), 25.0, places=3)
            self.assertAlmostEqual(float(site['p0m'][0]), 101.3, places=3)
            self.assertAlmostEqual(float(site['ws'][0]), 8.0, places=3)

    def test_merra2_columns_pick_temperature_2m_not_10m(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "SiteMERRA2_N16,500_W95,000.txt")
            rows = [
                ("2020-01-01T00:00:00", 7.0, 170.0, 30.0, 24.0, 100.7),
                ("2020-01-01T01:00:00", 7.5, 175.0, 29.5, 23.8, 100.8),
            ]
            _write_windnavigator(path, _MERRA2_HEADER, rows)
            site = read_wind_file(path)

            self.assertEqual(site['z_ref'], 50.0)
            # Debe tomar Temperature_2m (24.0), no Temperature_10m (30.0).
            self.assertAlmostEqual(float(site['t2m'][0]), 24.0, places=3)
            self.assertAlmostEqual(float(site['p0m'][0]), 100.7, places=3)

    def test_extrapolation_direction_matches_reference_height(self):
        """50m -> 80m debe subir la velocidad; 100m -> 80m debe bajarla."""
        with tempfile.TemporaryDirectory() as tmp:
            era5_path = os.path.join(tmp, "A_ERA5_N16,500_W95,000.txt")
            merra2_path = os.path.join(tmp, "A_MERRA2_N16,500_W95,000.txt")
            rows_common = [("2020-01-01T00:00:00", 7.0, 180.0, 25.0, 101.3)] * 24
            _write_windnavigator(era5_path, _ERA5_HEADER, rows_common)
            _write_windnavigator(
                merra2_path, _MERRA2_HEADER,
                [("2020-01-01T00:00:00", 7.0, 180.0, 25.0, 25.0, 101.3)] * 24,
            )

            era5_stats = site_wind_stats(read_wind_file(era5_path), hub_height_m=80.0)
            merra2_stats = site_wind_stats(read_wind_file(merra2_path), hub_height_m=80.0)

            self.assertLess(era5_stats['v_mean'], era5_stats['v_mean_raw'])
            self.assertGreater(merra2_stats['v_mean'], merra2_stats['v_mean_raw'])

    def test_analyze_sites_parallelizes_sites_and_fits_each_turbine_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            wind_dir = os.path.join(tmp, 'wind')
            turbine_dir = os.path.join(tmp, 'turbines')
            os.makedirs(wind_dir)
            os.makedirs(turbine_dir)
            timestamps = pd.date_range('2020-01-01', periods=48, freq='1h')
            for index, longitude in enumerate(('95,000', '95,250')):
                rows = [
                    (stamp.strftime('%Y-%m-%dT%H:%M:%S'), 7.0 + index + hour / 100.0,
                     180.0, 25.0, 101.3)
                    for hour, stamp in enumerate(timestamps)
                ]
                _write_windnavigator(
                    os.path.join(wind_dir, f'Site{index}_ERA5_N16,500_W{longitude}.txt'),
                    _ERA5_HEADER, rows,
                )
            curve_rows = [
                (0, 0), (3, 0), (4, 100), (6, 400),
                (8, 800), (10, 980), (12, 1000), (25, 1000), (25.1, 0),
            ]
            turbine_files = ('TurbineA_p1000_r40.csv', 'TurbineB_p1200_r45.csv')
            for filename in turbine_files:
                pd.DataFrame(curve_rows).to_csv(
                    os.path.join(turbine_dir, filename), header=False, index=False)

            clear_site_stats_cache()
            with patch('core.kriging.parallel_read_sites', wraps=parallel_read_sites) as parallel_mock, \
                    patch('core.kriging.fit_power_curve_gauss4', wraps=fit_power_curve_gauss4) as fit_mock:
                result = analyze_sites(
                    wind_dir, turbine_dir, year=2020,
                    demand_mwh=1000, tasa=0.08, price_usd_kwh=0.07,
                    price_mxn_per_usd=20.0, anios=25,
                )

            self.assertEqual(parallel_mock.call_count, 1)
            self.assertEqual(fit_mock.call_count, len(turbine_files))
            self.assertEqual(len(result), 2)
            self.assertTrue(all(len(site['turbines']) == 2 for site in result))

            first_site = site_wind_stats(read_wind_file(
                os.path.join(wind_dir, result[0]['name']), year=2020))
            first_turbine_file = sorted(turbine_files)[0]
            curve = pd.read_csv(
                os.path.join(turbine_dir, first_turbine_file), header=None).values
            _, rotor, nominal_kw = parse_turbine_filename(first_turbine_file)
            serial = turbine_site_economics(
                first_site['ws_hub'], first_site['rho'],
                curve[:, 0].astype(float), curve[:, 1].astype(float), rotor, nominal_kw,
                demand_mwh=1000, tasa=0.08, price_usd_kwh=0.07,
                price_mxn_per_usd=20.0, anios=25,
            )
            optimized = next(
                row for row in result[0]['turbines']
                if row['turbina'] == parse_turbine_filename(first_turbine_file)[0]
            )
            for key in ('potencia_wh_sitio', 'factor_capacidad', 'aep_mwh',
                        'capex_usd', 'van_usd', 'lcoe_usd_kwh'):
                self.assertAlmostEqual(optimized[key], serial[key], places=9)


if __name__ == '__main__':
    unittest.main()
