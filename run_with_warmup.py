#!/usr/bin/env python3
"""
AERO OPEN CIM — entry point con pre-calentamiento de cache.

USAR ESTE SCRIPT EN LUGAR DE run.py mientras se aplica el fix de kriging:
    python3 run_with_warmup.py

El servidor pre-calienta el cache de los 126 archivos meteorológicos
al arrancar (~35 s en segundo plano), para que las peticiones del usuario
sean rapidas (<2 s) desde el primer uso.
"""
import os
import threading
import logging

log = logging.getLogger(__name__)

def patch_and_warmup(app, data_folder, executor):
    """Inyecta el warm-up después de que la app esté lista."""
    from warmup import warmup_site_cache
    warmup_site_cache(data_folder, executor, year=2018, max_workers=4)


if __name__ == '__main__':
    from api import create_app
    from config import Config

    port = int(os.environ.get("PORT", "5051"))
    app = create_app()

    # Pre-calentar cache en background
    # Acceder al executor que crea create_app() es difícil desde fuera,
    # así que usamos un thread propio para el warm-up
    from concurrent.futures import ThreadPoolExecutor
    from core.kriging import parallel_read_sites, parse_coords_from_filename

    def _warmup():
        try:
            folder = Config.DATA_FOLDER
            if not os.path.isdir(folder):
                return
            paths = [
                os.path.join(folder, f)
                for f in sorted(os.listdir(folder))
                if f.lower().endswith('.txt') and not f.startswith('.')
                and parse_coords_from_filename(f) is not None
            ]
            if not paths:
                return
            print(f"  [warmup] Precalentando cache para {len(paths)} archivos...")
            parallel_read_sites(paths, year=2018, max_workers=4)
            print(f"  [warmup] Cache listo. Las peticiones de Kriging seran rapidas.")
        except Exception as e:
            print(f"  [warmup] Error: {e}")

    t = threading.Thread(target=_warmup, daemon=True)
    t.start()

    print(f"\n  Hey! AERO OPEN CIM starting at http://localhost:{port}\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
