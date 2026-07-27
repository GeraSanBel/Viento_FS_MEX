"""
Geocodifica la lista de municipios (con su estado) usando la API de
OpenWeatherMap (Geocoding API, incluida gratis con la misma API key del clima).

Al incluir el estado en la busqueda, se evita el problema de nombres de
municipios repetidos en distintos estados (ej: "Juarez" existe en varios).

Esto se corre UNA SOLA VEZ para generar municipios_coords.json con las
coordenadas exactas de cada municipio. El script principal de alertas
despues lee ese archivo en vez de buscar coordenadas cada vez.

Requisitos:
    pip install requests

Uso:
    python geocode_municipios.py
"""

import os
import json
import time
import requests

OWM_API_KEY = os.environ.get("OWM_API_KEY", "PON_AQUI_TU_API_KEY_DE_OPENWEATHERMAP")
ARCHIVO_MUNICIPIOS = "municipios_estado.json"
ARCHIVO_SALIDA = "municipios_coords.json"
ARCHIVO_REVISAR = "municipios_para_revisar.txt"

# Pausa entre llamadas para no exceder el limite gratuito de 60/minuto
PAUSA_SEGUNDOS = 1.1


def geocodificar(ciudad, estado):
    """
    Busca un municipio+estado en Mexico usando la Geocoding API de OpenWeatherMap.
    Al incluir el estado en la consulta, el resultado es mucho mas preciso.
    """
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {
        "q": f"{ciudad},{estado},MX",
        "limit": 3,
        "appid": OWM_API_KEY,
    }
    respuesta = requests.get(url, params=params, timeout=15)
    respuesta.raise_for_status()
    resultados = respuesta.json()

    if resultados:
        return resultados

    # Si no encuentra nada con el estado incluido, reintenta solo con el nombre
    params["q"] = f"{ciudad},MX"
    respuesta = requests.get(url, params=params, timeout=15)
    respuesta.raise_for_status()
    return respuesta.json()


def main():
    with open(ARCHIVO_MUNICIPIOS, "r", encoding="utf-8") as f:
        municipios = json.load(f)

    resultados = {}
    para_revisar = []

    for i, item in enumerate(municipios, start=1):
        ciudad = item["CIUDAD"]
        estado = item["ESTADO"]
        clave = f"{ciudad} ({estado})"

        print(f"[{i}/{len(municipios)}] Buscando: {clave}")
        try:
            candidatos = geocodificar(ciudad, estado)
        except Exception as error:
            print(f"  Error: {error}")
            para_revisar.append(f"{clave} -> ERROR: {error}")
            time.sleep(PAUSA_SEGUNDOS)
            continue

        if not candidatos:
            print(f"  No encontrado")
            para_revisar.append(f"{clave} -> NO ENCONTRADO")
        else:
            c = candidatos[0]
            resultados[clave] = {
                "ciudad": ciudad,
                "estado": estado,
                "lat": c["lat"],
                "lon": c["lon"],
            }
            print(f"  OK: ({c['lat']:.4f}, {c['lon']:.4f})")

        time.sleep(PAUSA_SEGUNDOS)

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)

    with open(ARCHIVO_REVISAR, "w", encoding="utf-8") as f:
        f.write("\n".join(para_revisar) if para_revisar else "Todos los municipios se encontraron correctamente.")

    print(f"\nListo. {len(resultados)} de {len(municipios)} municipios geocodificados.")
    print(f"Guardado en: {ARCHIVO_SALIDA}")
    print(f"Casos a revisar: {len(para_revisar)} (ver {ARCHIVO_REVISAR})")


if __name__ == "__main__":
    main()
