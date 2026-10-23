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
from math import radians, sin, cos, sqrt, atan2
import requests

# ============ CONFIGURACION ============

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8697170500:AAFc6vJ_VGSreH9B_FraDFrMdQjViEr21DE')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '8993916335')

# API key de OpenWeatherMap, usada SOLO como respaldo si Open-Meteo falla
OWM_API_KEY = os.environ.get('OWM_API_KEY', '')

# Umbral de riesgo MODERADO (aparece en reportes de rutina)
UMBRAL_MODERADO_KMH = 45

# Umbral de riesgo ALTO (avisa de inmediato, a cualquier hora)
UMBRAL_ALTO_KMH = 60

# Horas hacia adelante a revisar en el pronostico
HORAS_A_FUTURO = 18

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


def extraer_peor_viento(dato_municipio):
    """De la respuesta horaria de Open-Meteo, saca el peor momento de viento."""
    horas = dato_municipio.get('hourly', {})
    tiempos = horas.get('time', [])
    velocidades = horas.get('wind_speed_10m', [])
    rafagas = horas.get('wind_gusts_10m', [])

    peor_velocidad = 0
    peor_rafaga = 0
    peor_hora = None

    for i in range(len(tiempos)):
        velocidad = velocidades[i] if i < len(velocidades) else 0
        rafaga = rafagas[i] if i < len(rafagas) else velocidad
        if velocidad is None:
            velocidad = 0
        if rafaga is None:
            rafaga = velocidad

        if max(velocidad, rafaga) > max(peor_velocidad, peor_rafaga):
            peor_velocidad = velocidad
            peor_rafaga = rafaga
            peor_hora = tiempos[i]

    return peor_velocidad, peor_rafaga, peor_hora


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
        'hourly': 'wind_speed_10m,wind_gusts_10m',
        'wind_speed_unit': 'kmh',
        'forecast_hours': HORAS_A_FUTURO,
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

    resultados = {}
    for (clave, _info), dato_municipio in zip(lote, datos):
        resultados[clave] = extraer_peor_viento(dato_municipio)

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
        print(f'    Respaldo OWM tambien fallo para {clave}: {error}')
        return None

    bloques = max(1, HORAS_A_FUTURO // 3)
    peor_velocidad = 0
    peor_rafaga = 0
    peor_hora = None

    for bloque in datos.get('list', [])[:bloques]:
        viento = bloque.get('wind', {})
        velocidad = viento.get('speed', 0) * 3.6
        rafaga = viento.get('gust', viento.get('speed', 0)) * 3.6
        if max(velocidad, rafaga) > max(peor_velocidad, peor_rafaga):
            peor_velocidad = velocidad
            peor_rafaga = rafaga
            peor_hora = bloque.get('dt_txt')

    return peor_velocidad, peor_rafaga, peor_hora


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
                return {clave: respaldo}
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
        print(f'Error consultando NHC: {error}')
        return []

    ciclones = []
    for tormenta in datos.get('activeStorms', []):
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
            if respuesta.status_code != 200:
                todo_ok = False
                print(f'Telegram: fallo ({respuesta.status_code}) - {respuesta.text[:200]}')
        except Exception as error:
            todo_ok = False
            print(f'Telegram: error de conexion - {error}')

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
        if respuesta.status_code == 200:
            print('Telegram: imagen enviada')
            return True
        print(f'Telegram: fallo al enviar imagen ({respuesta.status_code}) - {respuesta.text[:200]}')
    except Exception as error:
        print(f'Telegram: error enviando imagen - {error}')
    return False


def enviar_lista(mensajes):
    """Envia varios mensajes en orden, con una pausa corta entre ellos."""
    for mensaje in mensajes:
        enviar_telegram(mensaje)
        time.sleep(0.7)


def enviar_ciclon(ciclon):
    """Envia un ciclon con su mapa oficial; si no hay mapa, solo el texto."""
    texto = formatear_ciclon(ciclon)
    imagen = descargar_imagen_ciclon(ciclon['id'])
    if imagen:
        pie = texto + '\n\n🗺 Mapa oficial NHC: posición actual, cono de trayectoria y avisos.'
        if len(pie) <= 1024 and enviar_foto_telegram(imagen, pie):
            return
        # Si el texto es muy largo para un pie de foto, va la imagen y luego el texto
        if enviar_foto_telegram(imagen, f"🗺 {esc(tipo_ciclon(ciclon))} {esc(nombre_amigable(ciclon['nombre']))}"):
            enviar_telegram(texto)
            return
    enviar_telegram(texto)


# ---------------------------------------------------------------
# Construccion de mensajes de viento
# ---------------------------------------------------------------

def formatear_entrada(entrada):
    icono = '🔴' if entrada['maximo'] >= UMBRAL_ALTO_KMH else '🟡'
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


def resumen_por_zona(entradas):
    """Lineas de resumen: cuantas localidades en riesgo hay en cada zona."""
    por_zona = agrupar_por_zona(entradas)
    lineas = []
    for zona in ORDEN_REGIONES:
        if zona == 'OTRAS' and not por_zona.get('OTRAS'):
            continue
        lista = por_zona.get(zona, [])
        etiqueta = 'Otras zonas' if zona == 'OTRAS' else zona.capitalize()
        if not lista:
            lineas.append(f"      {etiqueta}: ✅ sin riesgo")
            continue
        altos = sum(1 for e in lista if e['maximo'] >= UMBRAL_ALTO_KMH)
        moderados = len(lista) - altos
        partes = []
        if altos:
            partes.append(f"🔴 {altos}")
        if moderados:
            partes.append(f"🟡 {moderados}")
        lineas.append(f"      {etiqueta}: {' · '.join(partes)}")
    return lineas


# ---------------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------------

def revisar_y_alertar():
    municipios = cargar_municipios()
    items = list(municipios.items())

    riesgo = []       # todas las localidades con viento >= umbral moderado
    sin_datos = []

    lotes = [items[i:i + TAMANO_LOTE] for i in range(0, len(items), TAMANO_LOTE)]

    for numero_lote, lote in enumerate(lotes, start=1):
        print(f'Consultando lote {numero_lote}/{len(lotes)} ({len(lote)} municipios)...')
        resultados_lote = procesar_lote_adaptativo(lote)

        for clave, info in lote:
            if clave not in resultados_lote:
                sin_datos.append(f"{nombre_amigable(info['ciudad'])} ({nombre_amigable(info['estado'])})")
                continue

            velocidad, rafaga, hora = resultados_lote[clave]
            maximo = max(velocidad, rafaga)

            if maximo >= UMBRAL_MODERADO_KMH:
                riesgo.append({
                    'ciudad': info['ciudad'],
                    'estado': info['estado'],
                    'velocidad': velocidad,
                    'rafaga': rafaga,
                    'maximo': maximo,
                    'hora': hora,
                })

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
    for c in ciclones:
        print(f"Ciclon activo: {c['nombre']} ({c['clasificacion']}), {c['intensidad_kmh']} km/h")

    ahora = datetime.now(timezone.utc)
    hora_actual_utc = ahora.hour
    estado = cargar_estado()
    es_hora_de_rutina = hora_actual_utc in HORAS_RUTINA_UTC
    clave_rutina_actual = ahora.strftime('%Y-%m-%d-%H')
    rutina_pendiente = es_hora_de_rutina and estado.get('ultima_rutina_enviada') != clave_rutina_actual

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

    # ---- 1) CICLONES (lo mas critico va primero): avisa de inmediato, a cualquier hora ----
    # (independiente del viento; una tormenta NUEVA siempre avisa aunque
    # el viento haya avisado hace un minuto, y viceversa)
    if ciclones:
        ids_actuales = sorted(c['id'] for c in ciclones)
        ids_previos = sorted(estado.get('ciclones_activos_ids', []))
        hay_ciclon_nuevo = ids_actuales != ids_previos  # cambio en el set de tormentas activas

        horas_desde_ultima = None
        if estado.get('ultima_notificacion_ciclon'):
            ultima = datetime.fromisoformat(estado['ultima_notificacion_ciclon'])
            horas_desde_ultima = (ahora - ultima).total_seconds() / 3600

        toca_recordatorio = (
            horas_desde_ultima is not None and horas_desde_ultima >= HORAS_ENTRE_RECORDATORIOS
        )

        if hay_ciclon_nuevo or toca_recordatorio:
            # Los mas intensos primero
            for ciclon in sorted(ciclones, key=lambda c: c['intensidad_kmh'], reverse=True):
                enviar_ciclon(ciclon)
                time.sleep(0.7)
            estado['ultima_notificacion_ciclon'] = ahora.isoformat()
        else:
            print('Ciclon(es) siguen activos pero ya se aviso recientemente. No se repite.')
        estado['ciclones_activos_ids'] = ids_actuales
    else:
        if estado.get('ciclones_activos_ids'):
            enviar_telegram('✅ <b>Ya no hay huracanes ni tormentas activas en el litoral mexicano.</b>')
        estado['ciclones_activos_ids'] = []
        estado['ultima_notificacion_ciclon'] = ahora.isoformat()

    # ---- 2) VIENTO DE RIESGO ALTO: avisa de inmediato, a cualquier hora ----
    # (independiente de ciclones, para que uno nunca bloquee al otro)
    if riesgo_alto:
        viento_era_nuevo = not estado.get('viento_alto_activo', False)

        horas_desde_ultima = None
        if estado.get('ultima_notificacion_viento'):
            ultima = datetime.fromisoformat(estado['ultima_notificacion_viento'])
            horas_desde_ultima = (ahora - ultima).total_seconds() / 3600

        toca_recordatorio = (
            horas_desde_ultima is not None and horas_desde_ultima >= HORAS_ENTRE_RECORDATORIOS
        )

        if rutina_pendiente and (viento_era_nuevo or toca_recordatorio):
            print('Riesgo alto incluido en el reporte de rutina de esta hora; no se envia aviso aparte.')
            estado['ultima_notificacion_viento'] = ahora.isoformat()
        elif viento_era_nuevo or toca_recordatorio:
            titulo = (
                f"🔴 <b>ALERTA DE RIESGO ALTO · VIENTO</b>\n"
                f"Viento o rachas de {UMBRAL_ALTO_KMH} km/h o más en las próximas {HORAS_A_FUTURO} h\n"
                f"<i>(horas en hora del centro de México)</i>"
            )
            mensajes = construir_mensajes_viento(riesgo_alto, titulo)
            if aviso_cobertura:
                mensajes.append(aviso_cobertura)
            enviar_lista(mensajes)
            estado['ultima_notificacion_viento'] = ahora.isoformat()
        else:
            print('Viento de riesgo alto sigue activo pero ya se aviso recientemente. No se repite.')
        estado['viento_alto_activo'] = True
    else:
        if estado.get('viento_alto_activo', False):
            enviar_telegram('✅ <b>El riesgo ALTO de viento ha pasado.</b>')
        estado['viento_alto_activo'] = False
        estado['ultima_notificacion_viento'] = ahora.isoformat()

    # ---- 3) REPORTE DE RUTINA: 8am, 3pm, 9pm hora del centro de Mexico ----
    if es_hora_de_rutina:
        if estado.get('ultima_rutina_enviada') == clave_rutina_actual:
            print(f'El reporte de rutina de esta hora ({clave_rutina_actual}) ya se envio. No se repite.')
        else:
            fecha_local = formatear_fecha_local(a_hora_mexico(ahora))

            if ciclones:
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
            resumen += resumen_por_zona(riesgo)
            resumen += ['', f'📡 Cobertura: {revisados} de {len(items)} municipios revisados']

            mensajes = ['\n'.join(resumen)]

            titulo_rutina = (
                f"💨 <b>Viento previsto · próximas {HORAS_A_FUTURO} h</b>\n"
                f"<i>🔴 60 km/h o más · 🟡 45 a 59 km/h</i>"
            )
            mensajes += construir_mensajes_viento(riesgo, titulo_rutina)
            if aviso_cobertura:
                mensajes.append(aviso_cobertura)

            enviar_lista(mensajes)
            estado['ultima_rutina_enviada'] = clave_rutina_actual
    else:
        print(f'No es hora de reporte de rutina (hora UTC actual: {hora_actual_utc}).')

    guardar_estado(estado)


if __name__ == '__main__':
    revisar_y_alertar()
