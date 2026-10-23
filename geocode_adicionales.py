"""
Agrega municipios faltantes a municipios_coords.json (sin tocar los que ya existen).

Para evitar coordenadas equivocadas con nombres repetidos en varios estados
(por ejemplo "La Paz" o "Lerma"), solo se acepta un resultado de OpenWeatherMap
si cae dentro de la caja geografica (lat/lon) del estado esperado.

Los casos que no se puedan validar NO se agregan: quedan listados en
adicionales_para_revisar.txt para resolverlos a mano.

Requisitos:
    pip install requests
"""

import os
import json
import time
import unicodedata
import requests

OWM_API_KEY = os.environ.get('OWM_API_KEY', 'PON_AQUI_TU_API_KEY')
ARCHIVO_COORDS = 'municipios_coords.json'
ARCHIVO_REVISAR = 'adicionales_para_revisar.txt'
PAUSA_SEGUNDOS = 1.1

# Caja geografica aproximada del Estado de Mexico (lat_min, lat_max, lon_min, lon_max)
CAJA_EDOMEX = (18.3, 20.4, -100.7, -98.5)
ESTADO = 'ESTADO DE MEXICO'

# (nombre como aparecera en los mensajes, [consultas a probar en orden])
ADICIONES = [
    ('ACOLMAN', ['Acolman']),
    ('CHALCO', ['Chalco de Diaz Covarrubias', 'Chalco']),
    ('CHICOLOAPAN DE JUAREZ', ['Chicoloapan de Juarez', 'Chicoloapan']),
    ('CHIMALHUACAN', ['Chimalhuacan']),
    ('CIUDAD ADOLFO LOPEZ MATEOS', ['Ciudad Adolfo Lopez Mateos', 'Atizapan de Zaragoza', 'Atizapan']),
    ('COACALCO DE BERRIOZABAL', ['Coacalco de Berriozabal', 'Coacalco']),
    ('CUAUTITLAN IZCALLI', ['Cuautitlan Izcalli']),
    ('ECATEPEC', ['Ecatepec de Morelos', 'Ecatepec']),
    ('HUIXQUILUCAN', ['Huixquilucan']),
    ('IXTAPALUCA', ['Ixtapaluca']),
    ('IXTAPAN DE LA SAL', ['Ixtapan de la Sal']),
    ('JUCHITEPEC', ['Juchitepec de Mariano Riva Palacio', 'Juchitepec']),
    ('LERMA', ['Lerma de Villada', 'Lerma']),
    ('LOS REYES ACAQUILPAN', ['Los Reyes Acaquilpan', 'Los Reyes La Paz', 'La Paz']),
    ('NAUCALPAN', ['Naucalpan de Juarez', 'Naucalpan']),
    ('NEZAHUALCOYOTL', ['Nezahualcoyotl', 'Ciudad Nezahualcoyotl']),
    ('TECAMAC', ['Tecamac de Felipe Villanueva', 'Tecamac']),
    ('TENANGO DEL AIRE', ['Tenango del Aire']),
    ('TEZOYUCA', ['Tezoyuca']),
    ('TLALNEPANTLA DE BAZ', ['Tlalnepantla de Baz', 'Tlalnepantla']),
    ('TOLUCA', ['Toluca de Lerdo', 'Toluca']),
    ('TULTEPEC', ['Tultepec']),
    ('TULTITLAN', ['Tultitlan de Mariano Escobedo', 'Tultitlan']),
    ('VALLE DE BRAVO', ['Valle de Bravo']),
    ('VALLE DE CHALCO', ['Valle de Chalco Solidaridad', 'Valle de Chalco']),
    ('VILLA DEL CARBON', ['Villa del Carbon']),
    ('VILLA NICOLAS ROMERO', ['Villa Nicolas Romero', 'Nicolas Romero']),
    ('ZUMPANGO', ['Zumpango de Ocampo', 'Zumpango']),
]


def normalizar(texto):
    """Minusculas y sin acentos, para comparar nombres de estado."""
    texto = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn').lower().strip()


# Nombres con los que OpenWeatherMap puede devolver al Estado de Mexico.
# Ojo: "Mexico City" / "Ciudad de Mexico" NO estan aqui a proposito (es otra entidad).
NOMBRES_ESTADO_VALIDOS = {'state of mexico', 'estado de mexico', 'mexico state', 'mexico', 'edomex'}


def dentro_de_caja(lat, lon, caja):
    lat_min, lat_max, lon_min, lon_max = caja
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def validar_candidato(c, caja):
    """
    Regresa 'estado' (validado por nombre de estado), 'caja' (solo por ubicacion,
    menos confiable) o None (rechazado).
    La caja rectangular se traslapa con Morelos, CDMX, Hidalgo y Puebla, por eso
    el nombre de estado que devuelve OpenWeatherMap manda cuando existe.
    """
    if not dentro_de_caja(c['lat'], c['lon'], caja):
        return None
    estado_devuelto = c.get('state')
    if estado_devuelto:
        return 'estado' if normalizar(estado_devuelto) in NOMBRES_ESTADO_VALIDOS else None
    return 'caja'


def buscar(consulta):
    url = 'https://api.openweathermap.org/geo/1.0/direct'
    params = {'q': f'{consulta},MX', 'limit': 5, 'appid': OWM_API_KEY}
    respuesta = requests.get(url, params=params, timeout=20)
    respuesta.raise_for_status()
    return respuesta.json()


def resolver(nombre, consultas, caja):
    """Regresa (lat, lon, consulta_usada, tipo_validacion) o None si ningun resultado es valido."""
    for consulta in consultas:
        try:
            candidatos = buscar(consulta)
        except Exception as error:
            print(f'    Error con "{consulta}": {error}')
            time.sleep(PAUSA_SEGUNDOS)
            continue

        # Primero los validados por nombre de estado; si no hay, los validados solo por caja
        validados = [(validar_candidato(c, caja), c) for c in candidatos]
        for tipo, c in validados:
            if tipo == 'estado':
                return c['lat'], c['lon'], consulta, 'estado'
        for tipo, c in validados:
            if tipo == 'caja':
                return c['lat'], c['lon'], consulta, 'caja'
        time.sleep(PAUSA_SEGUNDOS)
    return None


def main():
    with open(ARCHIVO_COORDS, 'r', encoding='utf-8') as f:
        coords = json.load(f)

    agregados = []
    ya_existian = []
    para_revisar = []
    solo_por_caja = []

    for nombre, consultas in ADICIONES:
        clave = f'{nombre} ({ESTADO})'
        if clave in coords:
            ya_existian.append(clave)
            continue

        print(f'Buscando: {clave}')
        resultado = resolver(nombre, consultas, CAJA_EDOMEX)
        if resultado is None:
            print('  NO se pudo validar dentro del estado')
            para_revisar.append(f'{clave} -> sin resultado dentro del Estado de Mexico (probado: {", ".join(consultas)})')
            continue

        lat, lon, usada, tipo = resultado
        coords[clave] = {'ciudad': nombre, 'estado': ESTADO, 'lat': lat, 'lon': lon}
        agregados.append(clave)
        if tipo == 'caja':
            solo_por_caja.append(clave)
        print(f'  OK ({lat:.4f}, {lon:.4f}) usando "{usada}" [validado por {tipo}]')
        time.sleep(PAUSA_SEGUNDOS)

    with open(ARCHIVO_COORDS, 'w', encoding='utf-8') as f:
        json.dump(coords, f, ensure_ascii=False, indent=2)

    lineas = list(para_revisar)
    for clave in solo_por_caja:
        lineas.append(f'{clave} -> AGREGADO pero validado solo por ubicacion (revisar en el mapa que sea el correcto)')
    with open(ARCHIVO_REVISAR, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lineas) if lineas else 'Todos los municipios se agregaron y validaron correctamente.')

    print(f'\nAgregados: {len(agregados)} | Ya existian: {len(ya_existian)} | Por revisar: {len(para_revisar)}')
    print(f'Total en {ARCHIVO_COORDS}: {len(coords)}')


if __name__ == '__main__':
    main()
