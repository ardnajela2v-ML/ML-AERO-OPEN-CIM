"""
Módulo de pre-calentamiento del caché para WindPro Platform.
Importado automáticamente desde api.py cuando se aplica el fix de kriging.
"""
import os
import logging
from core.kriging import parallel_read_sites, parse_coords_from_filename, clear_site_stats_cache

log = logging.getLogger(__name__)


def warmup_site_cache(data_folder: str, executor, year: int = 2018, max_workers: int = 4):
    """
    Pre-calienta el caché de archivos meteorológicos en background.
    Se llama desde create_app() al arrancar el servidor.
    """
    def _run():
        try:
            if not os.path.isdir(data_folder):
                return
            paths = [
                os.path.join(data_folder, f)
                for f in sorted(os.listdir(data_folder))
                if f.lower().endswith('.txt') and not f.startswith('.')
                and parse_coords_from_filename(f) is not None
            ]
            if not paths:
                return
            log.info('Cache warm-up: leyendo %d archivos (year=%s)…', len(paths), year)
            parallel_read_sites(paths, year=year, max_workers=max_workers)
            log.info('Cache warm-up completado (%d archivos).', len(paths))
        except Exception as exc:
            log.warning('Cache warm-up falló: %s', exc)

    executor.submit(_run)
