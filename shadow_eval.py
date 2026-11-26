"""Evaluacion tecnica aislada. Solo puntos publicos del catalogo original.
No envia Telegram, no lee secretos, no escribe estado de produccion.
"""
import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter
import time
import requests
from alerta_viento import extraer_meteorologia, cargar_municipios, dato_viento_valido
from prioridades_sucursales import clasificar

URL="https://api.open-meteo.com/v1/forecast"
FUENTE="Open-Meteo (https://open-meteo.com/); datos CC BY 4.0; evaluacion tecnica"
def legado(h):
    # Reproduce el extractor de main sobre las mismas 18 horas.
    v=r=0
    for i,_ in enumerate(h.get("time",[])):
        vs=h.get("wind_speed_10m",[])
        rs=h.get("wind_gusts_10m",[])
        a=vs[i] if i<len(vs) else 0
        b=rs[i] if i<len(rs) else a
        a=0 if a is None else a
        b=a if b is None else b
        if max(a,b)>max(v,r): v,r=a,b
    return max(v,r)

def banda(w):
    return "SIN DATOS" if w is None else "ALTO" if w>=60 else "MODERADO" if w>=45 else "NORMAL"

def analizar(clave, info, dato, hora):
    fila={"punto":clave,"ciudad":info["ciudad"],"estado":info["estado"],
          "lat":info["lat"],"lon":info["lon"],"legado_kmh":None,"nuevo_kmh":None,
          "lluvia_previa":None,"lluvia_futura":None,"error":None}
    try:
        h=dato["hourly"]
        u=dato["hourly_units"]
        if u.get("wind_speed_10m")!="km/h" or u.get("wind_gusts_10m")!="km/h" or u.get("precipitation")!="mm":
            raise ValueError("Unidades inesperadas")
        if dato.get("utc_offset_seconds")!=0:
            raise ValueError("Desfase horario inesperado")
        central=datetime.fromisoformat(h["time"][24]).replace(tzinfo=timezone.utc)
        if central!=hora:
            raise ValueError("Ventana fuera del corte UTC")
        m=extraer_meteorologia(dato)
        recorte={k:h.get(k,[])[24:42] for k in ("time","wind_speed_10m","wind_gusts_10m")}
        # Conservar la salida historica incluso si el motor estricto rechaza nulos.
        if all(x is None or dato_viento_valido(x) for k in ("wind_speed_10m","wind_gusts_10m") for x in recorte[k]):
            fila["legado_kmh"]=legado(recorte)
        fila["nuevo_kmh"]=max(m["viento"][:2]) if m["viento"] else None
        fila["lluvia_previa"]=m["lluvia_previa"]
        fila["lluvia_futura"]=m["lluvia_futura"]
    except (ValueError,KeyError,IndexError,TypeError,AttributeError):
        fila["error"]="Respuesta ausente o invalida"
    fila["banda_legado"]=banda(fila["legado_kmh"])
    fila["banda_nueva"]=banda(fila["nuevo_kmh"])
    fila["evento"],fila["incompleto"]=clasificar({"maximo":fila["nuevo_kmh"],"lluvia_previa":fila["lluvia_previa"],"lluvia_futura":fila["lluvia_futura"]})
    fila["coincide"]=None if fila["nuevo_kmh"] is None or fila["legado_kmh"] is None else fila["banda_legado"]==fila["banda_nueva"]
    return fila

def consultar(lote):
    p={"latitude":",".join(str(v["lat"]) for _,v in lote),"longitude":",".join(str(v["lon"]) for _,v in lote),
       "hourly":"wind_speed_10m,wind_gusts_10m,precipitation","wind_speed_unit":"kmh",
       "precipitation_unit":"mm","timezone":"UTC","past_hours":24,"forecast_hours":25}
    r=requests.get(URL,params=p,timeout=25)
    r.raise_for_status()
    datos=r.json()
    if isinstance(datos,dict): datos=[datos]
    if not isinstance(datos,list) or len(datos)!=len(lote): raise ValueError("Lote incompleto")
    return datos

def ejecutar(municipios, consulta=consultar, pausa=1.5, ahora=None):
    inicio=ahora or datetime.now(timezone.utc)
    hora=inicio.replace(minute=0,second=0,microsecond=0)
    filas=[]
    items=list(municipios.items())
    for i in range(0,len(items),10):
        lote=items[i:i+10]
        try:
            datos=consulta(lote)
            if len(datos)!=len(lote): raise ValueError("Lote incompleto")
        except (requests.RequestException,ValueError,TypeError):
            datos=[{} for _ in lote]
        filas.extend(analizar(k,v,d,hora) for (k,v),d in zip(lote,datos))
        if pausa: time.sleep(pausa)
    comparable=[f for f in filas if f["coincide"] is not None]
    return {"version":1,"modo":"EVALUACION_REAL","fuente":FUENTE,
            "corte_utc":inicio.isoformat(),"hora_modelo_utc":hora.isoformat(),
            "fin_utc":datetime.now(timezone.utc).isoformat(),
            "comparacion":"Extractores sobre respuesta identica; no confirma mensajes Telegram entregados",
            "resumen":{"puntos":len(filas),"comparables":len(comparable),
                       "coincidentes":sum(f["coincide"] for f in comparable),
                       "incompletos":sum(f["incompleto"] for f in filas),
                       "eventos":dict(Counter(f["evento"] for f in filas)),
                       "viento_moderado":sum(f["banda_nueva"]=="MODERADO" for f in filas)},
            "localidades":filas}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--evaluacion-real",action="store_true",required=True)
    p.add_argument("--salida",required=True)
    args=p.parse_args()
    resultado=ejecutar(cargar_municipios())
    Path(args.salida).write_text(json.dumps(resultado,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps(resultado["resumen"],ensure_ascii=False))
    if not resultado["resumen"]["comparables"]: raise SystemExit("Sin cobertura comparable; revisar artefacto")
