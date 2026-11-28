"""Control estricto de piloto. Sin red, ni secretos ni envio de alertas."""
import argparse,json,os
from datetime import datetime,timedelta,timezone
from pathlib import Path

N=16
PASO=timedelta(minutes=90)
BASE=datetime(2026,1,1,0,0,tzinfo=timezone.utc)
DATA=Path("docs/data")
STATE=DATA/"pilot.json"

def utc(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)
def iso(d): return d.isoformat()
def siguiente(ahora):
    return BASE + ((ahora-BASE)//PASO+1)*PASO
def nuevo(ahora):
    inicio=siguiente(ahora)
    return {"version":1,"estado":"ACTIVO","inicio_utc":iso(inicio),
            "fin_utc":iso(inicio+N*PASO),"intervalo_min":90,"objetivo":N,
            "fuente":"Open-Meteo, datos CC BY 4.0; evaluacion tecnica",
            "cortes":[]}
def plan(s,ahora):
    if not s or s["estado"]!="ACTIVO": return None
    inicio,fin=utc(s["inicio_utc"]),utc(s["fin_utc"])
    if ahora<inicio or ahora>=fin: return None
    i=int((ahora-inicio)//PASO)
    if not 0<=i<N or any(c["slot"]==i for c in s["cortes"]): return None
    return i
def leer():
    return json.loads(STATE.read_text()) if STATE.exists() else None
def guardar(s):
    DATA.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(s,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
def salida(**kw):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"],"a") as f:
            for k,v in kw.items(): f.write(f"{k}={v}\n")
    print(kw)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("accion",choices=["preparar","iniciar","iniciar24","detener","cron","cerrar"])
    p.add_argument("--slot",type=int)
    p.add_argument("--archivo",default="shadow_corte.json")
    a=p.parse_args()
    ahora=datetime.now(timezone.utc)
    s=leer()
    if a.accion=="preparar":
        salida(consultar="false",publicar="true")
        return
    if a.accion=="iniciar24":
        archivo=DATA/"pilot-36h-archivado.json"
        if not s or s.get("estado")!="DETENIDO" or s.get("objetivo")!=24 or s.get("cortes") or not archivo.exists() or json.loads(archivo.read_text())!=s:
            raise SystemExit("Piloto previo no archivado o piloto 24 h ya iniciado")
        guardar(nuevo(ahora))
        salida(consultar="false",publicar="true")
        return
    if a.accion=="iniciar":
        if s: raise SystemExit("Ya existe un piloto. No reiniciar ni sobrescribir historial.")
        guardar(nuevo(ahora))
        salida(consultar="false",publicar="true")
        return
    if a.accion=="detener":
        if not s: raise SystemExit("No existe piloto")
        s["estado"]="DETENIDO";guardar(s)
        salida(consultar="false",publicar="true")
        return
    if a.accion=="cron":
        if s and s["estado"]=="ACTIVO" and ahora>=utc(s["fin_utc"]):
            s["estado"]="FINALIZADO";guardar(s)
            salida(consultar="false",publicar="true")
            return
        i=plan(s,ahora)
        if i is None:
            salida(consultar="false",publicar="false")
            return
        s["cortes"].append({"slot":i,"programado_utc":iso(utc(s["inicio_utc"])+i*PASO),
                           "intento_utc":iso(ahora),"estado":"RESERVADO"})
        guardar(s)
        salida(consultar="true",publicar="true",slot=i)
        return
    c=next(c for c in s["cortes"] if c["slot"]==a.slot and c["estado"]=="RESERVADO")
    try:
        d=json.loads(Path(a.archivo).read_text())
        if d.get("modo")!="EVALUACION_REAL" or len(d.get("localidades",[]))!=214: raise ValueError("Corte invalido")
        if not utc(s["inicio_utc"])<=utc(d["corte_utc"])<utc(s["fin_utc"]): raise ValueError("Corte fuera de ventana")
        nombre=f"corte-{a.slot:02d}.json"
        (DATA/nombre).write_text(json.dumps(d,ensure_ascii=False,allow_nan=False),encoding="utf-8")
        c.update(estado="COMPLETO" if d["resumen"]["incompletos"]==0 else "PARCIAL",archivo=nombre,resumen=d["resumen"])
    except (OSError,ValueError,KeyError,TypeError):
        c["estado"]="FALLIDO"
    c["fin_utc"]=iso(ahora);guardar(s)

if __name__=="__main__": main()
