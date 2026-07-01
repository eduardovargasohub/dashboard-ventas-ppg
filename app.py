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

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

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

/* Tarjetas KPI (glassmorphism). El color de acento se pasa por variable
   CSS inline (--accent) desde render_kpi_card(). */
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


def render_kpi_card(label: str, value: str, accent: str, icon: str = "") -> str:
    """Devuelve el HTML de una tarjeta KPI premium (glassmorphism) lista
    para pasar a st.markdown(..., unsafe_allow_html=True). `accent` es un
    color hex que colorea el borde izquierdo de la tarjeta.
    """
    return f"""
    <div class="kpi-card" style="--accent:{accent};">
        <div class="kpi-icon">{icon}</div>
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
    </div>
    """

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
MARGEN_PORCENTAJE = 0.20

# Ponla en True si quieres forzar el uso de datos simulados aunque ya
# tengas credenciales configuradas (util para hacer demos o pruebas).
FORZAR_MOCK_DATA = False

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


@st.cache_data(show_spinner="Geocodificando zonas (Nominatim/OpenStreetMap)...", ttl=None)
def geocodificar_zonas(zonas: tuple[str, ...]) -> dict:
    """Geocodifica un conjunto de zonas UNICAS y devuelve {zona: (lat, lon)}.

    Se cachea con @st.cache_data para que, al recargar el dashboard o
    cambiar un filtro, NO se vuelva a golpear la API de Nominatim por cada
    cliente: solo se geocodifica una vez por zona (mientras la cache viva).
    Si una zona no se puede geocodificar (vacia, sin internet, no
    reconocida por Nominatim, error/timeout), cae a las coordenadas
    centrales de Caracas para que el mapa nunca se rompa.
    """
    # Import local: si `geopy` no estuviera instalado, el resto del
    # dashboard sigue funcionando (mock data / coordenadas ya presentes)
    # en vez de tronar al importar el archivo.
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.geocoders import Nominatim

    geolocator = Nominatim(user_agent="dashboard_ppg_glidden_epa")
    # RateLimiter respeta el limite de 1 request/seg de Nominatim y
    # reintenta automaticamente ante timeouts esporadicos.
    geocode = RateLimiter(
        geolocator.geocode, min_delay_seconds=1, max_retries=2, error_wait_seconds=2
    )

    coordenadas_por_zona = {}
    for zona in zonas:
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


@st.cache_data(ttl=600, show_spinner="Leyendo Google Sheet en vivo...")
def cargar_datos_desde_google_sheets_csv() -> pd.DataFrame:
    """Lee el Google Sheet publico directamente como CSV (sin credenciales).

    Se cachea 10 minutos (ttl=600) para no volver a descargar el CSV en
    cada interaccion del usuario (filtros, clics, etc.). header=1 porque la
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

    # Resuelve coordenadas (directas o geocodificadas via "Zona", con
    # fallback al centro de Caracas) y garantiza la columna "Visitas".
    df = asegurar_coordenadas(df)
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


def construir_mapa(df: pd.DataFrame) -> folium.Map:
    centro_lat = df["Latitud"].mean()
    centro_lon = df["Longitud"].mean()

    # Tiles oscuros de CartoDB para que el mapa haga juego con el tema
    # premium dark mode del resto del dashboard.
    mapa = folium.Map(location=[centro_lat, centro_lon], zoom_start=6, tiles="cartodbdark_matter")
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
            f"Manager: <b style=\"color:#f5f7fa;\">{fila['Manager']}</b><br>" if tiene_manager else ""
        )

        popup_html = f"""
            <div style="font-family:'Inter',sans-serif; min-width:200px; padding:2px 4px;">
                <div style="font-size:14px; font-weight:700; margin-bottom:6px;">
                    {fila['Cliente']}<span style="color:{COLOR_ALERTA};">{alerta_html}</span>
                </div>
                <div style="font-size:12px; color:#9aa4b2; line-height:1.6;">
                    Categoría: <b style="color:#f5f7fa;">{fila['Categoría General']}</b><br>
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


# ==========================================================================
# 5. INTERFAZ PRINCIPAL
# ==========================================================================

df_crudo, datos_en_vivo = cargar_datos()
df = normalizar_datos(df_crudo)

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

# --------------------------------------------------------------------------
# PANEL LATERAL: FILTROS + MARGEN DE GANANCIA
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown('<div class="sidebar-title">🎯 Filtros</div>', unsafe_allow_html=True)
    categorias_disponibles = sorted(df["Categoría General"].unique().tolist())
    categorias_seleccionadas = st.multiselect(
        "Categoría general del cliente", categorias_disponibles, default=categorias_disponibles
    )
    df_filtrado = df[df["Categoría General"].isin(categorias_seleccionadas)]

    st.divider()
    st.markdown('<div class="sidebar-title">📊 Margen de Ganancia Estimado</div>', unsafe_allow_html=True)
    st.caption(f"Calculado como {int(MARGEN_PORCENTAJE * 100)}% lineal y estricto sobre Monto Total.")

    facturacion_total = df_filtrado["Monto Total"].sum()
    margen_total = df_filtrado["Margen Estimado"].sum()

    st.markdown(
        render_kpi_card("Facturación Total", f"${facturacion_total:,.2f}", COLOR_ALTA_FACTURACION, "💰"),
        unsafe_allow_html=True,
    )
    st.markdown(
        render_kpi_card("Margen Estimado (20%)", f"${margen_total:,.2f}", "#39ff14", "📈"),
        unsafe_allow_html=True,
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
            alertas[["Cliente", "Visitas", "Categoría General"]].sort_values("Visitas"),
            hide_index=True,
            width="stretch",
        )

# --------------------------------------------------------------------------
# CUERPO PRINCIPAL: FILA DE KPIs + MAPA DOMINANTE + TABLA
# --------------------------------------------------------------------------
st.markdown('<div class="section-title">Indicadores clave</div>', unsafe_allow_html=True)
col_a, col_b, col_c, col_d, col_e = st.columns(5)
with col_a:
    st.markdown(render_kpi_card("Total de Cuentas", f"{len(df_filtrado)}", COLOR_NORMAL, "🧾"), unsafe_allow_html=True)
with col_b:
    st.markdown(
        render_kpi_card("Facturación Total", f"${facturacion_total:,.0f}", COLOR_ALTA_FACTURACION, "💰"),
        unsafe_allow_html=True,
    )
with col_c:
    st.markdown(render_kpi_card("Margen Estimado", f"${margen_total:,.0f}", "#39ff14", "📈"), unsafe_allow_html=True)
with col_d:
    visitas_prom = f"{df_filtrado['Visitas'].mean():.1f}" if len(df_filtrado) else "0"
    st.markdown(render_kpi_card("Visitas Promedio", visitas_prom, "#a78bfa", "🗓️"), unsafe_allow_html=True)
with col_e:
    st.markdown(render_kpi_card("Cuentas en Alerta", f"{len(alertas)}", COLOR_ALERTA, "🚨"), unsafe_allow_html=True)

st.markdown('<div class="section-title">Mapa de cobertura</div>', unsafe_allow_html=True)
if df_filtrado.empty:
    st.warning("No hay cuentas que coincidan con los filtros seleccionados.")
else:
    mapa = construir_mapa(df_filtrado)
    st_folium(mapa, use_container_width=True, height=620)

st.markdown('<div class="section-title">Detalle de cuentas</div>', unsafe_allow_html=True)
st.dataframe(
    df_filtrado.sort_values("Cliente").reset_index(drop=True),
    width="stretch",
)
