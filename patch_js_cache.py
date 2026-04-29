js_file = 'static/js/aero.js'
with open(js_file, 'r') as f:
    c = f.read()

c = c.replace(
    "const fc = await api('/api/interest/forecast', 'POST', {\n      session_id: WP.sessionId, preset, horizon, models\n    });",
    "const fc = await api('/api/interest/forecast', 'POST', {\n      session_id: WP.sessionId, preset, horizon, models\n    });\n    WP.arenaCache.interest = fc;"
)

c = c.replace(
    "const fc = await api('/api/fx/forecast', 'POST', {\n      session_id: WP.sessionId, preset, horizon, models\n    });",
    "const fc = await api('/api/fx/forecast', 'POST', {\n      session_id: WP.sessionId, preset, horizon, models\n    });\n    WP.arenaCache.fx = fc;"
)

with open(js_file, 'w') as f:
    f.write(c)

print("Applied cache patch.")
