"""
Reintenta geocodificar SOLO los municipios que fallaron la primera vez,
usando variantes del nombre (ej: "CD." -> "CIUDAD", o quitando el
sufijo despues de "DE"). No vuelve a consultar los que ya funcionaron.

Requisitos:
    pip install requests

Uso:
    python geocode_reintento.py
"""

import os
import json
import time
import requests

OWM_API_KEY = os.environ.get("OWM_API_KEY", "PON_AQUI_TU_API_KEY_DE_OPENWEATHERMAP")
ARCHIVO_MUNICIPIOS = "municipios_estado.json"
ARCHIVO_COORDS = "municipios_coords.json"
ARCHIVO_REVISAR = "municipios_para_revisar.txt"

PAUSA_SEGUNDOS = 1.1


def geocodificar_variante(query):
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {"q": query, "limit": 3, "appid": OWM_API_KEY}
    respuesta = requests.get(url, params=params, timeout=15)
    respuesta.raise_for_status()
    return respuesta.json()


def generar_variantes(ciudad, estado):
    """Genera varias formas alternativas de buscar el mismo municipio."""
    variantes = []

    # Variante 1: expandir "CD." a "CIUDAD"
    if ciudad.upper().startswith("CD."):
        nombre_expandido = "CIUDAD" + ciudad[3:]
        variantes.append(f"{nombre_expandido},{estado},MX")
        variantes.append(f"{nombre_expandido},MX")

    # Variante 2: nombre completo sin estado
    variantes.append(f"{ciudad},MX")

    # Variante 3: solo la primera parte antes de " DE " (nombres compuestos)
    if " DE " in ciudad:
        base = ciudad.split(" DE ")[0].strip()
        variantes.append(f"{base},{estado},MX")
        variantes.append(f"{base},MX")

    return variantes


def main():
    with open(ARCHIVO_MUNICIPIOS, "r", encoding="utf-8") as f:
        municipios = json.load(f)

    with open(ARCHIVO_COORDS, "r", encoding="utf-8") as f:
        coords = json.load(f)

    pendientes = []
    for item in municipios:
        clave = f"{item['CIUDAD']} ({item['ESTADO']})"
        if clave not in coords:
            pendientes.append(item)

    print(f"Municipios pendientes por resolver: {len(pendientes)}")

    aun_sin_resolver = []

    for item in pendientes:
        ciudad = item["CIUDAD"]
        estado = item["ESTADO"]
        clave = f"{ciudad} ({estado})"
        print(f"\nReintentando: {clave}")

        encontrado = False
        for variante in generar_variantes(ciudad, estado):
            print(f"  Probando: {variante}")
            try:
                candidatos = geocodificar_variante(variante)
            except Exception as error:
                print(f"    Error: {error}")
                time.sleep(PAUSA_SEGUNDOS)
                continue

            if candidatos:
                c = candidatos[0]
                coords[clave] = {
                    "ciudad": ciudad,
                    "estado": estado,
                    "lat": c["lat"],
                    "lon": c["lon"],
                }
                print(f"    OK: ({c['lat']:.4f}, {c['lon']:.4f})")
                encontrado = True
                break

            time.sleep(PAUSA_SEGUNDOS)

        if not encontrado:
            print(f"  Sigue sin encontrarse tras todas las variantes.")
            aun_sin_resolver.append(clave)

        time.sleep(PAUSA_SEGUNDOS)

    with open(ARCHIVO_COORDS, "w", encoding="utf-8") as f:
        json.dump(coords, f, ensure_ascii=False, indent=2)

    with open(ARCHIVO_REVISAR, "w", encoding="utf-8") as f:
        if aun_sin_resolver:
            f.write("\n".join(f"{c} -> SIGUE SIN ENCONTRARSE, requiere coordenadas manuales" for c in aun_sin_resolver))
        else:
            f.write("Todos los municipios se encontraron correctamente.")

    print(f"\n\nResuelto: {len(pendientes) - len(aun_sin_resolver)} de {len(pendientes)} pendientes.")
    print(f"Total en municipios_coords.json: {len(coords)} de {len(municipios)}")
    print(f"Aun sin resolver: {len(aun_sin_resolver)}")


if __name__ == "__main__":
    main()
