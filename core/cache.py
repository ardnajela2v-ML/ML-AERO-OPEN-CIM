"""
Persistent cache helpers for deterministic, expensive computations.

Cache entries are keyed by explicit inputs (file signature, parameters and a
caller-provided version). This avoids recomputing identical parse/stat/feature
work without changing scientific methods or model settings.
"""
import hashlib
import json
import os
import pickle
import tempfile
import time
from contextlib import contextmanager

from config import Config


CACHE_SCHEMA_VERSION = "aero-cache-v1"


def _cache_root():
    root = getattr(Config, "CACHE_FOLDER", None) or os.path.join(Config.BASE_DIR, ".cache", "aero")
    os.makedirs(root, exist_ok=True)
    return root


def stable_hash(value):
    """Return a stable SHA256 for JSON-like values."""
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bytes_hash(raw_bytes):
    return hashlib.sha256(raw_bytes).hexdigest()


def file_signature(path):
    """File identity for invalidating cache when contents change."""
    st = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
    }


def make_key(namespace, *parts):
    return stable_hash({
        "schema": CACHE_SCHEMA_VERSION,
        "namespace": namespace,
        "parts": parts,
    })


def cache_path(namespace, key):
    safe_ns = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in namespace)
    folder = os.path.join(_cache_root(), safe_ns)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{key}.pkl")


def get(namespace, *parts):
    key = make_key(namespace, *parts)
    path = cache_path(namespace, key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
        return payload.get("value")
    except Exception:
        return None


def set(namespace, value, *parts):
    key = make_key(namespace, *parts)
    path = cache_path(namespace, key)
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "created_at": time.time(),
        "value": value,
    }
    folder = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".pkl", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return value


def get_or_compute(namespace, parts, compute):
    cached = get(namespace, *parts)
    if cached is not None:
        return cached, True
    value = compute()
    set(namespace, value, *parts)
    return value, False


def clear_namespace(namespace):
    folder = os.path.dirname(cache_path(namespace, "placeholder"))
    if not os.path.isdir(folder):
        return 0
    n = 0
    for name in os.listdir(folder):
        if not name.endswith(".pkl"):
            continue
        try:
            os.unlink(os.path.join(folder, name))
            n += 1
        except OSError:
            pass
    return n


@contextmanager
def timed(label, logger=None):
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        if logger is not None:
            logger.info("%s took %.3fs", label, elapsed)
