"""Pruebas unitarias para la lectura de PML en core/forecasting.py.

Verifica:
1. Formato nuevo CENACE tabulado (70,128 horas 2017-2024, hora 1-25, DD/MM/YY, pml_usd_kwh).
2. Construcción correcta de timestamps UTC a partir de medianoche local en America/Mexico_City.
3. Manejo de transiciones de horario de verano (días de 23 y 25 horas).
4. No regresión en los formatos legados (PML_usd.txt, PML_IPO.txt, PML_02JUD-115_2018_usd.txt).
5. Integración con normalize_fixed_hourly_profile.
"""
import io
import os
import tempfile
import numpy as np
import pandas as pd
import pytest

from core.forecasting import (
    read_pml,
    _is_cenace_tabulated,
    _read_cenace_tabulated,
    normalize_fixed_hourly_profile,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PML_DIR = os.path.join(BASE_DIR, 'mercado_pml')
NEW_PML_PATH = os.path.join(PML_DIR, 'PML_2017-2024 02JUD115.txt')
OLD_PML_USD_PATH = os.path.join(PML_DIR, 'PML_usd.txt')
OLD_PML_JUD18_PATH = os.path.join(PML_DIR, 'PML_02JUD-115_2018_usd.txt')


class TestPmlLegacyReaders:
    """Verifica que los archivos antiguos sin fecha no se rompen."""

    def test_legacy_format_detection(self):
        assert not _is_cenace_tabulated(OLD_PML_USD_PATH)
        assert not _is_cenace_tabulated(OLD_PML_JUD18_PATH)

    def test_read_pml_usd_legacy(self):
        data = read_pml(OLD_PML_USD_PATH)
        assert 'compra' in data and 'venta' in data
        assert len(data['compra']) == 8759
        assert len(data['venta']) == 8759
        assert data['timestamps'] is None
        assert np.isclose(np.mean(data['compra']), 0.1103435, atol=1e-5)

    def test_read_pml_jud2018_legacy(self):
        data = read_pml(OLD_PML_JUD18_PATH)
        assert len(data['compra']) == 8760
        assert data['timestamps'] is None
        assert np.isclose(np.mean(data['compra']), 0.078993, atol=1e-5)


class TestPmlCenaceTabulatedNew:
    """Verifica la lectura completa del archivo CENACE 2017-2024."""

    @pytest.fixture(scope="class")
    def pml_data(self):
        if not os.path.isfile(NEW_PML_PATH):
            pytest.skip(f"Archivo no disponible en {NEW_PML_PATH}")
        return read_pml(NEW_PML_PATH)

    def test_detection(self):
        assert _is_cenace_tabulated(NEW_PML_PATH)

    def test_row_count_and_footer_ignored(self, pml_data):
        # 70,128 filas de datos horarios (la fila de resumen al final es ignorada)
        assert len(pml_data['compra']) == 70128
        assert len(pml_data['venta']) == 70128
        assert len(pml_data['timestamps']) == 70128
        assert len(pml_data['timestamps_utc']) == 70128

    def test_compra_equals_venta(self, pml_data):
        np.testing.assert_array_equal(pml_data['compra'], pml_data['venta'])
        # Precios PML son no-negativos (existen 61 horas legítimas con PML=0)
        assert np.all(pml_data['compra'] >= 0)
        assert np.all(np.isfinite(pml_data['compra']))
        assert np.mean(pml_data['compra']) > 0.01

    def test_timestamps_utc_monotonic_and_unique(self, pml_data):
        ts_utc = pml_data['timestamps_utc']
        assert ts_utc.is_monotonic_increasing
        assert ts_utc.duplicated().sum() == 0

    def test_period_start_and_end(self, pml_data):
        ts_utc = pml_data['timestamps_utc']
        # 01/01/17 hora 1 local (UTC-6) -> 2017-01-01 06:00:00 UTC
        assert ts_utc[0] == pd.Timestamp('2017-01-01 06:00:00+00:00')
        # 31/12/24 hora 24 local (UTC-6) -> 2025-01-01 05:00:00 UTC
        assert ts_utc[-1] == pd.Timestamp('2025-01-01 05:00:00+00:00')

    def test_dst_transitions_in_data(self, pml_data):
        ts_utc = pml_data['timestamps_utc']
        # Verificar que el paso de hora 25 a hora 1 del día siguiente es continuo (exactamente 1 hora)
        # 29/10/2017 es domingo de otoño con hora 25
        idx_autumn_2017_h25 = 7247  # fila 0-indexada
        assert ts_utc[idx_autumn_2017_h25] == pd.Timestamp('2017-10-30 05:00:00+00:00')
        assert ts_utc[idx_autumn_2017_h25 + 1] == pd.Timestamp('2017-10-30 06:00:00+00:00')
        assert ts_utc[idx_autumn_2017_h25 + 1] - ts_utc[idx_autumn_2017_h25] == pd.Timedelta(hours=1)


class TestSyntheticCenaceParser:
    """Verifica el parser en un archivo sintético controlado con DST y resumen."""

    def test_synthetic_cenace_tabulated(self):
        content = (
            "nodo\tfecha\thora\tpml\ttipo_cambio_fix\tpml_usd_mwh\tpml_usd_kwh\n"
            "02JUD-115\t28/10/18\t23\t500.0\t20.0\t25.0\t0.025000\n"
            "02JUD-115\t28/10/18\t24\t520.0\t20.0\t26.0\t0.026000\n"
            "02JUD-115\t28/10/18\t25\t540.0\t20.0\t27.0\t0.027000\n"
            "02JUD-115\t29/10/18\t1\t480.0\t20.0\t24.0\t0.024000\n"
            "\t\t\t\t19.41\t58.94\t0.058940\n"  # Fila de resumen sin fecha/hora
        )
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            temp_path = f.name

        try:
            assert _is_cenace_tabulated(temp_path)
            res = read_pml(temp_path)
            # Solo 4 filas válidas; la fila de resumen debe ser ignorada
            assert len(res['compra']) == 4
            np.testing.assert_allclose(res['compra'], [0.025, 0.026, 0.027, 0.024])

            # Timestamps UTC:
            # 28/10/18 medianoche local (verano UTC-5) -> 2018-10-28 05:00 UTC
            # hora 23 -> +22h -> 2018-10-29 03:00 UTC
            # hora 24 -> +23h -> 2018-10-29 04:00 UTC
            # hora 25 -> +24h -> 2018-10-29 05:00 UTC
            # 29/10/18 medianoche local (estándar UTC-6) -> 2018-10-29 06:00 UTC
            # hora 1 -> +0h -> 2018-10-29 06:00 UTC
            ts = res['timestamps_utc']
            assert ts[0] == pd.Timestamp('2018-10-29 03:00:00+00:00')
            assert ts[1] == pd.Timestamp('2018-10-29 04:00:00+00:00')
            assert ts[2] == pd.Timestamp('2018-10-29 05:00:00+00:00')
            assert ts[3] == pd.Timestamp('2018-10-29 06:00:00+00:00')
            assert ts.is_monotonic_increasing
            assert ts.duplicated().sum() == 0
        finally:
            os.remove(temp_path)


class TestProfileNormalizationWithNewPml:
    """Verifica que normalize_fixed_hourly_profile acepte la serie multi-año."""

    def test_normalize_multi_year_pml(self):
        if not os.path.isfile(NEW_PML_PATH):
            pytest.skip("PML multi-año no encontrado")
        data = read_pml(NEW_PML_PATH)
        vals, ts, meta = normalize_fixed_hourly_profile(
            data['compra'], timestamps=data['timestamps'])
        assert len(vals) == 70128
        assert len(ts) == 70128
        assert meta['method'] == 'multi_year_keep_all_with_timestamps'
        assert meta['has_timestamps'] is True


class TestTzLocalizeFallbackWarning:
    """Verifica que el except específico emite RuntimeWarning cuando tz_localize falla."""

    def test_fallback_warns_on_type_error(self, monkeypatch, tmp_path):
        """TypeError (pandas viejo) → RuntimeWarning con 'Etc/GMT+6' en el mensaje."""
        original_localize = pd.DatetimeIndex.tz_localize

        def _mock_localize(self, tz, **kwargs):
            if tz == 'America/Mexico_City':
                raise TypeError('mocked old pandas: unexpected keyword argument')
            return original_localize(self, tz, **kwargs)

        monkeypatch.setattr(pd.DatetimeIndex, 'tz_localize', _mock_localize)

        f = tmp_path / 'pml_test.txt'
        f.write_text(
            'nodo\tfecha\thora\tpml\ttipo_cambio_fix\tpml_usd_mwh\tpml_usd_kwh\n'
            '02JUD-115\t01/01/18\t1\t500.0\t20.0\t25.0\t0.025000\n',
            encoding='utf-8',
        )
        with pytest.warns(RuntimeWarning, match='Etc/GMT'):
            result = _read_cenace_tabulated(str(f))
        # El fallback no debe romper el parsing: debe devolver 1 timestamp válido
        assert len(result['compra']) == 1
        assert result['timestamps_utc'] is not None
