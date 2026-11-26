import json
import tempfile
import unittest
from pathlib import Path
from prioridades_sucursales import clasificar, prioridad, evaluar, por_asesor, resumen, cargar_catalogo

class Prioridades(unittest.TestCase):
    def test_matriz(self):
        for evento, esperadas in {
            "VIENTO ALTO":("ALTA","MEDIA","MEDIA"),
            "LLUVIA ACUMULADA":("MEDIA","ALTA","PREVENTIVA"),
            "VIENTO Y LLUVIA":("ALTA","ALTA","MEDIA"),
            "VIENTO CRITICO":("PRIORITARIA",)*3,
        }.items():
            for estructura, p in zip(("TORRE","POSTE","ADOSADO"),esperadas):
                self.assertEqual(prioridad(evento,estructura),p)
    def test_critico_precede(self):
        self.assertEqual(clasificar({"maximo":90,"lluvia_previa":40,"lluvia_futura":20}),("VIENTO CRITICO",False))
    def test_combina(self):
        self.assertEqual(clasificar({"maximo":60,"lluvia_previa":10,"lluvia_futura":10.1}),("VIENTO Y LLUVIA",False))
    def test_ventanas_independientes(self):
        self.assertEqual(clasificar({"maximo":59,"lluvia_previa":6,"lluvia_futura":6}),("SIN ALERTA",False))
    def test_umbral_lluvia(self):
        self.assertEqual(clasificar({"maximo":59,"lluvia_previa":10.1,"lluvia_futura":0}),("LLUVIA ACUMULADA",False))
    def test_dato_faltante(self):
        self.assertEqual(clasificar({"maximo":0}),("SIN DATOS",True))
        self.assertEqual(clasificar({"maximo":75}),("VIENTO ALTO",True))
        self.assertEqual(clasificar({"lluvia_previa":20}),("LLUVIA ACUMULADA",True))
    def test_invalidos(self):
        self.assertEqual(clasificar({"maximo":float("nan"),"lluvia_previa":-1,"lluvia_futura":True}),("SIN DATOS",True))
    def test_sin_estructura(self):
        self.assertEqual(prioridad("VIENTO Y LLUVIA","SIN CLASIFICAR"),"REVISAR")
        self.assertEqual(prioridad("VIENTO CRITICO","SIN CLASIFICAR"),"PRIORITARIA")
        self.assertEqual(prioridad("VIENTO CRITICO","NO APLICA"),"NO APLICA")
    def test_ruteo(self):
        registros=[{"sucursal":"001","asesor":"A","estructura":"TORRE","punto":"P"},
                   {"sucursal":"002","asesor":"B","estructura":"ADOSADO","punto":"P"},
                   {"sucursal":"003","asesor":"B","estructura":"POSTE","punto":"Q"}]
        r=evaluar(registros,{"P":{"maximo":80,"lluvia_previa":20,"lluvia_futura":0}})
        self.assertEqual([e["prioridad"] for e in r],["ALTA","MEDIA","SIN DATOS"])
        self.assertEqual(len(por_asesor(r)["B"]),2)
        self.assertEqual(resumen(r)["cobertura_incompleta"],1)
    def test_carga(self):
        registro={"sucursal":"001","nombre":"Ejemplo","asesor":"A","estructura":"POSTE",
                  "punto":"P","calidad":"APROXIMADA","lat":19.1,"lon":-96.1}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"privado.json"
            p.write_text(json.dumps({"sucursales":[registro]}))
            self.assertEqual(cargar_catalogo(p)[0]["sucursal"],"001")
            p.write_text(json.dumps({"sucursales":[registro,registro]}))
            with self.assertRaises(ValueError):
                cargar_catalogo(p)
            p.write_text(json.dumps({"sucursales":[dict(registro,lon=96.1)]}))
            with self.assertRaises(ValueError):
                cargar_catalogo(p)
