"""Production-minded local REST API for WindMCP.

The API is intentionally bound to localhost by the module entry point. It
supports JSON requests and multipart uploads, returns a stable response
envelope, and keeps each run's artifacts isolated below ``output_root``.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from .config import ConfigurationError, VERSION
from .service import run_mcp_validation


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def _envelope(*, success: bool, request_id: str, data: Any = None, error: Dict[str, Any] | None = None) -> Any:
    body: Dict[str, Any] = {"success": success, "request_id": request_id}
    if success:
        body["data"] = data
    else:
        body["error"] = error or {"code": "unknown_error", "message": "Unknown error"}
    return jsonify(body)


def create_app(output_root: str = "runs") -> Flask:
    app = Flask(__name__)
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    app.config.update(
        MAX_CONTENT_LENGTH=256 * 1024 * 1024,
        JSON_SORT_KEYS=False,
    )

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error: RequestEntityTooLarge) -> Any:
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
        return _envelope(
            success=False,
            request_id=request_id,
            error={"code": "payload_too_large", "message": "Request exceeds the 256 MB limit"},
        ), 413

    @app.get("/health")
    def health() -> Any:
        return _envelope(
            success=True,
            request_id=uuid.uuid4().hex,
            data={"service": "windmcp", "version": VERSION, "status": "ok"},
        )

    @app.get("/v1/mcp/capabilities")
    def capabilities() -> Any:
        return _envelope(
            success=True,
            request_id=uuid.uuid4().hex,
            data={
                "service": "WindMCP",
                "version": VERSION,
                "formats": {"observations": ["CERTE"], "references": ["Windnavigator ERA5", "Windnavigator MERRA-2"]},
                "resolution": "1h",
                "timezone": "UTC",
                "artifacts": ["result.json", "run_manifest.json", "reference_ranking.csv", "prediction CSVs", "test-year model comparison", "test-year ML model comparison", "ML forward CSV", "diagnostic backtesting CSV", "final HTML report", "uncertainty bands", "QC JSON", "reconstruction_readiness.json", "historical_reconstruction.csv"],
            },
        )

    @app.post("/v1/mcp/run")
    def run() -> Any:
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
        run_id = uuid.uuid4().hex
        run_dir = root / run_id
        upload_dir = run_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        try:
            config = _config_from_request()
            observation_files = _save_uploads(
                request.files.getlist("observations") or request.files.getlist("observation_files"),
                upload_dir,
            )
            reference_files = _save_uploads(
                request.files.getlist("references") or request.files.getlist("reference_files"),
                upload_dir,
            )
            power_curve_files = _save_uploads(request.files.getlist("power_curve"), upload_dir)
            if not observation_files:
                observation_files = [str(path) for path in config.pop("observation_files", [])]
            if not reference_files:
                reference_files = [str(path) for path in config.pop("reference_files", [])]
            if not observation_files or not reference_files:
                raise ConfigurationError("observation_files and reference_files are required")
            config.update({
                "observation_files": observation_files,
                "reference_files": reference_files,
                "run_id": run_id,
                "output_dir": str(run_dir),
            })
            if power_curve_files:
                config["power_curve_file"] = power_curve_files[0]
            result = run_mcp_validation(config)
            return _envelope(success=True, request_id=request_id, data=result), 200
        except (ConfigurationError, ValueError, OSError) as error:
            app.logger.info("WindMCP rejected request %s: %s", request_id, error)
            return _envelope(
                success=False,
                request_id=request_id,
                error={"code": "validation_error", "message": str(error), "run_id": run_id},
            ), 422
        except Exception:
            app.logger.exception("WindMCP run failed (%s)", request_id)
            return _envelope(
                success=False,
                request_id=request_id,
                error={"code": "internal_error", "message": "Unexpected WindMCP failure", "run_id": run_id},
            ), 500

    @app.get("/v1/mcp/runs")
    def list_runs() -> Any:
        rows: List[Dict[str, Any]] = []
        for result_path in sorted(root.glob("*/result.json"), reverse=True):
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                rows.append({
                    "run_id": result.get("run_id", result_path.parent.name),
                    "created_at": result.get("created_at"),
                    "selected_reference": result.get("selected_reference"),
                    "result_json": str(result_path),
                })
            except (OSError, ValueError, TypeError):
                continue
        return _envelope(success=True, request_id=uuid.uuid4().hex, data={"runs": rows})

    @app.get("/v1/mcp/runs/<run_id>")
    def get_run(run_id: str) -> Any:
        request_id = uuid.uuid4().hex
        if not RUN_ID_PATTERN.fullmatch(run_id):
            return _envelope(success=False, request_id=request_id, error={"code": "invalid_run_id", "message": "Invalid run id"}), 400
        result_path = root / run_id / "result.json"
        if not result_path.is_file():
            return _envelope(success=False, request_id=request_id, error={"code": "run_not_found", "message": "Run not found"}), 404
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return _envelope(success=False, request_id=request_id, error={"code": "artifact_error", "message": str(error)}), 500
        return _envelope(success=True, request_id=request_id, data=result)

    return app


def _config_from_request() -> Dict[str, Any]:
    if request.is_json:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ConfigurationError("JSON body must be an object")
        return dict(payload)
    config_text = request.form.get("config", "{}")
    try:
        config = json.loads(config_text)
    except json.JSONDecodeError as error:
        raise ConfigurationError("config must contain valid JSON") from error
    if not isinstance(config, dict):
        raise ConfigurationError("config must be a JSON object")
    return config


def _save_uploads(files: List[Any], directory: Path) -> List[str]:
    paths: List[str] = []
    for index, file_storage in enumerate(files):
        if not file_storage or not file_storage.filename:
            continue
        filename = secure_filename(file_storage.filename) or ("upload_%s.txt" % index)
        path = directory / ("%02d_%s" % (index, filename))
        file_storage.save(path)
        paths.append(str(path))
    return paths


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5052, debug=False)
