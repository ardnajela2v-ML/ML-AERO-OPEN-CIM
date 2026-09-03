from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from windmcp.data import circular_mean, discover_certe_files, read_certe_file, read_windnavigator_file, resample_hourly
from windmcp.energy import PowerCurve, compare_period_energy
from windmcp.api import create_app
from windmcp.data import ReferenceData
from windmcp.mcp import (
    SEASONAL_QUANTILE_METHOD, apply_direction_offset,
    apply_seasonal_sector_direction_model, apply_sector_direction_model,
    fit_direction_offset, fit_linear, fit_seasonal_sector_direction_model,
    fit_sector_direction_model, metrics, validate_direction,
)
from windmcp.mcp import compare_mcp_methods, fit_mcp_model, predict_mcp, select_mcp_method
from windmcp.ml import compare_ml_methods, select_ml_method, validate_ml_direction
from windmcp.backtest import run_walk_forward_backtest, select_speed_model_training_holdout
from windmcp.config import ConfigurationError, MCPConfig
from windmcp.profile import fit_vertical_profile, normalize_reference_height
from windmcp.qc import quality_control
from windmcp.readiness import assess_reconstruction_readiness
from windmcp.service import _wind_rose_payload
from core.wind_stats import compute_wind_rose


class WindMCPTests(unittest.TestCase):
    def test_linear_model_recovers_known_parameters(self):
        reference = np.arange(1.0, 8.0)
        observed = 0.4 + 1.1 * reference
        model = fit_linear(reference, observed)
        self.assertAlmostEqual(model["intercept"], 0.4, places=10)
        self.assertAlmostEqual(model["slope"], 1.1, places=10)

    def test_identity_metrics_are_zero_error(self):
        values = np.array([1.0, 2.0, 5.0])
        result = metrics(values, values)
        self.assertEqual(result["N"], 3)
        self.assertAlmostEqual(result["RMSE"], 0.0)
        self.assertAlmostEqual(result["MAE"], 0.0)
        self.assertAlmostEqual(result["Bias"], 0.0)

    def test_certe_parser_and_hourly_resampling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "M04_201801.txt"
            path.write_text(
                "YYYY MM DD hh mm WS_80mA_mean WS_80mB_mean WD_78m_mean\n"
                " Height 80 80 78\n"
                " Units m/s m/s °\n"
                "2018 01 01 00 00 4.0 4.2 359\n"
                "2018 01 01 00 10 6.0 6.2 1\n"
                "2018 01 01 00 20 8.0 8.2 0\n",
                encoding="utf-8",
            )
            frame, metadata = read_certe_file(str(path))
            self.assertEqual(metadata["format"], "certe")
            self.assertEqual(frame.index[0].tz, pd.Timestamp("2018-01-01", tz="UTC").tz)
            normalized = frame.rename(columns={"WS_80mA_mean": "wind_speed_obs", "WD_78m_mean": "wind_direction_obs"})
            hourly = resample_hourly(normalized)
            self.assertAlmostEqual(hourly.iloc[0]["wind_speed_obs"], 6.0)
            self.assertTrue(hourly.iloc[0]["wind_direction_obs"] < 1.0 or hourly.iloc[0]["wind_direction_obs"] > 359.0)

    def test_certe_naive_logger_time_is_localized_before_utc_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "M04_201801.txt"
            path.write_text(
                "YYYY MM DD hh mm WS_80mA_mean WD_78m_mean\n"
                " Height 80 78\n"
                " Units m/s °\n"
                "2018 01 01 00 00 8.0 340\n",
                encoding="utf-8",
            )
            frame, metadata = read_certe_file(str(path), timezone="Etc/GMT+6")
            self.assertEqual(frame.index[0], pd.Timestamp("2018-01-01 06:00:00", tz="UTC"))
            self.assertEqual(metadata["timezone_input"], "Etc/GMT+6")
            self.assertEqual(metadata["timezone_output"], "UTC")

    def test_certe_uses_exact_measured_height_before_extrapolating(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "M04_multiheight.txt"
            path.write_text(
                "YYYY MM DD hh mm WS_40m_mean WS_60m_mean WS_80mA_mean WD_78m_mean\n"
                " Height 40 60 80 78\n"
                " Units m/s m/s m/s deg\n"
                "2018 01 01 00 00 4.0 6.0 8.0 350\n",
                encoding="utf-8",
            )
            frame, metadata = read_certe_file(
                str(path),
                target_column="WS_80mA_mean",
                target_height_m=60,
            )
            self.assertEqual(metadata["target_column"], "WS_60m_mean")
            self.assertEqual(metadata["measurement_height_m"], 60.0)
            self.assertEqual(metadata["target_column_selection"], "exact_height_auto_selected")
            self.assertEqual(frame.iloc[0]["WS_60m_mean"], 6.0)

    def test_windnavigator_parser_extracts_height_and_utc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference_ERA5.txt"
            path.write_text(
                "Reanalysis data requested by Windographer\n"
                "ERA5\nLatitude: 16.5\nLongitude: -95\n"
                "Date/time [UTC]\tSpeed_100m [m/s]\tDirection_100m [degrees]\tTemperature_2m [degrees C]\tPressure_0m [kPa]\n"
                "2018-01-01T00:00:00\t5.0\t359\t25.0\t100.0\n",
                encoding="utf-8",
            )
            reference = read_windnavigator_file(str(path))
            self.assertEqual(reference.metadata["wind_height_m"], 100.0)
            self.assertEqual(str(reference.frame.index[0].tz), "UTC")
            self.assertEqual(reference.frame.iloc[0]["wind_speed_ref"], 5.0)

    def test_qc_marks_missing_invalid_and_jump_without_deleting(self):
        index = pd.date_range("2018-01-01", periods=4, freq="10min", tz="UTC")
        frame = pd.DataFrame({"wind_speed_obs": [3.0, np.nan, -1.0, 30.0]}, index=index)
        checked, report = quality_control(frame, "wind_speed_obs", expected_minutes=10, max_jump=5.0)
        self.assertEqual(len(checked), 4)
        self.assertEqual(report["records_original"], 4)
        self.assertEqual(checked.iloc[1]["quality_status"], "MISSING")
        self.assertEqual(checked.iloc[2]["quality_status"], "INVALID")
        self.assertEqual(checked.iloc[3]["quality_status"], "SUSPECT")

    def test_temporal_validation_trains_only_on_train_year(self):
        train_index = pd.date_range("2018-01-01", periods=5, freq="1h", tz="UTC")
        test_index = pd.date_range("2019-01-01", periods=3, freq="1h", tz="UTC")
        index = train_index.append(test_index)
        reference = np.arange(1.0, 9.0)
        observed = np.r_[0.4 + 1.1 * reference[:5], np.array([2.0, 3.0, 4.0])]
        frame = pd.DataFrame({"wind_speed_ref": reference, "wind_speed_obs": observed}, index=index)
        result = validate_direction(frame, 2018, 2019)
        self.assertAlmostEqual(result["model"]["intercept"], 0.4, places=10)
        self.assertAlmostEqual(result["model"]["slope"], 1.1, places=10)
        self.assertEqual(result["metrics"]["N"], 3)

    def test_direction_offset_is_fitted_on_training_year_and_corrects_rotation(self):
        train_index = pd.date_range("2018-01-01", periods=6, freq="1h", tz="UTC")
        test_index = pd.date_range("2019-01-01", periods=4, freq="1h", tz="UTC")
        index = train_index.append(test_index)
        reference_direction = np.array([350, 20, 60, 100, 180, 260, 300, 330, 10, 40], dtype=float)
        observed_direction = (reference_direction + 25.0) % 360.0
        reference_speed = np.arange(1.0, len(index) + 1)
        frame = pd.DataFrame({
            "wind_speed_ref": reference_speed,
            "wind_speed_obs": 0.5 + reference_speed,
            "wind_direction_ref": reference_direction,
            "wind_direction_obs": observed_direction,
        }, index=index)
        fitted = fit_direction_offset(
            frame.loc[frame.index.year == 2018, "wind_direction_obs"],
            frame.loc[frame.index.year == 2018, "wind_direction_ref"],
        )
        self.assertAlmostEqual(fitted["offset_deg"], 25.0)
        corrected = apply_direction_offset(reference_direction[-4:], fitted["offset_deg"])
        np.testing.assert_allclose(corrected, observed_direction[-4:])
        validation = validate_direction(frame, 2018, 2019)
        np.testing.assert_allclose(
            validation["predictions"]["predicted_wind_direction"],
            observed_direction[-4:],
        )

    def test_16_sector_direction_model_is_independent_and_sector_specific(self):
        reference = np.r_[np.zeros(120), np.full(120, 180.0)]
        observed = np.r_[np.full(120, 30.0), np.full(120, 160.0)]
        model = fit_sector_direction_model(
            observed, reference, n_sectors=16, minimum_pairs_per_sector=100
        )
        corrected = apply_sector_direction_model(np.array([0.0, 180.0]), model)
        np.testing.assert_allclose(corrected, np.array([30.0, 160.0]), atol=1e-9)
        self.assertEqual(model["purpose"], "wind_rose_direction_only")
        self.assertEqual(model["n_sectors"], 16)

    def test_seasonal_direction_model_uses_2018_month_specific_offsets(self):
        january = pd.date_range("2018-01-01", periods=240, freq="1h", tz="UTC")
        may = pd.date_range("2018-05-01", periods=240, freq="1h", tz="UTC")
        index = january.append(may)
        reference = np.tile(np.arange(240, dtype=float) % 360.0, 2)
        observed = np.r_[(reference[:240] + 20.0) % 360.0,
                         (reference[240:] - 35.0) % 360.0]
        model = fit_seasonal_sector_direction_model(
            observed, reference, index, n_sectors=16,
            minimum_pairs_per_sector=5, minimum_pairs_per_month=100,
        )
        target_index = pd.DatetimeIndex([
            pd.Timestamp("2019-01-15", tz="UTC"),
            pd.Timestamp("2019-05-15", tz="UTC"),
        ])
        corrected = apply_seasonal_sector_direction_model(
            np.array([90.0, 90.0]), target_index, model
        )
        np.testing.assert_allclose(corrected, np.array([110.0, 55.0]), atol=1e-9)
        self.assertEqual(model["method"], "seasonal_sectorwise_circular_offset")
        self.assertEqual(model["N_train"], 480)

    def test_seasonal_quantile_mapping_preserves_monthly_2018_relationships(self):
        january_train = pd.date_range("2018-01-01", periods=120, freq="1h", tz="UTC")
        may_train = pd.date_range("2018-05-01", periods=120, freq="1h", tz="UTC")
        january_test = pd.date_range("2019-01-01", periods=40, freq="1h", tz="UTC")
        may_test = pd.date_range("2019-05-01", periods=40, freq="1h", tz="UTC")
        train_index = january_train.append(may_train)
        test_index = january_test.append(may_test)
        train_reference = np.r_[np.linspace(2.0, 12.0, 120), np.linspace(2.0, 12.0, 120)]
        train_observed = np.r_[train_reference[:120] * 1.4, train_reference[120:] * 0.7]
        model = fit_mcp_model(
            train_reference, train_observed, method=SEASONAL_QUANTILE_METHOD,
            timestamps=train_index,
        )
        test_reference = np.r_[np.linspace(3.0, 11.0, 40), np.linspace(3.0, 11.0, 40)]
        _, predicted = predict_mcp(
            model, test_reference, timestamps=test_index
        )
        np.testing.assert_allclose(predicted[:40], test_reference[:40] * 1.4, atol=0.02)
        np.testing.assert_allclose(predicted[40:], test_reference[40:] * 0.7, atol=0.02)
        self.assertEqual(model["monthly_models"]["1"]["fallback_level"], "month")
        self.assertEqual(model["monthly_models"]["5"]["fallback_level"], "month")

    def test_circular_mean_wraps_zero(self):
        value = circular_mean(pd.Series([359.0, 1.0]))
        self.assertTrue(value < 1.0 or value > 359.0)

    def test_wind_rose_sectors_are_centred_on_north_and_clockwise(self):
        rose = _wind_rose_payload(
            pd.Series([5.0, 6.0, 7.0, 8.0]),
            pd.Series([359.0, 0.0, 1.0, 340.0]),
            36,
        )
        self.assertAlmostEqual(rose["frequency_pct"][0], 75.0)
        self.assertAlmostEqual(rose["frequency_pct"][34], 25.0)
        self.assertEqual(rose["labels"][34], "340°")
        self.assertEqual(rose["sector_alignment"], "centred_on_label")

    def test_resource_wind_rose_uses_same_north_clockwise_convention(self):
        rose = compute_wind_rose(
            np.array([5.0, 6.0, 7.0, 8.0, 9.0]),
            np.array([359.0, 0.0, 1.0, 22.5, 90.0]),
            n_sectors=16,
        )
        self.assertAlmostEqual(rose["frequency_pct"][0], 60.0)
        self.assertAlmostEqual(rose["frequency_pct"][1], 20.0)
        self.assertAlmostEqual(rose["frequency_pct"][4], 20.0)
        self.assertEqual(rose["labels"][0], "0°")
        self.assertEqual(rose["labels"][4], "90°")
        self.assertEqual(rose["direction_convention"], "meteorological_from_north_clockwise")
        self.assertEqual(rose["sector_alignment"], "centred_on_label")

    def test_period_energy_uses_power_curve_and_reports_delta(self):
        curve = PowerCurve(
            name="synthetic",
            wind_speed_ms=np.array([0.0, 5.0, 10.0, 25.0]),
            power_kw_values=np.array([0.0, 100.0, 500.0, 500.0]),
        )
        predictions = pd.DataFrame({
            "timestamp": pd.date_range("2019-01-01", periods=2, freq="1h", tz="UTC"),
            "observed_wind_speed": [5.0, 10.0],
            "predicted_wind_speed": [5.0, 10.0],
        })
        summary, rows = compare_period_energy(predictions, curve, turbine_count=2, losses_pct=10.0)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(summary["observed_period_energy_mwh"], 1.08)
        self.assertAlmostEqual(summary["predicted_period_energy_mwh"], 1.08)
        self.assertAlmostEqual(summary["delta_energy_pct"], 0.0)

    def test_api_health_capabilities_and_structured_validation_error(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "runs")).test_client()
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.get_json()["success"])
            self.assertEqual(health.get_json()["data"]["version"], "1.0.0")

            capabilities = client.get("/v1/mcp/capabilities")
            self.assertEqual(capabilities.status_code, 200)
            self.assertIn("CERTE", capabilities.get_json()["data"]["formats"]["observations"])

            invalid = client.post("/v1/mcp/run", json={"observation_files": []})
            self.assertEqual(invalid.status_code, 422)
            payload = invalid.get_json()
            self.assertFalse(payload["success"])
            self.assertEqual(payload["error"]["code"], "validation_error")
            self.assertIn("request_id", payload)

    def test_certe_discovery_prefers_consolidated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "certe"
            consolidated = root / "consolidados"
            consolidated.mkdir(parents=True)
            (consolidated / "M04_CERTE_2018.txt").write_text("annual", encoding="utf-8")
            full = consolidated / "M04_CERTE_CONSOLIDADO_2017-2020.txt"
            full.write_text("full", encoding="utf-8")
            discovered = discover_certe_files(str(root))
            self.assertEqual(discovered, [str(full)])

    def test_vertical_profile_normalizes_reference_to_target_height(self):
        index = pd.date_range("2018-01-01", periods=40, freq="1h", tz="UTC")
        alpha = 0.2
        base = np.linspace(4.0, 12.0, len(index))
        frame = pd.DataFrame(
            {
                "WS_20m_mean": base * (20.0 / 80.0) ** alpha,
                "WS_40m_mean": base * (40.0 / 80.0) ** alpha,
                "WS_60m_mean": base * (60.0 / 80.0) ** alpha,
                "wind_speed_obs": base,
            },
            index=index,
        )
        profile = fit_vertical_profile(
            frame,
            observation_height_m=80,
            target_height_m=80,
            training_year=2018,
        )
        self.assertAlmostEqual(profile.alpha, alpha, places=8)
        reference = ReferenceData(
            "reference.txt",
            pd.DataFrame({"wind_speed_ref": [5.0]}, index=index[:1]),
            {"wind_height_m": 50.0},
        )
        normalized, adjustment = normalize_reference_height(reference, profile)
        self.assertEqual(adjustment["operation"], "extrapolation_up")
        self.assertAlmostEqual(normalized.frame.iloc[0]["wind_speed_ref"], 5.0 * (80.0 / 50.0) ** alpha)

    def test_reconstruction_readiness_requires_annual_seasonal_coverage(self):
        index = pd.date_range("2018-01-01", "2019-12-31 23:00", freq="1h", tz="UTC")
        reference = 8.0 + 2.0 * np.sin(np.arange(len(index)) / 100.0)
        aligned = pd.DataFrame(
            {"wind_speed_obs": 0.4 + 1.1 * reference, "wind_speed_ref": reference},
            index=index,
        )
        report = assess_reconstruction_readiness(aligned)
        self.assertEqual(report["status"], "recommended")
        self.assertEqual(report["minimum_recommended_months"], 12)
        self.assertEqual(report["durations"][0]["status"], "conditional")

    def test_mcp_method_arena_selects_and_compares_without_leaking_test_year(self):
        train_index = pd.date_range("2018-01-01", periods=48, freq="1h", tz="UTC")
        test_index = pd.date_range("2019-01-01", periods=24, freq="1h", tz="UTC")
        index = train_index.append(test_index)
        reference = np.linspace(2.0, 14.0, len(index))
        observed = 0.5 + 0.8 * reference + 0.02 * reference ** 2
        frame = pd.DataFrame({"wind_speed_ref": reference, "wind_speed_obs": observed}, index=index)
        selection = select_mcp_method(frame, 2018)
        self.assertIn(selection["selected_method"], {"linear", "variance_ratio", "quadratic"})
        comparison = compare_mcp_methods(frame, 2018, 2019)
        self.assertEqual({row["method"] for row in comparison}, {"linear", "variance_ratio", "quadratic"})
        model = fit_mcp_model(reference[:48], observed[:48], method="quadratic")
        _, prediction = predict_mcp(model, reference[48:])
        self.assertEqual(len(prediction), 24)

    def test_sector_quantile_mcp_preserves_distribution_out_of_sample(self):
        rng = np.random.default_rng(17)
        train_index = pd.date_range("2018-01-01", periods=2400, freq="1h", tz="UTC")
        test_index = pd.date_range("2019-01-01", periods=2400, freq="1h", tz="UTC")
        reference_train = np.clip(rng.weibull(2.0, len(train_index)) * 9.0, 0.1, 30.0)
        reference_test = np.clip(rng.weibull(2.0, len(test_index)) * 9.0, 0.1, 30.0)
        reference = np.r_[reference_train, reference_test]
        observed = 0.35 + 0.72 * reference + 0.025 * reference ** 2
        direction_ref = rng.uniform(0.0, 360.0, len(reference))
        direction_obs = (direction_ref + 18.0) % 360.0
        frame = pd.DataFrame({
            "wind_speed_ref": reference,
            "wind_speed_obs": observed,
            "wind_direction_ref": direction_ref,
            "wind_direction_obs": direction_obs,
        }, index=train_index.append(test_index))
        validation = validate_direction(frame, 2018, 2019, method="sector_qm_16")
        self.assertEqual(validation["model"]["n_sectors"], 16)
        self.assertLess(abs(validation["metrics"]["delta_weibull_k"]), 0.08)
        self.assertLess(abs(validation["metrics"]["delta_weibull_lambda"]), 0.2)

    def test_ml_arena_uses_chronological_training_and_test_year(self):
        train_index = pd.date_range("2018-01-01", periods=80, freq="1h", tz="UTC")
        test_index = pd.date_range("2019-01-01", periods=40, freq="1h", tz="UTC")
        index = train_index.append(test_index)
        reference = np.linspace(2.0, 12.0, len(index))
        observed = 0.6 + 0.9 * reference + 0.15 * np.sin(np.arange(len(index)) / 8.0)
        frame = pd.DataFrame({"wind_speed_ref": reference, "wind_speed_obs": observed}, index=index)
        selection = select_ml_method(frame, 2018, methods=("ridge",))
        self.assertEqual(selection["selected_method"], "ml_ridge")
        comparison = compare_ml_methods(frame, 2018, 2019, methods=("ridge",))
        self.assertEqual(comparison[0]["method"], "ml_ridge")
        validation = validate_ml_direction(frame, 2018, 2019, "ml_ridge")
        self.assertEqual(validation["test_year"], 2019)
        self.assertEqual(validation["metrics"]["N"], 40)

    def test_multi_year_backtest_skips_unavailable_fold_and_pools_predictions(self):
        index = pd.date_range("2018-01-01", periods=30, freq="1h", tz="UTC").append(
            pd.date_range("2019-01-01", periods=30, freq="1h", tz="UTC")
        )
        reference = np.linspace(2.0, 12.0, len(index))
        observed = 0.5 + 0.8 * reference
        frame = pd.DataFrame({"wind_speed_ref": reference, "wind_speed_obs": observed}, index=index)
        report = run_walk_forward_backtest(
            frame,
            folds=[(2018, 2019), (2019, 2020)],
            mcp_methods=("linear",),
            ml_methods=("ridge",),
        )
        self.assertEqual(report["valid_folds"], [{"train_year": 2018, "test_year": 2019}])
        self.assertEqual(report["selected_method"], "linear")
        self.assertEqual(report["summary"][0]["N"], 30)

    def test_speed_selection_does_not_consult_test_year(self):
        train_index = pd.date_range("2020-01-01", periods=160, freq="1h", tz="UTC")
        test_index = pd.date_range("2021-01-01", periods=80, freq="1h", tz="UTC")
        reference = np.linspace(2.0, 14.0, len(train_index) + len(test_index))
        train_observed = 0.5 + 0.7 * reference[:160] + 0.04 * reference[:160] ** 2
        frame_a = pd.DataFrame({
            "wind_speed_ref": reference,
            "wind_speed_obs": np.r_[train_observed, np.full(80, 1.0)],
        }, index=train_index.append(test_index))
        frame_b = frame_a.copy()
        frame_b.loc[test_index, "wind_speed_obs"] = np.linspace(5.0, 50.0, len(test_index))

        selection_a = select_speed_model_training_holdout(
            frame_a, 2020, mcp_methods=("linear", "quadratic"), ml_methods=("ridge",)
        )
        selection_b = select_speed_model_training_holdout(
            frame_b, 2020, mcp_methods=("linear", "quadratic"), ml_methods=("ridge",)
        )

        self.assertEqual(selection_a["selected_method"], selection_b["selected_method"])
        self.assertEqual(selection_a["selected_metrics"], selection_b["selected_metrics"])
        self.assertFalse(selection_a["test_year_used_for_selection"])
        self.assertEqual(selection_a["pairs_fit_base"], 120)
        self.assertEqual(selection_a["pairs_selection_base"], 40)

    def test_speed_selection_changes_when_training_holdout_changes(self):
        index = pd.date_range("2020-01-01", periods=160, freq="1h", tz="UTC")
        reference = np.linspace(2.0, 14.0, len(index))
        quadratic = 0.5 + 0.7 * reference + 0.04 * reference ** 2
        linear_tail = quadratic.copy()
        coefficients = np.polyfit(reference[:120], quadratic[:120], 1)
        linear_tail[120:] = np.polyval(coefficients, reference[120:])
        quadratic_frame = pd.DataFrame({
            "wind_speed_ref": reference, "wind_speed_obs": quadratic,
        }, index=index)
        linear_frame = pd.DataFrame({
            "wind_speed_ref": reference, "wind_speed_obs": linear_tail,
        }, index=index)

        quadratic_selection = select_speed_model_training_holdout(
            quadratic_frame, 2020, mcp_methods=("linear", "quadratic"), ml_methods=()
        )
        linear_selection = select_speed_model_training_holdout(
            linear_frame, 2020, mcp_methods=("linear", "quadratic"), ml_methods=()
        )

        self.assertEqual(quadratic_selection["selected_method"], "quadratic")
        self.assertEqual(linear_selection["selected_method"], "linear")

    def test_config_requires_training_year_before_test_year(self):
        base = {
            "observation_files": ["observation.csv"],
            "reference_files": ["reference.txt"],
        }
        with self.assertRaisesRegex(ConfigurationError, "earlier than test_year"):
            MCPConfig.from_mapping({**base, "train_year": 2021, "test_year": 2021})
        with self.assertRaisesRegex(ConfigurationError, "earlier than test_year"):
            MCPConfig.from_mapping({**base, "train_year": 2022, "test_year": 2021})


if __name__ == "__main__":
    unittest.main()
