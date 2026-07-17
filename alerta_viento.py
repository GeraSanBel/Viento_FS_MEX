"""
Alerta de viento por Telegram para estados de México.

Fuente de datos: OpenWeatherMap (pronostico de viento)
Envio de mensajes: Bot de Telegram (gratuito)

Requisitos:
    pip install requests
"""

import os
import requests

# ============ CONFIGURA ESTO ============

# Tu API key de OpenWeatherMap (openweathermap.org/api)
# Se lee primero de variable de entorno (para GitHub Actions), si no existe usa el texto de aqui.
OWM_API_KEY = os.environ.get("OWM_API_KEY", "PON_AQUI_TU_API_KEY_DE_OPENWEATHERMAP")

# El token que te dio BotFather
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "PON_AQUI_TU_TOKEN_DE_TELEGRAM")

# Tu chat_id obtenido con getUpdates
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PON_AQUI_TU_CHAT_ID")

# Umbral de viento en km/h a partir del cual se considera riesgo
UMBRAL_KMH = 45

# Lugares a monitorear: nombre, latitud, longitud
UBICACIONES = [
    {"nombre": "Veracruz",        "lat": 19.1738, "lon": -96.1342},
    {"nombre": "Ciudad de Mexico", "lat": 19.4326, "lon": -99.1332},
    {"nombre": "Oaxaca",          "lat": 17.0732, "lon": -96.7266},
    # Agrega mas estados/ciudades aqui con el mismo formato
]

# ==========================================


def obtener_viento(lat, lon):
    """Consulta OpenWeatherMap y regresa velocidad y rafaga de viento en km/h."""
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": OWM_API_KEY,
        "units": "metric",  # da velocidad en m/s, la convertimos abajo
    }
    respuesta = requests.get(url, params=params, timeout=15)
    respuesta.raise_for_status()
    datos = respuesta.json()

    velocidad_ms = datos.get("wind", {}).get("speed", 0)
    rafaga_ms = datos.get("wind", {}).get("gust", velocidad_ms)

    velocidad_kmh = velocidad_ms * 3.6
    rafaga_kmh = rafaga_ms * 3.6

    return velocidad_kmh, rafaga_kmh


def enviar_telegram(mensaje):
    """Envia un mensaje usando el bot de Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
    }
    respuesta = requests.post(url, data=payload, timeout=15)
    return respuesta.status_code == 200


def revisar_y_alertar():
    alertas = []

    for lugar in UBICACIONES:
        try:
            velocidad, rafaga = obtener_viento(lugar["lat"], lugar["lon"])
        except Exception as error:
            print(f"Error consultando {lugar['nombre']}: {error}")
            continue

        maximo = max(velocidad, rafaga)
        print(f"{lugar['nombre']}: viento {velocidad:.1f} km/h, rafaga {rafaga:.1f} km/h")

        if maximo >= UMBRAL_KMH:
            alertas.append(
                f"- {lugar['nombre']}: viento {velocidad:.0f} km/h, rafagas hasta {rafaga:.0f} km/h"
            )

    if alertas:
        mensaje = (
            "⚠️ ALERTA DE VIENTO ⚠️\n"
            f"Se supero el umbral de {UMBRAL_KMH} km/h en:\n"
            + "\n".join(alertas)
        )
        enviado = enviar_telegram(mensaje)
        print("Alerta enviada por Telegram" if enviado else "Fallo el envio de Telegram")
    else:
        print("Sin riesgos de viento por ahora, no se envia alerta.")


if __name__ == "__main__":
    revisar_y_alertar()
