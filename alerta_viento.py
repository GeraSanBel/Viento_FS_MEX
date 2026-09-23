"""
Alerta de viento y ciclones por Telegram, por municipio, para Mexico.

Fuentes de datos:
    - Open-Meteo (principal): pronostico de viento por hora, gratuito, sin API key.
    - OpenWeatherMap (respaldo): se usa solo si Open-Meteo falla para algun municipio.
    - NHC/NOAA: ciclones activos en Atlantico, Caribe y Pacifico Oriental.

Envio de mensajes: Bot de Telegram.

Dos niveles de riesgo de viento:
    - MODERADO (45-59 km/h): solo se reporta en los horarios de rutina.
    - ALTO (60+ km/h): se avisa de inmediato, a cualquier hora.

Requisitos:
    pip install requests
"""

import os
import json
import time
from datetime import datetime, timezone
import requests

# ============ CONFIGURACION ============

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8697170500:AAFc6vJ_VGSreH9B_FraDFrMdQjViEr21DE')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '8993916335')

# API key de OpenWeatherMap, usada SOLO como respaldo si Open-Meteo falla
OWM_API_KEY = os.environ.get('OWM_API_KEY', '837774a4942600dde476923a178e8e9c')

# Umbral de riesgo MODERADO (solo aparece en reportes de rutina)
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
# Corresponden a 8:00 AM, 3:00 PM y 9:00 PM hora de Mexico (CST, UTC-6).
HORAS_RUTINA_UTC = {14, 21, 3}

# Cada cuantas horas se repite el aviso de riesgo ALTO mientras siga activo
HORAS_ENTRE_RECORDATORIOS = 3

# Archivos
ARCHIVO_MUNICIPIOS_COORDS = 'municipios_coords.json'
ARCHIVO_ESTADO = 'estado_alertas.json'

# =======================================


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


def categoria_saffir_simpson(intensidad_mph, clasificacion):
    """Calcula la categoria Saffir-Simpson a partir de la velocidad en mph."""
    if clasificacion != 'HU':
        return None  # solo aplica a huracanes; TS/TD no tienen categoria
    if intensidad_mph >= 157:
        return 5
    if intensidad_mph >= 130:
        return 4
    if intensidad_mph >= 111:
        return 3
    if intensidad_mph >= 96:
        return 2
    if intensidad_mph >= 74:
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
    from math import radians, sin, cos, sqrt, atan2
    R = 6371  # radio de la Tierra en km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def estados_cercanos_a_ciclon(lat_ciclon, lon_ciclon, municipios, radio_km=400):
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
        intensidad_mph_raw = tormenta.get('intensity', 0)
        try:
            intensidad_mph = float(intensidad_mph_raw)
        except (TypeError, ValueError):
            intensidad_mph = 0
        intensidad_kmh = round(intensidad_mph * 1.60934)

        lat = parsear_coordenada(tormenta.get('latitudeNumeric', tormenta.get('latitude')))
        lon = parsear_coordenada(tormenta.get('longitudeNumeric', tormenta.get('longitude')))

        estados_cercanos = []
        if municipios and lat is not None and lon is not None:
            estados_cercanos = estados_cercanos_a_ciclon(lat, lon, municipios)

        ciclones.append({
            'id': tormenta.get('id', ''),
            'nombre': tormenta.get('name', 'Desconocido'),
            'clasificacion': clasificacion,
            'categoria': categoria_saffir_simpson(intensidad_mph, clasificacion),
            'intensidad_kmh': intensidad_kmh,
            'estados_cercanos': estados_cercanos,
        })
    return ciclones


def formatear_ciclon(ciclon):
    clasificaciones = {
        'HU': 'Huracan',
        'TS': 'Tormenta tropical',
        'TD': 'Depresion tropical',
        'STS': 'Tormenta subtropical',
        'STD': 'Depresion subtropical',
    }
    tipo = clasificaciones.get(ciclon['clasificacion'], ciclon['clasificacion'] or 'Sistema tropical')

    etiqueta_categoria = ''
    if ciclon['categoria']:
        etiqueta_categoria = f" (Categoria {ciclon['categoria']})"

    linea = f"- {tipo}{etiqueta_categoria} {ciclon['nombre']}: vientos {ciclon['intensidad_kmh']} km/h"

    if ciclon['estados_cercanos']:
        nombres = ', '.join(estado for estado, _ in ciclon['estados_cercanos'][:6])
        linea += f"\n  Estados en su zona de cercania (~400km): {nombres}"
    else:
        linea += "\n  Sin estados mexicanos dentro de ~400km de su posicion actual"

    return linea


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


def enviar_telegram(mensaje):
    """Envia mensaje a Telegram, dividiendolo si excede el limite de longitud."""
    LIMITE = 3800
    partes = [mensaje[i:i + LIMITE] for i in range(0, len(mensaje), LIMITE)] or [mensaje]

    todo_ok = True
    for parte in partes:
        url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
        payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': parte}
        try:
            respuesta = requests.post(url, data=payload, timeout=20)
            if respuesta.status_code != 200:
                todo_ok = False
                print(f'Telegram: fallo ({respuesta.status_code}) - {respuesta.text[:200]}')
        except Exception as error:
            todo_ok = False
            print(f'Telegram: error de conexion - {error}')

    print('Telegram: enviado' if todo_ok else 'Telegram: fallo parcial o total')
    return todo_ok


def revisar_y_alertar():
    municipios = cargar_municipios()
    items = list(municipios.items())

    riesgo_moderado = []
    riesgo_alto = []
    sin_datos = []

    lotes = [items[i:i + TAMANO_LOTE] for i in range(0, len(items), TAMANO_LOTE)]

    for numero_lote, lote in enumerate(lotes, start=1):
        print(f'Consultando lote {numero_lote}/{len(lotes)} ({len(lote)} municipios)...')
        resultados_lote = procesar_lote_adaptativo(lote)

        for clave, info in lote:
            if clave not in resultados_lote:
                sin_datos.append(f"{info['ciudad']} ({info['estado']})")
                continue

            velocidad, rafaga, hora = resultados_lote[clave]
            maximo = max(velocidad, rafaga)

            linea = (
                f"- {info['ciudad']} ({info['estado']}): hasta {velocidad:.0f} km/h "
                f"(rafagas {rafaga:.0f} km/h) previsto para {hora}"
            )

            if maximo >= UMBRAL_ALTO_KMH:
                riesgo_alto.append(linea)
            elif maximo >= UMBRAL_MODERADO_KMH:
                riesgo_moderado.append(linea)

        time.sleep(PAUSA_ENTRE_LOTES)

    revisados = len(items) - len(sin_datos)
    print(f'Municipios revisados: {revisados}/{len(items)}. Sin datos: {len(sin_datos)}')
    print(f'Riesgo alto: {len(riesgo_alto)}. Riesgo moderado: {len(riesgo_moderado)}')

    ciclones = obtener_ciclones_activos(municipios)
    for c in ciclones:
        print(f"Ciclon activo: {c['nombre']} ({c['clasificacion']}), {c['intensidad_kmh']} km/h")

    ahora = datetime.now(timezone.utc)
    hora_actual_utc = ahora.hour
    estado = cargar_estado()
    es_hora_de_rutina = hora_actual_utc in HORAS_RUTINA_UTC

    # Aviso de cobertura, solo si hubo municipios sin revisar
    aviso_cobertura = ''
    if sin_datos:
        listado = ', '.join(sin_datos[:15])
        extra = f' y {len(sin_datos) - 15} mas' if len(sin_datos) > 15 else ''
        aviso_cobertura = (
            f"\n\n⚠️ Aviso: no se pudo obtener pronostico de {len(sin_datos)} "
            f"de {len(items)} municipios ({listado}{extra}). "
            f"Esta informacion esta incompleta."
        )

    # ---- 1) VIENTO DE RIESGO ALTO: avisa de inmediato, a cualquier hora ----
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

        if viento_era_nuevo or toca_recordatorio:
            mensaje = (
                '🔴 ALERTA DE RIESGO ALTO DE VIENTO 🔴\n'
                f'Viento igual o mayor a {UMBRAL_ALTO_KMH} km/h en las proximas {HORAS_A_FUTURO}h:\n'
                + '\n'.join(riesgo_alto)
                + aviso_cobertura
            )
            enviar_telegram(mensaje)
            estado['ultima_notificacion_viento'] = ahora.isoformat()
        else:
            print('Viento de riesgo alto sigue activo pero ya se aviso recientemente. No se repite.')
        estado['viento_alto_activo'] = True
    else:
        if estado.get('viento_alto_activo', False):
            enviar_telegram('✅ El riesgo ALTO de viento ha pasado.')
        estado['viento_alto_activo'] = False
        estado['ultima_notificacion_viento'] = ahora.isoformat()

    # ---- 2) CICLONES: avisa de inmediato, a cualquier hora ----
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
            lineas = [formatear_ciclon(c) for c in ciclones]
            mensaje = (
                '🌀 HURACAN/TORMENTA EN EL LITORAL (Golfo/Caribe/Pacifico) 🌀\n'
                + '\n'.join(lineas)
                + '\nRevisa nhc.noaa.gov o conagua.gob.mx para trayectoria oficial.'
                + aviso_cobertura
            )
            enviar_telegram(mensaje)
            estado['ultima_notificacion_ciclon'] = ahora.isoformat()
        else:
            print('Ciclon(es) siguen activos pero ya se aviso recientemente. No se repite.')
        estado['ciclones_activos_ids'] = ids_actuales
    else:
        if estado.get('ciclones_activos_ids'):
            enviar_telegram('✅ Ya no hay huracanes/tormentas activas en el litoral mexicano.')
        estado['ciclones_activos_ids'] = []
        estado['ultima_notificacion_ciclon'] = ahora.isoformat()

    # ---- 3) REPORTE DE RUTINA: 8am, 3pm, 9pm hora Mexico ----
    clave_rutina_actual = ahora.strftime('%Y-%m-%d-%H')

    if es_hora_de_rutina:
        if estado.get('ultima_rutina_enviada') == clave_rutina_actual:
            print(f'El reporte de rutina de esta hora ({clave_rutina_actual}) ya se envio. No se repite.')
        else:
            bloques = [f'📋 Reporte de rutina ({revisados}/{len(items)} municipios revisados):']

            if riesgo_moderado:
                bloques.append(
                    '🟡 Viento moderado (45-59 km/h) previsto en:\n' + '\n'.join(riesgo_moderado)
                )
            else:
                bloques.append('- Sin viento moderado/fuerte pronosticado (por debajo de 45 km/h).')

            if not riesgo_alto:
                bloques.append(f'- Sin viento de riesgo alto (menor a {UMBRAL_ALTO_KMH} km/h).')

            if not ciclones:
                bloques.append('- Sin huracanes/tormentas activas en el litoral (Golfo, Caribe, Pacifico).')

            enviar_telegram('\n\n'.join(bloques) + aviso_cobertura)
            estado['ultima_rutina_enviada'] = clave_rutina_actual
    else:
        print(f'No es hora de reporte de rutina (hora UTC actual: {hora_actual_utc}).')

    guardar_estado(estado)


if __name__ == '__main__':
    revisar_y_alertar()
