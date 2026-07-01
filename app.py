"""
Dashboard de Cobertura y Facturacion - Portafolio PPG / Glidden Venezuela
==========================================================================
Visualiza en un mapa interactivo (Folium + MarkerCluster) la ubicacion,
facturacion, visitas y estatus de las cuentas del portafolio, y calcula
el margen de ganancia estimado en el panel lateral.

Fuente de datos: Google Sheets en vivo, leido directamente como CSV publico
(sin credenciales ni Service Account: el Sheet tiene acceso "Cualquier
usuario con el enlace"). Si la lectura falla por algun motivo (Sheet dejo
de ser publico, sin internet, etc.), el dashboard usa automaticamente
datos simulados (mock) de 29 cuentas para que nunca se rompa.
"""

import hashlib
import random
import re
import socket
import xmlrpc.client
from datetime import datetime

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import folium_static

# --------------------------------------------------------------------------
# CONFIGURACION GENERAL DE LA PAGINA
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Dashboard PPG/Glidden - Cobertura de Clientes",
    page_icon="🎨",
    layout="wide",
)

# --------------------------------------------------------------------------
# CSS PREMIUM (DARK MODE) - inyectado una sola vez por render.
# Oculta el chrome por defecto de Streamlit, aplica el fondo oscuro, las
# tarjetas KPI tipo "glassmorphism" y los titulos de seccion. El tema base
# (colores de los widgets nativos: sidebar, multiselect, dataframe, etc.)
# se define ademas en .streamlit/config.toml para que TODO el dashboard,
# no solo lo que inyectamos por CSS, se vea consistente en modo oscuro.
# --------------------------------------------------------------------------
PREMIUM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Oculta el menu hamburguesa, el footer "Made with Streamlit" y la
   barra de herramientas superior por defecto. */
#MainMenu, footer, header,
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"] {
    visibility: hidden;
    height: 0;
}

/* Fondo general con degrade sutil (dark navy / charcoal premium). */
.stApp {
    background: radial-gradient(circle at 15% 0%, #131a2a 0%, #0b0f19 55%, #070a10 100%);
}

/* Menos margen torpe alrededor del contenido para que el mapa domine. */
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 2rem;
    padding-left: 2.2rem;
    padding-right: 2.2rem;
    max-width: 1500px;
}

/* Sidebar con tono ligeramente distinto al fondo principal. */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #10141f 0%, #0b0f19 100%);
    border-right: 1px solid rgba(255,255,255,0.06);
}

/* Tarjeta glassmorphism reutilizada por el velocímetro de Pace to Goal
   (render_velocimetro). El color de acento se pasa por variable CSS
   inline (--accent). Las métricas KPI usan st.metric nativo (ver bloque
   "stMetric" más abajo), que soporta tooltips (help=...). */
.kpi-card {
    background: linear-gradient(145deg, rgba(255,255,255,0.06), rgba(255,255,255,0.015));
    border: 1px solid rgba(255,255,255,0.08);
    border-left: 3px solid var(--accent, #00e5ff);
    border-radius: 16px;
    padding: 16px 18px;
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.35);
    margin-bottom: 10px;
    transition: transform 0.15s ease;
}
.kpi-card:hover { transform: translateY(-2px); }
.kpi-icon { font-size: 18px; opacity: 0.85; margin-bottom: 6px; }
.kpi-label {
    font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    color: #9aa4b2; font-weight: 600; margin-bottom: 6px;
}
.kpi-value { font-size: 24px; font-weight: 800; color: #f5f7fa; }

/* Tarjetas de metricas nativas (st.metric): mismo lenguaje visual
   glassmorphism que .kpi-card, para que cada metrica pueda traer su
   tooltip (help="...") sin perder la estetica premium del dashboard. */
div[data-testid="stMetric"] {
    background: linear-gradient(145deg, rgba(255,255,255,0.06), rgba(255,255,255,0.015));
    border: 1px solid rgba(255,255,255,0.08);
    border-left: 3px solid #00e5ff;
    border-radius: 16px;
    padding: 14px 18px;
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.35);
}
div[data-testid="stMetricLabel"] { color: #9aa4b2 !important; font-weight: 600; }
div[data-testid="stMetricValue"] { color: #f5f7fa !important; }
/* El icono "?" de ayuda de st.metric hereda el color por defecto de
   Streamlit; se resalta un poco para que sea obvio que es interactivo. */
div[data-testid="stMetric"] [data-testid="stTooltipIcon"] { color: #00e5ff !important; }

/* Titulos de seccion del cuerpo principal y del sidebar. */
.section-title {
    font-size: 19px; font-weight: 800; color: #f5f7fa;
    margin: 26px 0 12px 0; padding-left: 12px;
    border-left: 4px solid #00e5ff;
}
.sidebar-title {
    font-size: 14px; font-weight: 700; color: #f5f7fa;
    letter-spacing: 0.02em; margin: 4px 0 10px 0; padding-bottom: 6px;
    border-bottom: 2px solid rgba(0,229,255,0.35);
}

/* Marco premium alrededor del iframe del mapa de Folium (bordes
   redondeados + sombra), evitando el recuadro por defecto sin estilo. */
[data-testid="stIFrame"] {
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 0 14px 40px rgba(0,0,0,0.45);
    border: 1px solid rgba(255,255,255,0.07);
}

h1 { font-weight: 800 !important; letter-spacing: -0.02em; }
</style>
"""
st.markdown(PREMIUM_CSS, unsafe_allow_html=True)

# Columnas "ideales" que idealmente trae el Google Sheet (documentacion).
# Estructura EXACTA de la hoja real conectada (ver seccion 1): Cliente,
# Tipo de Lead, Manager, Categoria General, Zona y Monto Total.
# Latitud/Longitud/Visitas no vienen en el Sheet real: se derivan solas
# (ver asegurar_coordenadas / asegurar_visitas mas abajo).
EXPECTED_COLUMNS = [
    "Cliente", "Tipo de Lead", "Manager", "Categoría General",
    "Zona", "Monto Total", "Latitud", "Longitud", "Visitas",
]

# Alias aceptados por columna (en minusculas, sin espacios extra ni
# guiones bajos) para que el Sheet real no tenga que usar exactamente los
# mismos encabezados. Por ejemplo, si el Sheet trae "CLIENTE", "monto",
# "Monto_Total" o "Ciudad", igual se reconocen.
COLUMN_ALIASES = {
    "cliente": "Cliente",
    "nombre cliente": "Cliente",
    "nombre": "Cliente",
    "tipo de lead": "Tipo de Lead",
    "tipo lead": "Tipo de Lead",
    "lead type": "Tipo de Lead",
    "manager": "Manager",
    "categoria general": "Categoría General",
    "categoría general": "Categoría General",
    "categoria": "Categoría General",
    "categoría": "Categoría General",
    # Alias legacy: si alguna hoja vieja todavia usa "Estatus"/"Status",
    # se trata como la categoria general del cliente.
    "estatus": "Categoría General",
    "status": "Categoría General",
    "latitud": "Latitud",
    "lat": "Latitud",
    "longitud": "Longitud",
    "lon": "Longitud",
    "lng": "Longitud",
    "monto total": "Monto Total",
    "monto": "Monto Total",
    "facturacion": "Monto Total",
    "facturación": "Monto Total",
    "monto facturacion": "Monto Total",
    "monto facturado": "Monto Total",
    "visitas": "Visitas",
    "visitas mes": "Visitas",
    "visitas del mes": "Visitas",
    "zona": "Zona",
    "zona cliente": "Zona",
    "ciudad": "Zona",
    "sector": "Zona",
    "region": "Zona",
    "región": "Zona",
}

# Columnas realmente indispensables para poder mostrar algo en el mapa y
# calcular el margen. Todo lo demas (Zona, Categoria General, Tipo de Lead,
# Manager, Latitud/Longitud, Visitas) es opcional y se resuelve solo si
# falta, para que la app nunca colapse con la data real del Sheet.
COLUMNAS_REQUERIDAS = ["Cliente", "Monto Total"]

# Si una cuenta tiene MENOS visitas que este numero en el mes, su pin se
# pinta de rojo en el mapa como alerta visual.
MIN_VISITAS_ALERTA = 2

# Margen de ganancia lineal asumido sobre la facturacion de cada cliente.
# INAMOVIBLE: 20% lineal y estricto sobre "Monto Total" (regla de negocio).
MARGEN_PORCENTAJE = 0.20

# El velocimetro de "Pace to Goal" asume un mes de 30 dias parejos (en vez
# del numero real de dias del mes calendario) para simplificar el ritmo
# diario necesario, tal como se pidio explicitamente.
DIAS_ASUMIDOS_MES = 30

# Cuentas de alto volumen para el filtro rapido "Cuentas Clave" (aisla en el
# mapa y las tablas solo a estos clientes estrategicos). El match es por
# substring (case-insensitive) para cubrir variantes del mismo nombre, ej.
# "Ferretotal Express" o "Mundo del Color Express" tambien cuentan como
# cuenta clave de su marca.
CUENTAS_CLAVE = ["Mundo del Color", "Ferretotal", "Savake"]

# Umbral de "cliente en riesgo" para la metrica de Recencia: si pasaron mas
# de estos dias desde su ultima compra, se resalta con alerta visual.
RECENCIA_ALERTA_DIAS = 14

# Ponla en True si quieres forzar el uso de datos simulados aunque ya
# tengas credenciales configuradas (util para hacer demos o pruebas).
FORZAR_MOCK_DATA = False


def es_cuenta_clave(nombre) -> bool:
    """True si `nombre` (Cliente o Nombre de factura) pertenece a alguna de
    las CUENTAS_CLAVE (match por substring, insensible a mayusculas).
    """
    nombre_lower = str(nombre).lower()
    return any(clave.lower() in nombre_lower for clave in CUENTAS_CLAVE)

# --------------------------------------------------------------------------
# GEOCODIFICACION AUTOMATICA (columna "Zona" -> Latitud/Longitud)
# --------------------------------------------------------------------------
# Coordenadas centrales de Caracas: se usan como valor por defecto cuando
# una zona no trae coordenadas propias y tampoco se pudo geocodificar
# (zona vacia, sin conexion a internet, Nominatim no la reconoce, etc.).
CARACAS_LAT = 10.4806
CARACAS_LON = -66.9036

# Mapeo best-effort de ciudades/zonas conocidas del portafolio al estado
# venezolano correspondiente, para que la busqueda en Nominatim sea mas
# precisa (ej. "Maracaibo" -> "Zulia, Venezuela" en vez de asumir Caracas).
# Si la zona no matchea ninguna clave, se usa "Caracas, Venezuela" por
# defecto, tal como se pidio.
ESTADO_POR_ZONA = {
    "caracas": "Distrito Capital, Venezuela",
    "chacao": "Miranda, Venezuela",
    "baruta": "Miranda, Venezuela",
    "el cafetal": "Miranda, Venezuela",
    "los teques": "Miranda, Venezuela",
    "boleita": "Distrito Capital, Venezuela",
    "la candelaria": "Distrito Capital, Venezuela",
    "los ruices": "Distrito Capital, Venezuela",
    "la trinidad": "Miranda, Venezuela",
    "la guaira": "La Guaira, Venezuela",
    "catia la mar": "La Guaira, Venezuela",
    "vargas": "La Guaira, Venezuela",
    "maracaibo": "Zulia, Venezuela",
    "valencia": "Carabobo, Venezuela",
    "maracay": "Aragua, Venezuela",
    "turmero": "Aragua, Venezuela",
    "cagua": "Aragua, Venezuela",
    "barquisimeto": "Lara, Venezuela",
    "cabudare": "Lara, Venezuela",
    "san cristobal": "Táchira, Venezuela",
    "san cristóbal": "Táchira, Venezuela",
    "merida": "Mérida, Venezuela",
    "mérida": "Mérida, Venezuela",
    "barinas": "Barinas, Venezuela",
    "acarigua": "Portuguesa, Venezuela",
    "punto fijo": "Falcón, Venezuela",
    "coro": "Falcón, Venezuela",
    "valera": "Trujillo, Venezuela",
    "san juan de los morros": "Guárico, Venezuela",
    "puerto la cruz": "Anzoátegui, Venezuela",
    "anaco": "Anzoátegui, Venezuela",
    "ciudad guayana": "Bolívar, Venezuela",
    "maturin": "Monagas, Venezuela",
    "maturín": "Monagas, Venezuela",
    # Zonas confirmadas en la hoja real que NO son de Caracas (sin esta
    # entrada caerian mal en el fallback ", Caracas, Venezuela").
    "barcelona": "Anzoátegui, Venezuela",
    "nueva esparta": "Nueva Esparta, Venezuela",
    "guarico": "Guárico, Venezuela",
    "guárico": "Guárico, Venezuela",
    "los valles del tuy": "Miranda, Venezuela",
}

# --------------------------------------------------------------------------
# OPTIMIZACION EXTREMA DE CARGA: coordenadas predefinidas de las zonas mas
# comunes del portafolio (Caracas central y sus urbanizaciones, mas
# capitales/ciudades del interior). geocodificar_zonas() consulta ESTE
# diccionario PRIMERO y solo cae a Nominatim (que impone 1 request/seg) si
# la zona no aparece aqui, lo que baja el tiempo de carga tipico de ~30s a
# menos de 1s en despliegues con las zonas habituales del negocio.
# --------------------------------------------------------------------------
COORDENADAS_ZONAS_CONOCIDAS: dict[str, tuple[float, float]] = {
    "caracas": (10.4806, -66.9036),
    "chacao": (10.4967, -66.8530),
    "baruta": (10.4380, -66.8781),
    "el cafetal": (10.4700, -66.8420),
    "las mercedes": (10.4890, -66.8567),
    "los teques": (10.3406, -67.0364),
    "boleita": (10.5216, -66.8478),
    "boleíta": (10.5216, -66.8478),
    "la candelaria": (10.5057, -66.9080),
    "los ruices": (10.4961, -66.8459),
    "la trinidad": (10.4459, -66.8298),
    "la guaira": (10.6019, -66.9311),
    "catia la mar": (10.6053, -67.0322),
    "vargas": (10.6019, -66.9311),
    "maracaibo": (10.6427, -71.6125),
    "valencia": (10.1620, -68.0077),
    "maracay": (10.2469, -67.5959),
    "turmero": (10.2278, -67.4756),
    "cagua": (10.1922, -67.4497),
    "barquisimeto": (10.0678, -69.3467),
    "cabudare": (10.0611, -69.2444),
    "san cristobal": (7.7669, -72.2250),
    "san cristóbal": (7.7669, -72.2250),
    "merida": (8.5921, -71.1442),
    "mérida": (8.5921, -71.1442),
    "barinas": (8.6226, -70.2075),
    "acarigua": (9.5597, -69.2000),
    "punto fijo": (11.6822, -70.2144),
    "coro": (11.4045, -69.6816),
    "valera": (9.3178, -70.6061),
    "san juan de los morros": (9.9112, -67.3378),
    "puerto la cruz": (10.2137, -64.6335),
    "anaco": (9.4372, -64.4692),
    "ciudad guayana": (8.3533, -62.6474),
    "puerto ordaz": (8.3086, -62.7220),
    "maturin": (9.7450, -63.1783),
    "maturín": (9.7450, -63.1783),
    "barcelona": (10.1352, -64.6858),
}


def construir_query_geocoding(zona: str) -> str:
    """Arma el texto de busqueda para Nominatim a partir de una Zona.

    Le agrega el estado venezolano correspondiente si reconoce la zona
    (ej. "Bella Vista" + Maracaibo -> "..., Zulia, Venezuela"); si no la
    reconoce, usa ", Caracas, Venezuela" por defecto.
    """
    zona_limpia = zona.strip()
    zona_lower = zona_limpia.lower()
    for clave, estado in ESTADO_POR_ZONA.items():
        if clave in zona_lower:
            return f"{zona_limpia}, {estado}"
    return f"{zona_limpia}, Caracas, Venezuela"


def _buscar_en_diccionario_estatico(zona: str) -> tuple[float, float] | None:
    """Busca `zona` (por substring, igual que construir_query_geocoding) en
    COORDENADAS_ZONAS_CONOCIDAS. Devuelve (lat, lon) si la reconoce, o None
    si hay que recurrir a Nominatim.
    """
    zona_lower = zona.strip().lower()
    for clave, coords in COORDENADAS_ZONAS_CONOCIDAS.items():
        if clave in zona_lower:
            return coords
    return None


@st.cache_data(show_spinner="Geocodificando zonas (Nominatim/OpenStreetMap)...", ttl=None)
def geocodificar_zonas(zonas: tuple[str, ...]) -> dict:
    """Geocodifica un conjunto de zonas UNICAS y devuelve {zona: (lat, lon)}.

    OPTIMIZACION CRITICA: cada zona se busca PRIMERO en el diccionario
    estatico COORDENADAS_ZONAS_CONOCIDAS (instantaneo, sin red). Nominatim
    (que impone 1 request/seg) solo se usa como fallback para las zonas que
    NO estan en ese diccionario, y el import de geopy ni siquiera ocurre si
    todas las zonas ya se resolvieron localmente. En un portafolio tipico,
    donde casi todas las cuentas caen en zonas conocidas, esto baja el
    tiempo de carga de ~30s a menos de 1s.

    Se cachea ademas con @st.cache_data para que, al recargar el dashboard
    o cambiar un filtro, NO se vuelva a golpear la API de Nominatim por
    cada cliente: solo se geocodifica una vez por zona (mientras la cache
    viva). Si una zona no se puede geocodificar (vacia, sin internet, no
    reconocida por Nominatim, error/timeout), cae a las coordenadas
    centrales de Caracas para que el mapa nunca se rompa.
    """
    coordenadas_por_zona: dict[str, tuple[float, float]] = {}
    zonas_pendientes = []

    for zona in zonas:
        coords_estaticas = _buscar_en_diccionario_estatico(zona) if zona else None
        if coords_estaticas is not None:
            coordenadas_por_zona[zona] = coords_estaticas
        else:
            zonas_pendientes.append(zona)

    if not zonas_pendientes:
        return coordenadas_por_zona

    # Import local: si `geopy` no estuviera instalado, el resto del
    # dashboard sigue funcionando (mock data / coordenadas ya presentes)
    # en vez de tronar al importar el archivo. Solo se importa cuando
    # realmente quedan zonas sin resolver por el diccionario estatico.
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.geocoders import Nominatim

    geolocator = Nominatim(user_agent="dashboard_ppg_glidden_epa")
    # RateLimiter respeta el limite de 1 request/seg de Nominatim y
    # reintenta automaticamente ante timeouts esporadicos.
    geocode = RateLimiter(
        geolocator.geocode, min_delay_seconds=1, max_retries=2, error_wait_seconds=2
    )

    for zona in zonas_pendientes:
        lat, lon = CARACAS_LAT, CARACAS_LON
        if zona:
            try:
                ubicacion = geocode(construir_query_geocoding(zona), timeout=10)
                if ubicacion is not None:
                    lat, lon = ubicacion.latitude, ubicacion.longitude
            except Exception:
                # Cualquier fallo de geocodificacion (timeout, sin
                # internet, zona no reconocida) -> se queda con Caracas.
                pass
        coordenadas_por_zona[zona] = (lat, lon)
    return coordenadas_por_zona


def asegurar_coordenadas(df: pd.DataFrame) -> pd.DataFrame:
    """Garantiza que el dataframe tenga Latitud/Longitud numericas validas
    en TODAS las filas, sin importar como venga la data real:

      1) Si el Sheet ya trae Latitud/Longitud validas, se respetan tal cual.
      2) Si faltan (columna ausente o vacia) y existe una columna "Zona",
         se geocodifica automaticamente esa zona (con cache).
      3) Si aun asi no se pudo resolver (sin Zona, Zona vacia, o fallo de
         geocodificacion), se usa el centro de Caracas por defecto.

    Esto evita que la aplicacion colapse cuando la data real no trae
    coordenadas.
    """
    df = df.copy()

    if "Latitud" in df.columns:
        df["Latitud"] = pd.to_numeric(df["Latitud"], errors="coerce")
    else:
        df["Latitud"] = np.nan

    if "Longitud" in df.columns:
        df["Longitud"] = pd.to_numeric(df["Longitud"], errors="coerce")
    else:
        df["Longitud"] = np.nan

    faltan_coords = df["Latitud"].isna() | df["Longitud"].isna()

    if faltan_coords.any() and "Zona" in df.columns:
        zonas_a_geocodificar = (
            df.loc[faltan_coords, "Zona"]
            .dropna()
            .astype(str)
            .str.strip()
        )
        zonas_unicas = tuple(sorted(set(z for z in zonas_a_geocodificar if z)))

        if zonas_unicas:
            mapa_coords = geocodificar_zonas(zonas_unicas)
            for idx in df.index[faltan_coords]:
                zona_valor = df.at[idx, "Zona"]
                zona_valor = str(zona_valor).strip() if pd.notna(zona_valor) else ""
                lat, lon = mapa_coords.get(zona_valor, (CARACAS_LAT, CARACAS_LON))
                df.at[idx, "Latitud"] = lat
                df.at[idx, "Longitud"] = lon

    # Cualquier fila que siga sin coordenadas (sin columna Zona, Zona
    # vacia, etc.) cae al centro de Caracas por defecto para que el mapa
    # nunca se quede sin renderizar.
    df["Latitud"] = df["Latitud"].fillna(CARACAS_LAT)
    df["Longitud"] = df["Longitud"].fillna(CARACAS_LON)

    return df


def asegurar_visitas(df: pd.DataFrame) -> pd.DataFrame:
    """Si el dataframe real no trae la columna "Visitas", inyecta un valor
    temporal (entero entre 0 y 5) para mantener viva la logica de alertas
    de baja cobertura (< MIN_VISITAS_ALERTA). El valor se deriva de un hash
    estable del nombre del cliente (no de random puro) para que no
    "parpadee" con cada recarga/rerun del dashboard.
    """
    df = df.copy()
    if "Visitas" not in df.columns:
        def visitas_pseudo_aleatorias(cliente: str) -> int:
            hash_hex = hashlib.md5(str(cliente).encode("utf-8")).hexdigest()
            return int(hash_hex, 16) % 6  # 0 a 5

        df["Visitas"] = df["Cliente"].apply(visitas_pseudo_aleatorias)
    return df


# --------------------------------------------------------------------------
# LIMPIEZA "A PRUEBA DE BALAS" DE COLUMNAS CLAVE (VENDEDOR/MANAGER, ZONA,
# CATEGORIA) - se aplica en AMBOS dataframes (Clientes y Facturas) ANTES de
# cualquier filtro encadenado o cruce entre ellos. Sin esto, "Caracas ",
# "CARACAS" y "caracas" (o espacios colgantes en un Manager) se tratan como
# valores distintos y rompen tanto los multiselect encadenados como el
# cruce Clientes <-> Facturas por Manager.
# --------------------------------------------------------------------------

def normalizar_columnas_clave(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    """Fuerza texto, recorta espacios y pasa a minusculas las `columnas`
    indicadas (si existen en `df`). Esta es la version "logica" del dato,
    la que se usa para filtrar y cruzar: la UI se encarga aparte de
    mostrarla capitalizada (ver `titular_para_mostrar` / `format_func`).
    """
    df = df.copy()
    for columna in columnas:
        if columna in df.columns:
            df[columna] = df[columna].astype(str).str.strip().str.lower()
    return df


def titular_para_mostrar(valor) -> str:
    """Capitaliza un valor ya normalizado (minusculas) para mostrarlo en la
    UI (ej. "mundo del color" -> "Mundo Del Color"), sin tocar el dato
    subyacente que sigue usandose en minusculas para filtrar/cruzar.
    """
    return str(valor).title()


def con_columnas_tituladas(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    """Copia de `df` con `columnas` pasadas a Title Case SOLO para
    presentacion (tablas, popups). La logica de filtros y cruces sigue
    operando sobre el dataframe original (en minusculas).
    """
    df_mostrar = df.copy()
    for columna in columnas:
        if columna in df_mostrar.columns:
            df_mostrar[columna] = df_mostrar[columna].astype(str).apply(titular_para_mostrar)
    return df_mostrar


# ==========================================================================
# 1. FUENTE DE DATOS: GOOGLE SHEETS EN VIVO (LECTURA PUBLICA VIA CSV)
# ==========================================================================
#
# El Google Sheet tiene acceso publico de lectura ("Cualquier usuario con
# el enlace"), asi que NO hace falta Service Account, credenciales ni
# secrets.toml: basta con pedirle a Google que exporte la hoja como CSV y
# leerla directo con pandas. Si el Sheet alguna vez deja de ser publico (o
# la URL cambia), esta funcion simplemente fallara y el dashboard cae de
# forma automatica a datos simulados (mock) para no romperse.
#
# OJO: el gid usado abajo (566196879) es el de la pestana que REALMENTE
# tiene la lista de clientes con las columnas Cliente/Tipo de Lead/Manager/
# Categoria General/Zona/Monto Total. El gid 1517345141 (indicado
# originalmente) apunta a otra pestana ("MES DE JUNIO", un tracker semanal
# de ventas sin esas columnas), asi que se corrigio tras inspeccionar el
# Sheet publico.
GOOGLE_SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1ZaZ5Iz9zbjdyC_7jx4jTNpASRRZ-fMxIlFWUePmV2Kc/export?format=csv&gid=566196879"
)


@st.cache_data(ttl=60, show_spinner="Leyendo Google Sheet en vivo...")
def cargar_datos_desde_google_sheets_csv() -> pd.DataFrame:
    """Lee el Google Sheet publico directamente como CSV (sin credenciales).

    Se cachea 1 minuto (ttl=60) para que el dashboard refleje cambios del
    Sheet casi en vivo sin descargar el CSV en cada rerun. El boton
    "🔄 Actualizar Datos" del sidebar fuerza una lectura inmediata via
    st.cache_data.clear(), sin esperar a que expire el TTL. header=1 porque la
    hoja real trae una fila en blanco antes de los encabezados. Se
    descartan las columnas "Unnamed" y las duplicadas con sufijo ".N" que
    vienen de otras tablas/pivotes que comparten la misma pestana, a la
    derecha de la lista de clientes.
    """
    df = pd.read_csv(GOOGLE_SHEET_CSV_URL, header=1)
    columnas_ruido = [
        columna for columna in df.columns
        if str(columna).startswith("Unnamed") or re.search(r"\.\d+$", str(columna))
    ]
    df = df.drop(columns=columnas_ruido)
    df = df.dropna(how="all")
    return df


# ==========================================================================
# 1B. SEGUNDA FUENTE DE DATOS: FACTURACION DIARIA (SABANA TRANSACCIONAL)
# ==========================================================================
#
# Segunda base de datos "transaccional", independiente de la lista de
# clientes de arriba: la sabana real de facturas diarias del negocio.
# Mismo approach "bypass CSV" (Sheet publico, sin credenciales). Se leyo
# la hoja real para confirmar su estructura exacta:
#   - Trae 3 filas de ruido (una vacia y una con un resumen suelto tipo
#     "CANCELADO") ANTES de los encabezados reales -> header=3.
#   - Los encabezados reales traen espacios colgantes ("Manager ", "Mes ").
#   - Columnas clave: "Fecha de Emision", "Nombre", "Manager",
#     "Monto Facturado", "Saldo Pendiente".
#   - Los montos vienen en formato moneda EUROPEO/venezolano de texto, ej.
#     "$1.234,56" (punto = separador de miles, coma = separador decimal).
#   - Trae ~750 filas "reservadas" al final que solo tienen el numero de
#     "Factura" relleno y todo lo demas vacio (huecos de numeracion sin
#     factura real emitida todavia): se descartan por no tener "Nombre".
FACTURACION_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "11XnHtmzg5TuvsBDBaLB_A-1CvQ6QPGWx4J-qUwJxzeI/export?format=csv"
)


@st.cache_data(ttl=60, show_spinner="Leyendo sabana de facturacion en vivo...")
def cargar_datos_facturacion() -> pd.DataFrame:
    """Lee la sabana de facturas diarias directamente como CSV publico
    (pd.read_csv puro, sin credenciales ni Service Account) y la deja lista
    para consumir: encabezados sin espacios y sin las filas de numeros de
    factura reservados que no tienen ningun otro dato.

    Cacheada 1 minuto (ttl=60) para que las facturas nuevas del dia se
    reflejen casi en vivo; el boton "🔄 Actualizar Datos" del sidebar puede
    forzar una lectura inmediata via st.cache_data.clear().
    """
    df = pd.read_csv(FACTURACION_CSV_URL, header=3)
    df.columns = df.columns.str.strip()
    if "Nombre" in df.columns:
        df = df.dropna(subset=["Nombre"])
    return df


def _limpiar_monto_moneda(serie: pd.Series) -> pd.Series:
    """Convierte una columna de montos con formato moneda europeo/venezolano
    de texto (ej. "$1.234,56", punto = miles y coma = decimales) a float.
    Tolera valores ya numericos, vacios o mal formados (caen en 0).
    """
    texto = serie.astype(str).str.strip()
    texto = texto.str.replace(r"[^\d,.\-]", "", regex=True)
    texto = texto.str.replace(".", "", regex=False)
    texto = texto.str.replace(",", ".", regex=False)
    return pd.to_numeric(texto, errors="coerce").fillna(0)


def normalizar_facturacion(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza la sabana de facturacion: fuerza encabezados sin espacios
    (por si se llama con un dataframe que no paso por
    cargar_datos_facturacion), castea "Monto Facturado"/"Saldo Pendiente" de
    texto-moneda a numero y parsea "Fecha de Emision" a datetime real para
    poder alimentar el velocimetro de meta (Pace to Goal).
    """
    df = df.copy()
    df.columns = df.columns.str.strip()

    df["Monto Facturado"] = (
        _limpiar_monto_moneda(df["Monto Facturado"]) if "Monto Facturado" in df.columns else 0.0
    )
    if "Saldo Pendiente" in df.columns:
        df["Saldo Pendiente"] = _limpiar_monto_moneda(df["Saldo Pendiente"])
    else:
        df["Saldo Pendiente"] = 0.0

    if "Fecha de Emisión" in df.columns:
        df["Fecha de Emisión"] = pd.to_datetime(
            df["Fecha de Emisión"].astype(str).str.strip(),
            format="%d/%m/%Y",
            errors="coerce",
        )

    # "Vencimiento" se parsea tambien a datetime real: la necesita el motor
    # de metricas financieras para calcular la deuda vencida (morosidad).
    if "Vencimiento" in df.columns:
        df["Vencimiento"] = pd.to_datetime(
            df["Vencimiento"].astype(str).str.strip(),
            format="%d/%m/%Y",
            errors="coerce",
        )

    if "Días que tardaron en pagar" in df.columns:
        df["Días que tardaron en pagar"] = pd.to_numeric(
            df["Días que tardaron en pagar"], errors="coerce"
        )

    for columna_texto in ("Manager", "Nombre", "Categoría de Cliente"):
        if columna_texto in df.columns:
            df[columna_texto] = df[columna_texto].fillna("Sin dato")

    # Limpieza a prueba de balas: Manager y Categoría de Cliente son
    # columnas clave para el cruce/filtro encadenado con la lista de
    # Clientes, asi que se normalizan igual (texto, sin espacios, en
    # minusculas). "Nombre" NO se toca: se muestra tal cual viene del
    # Sheet (es el nombre del cliente, no una columna de filtro).
    df = normalizar_columnas_clave(df, ["Manager", "Categoría de Cliente"])

    return df


def cargar_facturacion_segura() -> tuple[pd.DataFrame, bool]:
    """Envuelve cargar_datos_facturacion() + normalizar_facturacion() con el
    mismo criterio de resiliencia que el resto del dashboard: si la sabana
    de facturas no se puede leer (Sheet dejo de ser publico, sin internet,
    URL invalida), el dashboard NO se rompe, simplemente esa seccion queda
    vacia y se muestra un aviso.
    """
    columnas_vacias = [
        "Fecha de Emisión", "Vencimiento", "Nombre", "Manager",
        "Monto Facturado", "Saldo Pendiente", "Días que tardaron en pagar",
    ]
    try:
        df = normalizar_facturacion(cargar_datos_facturacion())
        if df is not None and not df.empty:
            return df, True
    except Exception:
        pass
    return pd.DataFrame(columns=columnas_vacias), False


# ==========================================================================
# 1C. INTEGRACION ODOO (XML-RPC) - PIPELINE DE VENTAS EN VIVO
# ==========================================================================
#
# Conexion DIRECTA a una instancia de Odoo via XML-RPC (protocolo nativo de
# Odoo, sin necesidad de modulos/addons adicionales del lado del servidor
# ni de la libreria "odoorpc"). Requiere 4 llaves en
# ".streamlit/secrets.toml", bajo la seccion [odoo]:
#
#   [odoo]
#   url      = "https://tu-instancia.odoo.com"
#   db       = "nombre_base_de_datos"
#   username = "usuario@tuempresa.com"
#   password = "tu_password_o_api_key"
#
# Ver ".streamlit/secrets.toml.example" para la plantilla completa.

COLUMNAS_PIPELINE_ODOO = [
    "ID", "Oportunidad", "Cliente", "Vendedor", "Etapa",
    "Monto Esperado", "Probabilidad (%)", "Fecha de Cierre Esperada",
]

# Timeout (segundos) para las llamadas XML-RPC a Odoo. Sin esto, una
# instancia caida o inalcanzable podria dejar el dashboard "colgado"
# indefinidamente en vez de fallar rapido y mostrar el mensaje amigable.
ODOO_TIMEOUT_SEGUNDOS = 10


@st.cache_data(ttl=120, show_spinner="Conectando con Odoo (XML-RPC)...")
def obtener_pipeline_odoo() -> tuple[pd.DataFrame, bool, str]:
    """Se conecta EN VIVO a Odoo via XML-RPC y trae el pipeline de ventas
    (modelo "crm.lead", solo oportunidades activas) usando las credenciales
    guardadas de forma segura en `st.secrets["odoo"]` (nunca hardcodeadas
    en el codigo).

    Retorna SIEMPRE una tupla (dataframe, conexion_exitosa, mensaje):
    si faltan credenciales, la autenticacion falla, la instancia esta
    caida o hay un timeout de red, `conexion_exitosa` es False y
    `mensaje` trae una descripcion legible del problema, en vez de dejar
    que la excepcion rompa la app (mismo criterio de resiliencia que
    `cargar_facturacion_segura`).
    """
    df_vacio = pd.DataFrame(columns=COLUMNAS_PIPELINE_ODOO)

    if "odoo" not in st.secrets:
        return df_vacio, False, (
            "No se encontró la sección [odoo] en .streamlit/secrets.toml. "
            "Agrega url, db, username y password para conectar el Pipeline en vivo "
            "(ver .streamlit/secrets.toml.example)."
        )

    config_odoo = st.secrets["odoo"]
    faltantes = [clave for clave in ("url", "db", "username", "password") if not config_odoo.get(clave)]
    if faltantes:
        return df_vacio, False, (
            "Faltan credenciales en la sección [odoo] de secrets.toml: " + ", ".join(faltantes) + "."
        )

    url = str(config_odoo["url"]).rstrip("/")
    db = str(config_odoo["db"])
    username = str(config_odoo["username"])
    password = str(config_odoo["password"])

    timeout_previo = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(ODOO_TIMEOUT_SEGUNDOS)

        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        uid = common.authenticate(db, username, password, {})
        if not uid:
            return df_vacio, False, "Odoo rechazó las credenciales (usuario, contraseña o base de datos incorrectos)."

        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        oportunidades = models.execute_kw(
            db, uid, password,
            "crm.lead", "search_read",
            [[["type", "=", "opportunity"], ["active", "=", True]]],
            {
                "fields": [
                    "id", "name", "partner_id", "user_id", "stage_id",
                    "expected_revenue", "probability", "date_deadline",
                ],
                "limit": 500,
            },
        )
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError) as error:
        return df_vacio, False, f"Odoo respondió con un error: {error}"
    except (socket.timeout, ConnectionError, OSError) as error:
        return df_vacio, False, f"No se pudo alcanzar la instancia de Odoo (¿URL correcta? ¿hay internet?): {error}"
    except Exception as error:
        return df_vacio, False, f"No se pudo conectar con Odoo: {error}"
    finally:
        socket.setdefaulttimeout(timeout_previo)

    if not oportunidades:
        return df_vacio, True, "Conexión exitosa: no hay oportunidades abiertas en el pipeline."

    filas = []
    for oportunidad in oportunidades:
        filas.append({
            "ID": oportunidad.get("id"),
            "Oportunidad": oportunidad.get("name") or "Sin nombre",
            "Cliente": (oportunidad.get("partner_id") or [None, "Sin cliente"])[1],
            "Vendedor": (oportunidad.get("user_id") or [None, "Sin asignar"])[1],
            "Etapa": (oportunidad.get("stage_id") or [None, "Sin etapa"])[1],
            "Monto Esperado": oportunidad.get("expected_revenue") or 0.0,
            "Probabilidad (%)": oportunidad.get("probability") or 0.0,
            "Fecha de Cierre Esperada": oportunidad.get("date_deadline") or None,
        })

    df_pipeline = pd.DataFrame(filas, columns=COLUMNAS_PIPELINE_ODOO)
    return df_pipeline, True, "Conectado a Odoo en vivo."


# ==========================================================================
# 2. DATOS SIMULADOS (MOCK) - 29 CUENTAS DEL PORTAFOLIO
# ==========================================================================
#
# Se usan mientras configuras tus credenciales reales de Google Sheets.
# Incluye clientes reales del portafolio (Ferretotal, Savake, Mundo del
# Color) mas cuentas adicionales ficticias para completar 29 filas,
# distribuidas geograficamente entre Caracas y ciudades del interior.
# La facturacion simula compras de lineas de pintura PPG y Glidden.

def generar_datos_mock() -> pd.DataFrame:
    """Genera 29 cuentas simuladas con nombres, ubicacion, facturacion,
    visitas y estatus realistas para un distribuidor de pinturas PPG/Glidden
    en Venezuela.
    """
    random.seed(42)
    np.random.seed(42)

    # (Cliente, Latitud, Longitud) - nombre de ciudad/zona en el comentario.
    clientes = [
        ("Ferretotal", 10.4967, -66.8530),                       # Chacao, Caracas
        ("Savake", 10.4459, -66.8298),                           # La Trinidad, Caracas
        ("Mundo del Color", 10.4961, -66.8459),                  # Los Ruices, Caracas
        ("Ferretotal Express", 10.4700, -66.8420),               # El Cafetal, Caracas
        ("Mundo del Color Express", 10.5057, -66.9080),          # La Candelaria, Caracas
        ("Home Center Pinturas", 10.5216, -66.8478),             # Boleita, Caracas
        ("Ferreteria Litoral", 10.6019, -66.9311),               # La Guaira
        ("Pinturas Vargas", 10.6053, -67.0322),                  # Catia La Mar
        ("Home Depot Los Teques", 10.3406, -67.0364),            # Los Teques
        ("Ferreteria La Economica", 10.1620, -68.0077),          # Valencia
        ("Distribuidora Colorama", 10.2469, -67.5959),           # Maracay
        ("Pinturas Turmero", 10.2278, -67.4756),                 # Turmero
        ("Ferreteria Cagua Total", 10.1922, -67.4497),           # Cagua
        ("Pinturas y Ferreteria San Rafael", 10.0678, -69.3467), # Barquisimeto
        ("Ferreteria Cabudare Hardware", 10.0611, -69.2444),     # Cabudare
        ("Ferrecentro Maracaibo", 10.6427, -71.6125),            # Maracaibo
        ("Colorventas del Zulia", 10.6650, -71.6300),            # Maracaibo (Bella Vista)
        ("Ferreteria El Constructor", 7.7669, -72.2250),         # San Cristobal
        ("Pinturas Andina", 8.5921, -71.1442),                   # Merida
        ("Ferreteria Barinas Hardware", 8.6226, -70.2075),       # Barinas
        ("Ferreteria Portuguesa", 9.5597, -69.2000),             # Acarigua
        ("Colorhogar Falcon", 11.6822, -70.2144),                # Punto Fijo
        ("Pinturas Coro", 11.4045, -69.6816),                    # Coro
        ("Pinturas Trujillo", 9.3178, -70.6061),                 # Valera
        ("Distribuidora Guarico Pinturas", 9.9112, -67.3378),    # San Juan de los Morros
        ("Ferreteria Oriente", 10.2137, -64.6335),               # Puerto La Cruz
        ("Ferreteria Anzoategui", 9.4372, -64.4692),             # Anaco
        ("Distribuidora Guayana Color", 8.3533, -62.6474),       # Ciudad Guayana
        ("Ferreteria Maturin Total", 9.7450, -63.1783),          # Maturin
    ]

    productos_ppg_glidden = [
        "PPG Break-Through!",
        "PPG Timeless",
        "PPG Copper Armor",
        "PPG Manor Hall",
        "Glidden Premium",
        "Glidden Diamond",
        "Glidden Duo",
        "Glidden Ceiling Paint",
    ]

    categorias_generales = ["Activo", "En Riesgo", "Inactivo", "Nuevo"]
    pesos_categoria = [0.55, 0.20, 0.10, 0.15]
    tipos_de_lead = ["Referido", "Inbound", "Outbound", "Feria/Evento"]
    managers = ["Carla Ríos", "Jesús Pérez", "María Gómez", "Andrés Ledezma"]

    filas = []
    for nombre, lat, lon in clientes:
        # Visitas del mes: sesgadas hacia valores bajos-medios para que
        # existan varias cuentas en alerta (< MIN_VISITAS_ALERTA), tal
        # como pasaria en un portafolio real.
        visitas = int(np.random.choice(
            [0, 1, 2, 3, 4, 5, 6, 7, 8],
            p=[0.06, 0.14, 0.18, 0.18, 0.14, 0.12, 0.08, 0.06, 0.04],
        ))
        monto_total = round(float(np.random.uniform(1_500, 45_000)), 2)
        producto_principal = random.choice(productos_ppg_glidden)
        categoria_general = np.random.choice(categorias_generales, p=pesos_categoria)

        # Coherencia minima: si el cliente esta "Inactivo", que casi no
        # tenga visitas ni facturacion reciente.
        if categoria_general == "Inactivo":
            visitas = min(visitas, 1)
            monto_total = round(monto_total * 0.15, 2)

        filas.append({
            "Cliente": nombre,
            "Tipo de Lead": random.choice(tipos_de_lead),
            "Manager": random.choice(managers),
            "Latitud": lat + np.random.uniform(-0.01, 0.01),  # pequeno jitter
            "Longitud": lon + np.random.uniform(-0.01, 0.01),
            "Monto Total": monto_total,
            "Visitas": visitas,
            "Categoría General": categoria_general,
            "Producto Principal": producto_principal,  # columna extra informativa (no requerida)
        })

    return pd.DataFrame(filas)


# ==========================================================================
# 3. CARGA DE DATOS CON FALLBACK AUTOMATICO A MOCK
# ==========================================================================

def cargar_datos() -> tuple[pd.DataFrame, bool]:
    """Intenta leer el CSV publico del Google Sheet en vivo. Si falla
    (Sheet dejo de ser publico, sin internet, URL invalida, etc.), regresa
    datos simulados para que el dashboard nunca se rompa.

    Retorna (dataframe, es_datos_en_vivo).
    """
    if FORZAR_MOCK_DATA:
        return generar_datos_mock(), False

    try:
        df = cargar_datos_desde_google_sheets_csv()
        if df is not None and not df.empty:
            return df, True
    except Exception:
        pass

    return generar_datos_mock(), False


def normalizar_encabezados(df: pd.DataFrame) -> pd.DataFrame:
    """Renombra encabezados del Sheet a los nombres canonicos esperados,
    sin importar mayusculas/minusculas, espacios extra o guiones bajos.

    Ej: "CLIENTE", " cliente ", "Monto", "Monto_Total" -> "Cliente" /
    "Monto Total". Si dos columnas del Sheet apuntan al mismo nombre
    canonico (caso raro), se conserva solo la primera para evitar columnas
    duplicadas.
    """
    renombres = {}
    for columna in df.columns:
        clave = re.sub(r"[\s_]+", " ", str(columna).strip().lower())
        canonico = COLUMN_ALIASES.get(clave)
        if canonico:
            renombres[columna] = canonico

    df = df.rename(columns=renombres)
    df = df.loc[:, ~df.columns.duplicated()]
    return df


def normalizar_datos(df: pd.DataFrame) -> pd.DataFrame:
    """Valida columnas minimas y castea tipos, sin importar si los datos
    vinieron de Google Sheets (texto plano) o del mock (ya tipados).

    Estructura real esperada: Cliente, Tipo de Lead, Manager,
    Categoria General, Zona, Monto Total. Latitud/Longitud, Visitas y
    Categoria General NO son obligatorias: si faltan, se resuelven
    automaticamente para que la app nunca colapse con data real incompleta.
    """
    df = normalizar_encabezados(df)

    faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
    if faltantes:
        st.error(
            "El Google Sheet no tiene las columnas esperadas: "
            f"{', '.join(faltantes)}. Verifica los encabezados de tu hoja."
        )
        st.stop()

    df = df.copy()
    # "Monto Total" puede venir formateado como moneda de texto (ej.
    # "$234,995.32") directamente del Google Sheet real: hay que limpiar
    # simbolos de moneda y separadores de miles antes de convertir a numero.
    monto_texto = df["Monto Total"].astype(str).str.replace(r"[^0-9.\-]", "", regex=True)
    df["Monto Total"] = pd.to_numeric(monto_texto, errors="coerce").fillna(0)

    if "Categoría General" in df.columns:
        df["Categoría General"] = df["Categoría General"].fillna("Sin dato")
    else:
        df["Categoría General"] = "Sin dato"

    for columna_opcional in ("Tipo de Lead", "Manager"):
        if columna_opcional in df.columns:
            df[columna_opcional] = df[columna_opcional].fillna("Sin dato")

    # Limpieza a prueba de balas ANTES de cualquier filtro encadenado o
    # cruce con la sábana de facturación: Manager y Categoría General se
    # normalizan a texto sin espacios y en minúsculas (Zona se normaliza
    # más abajo, después de usarse para geocodificar, para no alterar la
    # búsqueda en Nominatim). La UI sigue mostrando estos valores
    # capitalizados vía `titular_para_mostrar` / `format_func`.
    df = normalizar_columnas_clave(df, ["Manager", "Categoría General"])

    # Resuelve coordenadas (directas o geocodificadas via "Zona", con
    # fallback al centro de Caracas) y garantiza la columna "Visitas".
    df = asegurar_coordenadas(df)
    df = normalizar_columnas_clave(df, ["Zona"])
    df = asegurar_visitas(df)
    df["Visitas"] = pd.to_numeric(df["Visitas"], errors="coerce").fillna(0).astype(int)

    # Margen de ganancia: 20% lineal y ESTRICTO sobre la columna "Monto
    # Total" de cada cliente (regla de negocio pedida explicitamente).
    df["Margen Estimado"] = df["Monto Total"] * MARGEN_PORCENTAJE
    return df


# ==========================================================================
# 4. MAPA (FOLIUM + MARKERCLUSTER) CON ALERTA DE VISITAS
# ==========================================================================

# Colores neon usados tanto en los pines como en las tarjetas KPI, para que
# el mapa y el resto del dashboard compartan el mismo lenguaje visual.
COLOR_ALERTA = "#ff1744"          # rojo neon: < MIN_VISITAS_ALERTA visitas
COLOR_ALTA_FACTURACION = "#00e5ff"  # cian neon: cuenta en el top 25% de facturacion
COLOR_NORMAL = "#7dd3fc"          # azul suave: cuenta dentro de rango normal

# CSS inyectado DENTRO del documento HTML del mapa de Folium (no del
# dashboard). El mapa se renderiza en su propio iframe, por lo que el CSS
# de PREMIUM_CSS no le llega: hay que oscurecer aqui los popups por defecto
# de Leaflet (blancos) y darle un toque neon a los globos de MarkerCluster.
MAPA_DARK_CSS = """
<style>
.leaflet-popup-content-wrapper {
    background: #131826 !important;
    color: #f5f7fa !important;
    border-radius: 12px !important;
    box-shadow: 0 8px 24px rgba(0,0,0,0.45) !important;
}
.leaflet-popup-tip { background: #131826 !important; }
.leaflet-container a.leaflet-popup-close-button { color: #9aa4b2 !important; }

.marker-cluster-small { background-color: rgba(0,229,255,0.30) !important; }
.marker-cluster-small div { background-color: rgba(0,229,255,0.65) !important; color:#0b0f19 !important; font-weight:700; }
.marker-cluster-medium { background-color: rgba(125,211,252,0.28) !important; }
.marker-cluster-medium div { background-color: rgba(125,211,252,0.60) !important; color:#0b0f19 !important; font-weight:700; }
.marker-cluster-large { background-color: rgba(255,23,68,0.28) !important; }
.marker-cluster-large div { background-color: rgba(255,23,68,0.55) !important; color:#0b0f19 !important; font-weight:700; }
</style>
"""


def color_por_cliente(visitas: int, monto_total: float, umbral_alta_facturacion: float) -> str:
    """Logica de alerta SIN CAMBIOS: si el cliente tiene menos de
    MIN_VISITAS_ALERTA visitas en el mes, su pin es rojo (prioridad maxima).
    Encima de eso se agrega una capa puramente visual: las cuentas de alta
    facturacion (top 25% del grupo filtrado) que SI estan al dia con sus
    visitas resaltan en cian neon en vez del azul normal.
    """
    if visitas < MIN_VISITAS_ALERTA:
        return COLOR_ALERTA
    if monto_total >= umbral_alta_facturacion:
        return COLOR_ALTA_FACTURACION
    return COLOR_NORMAL


def construir_mapa(df: pd.DataFrame, vista_general: bool = False) -> folium.Map:
    # Sin filtros activos (portafolio completo): vista general fija sobre
    # Caracas/Venezuela con zoom bajo, en vez de centrar y ajustar el zoom
    # al promedio real de TODOS los puntos, que es mas lento de renderizar
    # (y de poco valor visual) con cientos de marcadores agrupados. Con
    # filtros activos, si se centra y hace zoom sobre el subconjunto
    # filtrado para que el usuario vea el detalle de su seleccion.
    if vista_general or df.empty:
        centro_lat, centro_lon = CARACAS_LAT, CARACAS_LON
        zoom_inicial = 7
    else:
        centro_lat = df["Latitud"].mean()
        centro_lon = df["Longitud"].mean()
        zoom_inicial = 11

    # Tiles oscuros de CartoDB para que el mapa haga juego con el tema
    # premium dark mode del resto del dashboard.
    mapa = folium.Map(location=[centro_lat, centro_lon], zoom_start=zoom_inicial, tiles="cartodbdark_matter")
    mapa.get_root().header.add_child(folium.Element(MAPA_DARK_CSS))

    cluster = MarkerCluster(name="Clientes").add_to(mapa)

    tiene_producto = "Producto Principal" in df.columns
    tiene_tipo_lead = "Tipo de Lead" in df.columns
    tiene_manager = "Manager" in df.columns
    # Umbral de "alta facturacion" = top 25% del grupo actualmente filtrado.
    umbral_alta_facturacion = df["Monto Total"].quantile(0.75) if len(df) else 0

    for _, fila in df.iterrows():
        color_hex = color_por_cliente(fila["Visitas"], fila["Monto Total"], umbral_alta_facturacion)
        en_alerta = fila["Visitas"] < MIN_VISITAS_ALERTA
        alerta_html = " ⚠️ BAJA COBERTURA" if en_alerta else ""
        producto_html = (
            f"Producto principal: <b>{fila['Producto Principal']}</b><br>" if tiene_producto else ""
        )
        tipo_lead_html = (
            f"Tipo de lead: <b style=\"color:#f5f7fa;\">{fila['Tipo de Lead']}</b><br>" if tiene_tipo_lead else ""
        )
        manager_html = (
            f"Manager: <b style=\"color:#f5f7fa;\">{titular_para_mostrar(fila['Manager'])}</b><br>"
            if tiene_manager else ""
        )

        popup_html = f"""
            <div style="font-family:'Inter',sans-serif; min-width:200px; padding:2px 4px;">
                <div style="font-size:14px; font-weight:700; margin-bottom:6px;">
                    {fila['Cliente']}<span style="color:{COLOR_ALERTA};">{alerta_html}</span>
                </div>
                <div style="font-size:12px; color:#9aa4b2; line-height:1.6;">
                    Categoría: <b style="color:#f5f7fa;">{titular_para_mostrar(fila['Categoría General'])}</b><br>
                    {tipo_lead_html}
                    {manager_html}
                    Visitas del mes: <b style="color:#f5f7fa;">{fila['Visitas']}</b><br>
                    Monto Total: <b style="color:{COLOR_ALTA_FACTURACION};">${fila['Monto Total']:,.2f}</b><br>
                    {producto_html}
                    Margen estimado ({int(MARGEN_PORCENTAJE * 100)}%):
                    <b style="color:#39ff14;">${fila['Margen Estimado']:,.2f}</b>
                </div>
            </div>
        """

        # Pin custom (DivIcon) en vez del pin por defecto de Leaflet: un
        # punto circular con "glow" neon que combina con el tema oscuro.
        icono_html = f"""
            <div style="
                width: 16px; height: 16px; border-radius: 50%;
                background: {color_hex};
                box-shadow: 0 0 6px 2px {color_hex}, 0 0 16px 5px {color_hex}66;
                border: 2px solid rgba(255,255,255,0.85);
            "></div>
        """

        folium.Marker(
            location=[fila["Latitud"], fila["Longitud"]],
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=fila["Cliente"],
            icon=folium.DivIcon(html=icono_html, icon_size=(16, 16), icon_anchor=(8, 8)),
        ).add_to(cluster)

    return mapa


@st.fragment
def render_mapa_y_tabla(df_mapa: pd.DataFrame, vista_general: bool) -> None:
    """Renderiza el mapa (folium_static, sin canal bidireccional con
    Python) y la tabla de detalle dentro de un @st.fragment.

    Al ser un fragmento, esta funcion se aisla del resto del script: si en
    el futuro se agrega algun widget interactivo aqui dentro, su rerun
    quedaria contenido a este bloque en vez de volver a ejecutar TODO el
    dashboard (Odoo, geocodificacion, metricas financieras, etc.), que es
    la causa tipica de la "pantalla opaca" al interactuar con el mapa o la
    tabla.
    """
    st.markdown('<div class="section-title">Mapa de cobertura</div>', unsafe_allow_html=True)
    if df_mapa.empty:
        st.warning("No hay cuentas que coincidan con los filtros seleccionados.")
    else:
        mapa = construir_mapa(df_mapa, vista_general=vista_general)
        # width=None -> el mapa ocupa el 100% del ancho del contenedor. Se
        # usa folium_static (en vez de st_folium) porque renderiza el mapa
        # como HTML estatico dentro de un iframe: no reenvia eventos de
        # pan/zoom de vuelta a Python, asi que interactuar con el mapa
        # nunca dispara un rerun de Streamlit.
        folium_static(mapa, width=None, height=620)

    st.markdown('<div class="section-title">Detalle de cuentas</div>', unsafe_allow_html=True)
    st.dataframe(
        con_columnas_tituladas(
            df_mapa.sort_values("Cliente").reset_index(drop=True),
            ["Manager", "Zona", "Categoría General"],
        ),
        width="stretch",
    )


# ==========================================================================
# 4B. VELOCIMETRO DE META (PACE TO GOAL) - FACTURACION DIARIA
# ==========================================================================

def calcular_pace_to_goal(df_facturas: pd.DataFrame, meta_mensual: float) -> dict:
    """Calcula el ritmo de facturacion del mes en curso contra una meta
    mensual configurable ("Pace to Goal"), asumiendo un mes de
    DIAS_ASUMIDOS_MES (30) dias parejos:

      1) Suma lo facturado en lo que va del mes actual (segun "Fecha de
         Emision").
      2) Calcula el ritmo diario REAL (facturado / dias transcurridos) y lo
         compara contra el ritmo diario NECESARIO (meta_mensual / 30) para
         saber, en cualquier momento del mes, si se esta por encima o por
         debajo del ritmo que hace falta.
      3) Proyecta el ritmo diario real a los 30 dias asumidos del mes y lo
         expresa como % de la meta mensual, listo para pintar en el
         velocimetro.

    Si la columna de fechas no existe o no trae ninguna fecha valida
    (Sheet vacio, sin conexion, etc.), devuelve el diccionario en cero con
    "tiene_fechas": False para que el velocimetro se dibuje en estado
    "sin datos" en vez de tronar.
    """
    resultado = {
        "facturado_mes_actual": 0.0,
        "proyeccion_fin_mes": 0.0,
        "porcentaje_meta": 0.0,
        "dias_transcurridos": 0,
        "dias_totales_mes": DIAS_ASUMIDOS_MES,
        "ritmo_diario_actual": 0.0,
        "ritmo_diario_necesario": 0.0,
        "diferencia_ritmo_diario": 0.0,
        "por_encima_del_ritmo": False,
        "tiene_fechas": False,
    }

    if "Fecha de Emisión" not in df_facturas.columns:
        return resultado

    fechas_validas = df_facturas["Fecha de Emisión"].dropna()
    if fechas_validas.empty:
        return resultado

    hoy = datetime.now()
    # Supuesto pedido explicitamente: mes de 30 dias parejos (no el numero
    # real de dias del mes calendario).
    dias_totales_mes = DIAS_ASUMIDOS_MES
    dias_transcurridos = min(hoy.day, dias_totales_mes)

    facturas_mes_actual = df_facturas[
        (df_facturas["Fecha de Emisión"].dt.year == hoy.year)
        & (df_facturas["Fecha de Emisión"].dt.month == hoy.month)
    ]
    facturado_mes_actual = float(facturas_mes_actual["Monto Facturado"].sum())

    ritmo_diario_actual = facturado_mes_actual / dias_transcurridos if dias_transcurridos else 0.0
    ritmo_diario_necesario = meta_mensual / dias_totales_mes if dias_totales_mes else 0.0
    proyeccion_fin_mes = ritmo_diario_actual * dias_totales_mes
    porcentaje_meta = (proyeccion_fin_mes / meta_mensual * 100) if meta_mensual else 0.0

    resultado.update({
        "facturado_mes_actual": facturado_mes_actual,
        "proyeccion_fin_mes": proyeccion_fin_mes,
        "porcentaje_meta": porcentaje_meta,
        "dias_transcurridos": dias_transcurridos,
        "dias_totales_mes": dias_totales_mes,
        "ritmo_diario_actual": ritmo_diario_actual,
        "ritmo_diario_necesario": ritmo_diario_necesario,
        "diferencia_ritmo_diario": ritmo_diario_actual - ritmo_diario_necesario,
        "por_encima_del_ritmo": ritmo_diario_actual >= ritmo_diario_necesario,
        "tiene_fechas": True,
    })
    return resultado


def render_velocimetro(pace: dict, meta_mensual: float) -> str:
    """Devuelve el HTML/SVG (glassmorphism, mismo estilo que la clase CSS
    ".kpi-card") de un velocimetro semicircular "Pace to Goal": la aguja marca que tan
    cerca esta la PROYECCION de fin de mes (segun el ritmo actual de
    facturacion) de la meta mensual configurada en el sidebar.

    Mismo lenguaje de color neon que el resto del dashboard: rojo si vamos
    muy por debajo del ritmo necesario (<70%), amarillo en zona de riesgo
    (70-100%) y verde si se proyecta cumplir o superar la meta (>=100%).
    """
    if not pace["tiene_fechas"]:
        return """
        <div class="kpi-card" style="text-align:center;">
            <div class="kpi-label">🎯 Velocímetro de Meta (Pace to Goal)</div>
            <div class="kpi-value" style="font-size:15px; color:#9aa4b2;">
                Sin datos de fecha disponibles todavía.
            </div>
        </div>
        """

    porcentaje = pace["porcentaje_meta"]
    porcentaje_clamp = max(0.0, min(porcentaje, 130.0))
    angulo_rad = np.radians(-90 + (porcentaje_clamp / 130.0) * 180.0)
    aguja_x = 100 + 65 * np.cos(angulo_rad)
    aguja_y = 100 + 65 * np.sin(angulo_rad)
    color = "#ff1744" if porcentaje < 70 else "#f5c542" if porcentaje < 100 else "#39ff14"
    arco_lleno = (porcentaje_clamp / 130.0) * 251.2

    return f"""
    <div class="kpi-card" style="--accent:{color}; text-align:center;">
        <div class="kpi-label">🎯 Velocímetro de Meta (Pace to Goal)</div>
        <svg viewBox="0 0 200 115" width="100%" style="max-width:260px; margin:4px auto 0; display:block;">
            <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none"
                  stroke="rgba(255,255,255,0.08)" stroke-width="14" stroke-linecap="round"/>
            <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none"
                  stroke="{color}" stroke-width="14" stroke-linecap="round"
                  stroke-dasharray="{arco_lleno:.1f} 251.2"/>
            <line x1="100" y1="100" x2="{aguja_x:.1f}" y2="{aguja_y:.1f}"
                  stroke="#f5f7fa" stroke-width="3" stroke-linecap="round"/>
            <circle cx="100" cy="100" r="6" fill="#f5f7fa"/>
        </svg>
        <div class="kpi-value" style="font-size:26px;">{porcentaje:,.0f}%</div>
        <div style="font-size:12px; color:#9aa4b2; margin-top:4px;">
            Día {pace['dias_transcurridos']}/{pace['dias_totales_mes']} ·
            Proyección: <b style="color:{color};">${pace['proyeccion_fin_mes']:,.0f}</b>
            &nbsp;/&nbsp; Meta: ${meta_mensual:,.0f}
        </div>
    </div>
    """


# ==========================================================================
# 4C. MOTOR DE METRICAS FINANCIERAS AVANZADAS (RECENCIA, FRECUENCIA,
#     CICLO DE PAGO Y MOROSIDAD) - FACTURACION DIARIA
# ==========================================================================

def calcular_metricas_financieras(df_facturas: pd.DataFrame) -> dict:
    """Calcula el set de KPIs financieros de alto nivel pedido para el
    panel, todos derivados de la sabana de facturas (transaccional):

      - Recencia: dias transcurridos desde la ULTIMA compra de cada
        cliente (agrupando por "Nombre"), con alerta si supera
        RECENCIA_ALERTA_DIAS.
      - Frecuencia: tiempo promedio entre compras consecutivas de un mismo
        cliente (solo tiene sentido para clientes con 2+ facturas),
        promediado despues entre todos los clientes recurrentes.
      - Ciclo de pago: promedio de la columna "Días que tardaron en pagar"
        que ya trae la sabana real.
      - Morosidad: suma de "Saldo Pendiente" de las facturas cuyo
        "Vencimiento" ya paso (deuda vencida), mas el conteo de esas
        facturas.

    Si faltan las columnas minimas ("Nombre"/"Fecha de Emisión") o no hay
    ninguna fecha valida, devuelve el diccionario en cero/"tiene_datos":
    False para que el panel se pinte en estado "sin datos" en vez de
    tronar (misma filosofia de resiliencia que el resto del dashboard).
    """
    resultado = {
        "recencia_df": pd.DataFrame(columns=["Cliente", "Última Compra", "Días sin Comprar"]),
        "clientes_en_riesgo": 0,
        "frecuencia_promedio_dias": None,
        "ciclo_pago_promedio_dias": None,
        "deuda_vencida_total": 0.0,
        "facturas_vencidas": 0,
        "tiene_datos": False,
    }

    if not {"Nombre", "Fecha de Emisión"}.issubset(df_facturas.columns):
        return resultado

    df_validas = df_facturas.dropna(subset=["Fecha de Emisión", "Nombre"])
    if df_validas.empty:
        return resultado

    hoy = pd.Timestamp(datetime.now().date())

    # --- Recencia: dias desde la ultima compra, por cliente ---
    ultima_compra = df_validas.groupby("Nombre")["Fecha de Emisión"].max().reset_index()
    ultima_compra.columns = ["Cliente", "Última Compra"]
    ultima_compra["Días sin Comprar"] = (hoy - ultima_compra["Última Compra"]).dt.days
    ultima_compra = ultima_compra.sort_values("Días sin Comprar", ascending=False)
    clientes_en_riesgo = int((ultima_compra["Días sin Comprar"] > RECENCIA_ALERTA_DIAS).sum())

    # --- Frecuencia: tiempo promedio entre compras consecutivas, por cliente ---
    def _intervalo_promedio(fechas: pd.Series):
        fechas_ordenadas = fechas.sort_values()
        if len(fechas_ordenadas) < 2:
            return np.nan
        return fechas_ordenadas.diff().dt.days.dropna().mean()

    intervalos_por_cliente = df_validas.groupby("Nombre")["Fecha de Emisión"].apply(_intervalo_promedio)
    frecuencia_promedio_dias = (
        float(intervalos_por_cliente.dropna().mean()) if intervalos_por_cliente.notna().any() else None
    )

    # --- Ciclo de pago promedio (columna ya provista por la sabana real) ---
    ciclo_pago_promedio_dias = None
    if "Días que tardaron en pagar" in df_facturas.columns:
        dias_pago = df_facturas["Días que tardaron en pagar"].dropna()
        if not dias_pago.empty:
            ciclo_pago_promedio_dias = float(dias_pago.mean())

    # --- Morosidad: saldo pendiente de facturas con vencimiento ya pasado ---
    deuda_vencida_total = 0.0
    facturas_vencidas = 0
    if {"Vencimiento", "Saldo Pendiente"}.issubset(df_facturas.columns):
        vencidas = df_facturas[
            df_facturas["Vencimiento"].notna()
            & (df_facturas["Vencimiento"] < hoy)
            & (df_facturas["Saldo Pendiente"] > 0)
        ]
        deuda_vencida_total = float(vencidas["Saldo Pendiente"].sum())
        facturas_vencidas = int(len(vencidas))

    resultado.update({
        "recencia_df": ultima_compra,
        "clientes_en_riesgo": clientes_en_riesgo,
        "frecuencia_promedio_dias": frecuencia_promedio_dias,
        "ciclo_pago_promedio_dias": ciclo_pago_promedio_dias,
        "deuda_vencida_total": deuda_vencida_total,
        "facturas_vencidas": facturas_vencidas,
        "tiene_datos": True,
    })
    return resultado


def resaltar_recencia(fila: pd.Series) -> list:
    """Estilo por fila (pandas Styler) para la tabla de detalle de
    Recencia: resalta en rojo neon semitransparente las cuentas que
    superan RECENCIA_ALERTA_DIAS sin comprar (alerta visual pedida).
    """
    if fila["Días sin Comprar"] > RECENCIA_ALERTA_DIAS:
        return ["background-color: rgba(255,23,68,0.28); color:#ffd9df;"] * len(fila)
    return [""] * len(fila)


# ==========================================================================
# 5. INTERFAZ PRINCIPAL
# ==========================================================================

df_crudo, datos_en_vivo = cargar_datos()
df = normalizar_datos(df_crudo)

df_facturas, facturacion_en_vivo = cargar_facturacion_segura()

st.title("🎨 Dashboard de Cobertura - Portafolio PPG / Glidden")

if datos_en_vivo:
    st.success("Conectado a Google Sheets en vivo.", icon="✅")
else:
    st.info(
        "Mostrando datos SIMULADOS (mock) de 29 cuentas. No se pudo leer "
        "el CSV publico del Google Sheet (revisa que siga compartido como "
        "'Cualquier usuario con el enlace' y que la URL/gid en app.py sea "
        "correcta).",
        icon="ℹ️",
    )

if not facturacion_en_vivo:
    st.warning(
        "No se pudo leer la sábana de facturación diaria (revisa que el "
        "Sheet siga público y que la URL en app.py sea correcta). La "
        "sección de facturación quedará vacía.",
        icon="⚠️",
    )

# --------------------------------------------------------------------------
# PANEL LATERAL: LIVE REFRESH + FILTROS ENCADENADOS + MARGEN DE GANANCIA
# --------------------------------------------------------------------------
with st.sidebar:
    # ----------------------------------------------------------------
    # SISTEMA DE ACTUALIZACION EN VIVO: limpia toda la cache de datos
    # (Google Sheets de clientes + sabana de facturacion) y fuerza un
    # rerun inmediato para traer la data mas fresca, sin esperar a que
    # expire el TTL de 60s de las funciones de lectura de CSV.
    # ----------------------------------------------------------------
    if st.button("🔄 Actualizar Datos", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Los datos se refrescan solos cada 60s. Usa el botón para forzar una lectura inmediata.")

    st.divider()

    # ------------------------------------------------------------------
    # FILTROS ENCADENADOS DENTRO DE UN st.form(): mover/tocar un multiselect
    # NO dispara un rerun de la app (eso es lo que congelaba la pantalla
    # con un efecto de "pantalla opaca" en cada click). El dashboard
    # completo (KPIs, mapa, tabla, facturación) solo recalcula y renderiza
    # cuando se presiona el botón físico "✅ Aplicar Filtros".
    # ------------------------------------------------------------------
    with st.form("filtros_form"):
        st.markdown('<div class="sidebar-title">🎯 Filtros</div>', unsafe_allow_html=True)

        # NOTA UX: las opciones de estos multiselect son los valores YA
        # normalizados (minusculas, sin espacios) por
        # normalizar_columnas_clave, que es lo que garantiza que el cruce
        # con la sábana de Facturación nunca falle por "Caracas " vs
        # "CARACAS". `format_func` se encarga de mostrarlos capitalizados
        # (.title()) sin alterar el valor real usado para filtrar.
        categorias_disponibles = sorted(df["Categoría General"].unique().tolist())
        categorias_seleccionadas = st.multiselect(
            "Categoría general del cliente",
            categorias_disponibles,
            default=categorias_disponibles,
            format_func=titular_para_mostrar,
        )

        # Filtros ENCADENADOS: cada multiselect recalcula sus opciones
        # sobre la última selección APLICADA (la del submit anterior), ya
        # que el form no dispara reruns con cada click intermedio.
        df_para_opciones = df[df["Categoría General"].isin(categorias_seleccionadas)]

        vendedores_disponibles = (
            sorted(df_para_opciones["Manager"].dropna().unique().tolist())
            if "Manager" in df_para_opciones.columns else []
        )
        vendedores_seleccionados = st.multiselect(
            "Vendedor / Manager",
            vendedores_disponibles,
            default=vendedores_disponibles,
            format_func=titular_para_mostrar,
        )
        if "Manager" in df_para_opciones.columns:
            df_para_opciones = df_para_opciones[df_para_opciones["Manager"].isin(vendedores_seleccionados)]

        canales_disponibles = (
            sorted(df_para_opciones["Tipo de Lead"].dropna().unique().tolist())
            if "Tipo de Lead" in df_para_opciones.columns else []
        )
        canales_seleccionados = st.multiselect(
            "Canal / Tipo de Lead", canales_disponibles, default=canales_disponibles
        )
        if "Tipo de Lead" in df_para_opciones.columns:
            df_para_opciones = df_para_opciones[df_para_opciones["Tipo de Lead"].isin(canales_seleccionados)]

        zonas_disponibles = (
            sorted(df_para_opciones["Zona"].dropna().unique().tolist())
            if "Zona" in df_para_opciones.columns else []
        )
        zonas_seleccionadas = st.multiselect(
            "Zona",
            zonas_disponibles,
            default=zonas_disponibles,
            format_func=titular_para_mostrar,
        )

        st.divider()
        solo_cuentas_clave = st.toggle(
            "🌟 Solo Cuentas Clave",
            value=False,
            help="Filtro de acceso rápido: aísla en el mapa y las tablas solo a " + ", ".join(CUENTAS_CLAVE) + ".",
        )

        st.divider()
        st.markdown('<div class="sidebar-title">📅 Periodo de Facturación</div>', unsafe_allow_html=True)
        st.caption("Filtra la sábana de facturas. Se encadena con Vendedor y Cuentas Clave.")

        # La sabana de facturas se filtra por Manager (Vendedor) y por
        # Cuentas Clave ANTES de calcular las opciones de Mes/Año, para que
        # el filtro de periodo quede encadenado con el resto de la
        # selección (misma lógica de encadenado que los filtros de arriba).
        df_facturas_para_opciones = df_facturas.copy()
        if "Manager" in df_facturas_para_opciones.columns:
            df_facturas_para_opciones = df_facturas_para_opciones[
                df_facturas_para_opciones["Manager"].isin(vendedores_seleccionados)
            ]
        if solo_cuentas_clave and "Nombre" in df_facturas_para_opciones.columns:
            df_facturas_para_opciones = df_facturas_para_opciones[
                df_facturas_para_opciones["Nombre"].apply(es_cuenta_clave)
            ]

        if "Fecha de Emisión" in df_facturas_para_opciones.columns:
            periodos_ordenados = (
                df_facturas_para_opciones["Fecha de Emisión"]
                .dropna()
                .dt.to_period("M")
                .drop_duplicates()
                .sort_values(ascending=False)
            )
            opciones_periodo = [str(p) for p in periodos_ordenados]
        else:
            opciones_periodo = []

        periodos_seleccionados = st.multiselect(
            "Mes / Año (AAAA-MM)", opciones_periodo, default=opciones_periodo
        )

        st.form_submit_button("✅ Aplicar Filtros", type="primary", use_container_width=True)

    # ------------------------------------------------------------------
    # A partir de aquí se aplica la cadena de filtros DE VERDAD. Esto solo
    # se ejecuta cuando el script completo corre (primera carga, "Aplicar
    # Filtros" o "Actualizar Datos"): un click dentro del form de arriba no
    # llega hasta acá, por lo que el mapa/tabla/KPIs nunca se recalculan a
    # medio camino de que el usuario arme su selección.
    # ------------------------------------------------------------------
    df_filtrado = df[df["Categoría General"].isin(categorias_seleccionadas)]
    if "Manager" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["Manager"].isin(vendedores_seleccionados)]
    if "Tipo de Lead" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["Tipo de Lead"].isin(canales_seleccionados)]
    if "Zona" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["Zona"].isin(zonas_seleccionadas)]
    if solo_cuentas_clave:
        df_filtrado = df_filtrado[df_filtrado["Cliente"].apply(es_cuenta_clave)]

    # True si el usuario dejó los filtros en su estado "sin filtrar" (todas
    # las opciones seleccionadas y el toggle de Cuentas Clave apagado): se
    # usa para decidir la vista del mapa (overview de Caracas/Venezuela vs.
    # zoom al subconjunto filtrado).
    sin_filtros_activos = (
        set(categorias_seleccionadas) == set(categorias_disponibles)
        and set(vendedores_seleccionados) == set(vendedores_disponibles)
        and set(canales_seleccionados) == set(canales_disponibles)
        and set(zonas_seleccionadas) == set(zonas_disponibles)
        and not solo_cuentas_clave
    )

    df_facturas_filtrado = df_facturas.copy()
    if "Manager" in df_facturas_filtrado.columns:
        df_facturas_filtrado = df_facturas_filtrado[df_facturas_filtrado["Manager"].isin(vendedores_seleccionados)]
    if solo_cuentas_clave and "Nombre" in df_facturas_filtrado.columns:
        df_facturas_filtrado = df_facturas_filtrado[df_facturas_filtrado["Nombre"].apply(es_cuenta_clave)]
    if "Fecha de Emisión" in df_facturas_filtrado.columns:
        df_facturas_filtrado = df_facturas_filtrado[
            df_facturas_filtrado["Fecha de Emisión"].dt.to_period("M").astype(str).isin(periodos_seleccionados)
        ]

    st.divider()
    st.markdown('<div class="sidebar-title">📊 Margen de Ganancia Estimado</div>', unsafe_allow_html=True)
    st.caption(f"Calculado como {int(MARGEN_PORCENTAJE * 100)}% lineal y estricto sobre Monto Total.")

    facturacion_total = df_filtrado["Monto Total"].sum()
    margen_total = df_filtrado["Margen Estimado"].sum()

    st.metric(
        "💰 Facturación Total",
        f"${facturacion_total:,.2f}",
        help="Suma del Monto Total de las cuentas que cumplen los filtros aplicados (Categoría, Vendedor, Canal, Zona y Cuentas Clave).",
    )
    st.metric(
        "📈 Margen Estimado (20%)",
        f"${margen_total:,.2f}",
        help=f"{int(MARGEN_PORCENTAJE * 100)}% lineal y estricto sobre la Facturación Total filtrada. Cálculo fijo, no editable.",
    )

    st.divider()
    st.markdown(
        f'<div class="sidebar-title">🚨 Cuentas con &lt; {MIN_VISITAS_ALERTA} visitas</div>',
        unsafe_allow_html=True,
    )
    alertas = df_filtrado[df_filtrado["Visitas"] < MIN_VISITAS_ALERTA]
    st.caption(f"{len(alertas)} cuenta(s) requieren atención inmediata.")
    if not alertas.empty:
        st.dataframe(
            con_columnas_tituladas(
                alertas[["Cliente", "Visitas", "Categoría General"]].sort_values("Visitas"),
                ["Categoría General"],
            ),
            hide_index=True,
            width="stretch",
        )

    st.divider()
    st.markdown('<div class="sidebar-title">🎯 Meta Mensual de Facturación</div>', unsafe_allow_html=True)
    st.caption("Ajusta la meta para recalcular el Pace to Goal (ritmo diario necesario, asumiendo 30 días de mes).")
    meta_mensual_facturacion = st.number_input(
        "Meta Mensual ($)",
        min_value=0.0,
        value=50_000.0,
        step=1_000.0,
        format="%.2f",
    )

# --------------------------------------------------------------------------
# CUERPO PRINCIPAL: DOS PESTAÑAS - "Radar de Clientes" (todo lo que ya
# existia: metricas financieras, KPIs, mapa y facturacion diaria) y
# "Pipeline de Odoo" (integracion en vivo via XML-RPC, seccion 1C).
# --------------------------------------------------------------------------
tab_radar, tab_pipeline_odoo = st.tabs(["📡 Radar de Clientes", "🧩 Pipeline de Odoo"])

with tab_radar:
    # ----------------------------------------------------------------------
    # MOTOR DE METRICAS FINANCIERAS AVANZADAS (EN LA PARTE SUPERIOR, tal
    # como se pidio) - Recencia, Frecuencia, Ciclo de Pago y Morosidad,
    # calculados sobre la sabana de facturacion ya filtrada por Vendedor /
    # Cuentas Clave / Mes-Año en el sidebar.
    # ----------------------------------------------------------------------
    st.markdown('<div class="section-title">📊 Métricas Financieras Avanzadas</div>', unsafe_allow_html=True)

    metricas_financieras = calcular_metricas_financieras(df_facturas_filtrado)

    if not metricas_financieras["tiene_datos"]:
        st.info("No hay datos de facturación suficientes para calcular estas métricas.", icon="ℹ️")
    else:
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        with col_m1:
            st.metric(
                f"⏰ Clientes en Riesgo (Recencia > {RECENCIA_ALERTA_DIAS}d)",
                f"{metricas_financieras['clientes_en_riesgo']}",
                help=f"Clientes que llevan más de {RECENCIA_ALERTA_DIAS} días sin facturar, según la sábana de facturación filtrada.",
            )
        with col_m2:
            frecuencia = metricas_financieras["frecuencia_promedio_dias"]
            valor_frecuencia = f"{frecuencia:.0f} días" if frecuencia is not None else "Sin dato"
            st.metric(
                "🔁 Frecuencia Promedio de Compra",
                valor_frecuencia,
                help="Promedio de días entre compras consecutivas de un mismo cliente (solo cuentas con 2 o más facturas).",
            )
        with col_m3:
            ciclo_pago = metricas_financieras["ciclo_pago_promedio_dias"]
            valor_ciclo = f"{ciclo_pago:.1f} días" if ciclo_pago is not None else "Sin dato"
            st.metric(
                "💳 Ciclo de Pago Promedio",
                valor_ciclo,
                help="Promedio de días que tarda un cliente en pagar una factura, desde la emisión hasta el pago.",
            )
        with col_m4:
            st.metric(
                "🔴 Deuda Vencida (Morosidad)",
                f"${metricas_financieras['deuda_vencida_total']:,.0f}",
                help="Saldo pendiente de cobro de facturas cuya fecha de Vencimiento ya pasó.",
            )
        st.caption(
            f"{metricas_financieras['facturas_vencidas']} factura(s) vencida(s) con saldo pendiente "
            "componen la morosidad mostrada arriba."
        )

        with st.expander("Ver detalle de Recencia por cliente"):
            recencia_df = metricas_financieras["recencia_df"].copy()
            recencia_df["Última Compra"] = recencia_df["Última Compra"].dt.strftime("%d/%m/%Y")
            st.dataframe(
                recencia_df.style.apply(resaltar_recencia, axis=1),
                hide_index=True,
                width="stretch",
            )

    # ----------------------------------------------------------------------
    # FILA DE KPIs + MAPA DOMINANTE + TABLA
    # ----------------------------------------------------------------------
    st.markdown('<div class="section-title">Indicadores clave</div>', unsafe_allow_html=True)
    col_a, col_b, col_c, col_d, col_e = st.columns(5)
    with col_a:
        st.metric(
            "🧾 Total de Cuentas",
            f"{len(df_filtrado)}",
            help="Número de clientes que cumplen los filtros activos del panel lateral.",
        )
    with col_b:
        st.metric(
            "💰 Facturación Total",
            f"${facturacion_total:,.0f}",
            help="Suma del Monto Total facturado por las cuentas filtradas.",
        )
    with col_c:
        st.metric(
            "📈 Margen Estimado",
            f"${margen_total:,.0f}",
            help=f"{int(MARGEN_PORCENTAJE * 100)}% lineal y estricto sobre la Facturación Total filtrada (regla de negocio fija).",
        )
    with col_d:
        visitas_prom = f"{df_filtrado['Visitas'].mean():.1f}" if len(df_filtrado) else "0"
        st.metric(
            "🗓️ Visitas Promedio",
            visitas_prom,
            help="Promedio de visitas del mes por cuenta, dentro del grupo filtrado.",
        )
    with col_e:
        # Renombrado de "Cuentas en Alerta" a "Riesgo de Fuga": se usa la
        # métrica de Recencia (facturación) en vez del conteo por Visitas,
        # para que el número mostrado coincida exactamente con lo que dice
        # el tooltip y no haya ambigüedad entre "poca cobertura" y "riesgo
        # real".
        st.metric(
            "🚨 Riesgo de Fuga",
            f"{metricas_financieras['clientes_en_riesgo']}",
            help=f"Clientes que llevan más de {RECENCIA_ALERTA_DIAS} días sin facturar.",
        )

    # Mapa + tabla viven en un @st.fragment (render_mapa_y_tabla, definida
    # en la sección 4): un componente pesado de construir/renderizar, ahora
    # aislado del resto del rerun del dashboard.
    render_mapa_y_tabla(df_filtrado, sin_filtros_activos)

    # ----------------------------------------------------------------------
    # FACTURACION DIARIA (SEGUNDA BASE DE DATOS TRANSACCIONAL)
    # ----------------------------------------------------------------------
    st.markdown('<div class="section-title">💵 Facturación Diaria (Sábana Transaccional)</div>', unsafe_allow_html=True)

    if df_facturas_filtrado.empty:
        st.warning("No hay facturas que coincidan con los filtros seleccionados (Vendedor / Cuentas Clave / Mes-Año).")
    else:
        hoy = datetime.now()
        facturacion_historica_total = df_facturas_filtrado["Monto Facturado"].sum()
        saldo_pendiente_total = df_facturas_filtrado["Saldo Pendiente"].sum()
        facturas_emitidas = len(df_facturas_filtrado)

        fechas_validas = df_facturas_filtrado["Fecha de Emisión"].dropna()
        if not fechas_validas.empty:
            facturacion_ultimos_30_dias = df_facturas_filtrado.loc[
                df_facturas_filtrado["Fecha de Emisión"] >= (hoy - pd.Timedelta(days=30)),
                "Monto Facturado",
            ].sum()
        else:
            facturacion_ultimos_30_dias = 0.0

        # El velocimetro de Pace to Goal se calcula sobre la sabana COMPLETA
        # (sin el filtro de Mes/Año, que no tendria sentido para un "ritmo
        # del mes en curso"), pero SI respeta Vendedor/Cuentas Clave para
        # que la meta se pueda evaluar por vendedor o por cuenta
        # estrategica.
        df_facturas_para_pace = df_facturas.copy()
        if "Manager" in df_facturas_para_pace.columns:
            df_facturas_para_pace = df_facturas_para_pace[df_facturas_para_pace["Manager"].isin(vendedores_seleccionados)]
        if solo_cuentas_clave and "Nombre" in df_facturas_para_pace.columns:
            df_facturas_para_pace = df_facturas_para_pace[df_facturas_para_pace["Nombre"].apply(es_cuenta_clave)]
        pace = calcular_pace_to_goal(df_facturas_para_pace, meta_mensual_facturacion)

        col_f1, col_f2, col_f3, col_f4 = st.columns(4)
        with col_f1:
            st.metric(
                "💵 Facturación Histórica",
                f"${facturacion_historica_total:,.0f}",
                help="Suma histórica de todas las facturas dentro del periodo y filtros seleccionados (Vendedor / Cuentas Clave / Mes-Año).",
            )
        with col_f2:
            st.metric(
                "📅 Facturación Últimos 30 Días",
                f"${facturacion_ultimos_30_dias:,.0f}",
                help="Monto facturado en los últimos 30 días corridos desde hoy, dentro del mismo filtrado.",
            )
        with col_f3:
            st.metric(
                "🧾 Facturas Emitidas",
                f"{facturas_emitidas:,}",
                help="Cantidad de facturas emitidas dentro del periodo y filtros seleccionados.",
            )
        with col_f4:
            st.metric(
                "⏳ Saldo Pendiente (Cobranza)",
                f"${saldo_pendiente_total:,.0f}",
                help="Suma del saldo pendiente de cobro de las facturas filtradas (incluye facturas aún no vencidas).",
            )

        col_gauge, col_tendencia = st.columns([1, 2])
        with col_gauge:
            st.markdown(render_velocimetro(pace, meta_mensual_facturacion), unsafe_allow_html=True)
            if pace["tiene_fechas"]:
                st.metric(
                    "🚦 Ritmo Diario vs. Necesario",
                    f"${pace['ritmo_diario_actual']:,.0f}/día",
                    delta=f"{pace['diferencia_ritmo_diario']:+,.0f} $/día vs. meta",
                    delta_color="normal",
                    help=(
                        f"Ritmo diario NECESARIO para llegar a la Meta Mensual (${meta_mensual_facturacion:,.0f}), "
                        f"asumiendo {DIAS_ASUMIDOS_MES} días de mes: ${pace['ritmo_diario_necesario']:,.0f}/día. "
                        "Verde = por encima del ritmo necesario, rojo = por debajo."
                    ),
                )
        with col_tendencia:
            if not fechas_validas.empty:
                tendencia_mensual = (
                    df_facturas_filtrado.dropna(subset=["Fecha de Emisión"])
                    .assign(Mes=lambda d: d["Fecha de Emisión"].dt.to_period("M").dt.to_timestamp())
                    .groupby("Mes")["Monto Facturado"]
                    .sum()
                    .tail(12)
                )
                st.markdown(
                    '<div class="kpi-label" style="margin-bottom:8px;">📈 Facturación mensual (últimos 12 meses)</div>',
                    unsafe_allow_html=True,
                )
                st.area_chart(tendencia_mensual, height=300)
            else:
                st.info("No hay fechas válidas en la sábana para construir la tendencia mensual.")

        with st.expander("Ver facturas más recientes"):
            columnas_detalle = [
                c for c in ["Fecha de Emisión", "Factura", "Nombre", "Manager", "Monto Facturado", "Saldo Pendiente"]
                if c in df_facturas_filtrado.columns
            ]
            st.dataframe(
                con_columnas_tituladas(
                    df_facturas_filtrado.dropna(subset=["Fecha de Emisión"])
                    .sort_values("Fecha de Emisión", ascending=False)
                    .head(50)[columnas_detalle],
                    ["Manager"],
                ),
                hide_index=True,
                width="stretch",
            )

# --------------------------------------------------------------------------
# CUERPO PRINCIPAL: PESTAÑA "PIPELINE DE ODOO" (conexion XML-RPC en vivo)
# --------------------------------------------------------------------------
with tab_pipeline_odoo:
    st.markdown('<div class="section-title">🧩 Pipeline de Ventas (Odoo CRM)</div>', unsafe_allow_html=True)
    st.caption(
        "Conexión directa a Odoo vía XML-RPC (modelo crm.lead). Configura tus credenciales en "
        ".streamlit/secrets.toml bajo la sección [odoo] (ver secrets.toml.example)."
    )

    df_pipeline_odoo, odoo_conectado, mensaje_odoo = obtener_pipeline_odoo()

    if not odoo_conectado:
        # Conexion fallida (sin credenciales, credenciales invalidas,
        # timeout, instancia caida, etc.): mensaje amigable en vez de
        # romper la app. El resto del dashboard sigue funcionando normal.
        st.warning(f"⚠️ No se pudo conectar con Odoo: {mensaje_odoo}", icon="⚠️")
        st.info(
            "El Radar de Clientes sigue funcionando con normalidad en la otra pestaña. "
            "Revisa la sección [odoo] de .streamlit/secrets.toml (url, db, username, password) "
            "y que la instancia sea alcanzable desde este servidor.",
            icon="ℹ️",
        )
    elif df_pipeline_odoo.empty:
        st.info(f"✅ {mensaje_odoo}", icon="✅")
    else:
        st.success("✅ Conectado a Odoo en vivo.", icon="✅")

        col_o1, col_o2, col_o3 = st.columns(3)
        with col_o1:
            st.metric(
                "🧩 Oportunidades Abiertas",
                f"{len(df_pipeline_odoo)}",
                help="Cantidad de oportunidades activas (type='opportunity') en el pipeline de Odoo.",
            )
        with col_o2:
            monto_esperado_total = df_pipeline_odoo["Monto Esperado"].sum()
            st.metric(
                "💰 Monto Esperado Total",
                f"${monto_esperado_total:,.0f}",
                help="Suma de 'Monto Esperado' (expected_revenue) de todas las oportunidades abiertas del pipeline.",
            )
        with col_o3:
            probabilidad_prom = df_pipeline_odoo["Probabilidad (%)"].mean()
            st.metric(
                "🎯 Probabilidad Promedio",
                f"{probabilidad_prom:.0f}%",
                help="Promedio de la probabilidad de cierre de las oportunidades abiertas, según Odoo.",
            )

        st.markdown('<div class="section-title">Detalle del Pipeline</div>', unsafe_allow_html=True)
        st.dataframe(
            df_pipeline_odoo.sort_values("Monto Esperado", ascending=False).reset_index(drop=True),
            hide_index=True,
            width="stretch",
        )
