"""Descarga y exportación de PML nodal (CENACE) a formato WindPro con fechas.

Formato canónico de salida (CSV/TXT, USD/kWh), con cabecera:

    fecha,hora,compra,venta
    2018-01-01,1,0.030524,0.030524

- ``fecha``: AAAA-MM-DD (día de operación CENACE)
- ``hora``: 1–24 (hora de operación CENACE)
- ``compra`` / ``venta``: USD/kWh (mismo valor si el SW-PML solo publica un PML)

El pipeline también acepta el legado sin fechas (``compra,venta`` por línea).
Con fechas, ``read_pml`` conserva timestamps para alinear con viento/despacho.
"""
from __future__ import annotations

import csv
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

import requests

BASE_URL = "https://ws01.cenace.gob.mx:8082/SWPML/SIM"
SERIE_TIPO_CAMBIO_FIX = "SF43718"

WINDPRO_PML_HEADER = ["fecha", "hora", "compra", "venta"]
WINDPRO_PML_FORMAT_NOTE = (
    "fecha,hora,compra,venta — fechas CENACE + USD/kWh; "
    "compatible con core.forecasting.read_pml"
)


def generar_bloques_semanales(fecha_ini: date, fecha_fin: date):
    actual = fecha_ini
    while actual <= fecha_fin:
        fin_bloque = min(actual + timedelta(days=6), fecha_fin)
        yield actual, fin_bloque
        actual = fin_bloque + timedelta(days=1)


def consultar_bloque(sistema, proceso, nodo, ini: date, fin: date,
                     intentos=3, espera=3):
    url = (
        f"{BASE_URL}/{sistema}/{proceso}/{nodo}/"
        f"{ini.year}/{ini.month:02d}/{ini.day:02d}/"
        f"{fin.year}/{fin.month:02d}/{fin.day:02d}/JSON"
    )
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 204:
                return None
            return None
        except requests.RequestException:
            time.sleep(espera)
    return None


def extraer_filas(data, nodo_consultado):
    filas = []
    if not data:
        return filas

    if isinstance(data, list):
        nodos = data
    else:
        resultados = data.get("Resultados", data)
        if isinstance(resultados, list):
            nodos = resultados
        elif isinstance(resultados, dict):
            nodos = resultados.get("Nodo", resultados)
            if isinstance(nodos, dict):
                nodos = [nodos]
        else:
            nodos = []

    if not isinstance(nodos, list):
        nodos = [nodos]

    for nodo_obj in nodos:
        if not isinstance(nodo_obj, dict):
            continue
        clave = nodo_obj.get("clv_nodo", nodo_consultado)
        valores = nodo_obj.get("Valores", nodo_obj.get("valores"))
        if valores is None and "fecha" in nodo_obj:
            valores = [nodo_obj]
        elif valores is None:
            valores = []
        if isinstance(valores, dict):
            valores = [valores]

        for v in valores:
            if not isinstance(v, dict):
                continue
            filas.append({
                "nodo": v.get("clv_nodo", clave),
                "fecha": v.get("fecha"),
                "hora": v.get("hora"),
                "pml": v.get("pml"),
                "pml_energia": v.get("pml_ene"),
                "pml_perdidas": v.get("pml_per"),
                "pml_congestion": v.get("pml_cng"),
            })
    return filas


def parse_fecha_cenace(valor) -> Optional[date]:
    if valor is None:
        return None
    s = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_hora_cenace(valor) -> Optional[int]:
    try:
        h = int(float(str(valor).strip()))
    except (TypeError, ValueError):
        return None
    if 1 <= h <= 25:  # 25 = hora extra en cambio de horario
        return h
    return None


def cargar_fx_local(path: str) -> dict:
    """Lee USDMXN diario (TSV date\\tusdmxn o CSV)."""
    tipos = {}
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        first = f.readline()
        delim = "\t" if "\t" in first else ","
        # Si la primera línea no es cabecera numérica, ya la consumimos
        def _try_row(line):
            parts = line.strip().split(delim)
            if len(parts) < 2:
                return
            try:
                d = parse_fecha_cenace(parts[0]) or date.fromisoformat(parts[0])
                tipos[d] = float(parts[1])
            except (ValueError, TypeError):
                return

        # Re-parse header row if it was data
        _try_row(first)
        for line in f:
            _try_row(line)
    return tipos


def tipo_cambio_mas_cercano(tipos: dict, fecha: date, max_dias_atras=14):
    d = fecha
    for _ in range(max_dias_atras):
        if d in tipos:
            return tipos[d]
        d -= timedelta(days=1)
    return None


def obtener_tipo_cambio_banxico(token, fecha_ini: date, fecha_fin: date) -> dict:
    url = (
        f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/"
        f"{SERIE_TIPO_CAMBIO_FIX}/datos/{fecha_ini.isoformat()}/{fecha_fin.isoformat()}"
    )
    resp = requests.get(url, headers={"Bmx-Token": token}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Banxico respondió {resp.status_code}: {resp.text[:300]}")
    datos = resp.json()["bmx"]["series"][0]["datos"]
    tipos = {}
    for d in datos:
        try:
            fecha = datetime.strptime(d["fecha"], "%d/%m/%Y").date()
            tipos[fecha] = float(d["dato"])
        except (ValueError, KeyError):
            continue
    return tipos


def mxn_mwh_a_usd_kwh(pml_mxn_mwh: float, fx_mxn_por_usd: float) -> float:
    return float(pml_mxn_mwh) / float(fx_mxn_por_usd) / 1000.0


def descargar_pml_horario(nodo: str, fecha_ini: date, fecha_fin: date,
                          sistema="SIN", proceso="MDA", espera=1.0,
                          progress_cb=None) -> list:
    """Descarga filas horarias crudas (PML en MXN/MWh) del SW-PML."""
    hoy = date.today()
    if fecha_fin > hoy:
        fecha_fin = hoy
    todas = []
    bloques = list(generar_bloques_semanales(fecha_ini, fecha_fin))
    for i, (ini, fin) in enumerate(bloques, start=1):
        if progress_cb:
            progress_cb(i, len(bloques), ini, fin)
        data = consultar_bloque(sistema, proceso, nodo, ini, fin)
        todas.extend(extraer_filas(data, nodo))
        time.sleep(espera)
    return todas


def buscar_variantes_nodo(base: str, sistema="SIN", voltaje="115",
                          dias_atras=7) -> list:
    """Prueba variantes de clave NodoP; regresa las que responden con datos."""
    fecha = date.today() - timedelta(days=dias_atras)
    base = re.sub(r"[^A-Za-z0-9]", "", base).upper()
    candidatos = [f"{base}-{voltaje}", f"{base}{voltaje}"]
    for i in range(0, 100):
        candidatos.append(f"{i:02d}{base}-{voltaje}")
    encontrados = []
    for candidato in candidatos:
        data = consultar_bloque(sistema, "MDA", candidato, fecha, fecha, intentos=1)
        if extraer_filas(data, candidato):
            encontrados.append(candidato)
        time.sleep(0.15)
    return encontrados


def filas_a_windpro_usd(filas: Iterable[dict], tipos_cambio: dict) -> list:
    """Convierte filas CENACE a registros WindPro con fecha/hora y USD/kWh."""
    out = []
    for fila in filas:
        d = parse_fecha_cenace(fila.get("fecha"))
        h = parse_hora_cenace(fila.get("hora"))
        try:
            pml = float(fila["pml"])
        except (TypeError, ValueError, KeyError):
            continue
        if d is None or h is None:
            continue
        tc = tipo_cambio_mas_cercano(tipos_cambio, d)
        if tc is None or tc <= 0:
            continue
        usd = mxn_mwh_a_usd_kwh(pml, tc)
        out.append({
            "fecha": d.isoformat(),
            "hora": h,
            "compra": round(usd, 6),
            "venta": round(usd, 6),
            "tipo_cambio_fix": tc,
            "pml_mxn_mwh": pml,
            "nodo": fila.get("nodo", ""),
        })
    out.sort(key=lambda r: (r["fecha"], r["hora"]))
    return out


def escribir_windpro_pml(ruta: str, registros: list) -> str:
    """Escribe el CSV/TXT canónico con fechas (USD/kWh)."""
    os.makedirs(os.path.dirname(os.path.abspath(ruta)) or ".", exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=WINDPRO_PML_HEADER)
        writer.writeheader()
        for r in registros:
            writer.writerow({
                "fecha": r["fecha"],
                "hora": r["hora"],
                "compra": f"{float(r['compra']):.6f}",
                "venta": f"{float(r['venta']):.6f}",
            })
    return ruta


def slug_nodo(nodo: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", nodo).strip("_")


def exportar_nodo_a_mercado_pml(
    nodo: str,
    fecha_ini: date,
    fecha_fin: date,
    *,
    carpeta_salida: str,
    fx_path: Optional[str] = None,
    banxico_token: Optional[str] = None,
    sistema: str = "SIN",
    proceso: str = "MDA",
    espera: float = 1.0,
    progress_cb=None,
) -> dict:
    """Descarga PML, convierte a USD/kWh y guarda archivo con fechas.

    Returns dict con path, n_hours, mean_usd_kwh, format note.
    """
    filas = descargar_pml_horario(
        nodo, fecha_ini, fecha_fin, sistema=sistema, proceso=proceso,
        espera=espera, progress_cb=progress_cb)
    if not filas:
        raise RuntimeError(
            f"Sin datos PML para {nodo} en {fecha_ini}–{fecha_fin}. "
            "Prueba --buscar-nodo o otra clave (ej. 02JUD-115).")

    fechas = [parse_fecha_cenace(f.get("fecha")) for f in filas]
    fechas = [d for d in fechas if d]
    if not fechas:
        raise RuntimeError("Las filas no traen fechas parseables.")
    f0, f1 = min(fechas), max(fechas)

    if fx_path and os.path.isfile(fx_path):
        tipos = cargar_fx_local(fx_path)
    elif banxico_token:
        tipos = obtener_tipo_cambio_banxico(
            banxico_token, f0 - timedelta(days=14), f1)
    else:
        raise RuntimeError(
            "Se requiere fx_path (USDMXN histórico) o banxico_token.")

    registros = filas_a_windpro_usd(filas, tipos)
    if not registros:
        raise RuntimeError(
            "No se pudo convertir a USD/kWh (falta tipo de cambio).")

    fname = (
        f"PML_{slug_nodo(nodo)}_{f0.year}_{f1.year}_usd.txt"
        if f0.year != f1.year
        else f"PML_{slug_nodo(nodo)}_{f0.year}_usd.txt"
    )
    path = os.path.join(carpeta_salida, fname)
    escribir_windpro_pml(path, registros)
    mean = sum(float(r["compra"]) for r in registros) / len(registros)
    return {
        "path": path,
        "file": fname,
        "nodo": nodo,
        "n_hours": len(registros),
        "fecha_inicio": registros[0]["fecha"],
        "fecha_fin": registros[-1]["fecha"],
        "mean_usd_kwh": mean,
        "unit": "USD/kWh",
        "format": WINDPRO_PML_FORMAT_NOTE,
        "header": WINDPRO_PML_HEADER,
    }


def convertir_csv_cenace_a_windpro(
    ruta_csv: str,
    ruta_salida: str,
    *,
    fx_path: Optional[str] = None,
    banxico_token: Optional[str] = None,
) -> dict:
    """Convierte un CSV ya descargado (MXN/MWh) al TXT WindPro con fechas."""
    with open(ruta_csv, newline="", encoding="utf-8", errors="ignore") as f:
        filas = list(csv.DictReader(f))
    fechas = [parse_fecha_cenace(r.get("fecha")) for r in filas]
    fechas = [d for d in fechas if d]
    if not fechas:
        raise RuntimeError("CSV sin fechas parseables.")
    f0, f1 = min(fechas), max(fechas)
    if fx_path and os.path.isfile(fx_path):
        tipos = cargar_fx_local(fx_path)
    elif banxico_token:
        tipos = obtener_tipo_cambio_banxico(
            banxico_token, f0 - timedelta(days=14), f1)
    else:
        raise RuntimeError("Se requiere fx_path o banxico_token.")
    registros = filas_a_windpro_usd(filas, tipos)
    escribir_windpro_pml(ruta_salida, registros)
    mean = sum(float(r["compra"]) for r in registros) / len(registros)
    return {
        "path": ruta_salida,
        "n_hours": len(registros),
        "fecha_inicio": registros[0]["fecha"],
        "fecha_fin": registros[-1]["fecha"],
        "mean_usd_kwh": mean,
        "unit": "USD/kWh",
        "format": WINDPRO_PML_FORMAT_NOTE,
    }
