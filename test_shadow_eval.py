import unittest
from datetime import datetime,timezone,timedelta
from shadow_eval import analizar, ejecutar, banda
H=datetime(2026,11,26,18,tzinfo=timezone.utc)
INFO={"ciudad":"Demo","estado":"Demo","lat":19.0,"lon":-96.0}
def muestra(v=75,p=0):
    return {"utc_offset_seconds":0,"hourly_units":{"wind_speed_10m":"km/h","wind_gusts_10m":"km/h","precipitation":"mm"},
            "hourly":{"time":[(H+timedelta(hours=i-24)).isoformat(timespec="minutes").replace("+00:00","") for i in range(49)],
                      "wind_speed_10m":[10]*49,"wind_gusts_10m":[v]*49,"precipitation":[p]*49}}
class Shadow(unittest.TestCase):
    def test_igual(self):
        r=analizar("D",INFO,muestra(),H)
        self.assertEqual((r["legado_kmh"],r["nuevo_kmh"],r["evento"],r["coincide"]),(75,75,"VIENTO ALTO",True))
    def test_lluvia(self):
        r=analizar("D",INFO,muestra(75,1),H)
        self.assertEqual((r["lluvia_previa"],r["lluvia_futura"],r["evento"]),(24,24,"VIENTO Y LLUVIA"))
    def test_critico(self):
        self.assertEqual(analizar("D",INFO,muestra(90,1),H)["evento"],"VIENTO CRITICO")
    def test_viento_moderado(self):
        r=analizar("D",INFO,muestra(45),H)
        self.assertEqual(r["banda_nueva"],"MODERADO")
    def test_faltante(self):
        d=muestra();d["hourly"]["wind_gusts_10m"][30]=None
        r=analizar("D",INFO,d,H)
        self.assertIsNone(r["nuevo_kmh"]);self.assertIsNone(r["coincide"])
    def test_hora(self):
        self.assertTrue(analizar("D",INFO,muestra(),H+timedelta(hours=1))["incompleto"])
    def test_unidades(self):
        d=muestra();d["hourly_units"]["wind_gusts_10m"]="m/s"
        self.assertIsNone(analizar("D",INFO,d,H)["nuevo_kmh"])
    def test_lote(self):
        r=ejecutar({"A":INFO,"B":INFO},consulta=lambda lote:[muestra(),muestra(45)],pausa=0,ahora=H)
        self.assertEqual(r["resumen"]["coincidentes"],2)
    def test_fallo_lote(self):
        r=ejecutar({"A":INFO},consulta=lambda lote:[],pausa=0,ahora=H)
        self.assertEqual(r["resumen"]["incompletos"],1);self.assertEqual(r["resumen"]["comparables"],0)
