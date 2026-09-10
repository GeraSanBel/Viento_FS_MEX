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
import time
from datetime import datetime, timezone
import requests

# ============ CONFIGURA ESTO ============

# Nota: ya no se usa OpenWeatherMap para el pronostico (se cambio a Open-Meteo,
# gratuito y sin necesidad de API key). Si en el futuro se vuelve a necesitar
# geocodificar nuevos municipios, esa parte SI sigue usando OWM_API_KEY
# (ver geocode_municipios.py).
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8697170500:AAFc6vJ_VGSreH9B_FraDFrMdQjViEr21DE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8993916335")

# Umbral de riesgo MODERADO (solo aparece en reportes de rutina)
UMBRAL_MODERADO_KMH = 45

# Umbral de riesgo ALTO (avisa de inmediato, a cualquier hora)
UMBRAL_ALTO_KMH = 60

# Cuantas horas hacia adelante revisar en el pronostico (max 120 = 5 dias)
HORAS_A_FUTURO = 18

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
        return {
            "riesgo_alto_activo": False,
            "ultima_notificacion_alta": None,
            "ultima_rutina_enviada": None,
        }


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


def obtener_pronostico_lote(lote):
    """
    Consulta el pronostico de varios municipios en UNA sola llamada a
    Open-Meteo (soporta listas de lat/lon separadas por coma). Esto reduce
    214 llamadas individuales a unas 11 llamadas por lotes, evitando
    timeouts y siendo mucho mas rapido.

    'lote' es una lista de tuplas (clave, info_municipio).
    Regresa un diccionario {clave: (velocidad, rafaga, hora)}.
    """
    lats = ",".join(str(info["lat"]) for _, info in lote)
    lons = ",".join(str(info["lon"]) for _, info in lote)

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": "wind_speed_10m,wind_gusts_10m",
        "wind_speed_unit": "kmh",
        "forecast_hours": HORAS_A_FUTURO,
        "timezone": "UTC",
    }

    ultimo_error = None
    for intento in range(3):  # hasta 3 intentos por lote
        try:
            respuesta = requests.get(url, params=params, timeout=30)
            respuesta.raise_for_status()
            datos = respuesta.json()
            break
        except Exception as error:
            ultimo_error = error
            print(f"  Intento {intento + 1} del lote fallo: {error}")
            time.sleep(3)
    else:
        raise ultimo_error

    # Si es un solo municipio, Open-Meteo regresa un dict; si son varios, una lista
    if isinstance(datos, dict):
        datos = [datos]

    resultados = {}
    for (clave, info), dato_municipio in zip(lote, datos):
        horas = dato_municipio.get("hourly", {})
        tiempos = horas.get("time", [])
        velocidades = horas.get("wind_speed_10m", [])
        rafagas = horas.get("wind_gusts_10m", [])

        peor_velocidad = 0
        peor_rafaga = 0
        peor_hora = None

        for i in range(len(tiempos)):
            velocidad_kmh = velocidades[i] if i < len(velocidades) else 0
            rafaga_kmh = rafagas[i] if i < len(rafagas) else velocidad_kmh
            if max(velocidad_kmh, rafaga_kmh) > max(peor_velocidad, peor_rafaga):
                peor_velocidad = velocidad_kmh
                peor_rafaga = rafaga_kmh
                peor_hora = tiempos[i]

        resultados[clave] = (peor_velocidad, peor_rafaga, peor_hora)

    return resultados


def revisar_y_alertar():
    municipios = cargar_municipios()
    items = list(municipios.items())

    riesgo_moderado = []  # 45-59 km/h
    riesgo_alto = []      # 60+ km/h

    TAMANO_LOTE = 20
    lotes = [items[i:i + TAMANO_LOTE] for i in range(0, len(items), TAMANO_LOTE)]

    for numero_lote, lote in enumerate(lotes, start=1):
        print(f"Consultando lote {numero_lote}/{len(lotes)} ({len(lote)} municipios)...")
        try:
            resultados_lote = obtener_pronostico_lote(lote)
        except Exception as error:
            nombres = ", ".join(clave for clave, _ in lote)
            print(f"Error en el lote completo ({nombres}): {error}")
            continue

        for clave, info in lote:
            if clave not in resultados_lote:
                print(f"  Sin datos para {clave}")
                continue

            velocidad, rafaga, hora = resultados_lote[clave]
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
    clave_rutina_actual = ahora.strftime("%Y-%m-%d-%H")  # identifica esta hora exacta (unica por dia)

    if es_hora_de_rutina:
        if estado.get("ultima_rutina_enviada") == clave_rutina_actual:
            print(f"El reporte de rutina de esta hora ({clave_rutina_actual}) ya se envio. No se repite.")
        else:
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
            estado["ultima_rutina_enviada"] = clave_rutina_actual
    else:
        print(f"No es hora de reporte de rutina (hora UTC actual: {hora_actual_utc}).")

    guardar_estado(estado)


if __name__ == "__main__":
    revisar_y_alertar()
