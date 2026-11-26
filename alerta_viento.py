"""
Alerta de viento y ciclones por Telegram, por municipio, para Mexico.

Fuentes de datos:
    - Open-Meteo (principal): pronostico de viento por hora, gratuito, sin API key.
    - OpenWeatherMap (respaldo): se usa solo si Open-Meteo falla para algun municipio.
    - NHC/NOAA: ciclones activos (Atlantico, Caribe y Pacifico Oriental) y su mapa.

Envio de mensajes: Bot de Telegram.

Dos niveles de riesgo de viento:
    - MODERADO (45-59 km/h): se reporta en los horarios de rutina.
    - ALTO (60+ km/h): se avisa de inmediato, a cualquier hora.

Los mensajes se separan por zona (Norte, Centro, Sur) y cada lista va
ordenada de mayor a menor velocidad de viento.

Requisitos:
    pip install requests
"""

import os
import re
import json
import time
import html
from datetime import datetime, timezone, timedelta
from math import radians, sin, cos, sqrt, atan2, isfinite
import requests

# ============ CONFIGURACION ============

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

# API key de OpenWeatherMap, usada SOLO como respaldo si Open-Meteo falla
OWM_API_KEY = os.environ.get('OWM_API_KEY', '')

# Umbral de riesgo MODERADO (aparece en reportes de rutina)
UMBRAL_MODERADO_KMH = 45

# Umbral de riesgo ALTO (avisa de inmediato, a cualquier hora)
UMBRAL_ALTO_KMH = 60
UMBRAL_CRITICO_KMH = 90
INCREMENTO_ALERTA_KMH = 15

# Horas hacia adelante a revisar en el pronostico
HORAS_A_FUTURO = 18
HORAS_LLUVIA = 24
UMBRAL_LLUVIA_MM = 10
UMBRAL_LLUVIA_ALTA_MM = 25
UMBRAL_LLUVIA_CRITICA_MM = 50

# Municipios por lote. Lotes chicos = respuestas mas ligeras = menos timeouts.
TAMANO_LOTE = 10

# Pausa en segundos entre lote y lote, para no saturar el servicio
PAUSA_ENTRE_LOTES = 1.5

# Horas del dia (en UTC) de los reportes de rutina.
# Corresponden a 8:00 AM, 3:00 PM y 9:00 PM hora del centro de Mexico (UTC-6).
HORAS_RUTINA_UTC = {14, 21, 3}

# Diferencia horaria para mostrar las horas en hora del centro de Mexico
DESFASE_HORAS_MEXICO = -6

# Cada cuantas horas se repite el aviso mientras el riesgo siga activo
HORAS_ENTRE_RECORDATORIOS = 3

# Radio (km) para considerar que un estado esta "cerca" de un ciclon
RADIO_CERCANIA_CICLON_KM = 400

# Zonas de trabajo. Edita esta lista si tu equipo agrupa los estados distinto.
# Los nombres de estado deben ir en MAYUSCULAS y sin acentos, igual que en
# tu archivo de municipios.
REGIONES = {
    'NORTE': [
        'BAJA CALIFORNIA', 'BAJA CALIFORNIA SUR', 'SONORA', 'CHIHUAHUA',
        'COAHUILA', 'NUEVO LEON', 'DURANGO', 'SINALOA', 'ZACATECAS',
        'SAN LUIS POTOSI',
    ],
    'CENTRO': [
        'AGUASCALIENTES', 'GUANAJUATO', 'QUERETARO', 'JALISCO', 'COLIMA',
        'MICHOACAN', 'NAYARIT', 'HIDALGO', 'ESTADO DE MEXICO',
        'CIUDAD DE MEXICO', 'MORELOS', 'TAMAULIPAS', 'TLAXCALA',
    ],
    'SUR': [
        'VERACRUZ', 'GUERRERO', 'OAXACA', 'CHIAPAS', 'TABASCO', 'CAMPECHE',
        'YUCATAN', 'QUINTANA ROO', 'PUEBLA',
    ],
}
ORDEN_REGIONES = ['NORTE', 'CENTRO', 'SUR', 'OTRAS']

# Archivos
ARCHIVO_MUNICIPIOS_COORDS = 'municipios_coords.json'
ARCHIVO_ESTADO = 'estado_alertas.json'

# =======================================

REGION_DE_ESTADO = {
    estado: region for region, estados in REGIONES.items() for estado in estados
}

DIAS = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']
MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
         'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
PALABRAS_MENORES = {'de', 'del', 'la', 'las', 'los', 'el', 'y'}


# ---------------------------------------------------------------
# Utilidades de texto y formato
# ---------------------------------------------------------------

def esc(texto):
    """Escapa texto para usarlo dentro de mensajes HTML de Telegram."""
    return html.escape(str(texto), quote=False)


ACENTOS = {
    'mexico': 'México', 'michoacan': 'Michoacán', 'queretaro': 'Querétaro',
    'potosi': 'Potosí', 'yucatan': 'Yucatán', 'leon': 'León', 'merida': 'Mérida',
    'cordoba': 'Córdoba', 'jimenez': 'Jiménez', 'juarez': 'Juárez',
    'garcia': 'García', 'gonzalez': 'González', 'zuniga': 'Zúñiga',
    'mascarenas': 'Mascareñas', 'lopez': 'López', 'nicolas': 'Nicolás',
    'sanchez': 'Sánchez', 'rio': 'Río', 'alvarez': 'Álvarez', 'guzman': 'Guzmán',
    'obregon': 'Obregón', 'tecoman': 'Tecomán', 'cuautitlan': 'Cuautitlán',
    'ixtlahuacan': 'Ixtlahuacán', 'apatzingan': 'Apatzingán', 'cardenas': 'Cárdenas',
    'gutierrez': 'Gutiérrez', 'ursulo': 'Úrsulo', 'galvan': 'Galván',
    'mazatlan': 'Mazatlán', 'culiacan': 'Culiacán', 'cancun': 'Cancún',
    'torreon': 'Torreón', 'acatlan': 'Acatlán', 'lazaro': 'Lázaro',
    'barragan': 'Barragán', 'murguia': 'Murguía', 'ocotlan': 'Ocotlán',
    'jaltipan': 'Jáltipan', 'maria': 'María', 'tecolotlan': 'Tecolotlán',
    'huehuetan': 'Huehuetán', 'tamuin': 'Tamuín', 'tanquian': 'Tanquián',
    'tepoztlan': 'Tepoztlán', 'cuitlahuac': 'Cuitláhuac', 'cunduacan': 'Cunduacán',
    'zapotlan': 'Zapotlán', 'tecamac': 'Tecámac', 'tultitlan': 'Tultitlán',
    'nezahualcoyotl': 'Nezahualcóyotl', 'chimalhuacan': 'Chimalhuacán',
    'minatitlan': 'Minatitlán', 'martinez': 'Martínez', 'perez': 'Pérez',
}


def nombre_amigable(texto):
    """'SAN LUIS RIO COLORADO' -> 'San Luis Río Colorado'."""
    palabras = str(texto).lower().split()
    resultado = []
    for i, palabra in enumerate(palabras):
        if palabra in ACENTOS:
            resultado.append(ACENTOS[palabra])
        elif i > 0 and palabra in PALABRAS_MENORES:
            resultado.append(palabra)
        else:
            resultado.append(palabra[:1].upper() + palabra[1:])
    return ' '.join(resultado)


def a_hora_mexico(dt_utc):
    return dt_utc + timedelta(hours=DESFASE_HORAS_MEXICO)


def formatear_fecha_local(dt_local):
    hora12 = dt_local.hour % 12 or 12
    sufijo = 'am' if dt_local.hour < 12 else 'pm'
    return (f"{DIAS[dt_local.weekday()]} {dt_local.day} {MESES[dt_local.month - 1]}, "
            f"{hora12}:{dt_local.minute:02d} {sufijo}")


def hora_local_texto(hora_utc_str):
    """Convierte '2026-09-22T21:00' (UTC) a 'mar 22 sep, 3:00 pm' (hora del centro)."""
    if not hora_utc_str:
        return 'hora no disponible'
    try:
        texto = str(hora_utc_str).replace('T', ' ')[:16]
        dt = datetime.strptime(texto, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
        return formatear_fecha_local(a_hora_mexico(dt))
    except ValueError:
        return str(hora_utc_str)


def region_de(estado):
    return REGION_DE_ESTADO.get(estado, 'OTRAS')


def dividir_en_mensajes(encabezado, encabezado_cont, bloques, limite=3500):
    """Agrupa bloques en mensajes que no excedan el limite de Telegram."""
    mensajes = []
    actual = encabezado
    bloques_en_actual = 0
    for bloque in bloques:
        if bloques_en_actual > 0 and len(actual) + 2 + len(bloque) > limite:
            mensajes.append(actual)
            actual = encabezado_cont
            bloques_en_actual = 0
        actual = actual + '\n\n' + bloque
        bloques_en_actual += 1
    mensajes.append(actual)
    return mensajes


# ---------------------------------------------------------------
# Datos de viento
# ---------------------------------------------------------------

def cargar_municipios():
    with open(ARCHIVO_MUNICIPIOS_COORDS, 'r', encoding='utf-8') as f:
        return json.load(f)


def dato_viento_valido(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and isfinite(valor) and valor >= 0


def extraer_peor_viento(dato_municipio):
    """No confundir datos faltantes con viento cero."""
    horas = dato_municipio.get('hourly', {})
    tiempos = horas.get('time', [])
    velocidades = horas.get('wind_speed_10m', [])
    rafagas = horas.get('wind_gusts_10m', [])
    if not (len(tiempos) == len(velocidades) == len(rafagas) == HORAS_A_FUTURO):
        raise ValueError('Pronostico horario incompleto')
    if any(not t or not dato_viento_valido(v) or not dato_viento_valido(r)
           for t, v, r in zip(tiempos, velocidades, rafagas)):
        raise ValueError('Pronostico con valores ausentes o invalidos')
    i = max(range(len(tiempos)), key=lambda j: max(velocidades[j], rafagas[j]))
    return velocidades[i], rafagas[i], tiempos[i]

def extraer_meteorologia(dato):
    """Ventanas UTC: lluvia de horas completas anteriores y siguientes.
    La precipitación de Open-Meteo corresponde a la hora que termina en time.
    """
    h = dato.get('hourly', {})
    tiempos = h.get('time', [])
    n = 2 * HORAS_LLUVIA + 1
    if len(tiempos) != n:
        raise ValueError('Ventana meteorologica incompleta')
    try:
        fechas = [datetime.fromisoformat(t) for t in tiempos]
        if any(b - a != timedelta(hours=1) for a, b in zip(fechas, fechas[1:])):
            raise ValueError('Ventana horaria discontinua')
    except (ValueError, TypeError):
        raise ValueError('Fechas meteorologicas invalidas')
    i = HORAS_LLUVIA
    viento = {'hourly': {k: h.get(k, [])[i:i + HORAS_A_FUTURO]
                        for k in ('time', 'wind_speed_10m', 'wind_gusts_10m')}}
    try:
        v = extraer_peor_viento(viento)
    except ValueError:
        v = None
    p = h.get('precipitation', [])
    def acumulado(valores):
        if len(valores) != HORAS_LLUVIA or not all(dato_viento_valido(x) for x in valores):
            return None
        return round(sum(valores), 3)
    return {'viento': v, 'lluvia_previa': acumulado(p[1:i+1]),
            'lluvia_futura': acumulado(p[i+1:i+1+HORAS_LLUVIA])}


def consultar_open_meteo(lote, intentos=1, espera=1):
    """
    Consulta varios municipios en UNA sola llamada a Open-Meteo.
    'lote' es una lista de tuplas (clave, info). Lanza excepcion si falla.
    """
    lats = ','.join(str(info['lat']) for _, info in lote)
    lons = ','.join(str(info['lon']) for _, info in lote)

    url = 'https://api.open-meteo.com/v1/forecast'
    params = {
        'latitude': lats,
        'longitude': lons,
        'hourly': 'wind_speed_10m,wind_gusts_10m,precipitation',
        'wind_speed_unit': 'kmh',
        'past_hours': HORAS_LLUVIA,
        'forecast_hours': HORAS_LLUVIA + 1,
        'precipitation_unit': 'mm',
        'timezone': 'UTC',
    }

    ultimo_error = None
    for intento in range(intentos):
        try:
            respuesta = requests.get(url, params=params, timeout=20)
            respuesta.raise_for_status()
            datos = respuesta.json()
            break
        except Exception as error:
            ultimo_error = error
            print(f'    Intento {intento + 1} ({len(lote)} municipios) fallo: {error}')
            time.sleep(espera)
    else:
        raise ultimo_error

    if isinstance(datos, dict):
        datos = [datos]

    if not isinstance(datos, list) or len(datos) != len(lote):
        raise ValueError('Respuesta de lote incompleta')
    resultados = {}
    for (clave, _info), dato_municipio in zip(lote, datos):
        resultados[clave] = extraer_meteorologia(dato_municipio)
        if resultados[clave]['viento'] is None:
            resultados[clave]['viento'] = consultar_openweather_respaldo(clave, _info)

    return resultados


def consultar_openweather_respaldo(clave, info):
    """
    Respaldo individual con OpenWeatherMap si Open-Meteo no pudo dar datos.
    Regresa (velocidad, rafaga, hora) o None si tampoco se pudo.
    """
    if not OWM_API_KEY:
        return None

    url = 'https://api.openweathermap.org/data/2.5/forecast'
    params = {
        'lat': info['lat'],
        'lon': info['lon'],
        'appid': OWM_API_KEY,
        'units': 'metric',
    }
    try:
        respuesta = requests.get(url, params=params, timeout=20)
        respuesta.raise_for_status()
        datos = respuesta.json()
    except Exception as error:
        print(f'    Respaldo OWM tambien fallo para {clave}: {type(error).__name__}')
        return None

    bloques = max(1, HORAS_A_FUTURO // 3)
    registros = datos.get('list', [])[:bloques]
    if len(registros) != bloques:
        return None
    muestras = []
    for bloque in registros:
        viento = bloque.get('wind', {})
        v, r, hora = viento.get('speed'), viento.get('gust'), bloque.get('dt_txt')
        if not hora or not dato_viento_valido(v) or not dato_viento_valido(r):
            return None
        muestras.append((v * 3.6, r * 3.6, hora))
    return max(muestras, key=lambda muestra: max(muestra[0], muestra[1]))

def procesar_lote_adaptativo(lote):
    """
    Intenta el lote completo. Si falla, lo parte a la mitad y reintenta cada
    mitad por separado, y asi sucesivamente hasta municipios individuales.
    Si un municipio individual sigue fallando, intenta el respaldo con
    OpenWeatherMap. Solo si ambos fallan queda registrado como sin datos.
    """
    try:
        return consultar_open_meteo(lote)
    except Exception:
        if len(lote) == 1:
            clave, info = lote[0]
            print(f'  {clave}: Open-Meteo fallo, probando respaldo OpenWeatherMap...')
            respaldo = consultar_openweather_respaldo(clave, info)
            if respaldo is not None:
                print(f'  {clave}: recuperado con respaldo OWM')
                return {clave: {'viento': respaldo, 'lluvia_previa': None, 'lluvia_futura': None}}
            return {}

        print(f'  Lote de {len(lote)} fallo, dividiendo en mitades mas chicas...')
        mitad = len(lote) // 2
        resultados = {}
        resultados.update(procesar_lote_adaptativo(lote[:mitad]))
        time.sleep(1)
        resultados.update(procesar_lote_adaptativo(lote[mitad:]))
        return resultados


# ---------------------------------------------------------------
# Ciclones tropicales
# ---------------------------------------------------------------

def categoria_saffir_simpson(intensidad_kt, clasificacion):
    """
    Categoria Saffir-Simpson a partir de la velocidad en NUDOS (knots), que es
    la unidad que entrega el feed del NHC.
    Cat 1: 64-82 kt | Cat 2: 83-95 | Cat 3: 96-112 | Cat 4: 113-136 | Cat 5: 137+
    """
    if clasificacion != 'HU':
        return None  # solo aplica a huracanes; TS/TD no tienen categoria
    if intensidad_kt >= 137:
        return 5
    if intensidad_kt >= 113:
        return 4
    if intensidad_kt >= 96:
        return 3
    if intensidad_kt >= 83:
        return 2
    if intensidad_kt >= 64:
        return 1
    return None


def parsear_coordenada(valor):
    """Convierte formatos como '18.3N' o '105.2W' a numero decimal con signo."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip()
    try:
        if texto.endswith('N') or texto.endswith('E'):
            return float(texto[:-1])
        if texto.endswith('S') or texto.endswith('W'):
            return -float(texto[:-1])
        return float(texto)
    except ValueError:
        return None


def distancia_km(lat1, lon1, lat2, lon2):
    """Distancia aproximada entre dos coordenadas (formula de Haversine)."""
    radio_tierra = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return radio_tierra * 2 * atan2(sqrt(a), sqrt(1 - a))


def estados_cercanos_a_ciclon(lat_ciclon, lon_ciclon, municipios, radio_km=RADIO_CERCANIA_CICLON_KM):
    """
    Aproximacion de que estados podrian estar en la zona de influencia del
    ciclon, por CERCANIA a su posicion actual (no es la trayectoria oficial
    pronosticada). Regresa lista de (estado, distancia_km_mas_cercana).
    """
    if lat_ciclon is None or lon_ciclon is None:
        return []

    distancia_por_estado = {}
    for info in municipios.values():
        d = distancia_km(lat_ciclon, lon_ciclon, info['lat'], info['lon'])
        estado = info['estado']
        if estado not in distancia_por_estado or d < distancia_por_estado[estado]:
            distancia_por_estado[estado] = d

    cercanos = [(estado, d) for estado, d in distancia_por_estado.items() if d <= radio_km]
    cercanos.sort(key=lambda x: x[1])
    return cercanos


def obtener_ciclones_activos(municipios=None):
    """Ciclones activos en Atlantico (Golfo y Caribe) y Pacifico Oriental."""
    url = 'https://www.nhc.noaa.gov/CurrentStorms.json'
    try:
        respuesta = requests.get(url, timeout=20)
        respuesta.raise_for_status()
        datos = respuesta.json()
    except Exception as error:
        print(f'Error consultando NHC: {type(error).__name__}')
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get('activeStorms'), list):
        return None
    if any(not isinstance(t, dict) or not isinstance(t.get('id'), str) or not t['id'] for t in datos['activeStorms']):
        return None

    ciclones = []
    for tormenta in datos['activeStorms']:
        storm_id = tormenta.get('id', '').upper()
        if not (storm_id.startswith('AL') or storm_id.startswith('EP')):
            continue

        clasificacion = tormenta.get('classification', '')
        # OJO: el feed del NHC entrega 'intensity' en NUDOS (knots), no en mph
        try:
            intensidad_kt = float(tormenta.get('intensity', 0))
        except (TypeError, ValueError):
            intensidad_kt = 0
        intensidad_kmh = round(intensidad_kt * 1.852)

        lat = parsear_coordenada(tormenta.get('latitudeNumeric', tormenta.get('latitude')))
        lon = parsear_coordenada(tormenta.get('longitudeNumeric', tormenta.get('longitude')))

        estados_cercanos = []
        if municipios and lat is not None and lon is not None:
            estados_cercanos = estados_cercanos_a_ciclon(lat, lon, municipios)

        ciclones.append({
            'id': tormenta.get('id', ''),
            'nombre': tormenta.get('name', 'Desconocido'),
            'clasificacion': clasificacion,
            'categoria': categoria_saffir_simpson(intensidad_kt, clasificacion),
            'intensidad_kmh': intensidad_kmh,
            'lat': lat,
            'lon': lon,
            'estados_cercanos': estados_cercanos,
        })
    return ciclones


TIPOS_CICLON = {
    'HU': 'Huracán',
    'TS': 'Tormenta tropical',
    'TD': 'Depresión tropical',
    'STS': 'Tormenta subtropical',
    'STD': 'Depresión subtropical',
}


def tipo_ciclon(ciclon):
    return TIPOS_CICLON.get(ciclon['clasificacion'], ciclon['clasificacion'] or 'Sistema tropical')


def formatear_ciclon(ciclon):
    """Texto (HTML) con todos los datos de un ciclon."""
    lineas = [f"🌀 <b>{esc(tipo_ciclon(ciclon).upper())} {esc(str(ciclon['nombre']).upper())}</b>", '']

    if ciclon['categoria']:
        lineas.append(f"🏷 <b>Categoría {ciclon['categoria']}</b> (escala Saffir-Simpson)")
    lineas.append(f"💨 Vientos máximos sostenidos: <b>{ciclon['intensidad_kmh']} km/h</b>")

    if ciclon['lat'] is not None and ciclon['lon'] is not None:
        ns = 'N' if ciclon['lat'] >= 0 else 'S'
        eo = 'E' if ciclon['lon'] >= 0 else 'O'
        lineas.append(f"📌 Posición actual: {abs(ciclon['lat']):.1f}°{ns}, {abs(ciclon['lon']):.1f}°{eo}")

    if ciclon['estados_cercanos']:
        nombres = ', '.join(nombre_amigable(e) for e, _ in ciclon['estados_cercanos'][:6])
        lineas.append(f"📍 Estados en su zona de cercanía (~{RADIO_CERCANIA_CICLON_KM} km): {esc(nombres)}")
    else:
        lineas.append(f"📍 Ningún estado monitoreado a menos de ~{RADIO_CERCANIA_CICLON_KM} km de su posición actual")

    lineas.append('')
    lineas.append('ℹ️ La cercanía se calcula con su posición actual; no es la trayectoria oficial. '
                  'Consulta nhc.noaa.gov o conagua.gob.mx.')
    return '\n'.join(lineas)


def descargar_imagen_ciclon(storm_id):
    """
    Descarga el mapa oficial del NHC (cono de trayectoria, posicion actual,
    campo de viento y avisos). Regresa los bytes de la imagen o None.
    """
    sid = str(storm_id).upper()
    coincidencia = re.match(r'^(AL|EP)(\d{2})(\d{4})$', sid)
    if not coincidencia:
        return None

    cuenca, numero, anio = coincidencia.groups()
    carpeta = ('AT' if cuenca == 'AL' else 'EP') + numero
    prefijos = [sid]
    if cuenca == 'AL':
        prefijos.append(f'AT{numero}{anio}')

    tipos = [
        '5day_cone_no_line_and_wind',
        '5day_cone_with_line_and_wind',
        '3day_cone_no_line_and_wind',
        '5day_cone_no_line',
    ]

    for prefijo in prefijos:
        for tipo in tipos:
            url = f'https://www.nhc.noaa.gov/storm_graphics/{carpeta}/{prefijo}_{tipo}.png'
            try:
                respuesta = requests.get(url, timeout=20)
            except Exception as error:
                print(f'    No se pudo descargar {url}: {error}')
                continue
            tipo_contenido = respuesta.headers.get('content-type', '')
            if respuesta.status_code == 200 and tipo_contenido.startswith('image'):
                print(f'    Mapa NHC obtenido: {url}')
                return respuesta.content

    print(f'    No se encontro mapa NHC para {storm_id}')
    return None


# ---------------------------------------------------------------
# Estado entre corridas
# ---------------------------------------------------------------

def cargar_estado():
    try:
        with open(ARCHIVO_ESTADO, 'r', encoding='utf-8') as f:
            estado = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        estado = {}

    # Valores por defecto para campos que puedan faltar (compatibilidad
    # con archivos de estado de versiones anteriores del script)
    estado.setdefault('viento_alto_activo', False)
    estado.setdefault('ultima_notificacion_viento', None)
    estado.setdefault('ciclones_activos_ids', [])
    estado.setdefault('ultima_notificacion_ciclon', None)
    estado.setdefault('ultima_rutina_enviada', None)
    estado.setdefault('riesgos_notificados', {})
    estado.setdefault('lluvia_notificada', {})
    estado.setdefault('combinado_notificado', {})
    estado.setdefault('fallas_notificadas', [])
    estado.setdefault('ultima_notificacion_cobertura', None)
    return estado


def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, 'w', encoding='utf-8') as f:
        json.dump(estado, f)


# ---------------------------------------------------------------
# Envio a Telegram
# ---------------------------------------------------------------

def enviar_telegram(texto):
    """Envia un mensaje HTML a Telegram, cortando por lineas si es muy largo."""
    LIMITE = 3900
    partes = []
    actual = ''
    for linea in str(texto).split('\n'):
        if len(actual) + len(linea) + 1 > LIMITE and actual:
            partes.append(actual)
            actual = ''
        actual = actual + ('\n' if actual else '') + linea
    partes.append(actual)

    todo_ok = True
    for parte in partes:
        url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': parte,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }
        try:
            respuesta = requests.post(url, data=payload, timeout=20)
            if respuesta.status_code != 200 or respuesta.json().get('ok') is not True:
                todo_ok = False
                print(f'Telegram: fallo ({respuesta.status_code}) - {respuesta.text[:200]}')
        except Exception as error:
            todo_ok = False
            print(f'Telegram: error de conexion - {type(error).__name__}')

    print('Telegram: mensaje enviado' if todo_ok else 'Telegram: fallo parcial o total')
    return todo_ok


def enviar_foto_telegram(imagen_bytes, texto):
    """Envia una imagen con texto (HTML) como pie de foto."""
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'caption': texto[:1024],
        'parse_mode': 'HTML',
    }
    try:
        respuesta = requests.post(
            url, data=payload,
            files={'photo': ('ciclon.png', imagen_bytes, 'image/png')},
            timeout=40,
        )
        if respuesta.status_code == 200 and respuesta.json().get('ok') is True:
            print('Telegram: imagen enviada')
            return True
        print(f'Telegram: fallo al enviar imagen ({respuesta.status_code}) - {respuesta.text[:200]}')
    except Exception as error:
        print(f'Telegram: error enviando imagen - {type(error).__name__}')
    return False


def enviar_lista(mensajes):
    """Envia varios mensajes en orden, con una pausa corta entre ellos."""
    todo_ok = True
    for mensaje in mensajes:
        if not enviar_telegram(mensaje):
            todo_ok = False
        time.sleep(0.7)
    return todo_ok


def enviar_ubicacion_ciclon(ciclon):
    """
    Respaldo del mapa: manda un pin de ubicacion de Telegram con el nombre del
    ciclon en su posicion actual. No depende de ninguna imagen externa.
    """
    if ciclon['lat'] is None or ciclon['lon'] is None:
        return False

    if ciclon['categoria']:
        titulo = f"{tipo_ciclon(ciclon)} {nombre_amigable(ciclon['nombre'])} · Cat. {ciclon['categoria']}"
    else:
        titulo = f"{tipo_ciclon(ciclon)} {nombre_amigable(ciclon['nombre'])}"
    direccion = f"Posición actual · vientos {ciclon['intensidad_kmh']} km/h"

    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVenue'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'latitude': ciclon['lat'],
        'longitude': ciclon['lon'],
        'title': titulo[:100],
        'address': direccion[:200],
    }
    try:
        respuesta = requests.post(url, data=payload, timeout=20)
        if respuesta.status_code == 200 and respuesta.json().get('ok') is True:
            print('Telegram: ubicacion (pin) enviada')
            return True
        print(f'Telegram: fallo al enviar ubicacion ({respuesta.status_code}) - {respuesta.text[:200]}')
    except Exception as error:
        print(f'Telegram: error enviando ubicacion - {type(error).__name__}')
    return False


def enviar_ciclon(ciclon):
    """
    Envia un ciclon con su posicion visible en un mapa:
      1) Mapa oficial del NHC como imagen (posicion, cono de trayectoria, avisos).
      2) Si la imagen no esta disponible: texto + pin de ubicacion de Telegram.
    """
    texto = formatear_ciclon(ciclon)
    imagen = descargar_imagen_ciclon(ciclon['id'])
    if imagen:
        pie = texto + '\n\n🗺 Mapa oficial NHC: posición actual, cono de trayectoria y avisos.'
        if len(pie) <= 1024 and enviar_foto_telegram(imagen, pie):
            return True
        # Si el texto es muy largo para un pie de foto, va la imagen y luego el texto
        if enviar_foto_telegram(imagen, f"🗺 {esc(tipo_ciclon(ciclon))} {esc(nombre_amigable(ciclon['nombre']))}"):
            return enviar_telegram(texto)

    print(f"Mapa NHC no disponible para {ciclon['nombre']}: se usa el pin de ubicacion.")
    texto_ok = enviar_telegram(texto)
    if not enviar_ubicacion_ciclon(ciclon):
        print(f"AVISO: no se pudo mostrar la posicion de {ciclon['nombre']} (sin imagen ni coordenadas).")
    return texto_ok


# ---------------------------------------------------------------
# Construccion de mensajes de viento
# ---------------------------------------------------------------

def formatear_entrada(entrada):
    icono = {'CRITICO': '🟣', 'ALTO': '🔴', 'MODERADO': '🟡', 'NORMAL': '🟢'}[nivel_viento(entrada['maximo'])]
    return (
        f"{icono} <b>{esc(nombre_amigable(entrada['ciudad']))}</b> · {esc(nombre_amigable(entrada['estado']))}\n"
        f"      💨 Viento {entrada['velocidad']:.0f} km/h · Rachas {entrada['rafaga']:.0f} km/h\n"
        f"      🕒 {esc(hora_local_texto(entrada['hora']))}"
    )


def agrupar_por_zona(entradas):
    """Agrupa por zona y ordena cada zona de mayor a menor viento."""
    por_zona = {}
    for entrada in entradas:
        por_zona.setdefault(region_de(entrada['estado']), []).append(entrada)
    for lista in por_zona.values():
        lista.sort(key=lambda e: e['maximo'], reverse=True)
    return por_zona


def construir_mensajes_viento(entradas, titulo):
    """Un mensaje (o mas, si es muy largo) por zona, de mayor a menor viento."""
    por_zona = agrupar_por_zona(entradas)
    mensajes = []
    for zona in ORDEN_REGIONES:
        lista = por_zona.get(zona, [])
        if not lista:
            continue
        etiqueta = 'OTRAS ZONAS' if zona == 'OTRAS' else f'ZONA {zona}'
        sufijo = 'localidad' if len(lista) == 1 else 'localidades'
        encabezado = f"{titulo}\n📍 <b>{etiqueta}</b> · {len(lista)} {sufijo}"
        bloques = [formatear_entrada(e) for e in lista]
        mensajes += dividir_en_mensajes(encabezado, encabezado + ' (cont.)', bloques)
    return mensajes


def resumen_por_zona(entradas, zonas_sin_datos=()):
    """Lineas de resumen: cuantas localidades en riesgo hay en cada zona."""
    por_zona = agrupar_por_zona(entradas)
    lineas = []
    for zona in ORDEN_REGIONES:
        if zona == 'OTRAS' and not por_zona.get('OTRAS'):
            continue
        lista = por_zona.get(zona, [])
        etiqueta = 'Otras zonas' if zona == 'OTRAS' else zona.capitalize()
        if not lista:
            texto = '⚠️ datos incompletos' if zona in zonas_sin_datos else '✅ sin riesgo previsto'
            lineas.append(f"      {etiqueta}: {texto}")
            continue
        criticos = sum(1 for e in lista if e['maximo'] >= UMBRAL_CRITICO_KMH)
        altos = sum(1 for e in lista if UMBRAL_ALTO_KMH <= e['maximo'] < UMBRAL_CRITICO_KMH)
        moderados = len(lista) - altos - criticos
        partes = []
        if criticos:
            partes.append(f'🟣 {criticos}')
        if altos:
            partes.append(f"🔴 {altos}")
        if moderados:
            partes.append(f"🟡 {moderados}")
        if zona in zonas_sin_datos:
            partes.append('⚠️ datos incompletos')
        lineas.append(f"      {etiqueta}: {' · '.join(partes)}")
    return lineas


# ---------------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------------

def ultima_rutina_programada(ahora):
    """Ultimo horario de rutina vencido en UTC, incluso tras retrasos del cron."""
    hora = ahora.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    candidatos = [
        (hora - timedelta(days=dias)).replace(hour=h)
        for dias in (0, 1)
        for h in HORAS_RUTINA_UTC
    ]
    return max(c for c in candidatos if c <= hora)


def rutina_por_enviar(ahora, ultima_enviada):
    """Envia solo el reporte mas reciente, sin acumular reportes obsoletos."""
    clave = ultima_rutina_programada(ahora).strftime('%Y-%m-%d-%H')
    return clave if not ultima_enviada or ultima_enviada < clave else None


def nivel_viento(maximo):
    if maximo >= UMBRAL_CRITICO_KMH:
        return 'CRITICO'
    if maximo >= UMBRAL_ALTO_KMH:
        return 'ALTO'
    if maximo >= UMBRAL_MODERADO_KMH:
        return 'MODERADO'
    return 'NORMAL'


def recordatorio_vencido(ahora, ultima):
    if not ultima:
        return True
    return (ahora - datetime.fromisoformat(ultima)).total_seconds() >= HORAS_ENTRE_RECORDATORIOS * 3600


def motivo_alerta(entrada, previo, ahora):
    nivel = nivel_viento(entrada['maximo'])
    if not previo:
        return 'NUEVO' if nivel in ('ALTO', 'CRITICO') else None
    anterior = previo['nivel']
    orden = {'NORMAL': 0, 'MODERADO': 1, 'ALTO': 2, 'CRITICO': 3}
    if nivel != anterior:
        if orden[nivel] > orden[anterior] and nivel in ('ALTO', 'CRITICO'):
            return 'ESCALAMIENTO'
        if orden[nivel] < orden[anterior]:
            return 'DESCENSO'
    if nivel in ('ALTO', 'CRITICO'):
        if entrada['maximo'] - previo['maximo'] >= INCREMENTO_ALERTA_KMH:
            return 'AUMENTO'
        if recordatorio_vencido(ahora, previo['ultima']):
            return 'RECORDATORIO'
    return None


def notificar_viento(observaciones, estado, ahora):
    """Estado por clave de localidad; un dato ausente nunca confirma descenso."""
    anteriores = estado.setdefault('riesgos_notificados', {})
    grupos = {}
    for clave, entrada in observaciones.items():
        motivo = motivo_alerta(entrada, anteriores.get(clave), ahora)
        if motivo:
            nivel = nivel_viento(entrada['maximo'])
            grupos.setdefault((motivo, nivel), []).append((clave, entrada))
    prioridad = {'CRITICO': 0, 'ALTO': 1, 'MODERADO': 2, 'NORMAL': 3}
    etiquetas = {
        'NUEVO': 'NUEVO RIESGO', 'ESCALAMIENTO': 'AUMENTO DE NIVEL',
        'AUMENTO': 'INCREMENTO DE VIENTO', 'RECORDATORIO': 'RIESGO PERSISTENTE',
        'DESCENSO': 'DISMINUCIÓN DEL RIESGO PREVISTO',
    }
    for (motivo, nivel), pares in sorted(grupos.items(), key=lambda g: prioridad[g[0][1]]):
        titulo = (f"<b>{etiquetas[motivo]} · {nivel}</b>\n"
                  f"Pronóstico de las próximas {HORAS_A_FUTURO} h (hora del centro de México)")
        if motivo == 'DESCENSO':
            titulo += '\nLa reducción del pronóstico no certifica la seguridad de las estructuras.'
        if enviar_lista(construir_mensajes_viento([e for _, e in pares], titulo)):
            for clave, entrada in pares:
                if nivel == 'NORMAL':
                    anteriores.pop(clave, None)
                else:
                    anteriores[clave] = {'nivel': nivel, 'maximo': entrada['maximo'], 'ultima': ahora.isoformat()}
        else:
            print('AVISO: alerta por localidad no confirmada; se reevaluara en la siguiente consulta.')


def notificar_cobertura(fallas, estado, ahora):
    anteriores = set(estado.get('fallas_notificadas', []))
    actuales = set(fallas)
    if actuales:
        if actuales - anteriores or recordatorio_vencido(ahora, estado.get('ultima_notificacion_cobertura')):
            lista = sorted(actuales)
            extra = f' y {len(lista)-15} más' if len(lista) > 15 else ''
            texto = (f"⚠️ <b>DATOS METEOROLÓGICOS INCOMPLETOS</b>\n"
                     f"Fuentes/localidades sin datos: {len(lista)}.\n"
                     f"{esc(', '.join(lista[:15]))}{extra}.\n"
                     "No se confirma la normalización de las localidades sin pronóstico.")
            if enviar_telegram(texto):
                estado['fallas_notificadas'] = lista
                estado['ultima_notificacion_cobertura'] = ahora.isoformat()
    elif anteriores:
        if enviar_telegram('✅ <b>Cobertura meteorológica restablecida.</b>'):
            estado['fallas_notificadas'] = []
            estado['ultima_notificacion_cobertura'] = None


def nivel_lluvia(mm):
    if mm >= UMBRAL_LLUVIA_CRITICA_MM:
        return 3
    if mm >= UMBRAL_LLUVIA_ALTA_MM:
        return 2
    if mm > UMBRAL_LLUVIA_MM:
        return 1
    return 0


def clasificar_lluvia(entrada):
    """Un periodo disponible puede confirmar riesgo, pero no descartar el otro."""
    valores = [entrada.get('lluvia_previa'), entrada.get('lluvia_futura')]
    validos = [v for v in valores if v is not None]
    nivel = nivel_lluvia(max(validos)) if validos else 0
    return nivel if nivel or len(validos) == 2 else None


def clasificar_combinado(entrada):
    lluvia = clasificar_lluvia(entrada)
    viento = entrada.get('maximo')
    if lluvia is not None and lluvia > 0 and viento is not None and viento >= UMBRAL_ALTO_KMH:
        return max(lluvia, 3 if viento >= UMBRAL_CRITICO_KMH else 2)
    if viento is not None and viento < UMBRAL_ALTO_KMH:
        return 0
    if lluvia == 0:
        return 0
    return None


def formatear_lluvia(entrada):
    def mm(v):
        return 'sin datos' if v is None else f'{v:.1f} mm'
    texto = (f"📍 <b>{esc(nombre_amigable(entrada['ciudad']))}</b> · "
             f"{esc(nombre_amigable(entrada['estado']))}\n"
             f"Últimas 24 h (estimadas): {mm(entrada.get('lluvia_previa'))}\n"
             f"Próximas 24 h: {mm(entrada.get('lluvia_futura'))}")
    if entrada.get('maximo') is not None:
        texto += f"\nViento/rachas máximas próximas 18 h: {entrada['maximo']:.0f} km/h"
    else:
        texto += '\nViento: sin datos'
    return texto


def mensajes_lluvia(pares, titulo):
    regiones = {}
    for clave, entrada in pares:
        regiones.setdefault(region_de(entrada['estado']), []).append(entrada)
    mensajes = []
    for zona in ORDEN_REGIONES:
        if zona in regiones:
            entradas = sorted(regiones[zona], key=lambda e: max(
                (v for v in (e.get('lluvia_previa'), e.get('lluvia_futura')) if v is not None),
                default=0), reverse=True)
            cabecera = f"{titulo}\n📍 <b>ZONA {zona}</b>"
            mensajes.extend(dividir_en_mensajes(cabecera, cabecera + ' (cont.)',
                                                [formatear_lluvia(e) for e in entradas]))
    return mensajes


def notificar_lluvia(observaciones, estado, ahora, combinado=False):
    campo = 'combinado_notificado' if combinado else 'lluvia_notificada'
    anteriores = estado.setdefault(campo, {})
    grupos = {}
    for clave, entrada in observaciones.items():
        nivel = clasificar_combinado(entrada) if combinado else clasificar_lluvia(entrada)
        if nivel is None:
            continue
        previo = anteriores.get(clave)
        anterior = previo['nivel'] if previo else 0
        # Sin ambas ventanas de lluvia no se confirma un descenso de precipitación.
        incompleta = any(entrada.get(k) is None for k in ('lluvia_previa', 'lluvia_futura'))
        if nivel < anterior and incompleta and not (combinado and entrada.get('maximo') is not None and entrada['maximo'] < UMBRAL_ALTO_KMH):
            continue
        if nivel > anterior:
            motivo = 'NUEVO' if anterior == 0 else 'ESCALAMIENTO'
        elif nivel < anterior:
            motivo = 'DISMINUCIÓN'
        elif nivel > 0 and recordatorio_vencido(ahora, previo['ultima']):
            motivo = 'RECORDATORIO'
        else:
            continue
        grupos.setdefault((nivel, motivo), []).append((clave, entrada))
    for (nivel, motivo), pares in sorted(grupos.items(), key=lambda g: -g[0][0]):
        etiqueta = ('NORMAL' if nivel == 0 else ('PREVENTIVO', 'ALTO', 'CRÍTICO')[nivel-1])
        tipo = 'VIENTO Y LLUVIA' if combinado else 'PRECIPITACIÓN'
        titulo = (f"<b>{motivo} · {tipo} · {etiqueta}</b>\n"
                  "Indicador preventivo: revisar drenaje, terreno y cimentación cuando sea seguro. "
                  "No certifica estabilidad estructural.")
        if enviar_lista(mensajes_lluvia(pares, titulo)):
            for clave, _ in pares:
                if nivel:
                    anteriores[clave] = {'nivel': nivel, 'ultima': ahora.isoformat()}
                else:
                    anteriores.pop(clave, None)


def revisar_y_alertar():
    municipios = cargar_municipios()
    items = list(municipios.items())

    riesgo = []       # todas las localidades con viento >= umbral moderado
    sin_datos = []
    zonas_sin_datos = set()
    observaciones = {}
    observaciones_lluvia = {}
    sin_lluvia = []

    lotes = [items[i:i + TAMANO_LOTE] for i in range(0, len(items), TAMANO_LOTE)]

    for numero_lote, lote in enumerate(lotes, start=1):
        print(f'Consultando lote {numero_lote}/{len(lotes)} ({len(lote)} municipios)...')
        resultados_lote = procesar_lote_adaptativo(lote)

        for clave, info in lote:
            meteo = resultados_lote.get(clave, {})
            entrada_lluvia = {
                'ciudad': info['ciudad'], 'estado': info['estado'],
                'lluvia_previa': meteo.get('lluvia_previa'),
                'lluvia_futura': meteo.get('lluvia_futura'),
                'maximo': max(meteo['viento'][:2]) if meteo.get('viento') else None,
            }
            observaciones_lluvia[clave] = entrada_lluvia
            if any(entrada_lluvia[k] is None for k in ('lluvia_previa', 'lluvia_futura')):
                sin_lluvia.append(f"Lluvia: {nombre_amigable(info['ciudad'])} ({nombre_amigable(info['estado'])})")
            if not meteo.get('viento'):
                sin_datos.append(f"{nombre_amigable(info['ciudad'])} ({nombre_amigable(info['estado'])})")
                zonas_sin_datos.add(region_de(info['estado']))
                continue

            velocidad, rafaga, hora = meteo['viento']
            maximo = max(velocidad, rafaga)

            entrada = {
                'ciudad': info['ciudad'], 'estado': info['estado'],
                'velocidad': velocidad, 'rafaga': rafaga,
                'maximo': maximo, 'hora': hora,
            }
            observaciones[clave] = entrada
            if maximo >= UMBRAL_MODERADO_KMH:
                riesgo.append(entrada)

        time.sleep(PAUSA_ENTRE_LOTES)

    riesgo_alto = [e for e in riesgo if e['maximo'] >= UMBRAL_ALTO_KMH]
    riesgo_moderado = [e for e in riesgo if e['maximo'] < UMBRAL_ALTO_KMH]

    revisados = len(items) - len(sin_datos)
    print(f'Municipios revisados: {revisados}/{len(items)}. Sin datos: {len(sin_datos)}')
    print(f'Riesgo alto: {len(riesgo_alto)}. Riesgo moderado: {len(riesgo_moderado)}')

    estados_sin_zona = sorted({e['estado'] for e in riesgo if region_de(e['estado']) == 'OTRAS'})
    if estados_sin_zona:
        print(f'AVISO: estados sin zona asignada en REGIONES: {estados_sin_zona}')

    ciclones = obtener_ciclones_activos(municipios)
    for c in ciclones or []:
        print(f"Ciclon activo: {c['nombre']} ({c['clasificacion']}), {c['intensidad_kmh']} km/h")

    ahora = datetime.now(timezone.utc)
    hora_actual_utc = ahora.hour
    estado = cargar_estado()
    clave_rutina_actual = rutina_por_enviar(ahora, estado.get('ultima_rutina_enviada'))
    rutina_pendiente = clave_rutina_actual is not None

    # Aviso de cobertura, solo si hubo municipios sin revisar
    aviso_cobertura = None
    if sin_datos:
        listado = ', '.join(sin_datos[:15])
        extra = f' y {len(sin_datos) - 15} más' if len(sin_datos) > 15 else ''
        aviso_cobertura = (
            f"⚠️ <b>Cobertura incompleta</b>\n"
            f"No se pudo obtener pronóstico de {len(sin_datos)} de {len(items)} municipios "
            f"({esc(listado)}{esc(extra)}). Esta información puede estar incompleta."
        )

    # 1) CICLONES: diferenciar error de fuente de ausencia real de ciclones.
    if ciclones is not None:
        ids_actuales = sorted(c['id'] for c in ciclones)
        ids_previos = sorted(estado.get('ciclones_activos_ids', []))
        if ciclones and (ids_actuales != ids_previos or recordatorio_vencido(ahora, estado.get('ultima_notificacion_ciclon'))):
            resultados = []
            for ciclon in sorted(ciclones, key=lambda c: c['intensidad_kmh'], reverse=True):
                resultados.append(enviar_ciclon(ciclon))
                time.sleep(0.7)
            if all(resultados):
                estado['ultima_notificacion_ciclon'] = ahora.isoformat()
                estado['ciclones_activos_ids'] = ids_actuales
        elif not ciclones and ids_previos:
            if enviar_telegram('✅ <b>El NHC ya no reporta ciclones activos en las cuencas monitoreadas.</b>'):
                estado['ciclones_activos_ids'] = []
                estado['ultima_notificacion_ciclon'] = ahora.isoformat()

    # 2) VIENTO por localidad. Una alerta nueva no depende del estado nacional.
    notificar_lluvia(observaciones_lluvia, estado, ahora, combinado=True)
    notificar_viento(observaciones, estado, ahora)
    notificar_lluvia(observaciones_lluvia, estado, ahora)
    fallas = sin_datos + sin_lluvia + (['NHC/NOAA (ciclones)'] if ciclones is None else [])
    notificar_cobertura(fallas, estado, ahora)
    estado['viento_alto_activo'] = bool(riesgo_alto or any(
        v['nivel'] in ('ALTO', 'CRITICO') for v in estado['riesgos_notificados'].values()
    ) or (sin_datos and estado.get('viento_alto_activo')))


    # ---- 3) REPORTE DE RUTINA: 8am, 3pm, 9pm hora del centro de Mexico ----
    if rutina_pendiente:
        fecha_local = formatear_fecha_local(a_hora_mexico(ahora))

        if ciclones is None:
            linea_ciclones = '⚠️ <b>Ciclones:</b> datos NHC no disponibles; situación no confirmada.'
        elif ciclones:
            nombres = []
            for c in sorted(ciclones, key=lambda c: c['intensidad_kmh'], reverse=True):
                detalle = f"Cat. {c['categoria']}" if c['categoria'] else tipo_ciclon(c)
                nombres.append(f"{nombre_amigable(c['nombre'])} ({detalle}, {c['intensidad_kmh']} km/h)")
            linea_ciclones = f"🌀 <b>Ciclones activos:</b> {esc(', '.join(nombres))}"
        else:
            linea_ciclones = ('🌀 <b>Ciclones:</b> sin huracanes ni tormentas activas '
                              'en el litoral (Golfo, Caribe y Pacífico)')

        resumen = [
            '📋 <b>REPORTE DE RUTINA</b>',
            f'🗓 {esc(fecha_local)} (hora del centro de México)',
            '',
            linea_ciclones,
            '',
            f'💨 <b>Viento por zona</b> (≥ {UMBRAL_MODERADO_KMH} km/h, próximas {HORAS_A_FUTURO} h):',
        ]
        resumen += resumen_por_zona(riesgo, zonas_sin_datos)
        lluvia_activa = [(k, e) for k, e in observaciones_lluvia.items() if (clasificar_lluvia(e) or 0) > 0]
        combinadas = sum((clasificar_combinado(e) or 0) > 0 for e in observaciones_lluvia.values())
        resumen += ['', f'🌧 Precipitación >10 mm/24 h: {len(lluvia_activa)} localidades',
                    f'Viento y lluvia: {combinadas} localidades',
                    f'📡 Cobertura viento: {revisados} de {len(items)}; lluvia completa: {len(items)-len(sin_lluvia)} de {len(items)}']

        mensajes = ['\n'.join(resumen)]

        titulo_rutina = (
            f"💨 <b>Viento previsto · próximas {HORAS_A_FUTURO} h</b>\n"
            f"<i>🟣 90+ km/h · 🔴 60 a 89 km/h · 🟡 45 a 59 km/h</i>"
        )
        mensajes += construir_mensajes_viento(riesgo, titulo_rutina)
        if lluvia_activa:
            mensajes += mensajes_lluvia(lluvia_activa, '🌧 <b>PRECIPITACIÓN · REPORTE DE RUTINA</b>')
        if aviso_cobertura:
            mensajes.append(aviso_cobertura)

        if enviar_lista(mensajes):
            estado['ultima_rutina_enviada'] = clave_rutina_actual
        else:
            print('AVISO: reporte de rutina incompleto; se reintentara en la siguiente consulta.')
    else:
        print(f'No hay reporte de rutina pendiente (hora UTC actual: {hora_actual_utc}).')

    guardar_estado(estado)


if __name__ == '__main__':
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise SystemExit('Faltan las variables TELEGRAM_TOKEN o TELEGRAM_CHAT_ID. Configura GitHub Secrets.')
    revisar_y_alertar()
