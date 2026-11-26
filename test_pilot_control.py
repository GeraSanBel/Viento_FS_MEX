import unittest
from datetime import datetime,timedelta,timezone
from pilot_control import nuevo,plan,siguiente,PASO
class Control(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,26,21,10,tzinfo=timezone.utc)
        self.s=nuevo(self.now)
        self.start=datetime.fromisoformat(self.s["inicio_utc"])
    def test_alinea(self): self.assertEqual(self.start.minute,37);self.assertEqual(self.start.hour,21)
    def test_no_antes(self): self.assertIsNone(plan(self.s,self.start-timedelta(seconds=1)))
    def test_24(self): self.assertEqual([plan(self.s,self.start+i*PASO) for i in range(24)],list(range(24)))
    def test_limite(self): self.assertIsNone(plan(self.s,self.start+24*PASO))
    def test_dedup(self):
        self.s["cortes"].append({"slot":0});self.assertIsNone(plan(self.s,self.start+timedelta(minutes=5)))
    def test_stop(self):
        self.s["estado"]="DETENIDO";self.assertIsNone(plan(self.s,self.start))
    def test_sin_inicio(self): self.assertIsNone(plan(None,self.start))
