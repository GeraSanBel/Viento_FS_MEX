"""Motor de prioridades por sucursal. Prototipo sin red ni envio de mensajes.

El inventario privado se carga desde un archivo externo al repositorio.
Las observaciones se reciben por identificador de punto meteorologico.
No confirma estabilidad estructural ni sustituye avisos oficiales.
"""
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from alerta_viento import UMBRAL_ALTO_KMH, UMBRAL_CRITICO_KMH, UMBRAL_LLUVIA_MM

MATRIZ = {
    "VIENTO ALTO": {"TORRE":"ALTA","POSTE":"MEDIA","ADOSADO":"MEDIA"},
    "LLUVIA ACUMULADA": {"TORRE":"MEDIA","POSTE":"ALTA","ADOSADO":"PREVENTIVA"},
    "VIENTO Y LLUVIA": {"TORRE":"ALTA","POSTE":"ALTA","ADOSADO":"MEDIA"},
    "VIENTO CRITICO": {"TORRE":"PRIORITARIA","POSTE":"PRIORITARIA","ADOSADO":"PRIORITARIA"},
}
ORDEN = {"PRIORITARIA":0,"ALTA":1,"MEDIA":2,"PREVENTIVA":3,"REVISAR":4,
         "SIN DATOS":5,"SIN ALERTA":6,"NO APLICA":7}

def numero(v):
    return v if isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v) and v >= 0 else None

def clasificar(observacion):
    """Conserva incertidumbre si falta alguna ventana. Nunca suma 48 h."""
    viento=numero(observacion.get("maximo"))
    lluvia=[numero(observacion.get(k)) for k in ("lluvia_previa","lluvia_futura")]
    exceso=any(v is not None and v > UMBRAL_LLUVIA_MM for v in lluvia)
    incompleto=viento is None or any(v is None for v in lluvia)
    if viento is not None and viento >= UMBRAL_CRITICO_KMH:
        evento="VIENTO CRITICO"
    elif viento is not None and viento >= UMBRAL_ALTO_KMH and exceso:
        evento="VIENTO Y LLUVIA"
    elif viento is not None and viento >= UMBRAL_ALTO_KMH:
        evento="VIENTO ALTO"
    elif exceso:
        evento="LLUVIA ACUMULADA"
    else:
        evento="SIN DATOS" if incompleto else "SIN ALERTA"
    return evento, incompleto

def prioridad(evento, estructura):
    if estructura=="NO APLICA":
        return "NO APLICA"
    if evento in ("SIN ALERTA","SIN DATOS"):
        return evento
    if evento=="VIENTO CRITICO":
        return "PRIORITARIA"
    return MATRIZ[evento].get(estructura,"REVISAR")

def cargar_catalogo(ruta):
    datos=json.loads(Path(ruta).read_text(encoding="utf-8"))
    registros=datos["sucursales"]
    vistos=set()
    for r in registros:
        for k in ("sucursal","nombre","asesor","estructura","punto","calidad"):
            if not isinstance(r.get(k),str) or not r[k].strip():
                raise ValueError("Campo obligatorio ausente o invalido: "+k)
        if r["sucursal"] in vistos:
            raise ValueError("Sucursal duplicada")
        vistos.add(r["sucursal"])
        for k, lo, hi in (("lat",14,33),("lon",-119,-86)):
            v=r.get(k)
            if not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) or not lo<=v<=hi:
                raise ValueError("Coordenada fuera de rango")
    return registros

def evaluar(registros, observaciones):
    """Una fila por sucursal, sin duplicados por fenomenos simultaneos.
    No envia alertas ni modifica estado persistente.
    """
    salidas=[]
    for r in registros:
        obs=observaciones.get(r["punto"],{})
        evento,incompleto=clasificar(obs)
        salidas.append({**r,"evento":evento,"prioridad":prioridad(evento,r["estructura"]),
                        "cobertura_incompleta":incompleto,
                        "viento_kmh":numero(obs.get("maximo")),
                        "lluvia_previa_mm":numero(obs.get("lluvia_previa")),
                        "lluvia_futura_mm":numero(obs.get("lluvia_futura"))})
    return sorted(salidas,key=lambda r:(ORDEN[r["prioridad"]],r["asesor"],r["sucursal"]))

def por_asesor(resultados):
    """Segmentacion de resultados, NO constituye control de acceso."""
    grupos=defaultdict(list)
    for r in resultados:
        grupos[r["asesor"]].append(r)
    return dict(grupos)

def resumen(resultados):
    return {"total":len(resultados),
            "prioridades":dict(Counter(r["prioridad"] for r in resultados)),
            "cobertura_incompleta":sum(r["cobertura_incompleta"] for r in resultados)}

if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description="Evaluacion local; no publicar datos privados")
    parser.add_argument("catalogo")
    parser.add_argument("observaciones")
    parser.add_argument("salida")
    args=parser.parse_args()
    resultados=evaluar(cargar_catalogo(args.catalogo),json.loads(Path(args.observaciones).read_text(encoding="utf-8")))
    Path(args.salida).write_text(json.dumps({"resumen":resumen(resultados),"sucursales":resultados},ensure_ascii=False,indent=2),encoding="utf-8")
