"""Typed station catalog used by Globe and MCP Lab.

The catalog deliberately separates the scientific role of a file from its
filename.  Several legacy CERTE observations were exported with Windnavigator
names containing ``MERRA2``; content signatures and explicit catalog entries
therefore take precedence over filename heuristics.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from core.kriging import parse_coords_from_filename
from core.site_catalog import get_site_info


M04_STATION_ID = "obs-certe-m04"
M04_LATITUDE = 16.55
M04_LONGITUDE = -94.955
M04_LEGACY_ALIASES = (
    "A2018Windnavigator_MERRA2_N16,550_W94,955.txt",
    "AS2018newWindnavigator_MERRA2_N16,550_W94,955.txt",
    "CERTE2018Windnavigator_MERRA2_N16,555_W94,955.txt",
    "CERTEnew2018Windnavigator_MERRA2_N16,555_W94,955.txt",
    "CERTEnewWindnavigator_MERRA2_N16,550_W94,955.txt",
    "M04CERTE_2018_Windnavigator_MERRA2_N16,560_W94,950.txt",
)


def _read_signature(path: Path, limit: int = 96 * 1024) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode("utf-8", errors="ignore")
    except OSError:
        return ""


def classify_file(path: str | Path) -> dict[str, str]:
    """Classify one file using content before filename hints."""
    source = Path(path)
    sample = _read_signature(source)
    upper = sample.upper()
    filename = source.name.upper()

    raw_certe = "WS_80MA_MEAN" in upper and "WD_78M_MEAN" in upper
    converted_certe = (
        "SOURCE: CERTE MEASURED MAST DATA" in upper
        or "CONVERTED FROM CERTE MEASURED DATA" in upper
    )
    if raw_certe or converted_certe:
        return {"data_kind": "observed", "provider": "CERTE", "confidence": "content"}
    if "MERRA-2" in upper or "NASA GLOBAL MODELING AND ASSIMILATION" in upper:
        return {"data_kind": "reanalysis", "provider": "MERRA2", "confidence": "content"}
    if "ERA5" in upper or "ECMWF" in upper:
        return {"data_kind": "reanalysis", "provider": "ERA5", "confidence": "content"}
    if "MERRA" in filename:
        return {"data_kind": "reanalysis", "provider": "MERRA2", "confidence": "filename"}
    if "ERA5" in filename:
        return {"data_kind": "reanalysis", "provider": "ERA5", "confidence": "filename"}
    return {"data_kind": "unknown", "provider": "UNKNOWN", "confidence": "ambiguous"}


def _station_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _m04_files(certe_folder: Path) -> list[Path]:
    consolidated = certe_folder / "M04_CERTE_CONSOLIDADO_2017-2020.txt"
    if consolidated.is_file():
        return [consolidated.resolve()]
    return sorted(path.resolve() for path in certe_folder.glob("M04_CERTE_*.txt") if path.is_file())


def build_mcp_catalog(data_folder: str | Path, certe_folder: str | Path) -> dict[str, Any]:
    data_root = Path(data_folder).expanduser().resolve()
    certe_root = Path(certe_folder).expanduser().resolve()
    observations: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []

    m04_paths = _m04_files(certe_root)
    if m04_paths:
        observations.append({
            "station_id": M04_STATION_ID,
            "display_name": "Estación M04 CERTE",
            "data_kind": "observed",
            "provider": "CERTE",
            "allowed_role": "observation",
            "latitude": M04_LATITUDE,
            "longitude": M04_LONGITUDE,
            "measurement_heights": [20, 40, 60, 80],
            "direction_heights": [58, 78],
            # M04 records fixed local standard time without an offset in the
            # raw five-column timestamp.  Etc/GMT+6 means UTC-06:00 (IANA's
            # Etc sign convention is intentionally reversed).
            "timezone": "Etc/GMT+6",
            "timezone_label": "UTC−06:00 fijo · hora estándar del registrador",
            "file_ids": [path.name for path in m04_paths],
            "legacy_aliases": list(M04_LEGACY_ALIASES),
            "source_confidence": "catalog",
            "_paths": [str(path) for path in m04_paths],
        })

    if data_root.is_dir():
        for path in sorted(data_root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".txt", ".csv", ".tsv", ".dat"}:
                continue
            classification = classify_file(path)
            if classification["data_kind"] != "reanalysis":
                # Converted CERTE observations are legacy aliases, not MCP references.
                continue
            coords = parse_coords_from_filename(path.name)
            if coords is None:
                continue
            lat, lon = coords
            info = get_site_info(path.name)
            references.append({
                "station_id": _station_id("ref", path.name),
                "display_name": info.get("display_name") or path.stem,
                "data_kind": "reanalysis",
                "provider": classification["provider"],
                "allowed_role": "reference",
                "latitude": float(lat),
                "longitude": float(lon),
                "measurement_heights": [100] if classification["provider"] == "ERA5" else [50],
                "file_ids": [path.name],
                "legacy_aliases": [],
                "state": info.get("state", ""),
                "municipality": info.get("municipality", ""),
                "size_bytes": path.stat().st_size,
                "source_confidence": classification["confidence"],
                "_paths": [str(path.resolve())],
            })

    return {
        "observations": observations,
        "references": references,
        "counts": {"observations": len(observations), "references": len(references)},
    }


def public_catalog(catalog: dict[str, Any], uploads: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    observations = list(catalog.get("observations", []))
    references = list(catalog.get("references", []))
    for record in uploads:
        (observations if record.get("data_kind") == "observed" else references).append(record)

    def clean(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if not key.startswith("_")}

    return {
        "observations": [clean(row) for row in observations],
        "references": [clean(row) for row in references],
        "counts": {"observations": len(observations), "references": len(references)},
    }


def catalog_index(catalog: dict[str, Any], uploads: Iterable[dict[str, Any]] = ()) -> dict[str, dict[str, Any]]:
    rows = list(catalog.get("observations", [])) + list(catalog.get("references", [])) + list(uploads)
    return {str(row["station_id"]): row for row in rows}
