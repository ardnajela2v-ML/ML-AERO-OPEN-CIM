#!/usr/bin/env python3
"""
WindPro Platform — entry point.
Run:  python run.py
Optional: PORT=8080 python run.py  (default port is 5051)
"""
import os

from api import create_app

if __name__ == '__main__':
    port = int(os.environ.get("PORT", "5051"))
    app = create_app()
    print(f"\n  Hey! WindPro Platform starting at http://localhost:{port}\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
