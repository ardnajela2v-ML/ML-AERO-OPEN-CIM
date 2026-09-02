from __future__ import annotations

import os
import tempfile
import unittest

from core.kriging import read_wind_file, site_wind_stats

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


if __name__ == '__main__':
    unittest.main()
