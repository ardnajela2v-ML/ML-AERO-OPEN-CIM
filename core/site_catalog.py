"""
Human-readable names and metadata for wind resource site files.

Lookup order in get_site_info():
  1. Exact filename match in _CATALOG
  2. Fallback: parse prefix + coordinates from filename
"""
from __future__ import annotations
import re

# ── lat/lon regex (reuses same pattern as core/kriging.py) ────────────────────
_COORD_RE = re.compile(r'(N|S)(\d+(?:[.,]\d+)?)[_\s]*[WE](\d+(?:[.,]\d+)?)', re.IGNORECASE)


def _c(display_name: str, state: str, municipality: str) -> dict:
    return {"display_name": display_name, "state": state, "municipality": municipality}


# ── Complete catalog: exact filename → metadata ───────────────────────────────
_CATALOG: dict[str, dict] = {

    # ── Oaxaca / Istmo — Agua ─────────────────────────────────────────────────
    "AguaWindnavigator_MERRA2_N16,000_W95,000.txt":
        _c("P.E. Agua",            "Oaxaca", "San Mateo del Mar"),
    "AguaWindnavigator_MERRA2_N16,000_W95,000 2.txt":
        _c("P.E. Agua (alt.)",     "Oaxaca", "San Mateo del Mar"),
    "AguaWindnavigator_MERRA2_N16,000_W95,000 copia (1).txt":
        _c("P.E. Agua (copia)",    "Oaxaca", "San Mateo del Mar"),
    "AguaWindnavigator_MERRA2_N16,000_W95,100.txt":
        _c("P.E. Agua — S2",       "Oaxaca", "Salina Cruz"),

    # ── Oaxaca / Istmo — Bii Nee Stipa ───────────────────────────────────────
    "BIINEESTIPA_ERA5_N16.500_W95.000.txt":
        _c("P.E. Bii Nee Stipa",          "Oaxaca", "Juchitán de Zaragoza"),
    "BIINEESTIPA_ERA5_N16.750_W95.000.txt":
        _c("P.E. Bii Nee Stipa — N",      "Oaxaca", "Juchitán de Zaragoza"),
    "BIINEESTIPA_MERRA2_N16.500_W95.000.txt":
        _c("P.E. Bii Nee Stipa (MERRA-2)","Oaxaca", "Juchitán de Zaragoza"),

    # ── Oaxaca / Istmo — Est. CERTE ───────────────────────────────────────────
    "CERTEWindnavigator_MERRA2_N16,500_W94,375.txt":
        _c("Est. CERTE",          "Oaxaca", "Zanatepec"),

    # ── Oaxaca / Istmo — Dos Arbolitos ────────────────────────────────────────
    "Dosarbolitos_ERA5_N16.500_W95.000.txt":
        _c("P.E. Dos Arbolitos",          "Oaxaca", "Juchitán de Zaragoza"),
    "Dosarbolitos_MERRA2_N16.500_W95.000.txt":
        _c("P.E. Dos Arbolitos (MERRA-2)","Oaxaca", "Juchitán de Zaragoza"),

    # ── Oaxaca / Istmo — Ixtepec / Zanatepec ─────────────────────────────────
    "IxtWindnavigator_MERRA2_N16,500_W94,375.txt":
        _c("P.E. Ixtepec",         "Oaxaca", "Zanatepec"),
    "IxtWindnavigator_MERRA2_N16,500_W94,375 2.txt":
        _c("P.E. Ixtepec (alt.)",  "Oaxaca", "Zanatepec"),
    "IxhuWindnavigator_ERA5_N16,250_W94,500.txt":
        _c("Sitio Ixhuatán",       "Oaxaca", "Ixhuatán"),

    # ── Oaxaca / Istmo — Oaxaca II-III-IV ────────────────────────────────────
    "OaxacaII-III-IV_ERA5_N16.500_W94.750.txt":
        _c("P.E. Oaxaca II-III-IV",        "Oaxaca", "El Espinal"),
    "OaxacaII-III-IV_ERA5_N16.750_W94.750.txt":
        _c("P.E. Oaxaca II-III-IV — N",    "Oaxaca", "El Espinal"),
    "OaxacaII-III-IV_MERRA2_N16.500_W95.000.txt":
        _c("P.E. Oaxaca II-III-IV (MERRA-2)","Oaxaca", "Unión Hidalgo"),
    "OaxacaIII_ERA5_N16.500_W94.750ingenio.txt":
        _c("P.E. Oaxaca III — Ingenio",    "Oaxaca", "El Espinal"),
    "OaxacaIII_ERA5_N16.750_W94.750chimalapas.txt":
        _c("P.E. Oaxaca III — Chimalapas", "Oaxaca", "Santa María Chimalapa"),
    "OaxacaIII_MERRA2_N16.500_W95.000.txt":
        _c("P.E. Oaxaca III (MERRA-2)",    "Oaxaca", "Juchitán de Zaragoza"),

    # ── Oaxaca / Istmo — Unión Hidalgo ───────────────────────────────────────
    "UnionWindnavigator_MERRA2_N16,500_W95,000.txt":
        _c("P.E. Unión Hidalgo",   "Oaxaca", "Unión Hidalgo"),

    # ── Oaxaca / Istmo — Bii Hioxo ────────────────────────────────────────────
    "Windnavigator_MERRA2_N17.000_W95.000BIHIOXO.txt":
        _c("P.E. Bii Hioxo",       "Oaxaca", "San Dionisio del Mar"),
    "Windnavigator_MERRA2_N17.000_W95.000_ALE.txt":
        _c("Sitio Tehuantepec Norte","Oaxaca","Santo Domingo Tehuantepec"),

    # ── Oaxaca / Istmo — Arriaga / San Jacinto (Chiapas/Oaxaca) ──────────────
    "Windnavigator_MERRA2_N16.000_W93.750ARRIAGA.txt":
        _c("P.E. Arriaga — Costa",  "Chiapas", "Tonalá"),
    "Windnavigator_MERRA2_N16.000_W94.375ARRIAGAMAR.txt":
        _c("P.E. Arriaga — Mar",    "Oaxaca",  "Chahuites"),
    "Windnavigator_MERRA2_N16.500_W94.375ARRIAGA.txt":
        _c("P.E. Arriaga — Zanatepec","Oaxaca","Zanatepec"),
    "ArriWindnavigator_ERA5_N16,250_W94,000.txt":
        _c("P.E. Arriaga — Chahuites","Oaxaca","Chahuites"),
    "San Jacinto ERA5_N16.250_W93.750.txt":
        _c("P.E. San Jacinto",      "Chiapas", "Tonalá"),
    "San Jacinto ERA5_N16.250_W94.000.txt":
        _c("P.E. San Jacinto — E",  "Oaxaca",  "Chahuites"),
    "San Jacinto MERRA2_N16.50_W93.750.txt":
        _c("P.E. San Jacinto (MERRA-2)","Chiapas","Tonalá"),

    # ── Oaxaca / Istmo — Sitios costeros / codificados ───────────────────────
    "SalWindnavigator_MERRA2_N16,000_W94,375.txt":
        _c("Sitio Zanatepec SW",    "Oaxaca", "Zanatepec"),
    "chaWindnavigator_ERA5_N16,250_W94,250.txt":
        _c("Sitio Chahuites",       "Oaxaca", "Chahuites"),
    "MararWindnavigator_ERA5_N16,000_W94,000.txt":
        _c("Sitio Tapanatepec",     "Oaxaca", "San Pedro Tapanatepec"),
    "MarcaWindnavigator_ERA5_N16,000_W94,250.txt":
        _c("Sitio Tapanatepec E",   "Oaxaca", "San Pedro Tapanatepec"),
    "MaragWindnavigator_ERA5_N16,000_W94,500.txt":
        _c("Sitio Zanatepec — SW",  "Oaxaca", "Zanatepec"),
    "MarmWindnavigator_ERA5_N16,000_W94,750.txt":
        _c("Sitio Niltepec",        "Oaxaca", "Santiago Niltepec"),
    "MarroWindnavigator_ERA5_N16,000_W95,000.txt":
        _c("Sitio San Mateo del Mar","Oaxaca","San Mateo del Mar"),
    "MarscWindnavigator_ERA5_N16,000_W95,250.txt":
        _c("Sitio Salina Cruz",     "Oaxaca", "Salina Cruz"),

    # ── Oaxaca / Istmo — Puntos genéricos ERA5 ───────────────────────────────
    "Windnavigator_ERA5_N16.000_W94.000.txt":
        _c("Sitio Tapanatepec (ERA5)",  "Oaxaca", "San Pedro Tapanatepec"),
    "Windnavigator_ERA5_N16.000_W94.250.txt":
        _c("Sitio Tapanatepec E (ERA5)","Oaxaca", "San Pedro Tapanatepec"),
    "Windnavigator_ERA5_N16.000_W94.500.txt":
        _c("Sitio Zanatepec (ERA5)",    "Oaxaca", "Zanatepec"),
    "Windnavigator_ERA5_N16.250_W94.500.txt":
        _c("Sitio Ixhuatán (ERA5)",     "Oaxaca", "Ixhuatán"),
    "Windnavigator_ERA5_N16.500_W94.500.txt":
        _c("Sitio Ixtaltepec (ERA5)",   "Oaxaca", "Ciudad Ixtepec"),
    "Windnavigator_ERA5_N16.500_W94.750.txt":
        _c("Sitio El Espinal (ERA5)",   "Oaxaca", "El Espinal"),

    # ── Oaxaca / Istmo — Puntos genéricos MERRA-2 ────────────────────────────
    "Windnavigator_MERRA2_N16.000_W94.375.txt":
        _c("Sitio Zanatepec SW (MERRA-2)","Oaxaca","Zanatepec"),
    "Windnavigator_MERRA2_N16.500_W94.667.txt":
        _c("Sitio Juchitán W (MERRA-2)", "Oaxaca","Juchitán de Zaragoza"),
    "Windnavigator_MERRA2_N16.500_W95.000.txt":
        _c("Sitio Unión Hidalgo (MERRA-2)","Oaxaca","Unión Hidalgo"),

    # ── Quintana Roo / Yucatán — Cancún ──────────────────────────────────────
    "CanWindnavigator_ERA5_N21,000_W86,750.txt":
        _c("Sitio Cancún — Costa",  "Quintana Roo", "Benito Juárez"),
    "Cancún_ERA5_N21.000_W86.750mar.txt":
        _c("Sitio Cancún — Mar",    "Quintana Roo", "Benito Juárez"),
    "Cancún_ERA5_N21.000_W87.000terra.txt":
        _c("Sitio Cancún — Tierra", "Quintana Roo", "Benito Juárez"),
    "Cancún_MERRA2_N21.000_W86.875.txt":
        _c("Sitio Cancún (MERRA-2)","Quintana Roo", "Benito Juárez"),

    # ── Yucatán — Dzilam ─────────────────────────────────────────────────────
    "Dzilam_ERA5_N21.250_W88.750.txt":
        _c("P.E. Dzilam — Costa",   "Yucatán", "Dzilam de Bravo"),
    "Dzilam_ERA5_N21.250_W89.000L.txt":
        _c("P.E. Dzilam — W",       "Yucatán", "Dzilam de Bravo"),
    "Dzilam_ERA5_N21.500_W88.750MAR.txt":
        _c("P.E. Dzilam — Mar",     "Yucatán", "Dzilam de Bravo"),
    "Dzilam_MERRA2_N21.000_W88.750TIERRA.txt":
        _c("P.E. Dzilam — Tierra",  "Yucatán", "Dzilam de Bravo"),
    "Dzilam_MERRA2_N21.500_W88.750MAR.txt":
        _c("P.E. Dzilam — Mar (MERRA-2)","Yucatán","Dzilam de Bravo"),

    # ── Yucatán — Tizimín ────────────────────────────────────────────────────
    "Tizimin_ERA5_N21.500_W88.000.txt":
        _c("P.E. Tizimín",          "Yucatán", "Tizimín"),
    "Tizimin_MERRA2_N21.500_W88.125.txt":
        _c("P.E. Tizimín (MERRA-2)","Yucatán", "Tizimín"),

    # ── Tamaulipas — Cd. Victoria ─────────────────────────────────────────────
    "Cd.Victoria_MERRA2_N23.500_W98.750.txt":
        _c("Sitio Cd. Victoria",    "Tamaulipas", "Ciudad Victoria"),
    "Cd.Victoria_MERRA2_N24.000_W98.750ARRIBA.txt":
        _c("Sitio Cd. Victoria — N","Tamaulipas", "Ciudad Victoria"),
    "Cd.ictoria_ERA5_N23.750_W98.750.txt":
        _c("Sitio Cd. Victoria (ERA5)","Tamaulipas","Ciudad Victoria"),

    # ── Tamaulipas — Tres Mesas ───────────────────────────────────────────────
    "TresmesasI_MERRA2_N23.000_W98.750.txt":
        _c("P.E. Tres Mesas I",          "Tamaulipas", "San Fernando"),
    "TresmesasI_MERRA2_N23.500_W98.750ARRIBA CERCA.txt":
        _c("P.E. Tres Mesas I — N",      "Tamaulipas", "San Fernando"),
    "TresmesasII_ERA5_N23.250_W98.750.txt":
        _c("P.E. Tres Mesas II (ERA5)",  "Tamaulipas", "San Fernando"),
    "TresmesasII_MERRA2_N23.000_W98.750.txt":
        _c("P.E. Tres Mesas II — S",     "Tamaulipas", "San Fernando"),
    "TresmesasII_MERRA2_N23.500_W98.750ARRIBA.txt":
        _c("P.E. Tres Mesas II — N",     "Tamaulipas", "San Fernando"),
    "Tres mesas III_ERA5_N23.250_W99.000.txt":
        _c("P.E. Tres Mesas III (ERA5)", "Tamaulipas", "San Fernando"),
    "Tres mesas III_MERRA2_N23.000_W98.750ABAJO.txt":
        _c("P.E. Tres Mesas III — S",    "Tamaulipas", "San Fernando"),
    "Tres mesas III_MERRA2_N23.500_W98.750UP.txt":
        _c("P.E. Tres Mesas III — N",    "Tamaulipas", "San Fernando"),
    "Tresmesas_ERA5_N23.250_W99.000.txt":
        _c("P.E. Tres Mesas",            "Tamaulipas", "San Fernando"),

    # ── Tamaulipas — El Cortijo ───────────────────────────────────────────────
    "ElCortijo_ERA5_N25.750_W98.250.txt":
        _c("P.E. El Cortijo",       "Tamaulipas", "Camargo"),
    "ElCortijo_MERRA2_N25.500_W98.125.txt":
        _c("P.E. El Cortijo — S",   "Tamaulipas", "Camargo"),
    "ElCortijo_MERRA2_N26.000_W98.125ARRIBA.txt":
        _c("P.E. El Cortijo — N",   "Tamaulipas", "Camargo"),

    # ── Tamaulipas — El Porvenir ──────────────────────────────────────────────
    "El Porvenir_ERA5_N26.000_W98.250.txt":
        _c("P.E. El Porvenir",          "Tamaulipas", "Miguel Alemán"),
    "El Porvenir_MERRA2_N26.000_W98.125.txt":
        _c("P.E. El Porvenir (MERRA-2)","Tamaulipas", "Miguel Alemán"),

    # ── Tamaulipas — Río Dolores ──────────────────────────────────────────────
    "DoloresERA5_N25.500_W98.500.txt":
        _c("Sitio Río Dolores — W",  "Tamaulipas", "Guerrero"),
    "DoloresMERRA2_N25.500_W98.750.txt":
        _c("Sitio Río Dolores",      "Tamaulipas", "Guerrero"),

    # ── Nuevo León — Ventika ──────────────────────────────────────────────────
    "VENTIKA_I__ERA5_N25.750_W98.750.txt":
        _c("P.E. Ventika I",         "Nuevo León", "General Bravo"),
    "VENTIKA_I__ERA5_N26.000_W98.750.txt":
        _c("P.E. Ventika I — N",     "Nuevo León", "General Bravo"),
    "VENTIKA_I__MERRA2_N25.500_W98.750.txt":
        _c("P.E. Ventika I — S",     "Nuevo León", "General Bravo"),
    "VENTIKA_I__MERRA2_N26.000_W98.750.txt":
        _c("P.E. Ventika I — N (MERRA-2)","Nuevo León","General Bravo"),
    "VENTIKA_II__ERA5_N25.750_W98.750.txt":
        _c("P.E. Ventika II",        "Nuevo León", "Pesquería"),
    "VENTIKA_II__MERRA2_N25.500_W98.750.txt":
        _c("P.E. Ventika II — S",    "Nuevo León", "Pesquería"),
    "VENTIKA_II__MERRA2_N26_W98.750.txt":
        _c("P.E. Ventika II — N",    "Nuevo León", "Pesquería"),

    # ── Nuevo León — Santa Catarina ───────────────────────────────────────────
    "Santa Catarina ERA5_N25.750_W100.500.txt":
        _c("P.E. Santa Catarina — W",       "Nuevo León", "Santa Catarina"),
    "Santa Catarina ERA5_N25.750_W100.750.txt":
        _c("P.E. Santa Catarina — E",       "Nuevo León", "Santa Catarina"),
    "Santa Catarina MERRA2_N25.500_W100.625.txt":
        _c("P.E. Santa Catarina (MERRA-2)", "Nuevo León", "Santa Catarina"),

    # ── Nuevo León / Coahuila — El Mezquite ──────────────────────────────────
    "El mezquite ERA5_N26.250_W100.500.txt":
        _c("P.E. El Mezquite — N",       "Coahuila", "Muzquiz"),
    "El mezquite MERRA2_N26.000_W100.625.txt":
        _c("P.E. El Mezquite",           "Nuevo León","García"),
    "El mezquite MERRA2_N26.500_W100.625.txt":
        _c("P.E. El Mezquite — NW",      "Coahuila", "Muzquiz"),

    # ── Coahuila — Corredor Eólico ────────────────────────────────────────────
    "CoahuilaERA5_N25.750_W101.500.txt":
        _c("Corredor Eólico Coahuila",   "Coahuila", "Sabinas"),
    "CoahuilaMERRA2_N25.500_W101.250.txt":
        _c("Corredor Eólico Coahuila — S","Coahuila","Sabinas"),
    "CoahuilaMERRA2_N26.000_W101.250.txt":
        _c("Corredor Eólico Coahuila — N","Coahuila","Múzquiz"),

    # ── Coahuila — P.E. Amistad ───────────────────────────────────────────────
    "Windnavigator_MERRA2_N29.000_W101.875AMISTAD.txt":
        _c("P.E. Amistad — S",      "Coahuila", "Ciudad Acuña"),
    "Windnavigator_MERRA2_N29.500_W101.250AMISTAD.txt":
        _c("P.E. Amistad — N",      "Coahuila", "Ciudad Acuña"),

    # ── Zacatecas — La Bufa ───────────────────────────────────────────────────
    "La Bufa_MERRA2_N22.500_W102.500.txt":
        _c("P.E. La Bufa",          "Zacatecas", "Zacatecas"),
    "Laufa_ERA5_N22.750_W102.500.txt":
        _c("P.E. La Bufa — N (ERA5)","Zacatecas","Zacatecas"),

    # ── Jalisco / Zacatecas — Vientos del Altiplano ───────────────────────────
    "VientosdelAltiplano_ERA5_N23.750_W101.500.txt":
        _c("P.E. Vientos del Altiplano",        "Jalisco",  "Tlaltenango"),
    "VientosdelAltiplano_MERRA2_N23.500_W101.250D.txt":
        _c("P.E. Vientos del Altiplano — S",    "Zacatecas","Tlaltenango"),
    "VientosdelAltiplano_MERRA2_N24.000_W101.250UP.txt":
        _c("P.E. Vientos del Altiplano — N",    "Zacatecas","Tlaltenango"),

    # ── Jalisco / Zacatecas — La Dominica ────────────────────────────────────
    "Dominica_ERA5_N23.250_W101.250ABAJ.txt":
        _c("P.E. La Dominica — S",   "Zacatecas", "Tlaltenango"),
    "Dominica_ERA5_N23.500_W101.250ARRIBA.txt":
        _c("P.E. La Dominica — N",   "Zacatecas", "Tlaltenango"),
    "Dominica_MERRA2_N23.000_W101.250ABAJO.txt":
        _c("P.E. La Dominica — S (MERRA-2)","Jalisco","Totatiche"),
    "Dominica_MERRA2_N23.500_W101.250ARRIBA.txt":
        _c("P.E. La Dominica (MERRA-2)","Zacatecas","Tlaltenango"),

    # ── Jalisco / SLP — Los Altos ─────────────────────────────────────────────
    "LosaltosERA5_N21.750_W101.750.txt":
        _c("P.E. Los Altos",         "Jalisco",     "Lagos de Moreno"),
    "LosaltosMERRA2_N21.500_W101.875.txt":
        _c("P.E. Los Altos — S",     "Jalisco",     "Lagos de Moreno"),
    "LosaltosMERRA2_N22.000_W101.875.txt":
        _c("P.E. Los Altos — N",     "San Luis Potosí","Villa de Guadalupe"),

    # ── Jalisco / SLP — Palo Alto ─────────────────────────────────────────────
    "Palo Alto ERA5_N21.750_W101.750.txt":
        _c("P.E. Palo Alto",         "Jalisco",      "Lagos de Moreno"),
    "Palo Alto ERA5_N21.750_W102.000.txt":
        _c("P.E. Palo Alto — W",     "Jalisco",      "Lagos de Moreno"),
    "Palo Alto MERRA2_N22.000_W101.875.txt":
        _c("P.E. Palo Alto — N",     "San Luis Potosí","Villa de Guadalupe"),

    # ── Veracruz / Puebla — Pier II ──────────────────────────────────────────
    "Pier II_ERA5_N18.750_W97.250mendoza.txt":
        _c("P.E. Pier II — Mendoza",   "Veracruz", "Mendoza"),
    "Pier II_ERA5_N19.000_W97.250escobedo.txt":
        _c("P.E. Pier II — Escobedo",  "Veracruz", "Soledad Atzompa"),
    "Pier II_MERRA2_N19.000_W97.500cerdam(1).txt":
        _c("P.E. Pier II — Cerdán",    "Puebla",   "Tehuacán"),
    "Pier II_MERRA2_N19.000_W97.500cerdam.txt":
        _c("P.E. Pier II — Cerdán (MERRA-2)","Puebla","Tehuacán"),

    # ── Baja California — Sierra Juárez / La Rumorosa ─────────────────────────
    "Sierra Juarez  ERA5_N32.500_W116.250.txt":
        _c("P.E. Energía Sierra Juárez",        "Baja California","Tecate"),
    "Sierra Juarez  MERRA2_N32.500_W116.250.txt":
        _c("P.E. Energía Sierra Juárez (MERRA-2)","Baja California","Tecate"),
    "Sierra Juarez ERA5 N32.500_W116.000.txt":
        _c("P.E. Energía Sierra Juárez — E",    "Baja California","Tecate"),
    "LaRumorosaERA5 N32.500_W116.250L.txt":
        _c("P.E. La Rumorosa (ERA5)",    "Baja California","Tecate"),
    "LaRumorosaMERRA2 N32.500_W116.250.txt":
        _c("P.E. La Rumorosa",           "Baja California","Tecate"),
    "LaERA5N32.500_W116.000.txt":
        _c("P.E. La Rumorosa — E (ERA5)","Baja California","Tecate"),

    # ── Baja California — Fuerza Eólica San Matías ───────────────────────────
    "FuerzMERRA2N31.500_W115.625.txt":
        _c("P.E. Fuerza Eólica San Matías",       "Baja California","Mexicali"),
    "FuerzaEolicaSanMatias ERA5N31.250_W115.500.txt":
        _c("P.E. Fuerza Eólica San Matías (ERA5)","Baja California","Mexicali"),

    # ── Baja California Sur — Coromuel ───────────────────────────────────────
    "CoromuelERA5N24.250_W110.500.txt":
        _c("Recurso Eólico Coromuel — W",  "Baja California Sur","La Paz"),
    "CoromuelERA5_N24.000_W110.750.txt":
        _c("Recurso Eólico Coromuel",      "Baja California Sur","La Paz"),
    "CoromuelMERRA2 N24.000_W110.625.txt":
        _c("Recurso Eólico Coromuel (MERRA-2)","Baja California Sur","La Paz"),
}


def get_site_info(filename: str) -> dict:
    """Return {display_name, state, municipality} for the given filename.

    Falls back to a name derived from the filename prefix + parsed coordinates
    when the file is not in the catalog.
    """
    # 1. Exact match
    entry = _CATALOG.get(filename)
    if entry:
        return entry

    # 2. Fallback: derive from filename
    base = filename
    for ext in (".txt", ".csv", ".tsv", ".dat"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break

    # Extract coordinate hint
    m = _COORD_RE.search(base)
    coord_hint = ""
    if m:
        lat = float(m.group(2).replace(",", "."))
        lon = float(m.group(3).replace(",", "."))
        coord_hint = f"  {lat:.2f}°N {lon:.2f}°W"

    # Build a readable prefix: strip trailing coordinate portion and noise
    prefix = _COORD_RE.sub("", base).strip("_ -")
    # Remove common suffixes that add no meaning
    for noise in ("Windnavigator", "MERRA2", "ERA5", "Exported"):
        prefix = prefix.replace(noise, "").strip("_ -")
    prefix = prefix.strip("_ -") or "Sitio"

    display_name = f"{prefix}{coord_hint}".strip()
    return {"display_name": display_name, "state": "", "municipality": ""}
