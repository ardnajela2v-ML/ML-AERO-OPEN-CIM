"""Typed station catalog used by Globe and MCP Lab.

The catalog deliberately separates the scientific role of a file from its
filename.  Several legacy CERTE observations were exported with Windnavigator
names containing ``MERRA2``; content signatures and explicit catalog entries
therefore take precedence over filename heuristics.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from pathlib import Path
from typing import Any, Iterable

from core.kriging import parse_coords_from_filename
M04_STATION_ID = "obs-certe-m04"
# INEEL, Atlas Eolico Mexicano: 16°32'49.27" N, 94°57'20.83" W.
M04_LATITUDE = 16.54701944
M04_LONGITUDE = -94.95578611
COORDINATE_WARNING_KM = 0.5
M04_LEGACY_ALIASES = (
    "A2018Windnavigator_MERRA2_N16,550_W94,955.txt",
    "AS2018newWindnavigator_MERRA2_N16,550_W94,955.txt",
    "CERTE2018Windnavigator_MERRA2_N16,555_W94,955.txt",
    "CERTEnew2018Windnavigator_MERRA2_N16,555_W94,955.txt",
    "CERTEnewWindnavigator_MERRA2_N16,550_W94,955.txt",
    "M04CERTE_2018_Windnavigator_MERRA2_N16,560_W94,950.txt",
)


def _read_signature(path: Path, limit: int = 96 * 1024, attempts: int = 3) -> str:
    """Read up to ``limit`` bytes for content-based classification.

    A short read here (fewer bytes than the file actually has, with no
    exception raised) is legitimate POSIX behaviour, not just a theoretical
    edge case: it was observed in practice against files on slow-to-mount
    storage, where the first read of a "cold" file can return 0 bytes even
    though ``stat()`` already reports the real size. A silent short read
    makes content-signature checks fail and misclassifies the file via the
    filename fallback instead — exactly what content signatures exist to
    avoid for the legacy CERTE/Windnavigator exports (see module docstring).
    Retrying a couple of times with a short backoff is enough to observe the
    file materialize; it costs nothing on the normal (already-local) path.
    """
    try:
        expected = path.stat().st_size
    except OSError:
        return ""
    for attempt in range(attempts):
        try:
            with path.open("rb") as handle:
                data = handle.read(limit)
        except OSError:
            return ""
        if data or expected == 0 or attempt == attempts - 1:
            return data.decode("utf-8", errors="ignore")
        time.sleep(0.1 * (attempt + 1))
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


_LATITUDE_RE = re.compile(r"(?im)^\s*Latitude\s*:\s*([-+]?\d+(?:[.,]\d+)?)")
_LONGITUDE_RE = re.compile(r"(?im)^\s*Longitude\s*:\s*([-+]?\d+(?:[.,]\d+)?)")


def coordinates_from_content(path: str | Path) -> tuple[float, float] | None:
    """Read the coordinate declared by a Windnavigator file.

    Filename coordinates are only a fallback.  The internal request metadata
    describes the actual virtual reanalysis point and therefore wins whenever
    both values are present.
    """

    sample = _read_signature(Path(path))
    latitude = _LATITUDE_RE.search(sample)
    longitude = _LONGITUDE_RE.search(sample)
    if not latitude or not longitude:
        return None
    try:
        return (
            float(latitude.group(1).replace(",", ".")),
            float(longitude.group(1).replace(",", ".")),
        )
    except ValueError:
        return None


def _distance_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    radius_km = 6371.0088
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * radius_km * math.asin(math.sqrt(value))


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
            "coordinate_source": "INEEL_AEM_official_catalog",
            "coordinate_status": "verified",
            "_paths": [str(path) for path in m04_paths],
        })

    grouped_references: dict[tuple[str, float, float, float], list[dict[str, Any]]] = {}
    if data_root.is_dir():
        for path in sorted(data_root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".txt", ".csv", ".tsv", ".dat"}:
                continue
            classification = classify_file(path)
            if classification["data_kind"] != "reanalysis":
                # Converted CERTE observations are legacy aliases, not MCP references.
                continue
            internal_coords = coordinates_from_content(path)
            filename_coords = parse_coords_from_filename(path.name)
            coords = internal_coords or filename_coords
            if coords is None:
                continue
            lat, lon = coords
            source_height = 100.0 if classification["provider"] == "ERA5" else 50.0
            discrepancy = (
                _distance_km(filename_coords, internal_coords)
                if filename_coords is not None and internal_coords is not None else None
            )
            key = (classification["provider"], round(float(lat), 6), round(float(lon), 6), source_height)
            grouped_references.setdefault(key, []).append({
                "path": path.resolve(),
                "filename_coordinates": filename_coords,
                "internal_coordinates": internal_coords,
                "coordinate_discrepancy_km": discrepancy,
                "classification": classification,
            })

    for (provider, lat, lon, source_height), candidates in sorted(grouped_references.items()):
        ordered = sorted(candidates, key=lambda row: (-row["path"].stat().st_size, row["path"].name))
        primary = ordered[0]
        alias_details = []
        for row in ordered:
            alias_details.append({
                "file_id": row["path"].name,
                "filename_coordinates": list(row["filename_coordinates"]) if row["filename_coordinates"] else None,
                "internal_coordinates": list(row["internal_coordinates"]) if row["internal_coordinates"] else None,
                "coordinate_discrepancy_km": (
                    round(float(row["coordinate_discrepancy_km"]), 3)
                    if row["coordinate_discrepancy_km"] is not None else None
                ),
            })
        discrepancies = [
            float(row["coordinate_discrepancy_km"])
            for row in ordered if row["coordinate_discrepancy_km"] is not None
        ]
        corrected = any(value > COORDINATE_WARNING_KM for value in discrepancies)
        physical_key = f"{provider}:{lat:.6f}:{lon:.6f}:{source_height:.1f}"
        references.append({
            "station_id": _station_id("node", physical_key),
            "physical_node_key": physical_key,
            "display_name": f"{provider} · {lat:.4f}°, {lon:.4f}°",
            "data_kind": "reanalysis",
            "provider": provider,
            "allowed_role": "reference",
            "latitude": float(lat),
            "longitude": float(lon),
            "measurement_heights": [source_height],
            "file_ids": [row["path"].name for row in ordered],
            "primary_file_id": primary["path"].name,
            "legacy_aliases": [row["path"].name for row in ordered[1:]],
            "alias_details": alias_details,
            "size_bytes": primary["path"].stat().st_size,
            "source_confidence": "content" if primary["internal_coordinates"] else "filename",
            "coordinate_source": "file_metadata" if primary["internal_coordinates"] else "filename_fallback",
            "coordinate_status": "corrected_filename_mismatch" if corrected else "verified",
            "maximum_coordinate_discrepancy_km": round(max(discrepancies), 3) if discrepancies else None,
            "alias_count": len(ordered),
            # Calibration reads one representative time series per physical
            # node.  All other exports remain auditable aliases.
            "_paths": [str(primary["path"])],
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
