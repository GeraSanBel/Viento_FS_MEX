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

# Cuantas horas hacia adelante revisar en el pronostico (max 120 = 5 dias)
HORAS_A_FUTURO = 48

# Lugares a monitorear: nombre, latitud, longitud (capital de cada estado)
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

# ==========================================


def obtener_pronostico_viento(lat, lon):
    """
    Consulta el pronostico de OpenWeatherMap (bloques de 3 horas) y regresa
    el punto con mayor viento dentro de las proximas HORAS_A_FUTURO,
    junto con la fecha/hora en que se espera.
    """
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

    bloques_a_revisar = HORAS_A_FUTURO // 3  # el pronostico viene cada 3 horas

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
            peor_hora = bloque.get("dt_txt")  # ej: "2026-07-18 15:00:00"

    return peor_velocidad, peor_rafaga, peor_hora


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
            velocidad, rafaga, hora = obtener_pronostico_viento(lugar["lat"], lugar["lon"])
        except Exception as error:
            print(f"Error consultando {lugar['nombre']}: {error}")
            continue

        maximo = max(velocidad, rafaga)
        print(f"{lugar['nombre']}: max previsto {velocidad:.1f} km/h, rafaga {rafaga:.1f} km/h a las {hora}")

        if maximo >= UMBRAL_KMH:
            alertas.append(
                f"- {lugar['nombre']}: hasta {velocidad:.0f} km/h (rafagas {rafaga:.0f} km/h) previsto para {hora}"
            )

    if alertas:
        mensaje = (
            "⚠️ ALERTA DE VIENTO PRONOSTICADO ⚠️\n"
            f"Se espera superar {UMBRAL_KMH} km/h en las proximas {HORAS_A_FUTURO}h en:\n"
            + "\n".join(alertas)
        )
        enviado = enviar_telegram(mensaje)
        print("Alerta enviada por Telegram" if enviado else "Fallo el envio de Telegram")
    else:
        print("Sin riesgos de viento pronosticado por ahora, no se envia alerta.")


if __name__ == "__main__":
    revisar_y_alertar()
