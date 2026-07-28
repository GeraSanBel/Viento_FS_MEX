"""
Alerta de viento y ciclones por Telegram, por municipio, para Mexico.

Fuente de datos:
    - OpenWeatherMap: pronostico de viento (usa municipios_coords.json,
      generado una sola vez con geocode_municipios.py)
    - NHC/NOAA: ciclones activos en Atlantico, Pacifico y Caribe (sin API key)

Envio de mensajes: Bot de Telegram (gratuito)

Dos niveles de riesgo de viento:
    - MODERADO (45-59 km/h): solo se reporta en los horarios de rutina.
    - ALTO (60+ km/h): se avisa de inmediato, a cualquier hora.

Requisitos:
    pip install requests
"""

import os
import json
from datetime import datetime, timezone
import requests

# ============ CONFIGURA ESTO ============

OWM_API_KEY = os.environ.get("OWM_API_KEY", "837774a4942600dde476923a178e8e9c")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8697170500:AAFc6vJ_VGSreH9B_FraDFrMdQjViEr21DE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8993916335")

# Umbral de riesgo MODERADO (solo aparece en reportes de rutina)
UMBRAL_MODERADO_KMH = 45

# Umbral de riesgo ALTO (avisa de inmediato, a cualquier hora)
UMBRAL_ALTO_KMH = 10

# Cuantas horas hacia adelante revisar en el pronostico (max 120 = 5 dias)
HORAS_A_FUTURO = 36

# Horas del dia (en UTC) de los reportes de rutina.
# Corresponden a 8:00 AM, 3:00 PM y 9:00 PM hora de Mexico (CST, UTC-6 fijo).
HORAS_RUTINA_UTC = {14, 21, 3}

# Cada cuantas horas se repite el aviso de riesgo ALTO mientras siga activo
HORAS_ENTRE_RECORDATORIOS = 3

# Archivos
ARCHIVO_MUNICIPIOS_COORDS = "municipios_coords.json"
ARCHIVO_ESTADO = "estado_alertas.json"

# ==========================================


def cargar_municipios():
    """Carga la lista de municipios con sus coordenadas ya geocodificadas."""
    with open(ARCHIVO_MUNICIPIOS_COORDS, "r", encoding="utf-8") as f:
        return json.load(f)


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
    """
    Consulta el feed publico del NHC (NOAA): ciclones activos en Atlantico
    (incluye Golfo de Mexico y Caribe) y Pacifico Oriental. No requiere API key.
    """
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
        # AL = Atlantico (incluye Golfo de Mexico y Caribe), EP = Pacifico Oriental
        if not (storm_id.startswith("AL") or storm_id.startswith("EP")):
            continue

        ciclones.append({
            "nombre": tormenta.get("name", "Desconocido"),
            "clasificacion": tormenta.get("classification", ""),
            "intensidad_mph": tormenta.get("intensity", "N/D"),
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


def cargar_estado():
    try:
        with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"riesgo_alto_activo": False, "ultima_notificacion_alta": None}


def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f)


def enviar_telegram(mensaje):
    """Envia un mensaje usando el bot de Telegram. Si es muy largo, lo divide en partes."""
    LIMITE = 3800  # Telegram permite 4096, dejamos margen
    partes = [mensaje[i:i + LIMITE] for i in range(0, len(mensaje), LIMITE)] or [mensaje]

    todo_ok = True
    for parte in partes:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": parte}
        respuesta = requests.post(url, data=payload, timeout=15)
        if respuesta.status_code != 200:
            todo_ok = False
            print(f"Telegram: fallo ({respuesta.status_code}) - {respuesta.text[:200]}")

    print("Telegram: enviado" if todo_ok else "Telegram: fallo parcial o total")
    return todo_ok


def revisar_y_alertar():
    municipios = cargar_municipios()

    riesgo_moderado = []  # 45-59 km/h
    riesgo_alto = []      # 60+ km/h

    for clave, info in municipios.items():
        try:
            velocidad, rafaga, hora = obtener_pronostico_viento(info["lat"], info["lon"])
        except Exception as error:
            print(f"Error consultando {clave}: {error}")
            continue

        maximo = max(velocidad, rafaga)

        if maximo >= UMBRAL_ALTO_KMH:
            riesgo_alto.append(
                f"- {info['ciudad']} ({info['estado']}): hasta {velocidad:.0f} km/h "
                f"(rafagas {rafaga:.0f} km/h) previsto para {hora}"
            )
        elif maximo >= UMBRAL_MODERADO_KMH:
            riesgo_moderado.append(
                f"- {info['ciudad']} ({info['estado']}): hasta {velocidad:.0f} km/h "
                f"(rafagas {rafaga:.0f} km/h) previsto para {hora}"
            )

    print(f"Riesgo alto: {len(riesgo_alto)} municipios. Riesgo moderado: {len(riesgo_moderado)} municipios.")

    ciclones = obtener_ciclones_activos()
    for c in ciclones:
        print(f"Ciclon activo: {c['nombre']} ({c['clasificacion']}), {c['intensidad_mph']} mph")

    hora_actual_utc = datetime.now(timezone.utc).hour
    ahora = datetime.now(timezone.utc)
    estado = cargar_estado()
    es_hora_de_rutina = hora_actual_utc in HORAS_RUTINA_UTC

    # ---- 1) RIESGO ALTO: se avisa de inmediato, a cualquier hora ----
    if riesgo_alto or ciclones:
        riesgo_era_nuevo = not estado.get("riesgo_alto_activo", False)

        horas_desde_ultima = None
        if estado.get("ultima_notificacion_alta"):
            ultima = datetime.fromisoformat(estado["ultima_notificacion_alta"])
            horas_desde_ultima = (ahora - ultima).total_seconds() / 3600

        toca_recordatorio = (
            horas_desde_ultima is not None and horas_desde_ultima >= HORAS_ENTRE_RECORDATORIOS
        )

        if riesgo_era_nuevo or toca_recordatorio:
            bloques = []
            if riesgo_alto:
                bloques.append(
                    "🔴 ALERTA DE RIESGO ALTO 🔴\n"
                    f"Viento igual o mayor a {UMBRAL_ALTO_KMH} km/h en las proximas {HORAS_A_FUTURO}h:\n"
                    + "\n".join(riesgo_alto)
                )
            if ciclones:
                lineas = [formatear_ciclon(c) for c in ciclones]
                bloques.append(
                    "🌀 HURACAN/TORMENTA EN EL LITORAL (Golfo/Caribe/Pacifico) 🌀\n"
                    + "\n".join(lineas)
                    + "\nRevisa nhc.noaa.gov o conagua.gob.mx para trayectoria oficial."
                )
            enviar_telegram("\n\n".join(bloques))
            estado["riesgo_alto_activo"] = True
            estado["ultima_notificacion_alta"] = ahora.isoformat()
        else:
            print("Riesgo alto sigue activo pero ya se aviso recientemente. No se repite.")
            estado["riesgo_alto_activo"] = True
    else:
        # Si el riesgo alto se acaba de despejar, avisar una vez
        if estado.get("riesgo_alto_activo", False):
            enviar_telegram("✅ El riesgo ALTO de viento/ciclones ha pasado.")
        estado["riesgo_alto_activo"] = False
        estado["ultima_notificacion_alta"] = ahora.isoformat()

    # ---- 2) REPORTE DE RUTINA: 8am, 3pm, 9pm hora Mexico ----
    if es_hora_de_rutina:
        bloques = ["📋 Reporte de rutina:"]

        if riesgo_moderado:
            bloques.append(
                "🟡 Viento moderado (45-59 km/h) previsto en:\n" + "\n".join(riesgo_moderado)
            )
        else:
            bloques.append("- Sin viento moderado/fuerte pronosticado (por debajo de 45 km/h).")

        if not riesgo_alto:
            bloques.append(f"- Sin viento de riesgo alto (menor a {UMBRAL_ALTO_KMH} km/h).")

        if not ciclones:
            bloques.append("- Sin huracanes/tormentas activas en el litoral (Golfo, Caribe, Pacifico).")

        enviar_telegram("\n\n".join(bloques))
    else:
        print(f"No es hora de reporte de rutina (hora UTC actual: {hora_actual_utc}).")

    guardar_estado(estado)


if __name__ == "__main__":
    revisar_y_alertar()
