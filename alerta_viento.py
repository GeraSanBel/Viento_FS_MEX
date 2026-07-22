"""
Alerta de viento por Telegram para estados de México.

Fuente de datos: OpenWeatherMap (pronostico de viento)
Envio de mensajes: Bot de Telegram (gratuito)

Requisitos:
    pip install requests
"""

import os
from datetime import datetime, timezone
import requests

# ============ CONFIGURA ESTO ============

OWM_API_KEY = os.environ.get("OWM_API_KEY", "PON_AQUI_TU_API_KEY_DE_OPENWEATHERMAP")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "PON_AQUI_TU_TOKEN_DE_TELEGRAM")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PON_AQUI_TU_CHAT_ID")

UMBRAL_KMH = 45
HORAS_A_FUTURO = 24
HORAS_CONFIRMACION_UTC = {0, 6, 12, 18}

UBICACIONES = [
    {"nombre": "Aguascalientes",      "lat": 21.8853, "lon": -102.2916},
    {"nombre": "Baja California",     "lat": 32.6245, "lon": -115.4523},
    {"nombre": "Baja California Sur", "lat": 24.1426, "lon": -110.3128},
    {"nombre": "Campeche",            "lat": 19.8301, "lon": -90.5349},
    {"nombre": "Chiapas",             "lat": 16.7516, "lon": -93.1029},
    {"nombre": "Chihuahua",           "lat": 28.6353, "lon": -106.0889},
    {"nombre": "Ciudad de Mexico",    "lat": 19.4326, "lon": -99.1332},
    {"nombre": "Coahuila",            "lat": 25.4260, "lon": -101.0053},
    {"nombre": "Colima",              "lat": 19.2433, "lon": -103.7250},
    {"nombre": "Durango",             "lat": 24.0277, "lon": -104.6532},
    {"nombre": "Estado de Mexico",    "lat": 19.2926, "lon": -99.6568},
    {"nombre": "Guanajuato",          "lat": 21.0190, "lon": -101.2574},
    {"nombre": "Guerrero",            "lat": 17.5506, "lon": -99.5024},
    {"nombre": "Hidalgo",             "lat": 20.1011, "lon": -98.7591},
    {"nombre": "Jalisco",             "lat": 20.6597, "lon": -103.3496},
    {"nombre": "Michoacan",           "lat": 19.7008, "lon": -101.1844},
    {"nombre": "Morelos",             "lat": 18.9242, "lon": -99.2216},
    {"nombre": "Nayarit",             "lat": 21.5041, "lon": -104.8942},
    {"nombre": "Nuevo Leon",          "lat": 25.6866, "lon": -100.3161},
    {"nombre": "Oaxaca",              "lat": 17.0732, "lon": -96.7266},
    {"nombre": "Puebla",              "lat": 19.0414, "lon": -98.2063},
    {"nombre": "Queretaro",           "lat": 20.5888, "lon": -100.3899},
    {"nombre": "Quintana Roo",        "lat": 18.5036, "lon": -88.3055},
    {"nombre": "San Luis Potosi",     "lat": 22.1565, "lon": -100.9855},
    {"nombre": "Sinaloa",             "lat": 24.8091, "lon": -107.3940},
    {"nombre": "Sonora",              "lat": 29.0729, "lon": -110.9559},
    {"nombre": "Tabasco",             "lat": 17.9895, "lon": -92.9475},
    {"nombre": "Tamaulipas",          "lat": 23.7369, "lon": -99.1411},
    {"nombre": "Tlaxcala",            "lat": 19.3182, "lon": -98.2375},
    {"nombre": "Veracruz",            "lat": 19.5438, "lon": -96.9102},
    {"nombre": "Yucatan",             "lat": 20.9674, "lon": -89.5926},
    {"nombre": "Zacatecas",           "lat": 22.7709, "lon": -102.5832},
]


def obtener_pronostico_viento(lat, lon):
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": OWM_API_KEY,
        "units": "metric",
    }
    respuesta = requests.get(url, params=params, timeout=15)
    respuesta.raise_for_status()
    datos = respuesta.json()

    bloques_a_revisar = HORAS_A_FUTURO // 3

    peor_velocidad = 0
    peor_rafaga = 0
    peor_hora = None

    for bloque in datos.get("list", [])[:bloques_a_revisar]:
        viento = bloque.get("wind", {})
        velocidad_kmh = viento.get("speed", 0) * 3.6
        rafaga_kmh = viento.get("gust", viento.get("speed", 0)) * 3.6
        maximo_bloque = max(velocidad_kmh, rafaga_kmh)

        if maximo_bloque > max(peor_velocidad, peor_rafaga):
            peor_velocidad = velocidad_kmh
            peor_rafaga = rafaga_kmh
            peor_hora = bloque.get("dt_txt")

    return peor_velocidad, peor_rafaga, peor_hora


def obtener_ciclones_activos():
    url = "https://www.nhc.noaa.gov/CurrentStorms.json"
    try:
        respuesta = requests.get(url, timeout=15)
        respuesta.raise_for_status()
        datos = respuesta.json()
    except Exception as error:
        print(f"Error consultando NHC: {error}")
        return []

    ciclones = []
    for tormenta in datos.get("activeStorms", []):
        storm_id = tormenta.get("id", "")
        if not (storm_id.startswith("AL") or storm_id.startswith("EP")):
            continue

        ciclones.append({
            "nombre": tormenta.get("name", "Desconocido"),
            "clasificacion": tormenta.get("classification", ""),
            "intensidad_mph": tormenta.get("intensity", "N/D"),
            "lat": tormenta.get("latitudeNumeric"),
            "lon": tormenta.get("longitudeNumeric"),
            "movimiento": tormenta.get("movementDir", ""),
        })

    return ciclones


def formatear_ciclon(ciclon):
    clasificaciones = {
        "HU": "Huracan",
        "TS": "Tormenta tropical",
        "TD": "Depresion tropical",
        "STS": "Tormenta subtropical",
        "STD": "Depresion subtropical",
    }
    tipo = clasificaciones.get(ciclon["clasificacion"], ciclon["clasificacion"] or "Sistema tropical")
    return f"- {tipo} {ciclon['nombre']}: vientos {ciclon['intensidad_mph']} mph"


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
    }
    respuesta = requests.post(url, data=payload, timeout=15)
    return respuesta.status_code == 200


def revisar_y_alertar():
    alertas_viento = []

    for lugar in UBICACIONES:
        try:
            velocidad, rafaga, hora = obtener_pronostico_viento(lugar["lat"], lugar["lon"])
        except Exception as error:
            print(f"Error consultando {lugar['nombre']}: {error}")
            continue

        maximo = max(velocidad, rafaga)
        print(f"{lugar['nombre']}: max previsto {velocidad:.1f} km/h, rafaga {rafaga:.1f} km/h a las {hora}")

        if maximo >= UMBRAL_KMH:
            alertas_viento.append(
                f"- {lugar['nombre']}: hasta {velocidad:.0f} km/h (rafagas {rafaga:.0f} km/h) previsto para {hora}"
            )

    ciclones = obtener_ciclones_activos()
    for c in ciclones:
        print(f"Ciclon activo: {c['nombre']} ({c['clasificacion']}), {c['intensidad_mph']} mph")

    bloques_mensaje = []

    if alertas_viento:
        bloques_mensaje.append(
            "VIENTO PRONOSTICADO\n"
            f"Se espera superar {UMBRAL_KMH} km/h en las proximas {HORAS_A_FUTURO}h en:\n"
            + "\n".join(alertas_viento)
        )

    if ciclones:
        lineas_ciclones = [formatear_ciclon(c) for c in ciclones]
        bloques_mensaje.append(
            "CICLONES ACTIVOS (Atlantico / Pacifico)\n"
            + "\n".join(lineas_ciclones)
            + "\nRevisa nhc.noaa.gov o conagua.gob.mx para trayectoria y avisos oficiales."
        )
    elif alertas_viento:
        bloques_mensaje.append("Sin ciclones activos en Atlantico/Pacifico por ahora.")

    hora_actual_utc = datetime.now(timezone.utc).hour

    if bloques_mensaje:
        mensaje = "\n\n".join(bloques_mensaje)
        enviado = enviar_telegram(mensaje)
        print("Alerta enviada por Telegram" if enviado else "Fallo el envio de Telegram")
    elif hora_actual_utc in HORAS_CONFIRMACION_UTC:
        mensaje = (
            "Reporte de rutina:\n"
            "- Sin viento fuerte pronosticado en los 32 estados.\n"
            "- Sin ciclones activos en Atlantico/Pacifico."
        )
        enviado = enviar_telegram(mensaje)
        print("Confirmacion de sin riesgo enviada" if enviado else "Fallo el envio de Telegram")
    else:
        print(f"Sin riesgos y no es hora de confirmacion (hora UTC actual: {hora_actual_utc}). No se envia nada.")


if __name__ == "__main__":
    revisar_y_alertar()
