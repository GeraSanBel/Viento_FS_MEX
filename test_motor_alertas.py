"""Pruebas del motor de alertas. No consultan la red ni Telegram."""
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, Mock
import alerta_viento as bot

AHORA = datetime(2026, 11, 26, 18, tzinfo=timezone.utc)


def entrada(maximo, ciudad='VERACRUZ', estado='VERACRUZ'):
    return {'ciudad': ciudad, 'estado': estado, 'velocidad': 25,
            'rafaga': maximo, 'maximo': maximo, 'hora': '2026-11-26T20:00'}


def previo(maximo, horas=1):
    return {'nivel': bot.nivel_viento(maximo), 'maximo': maximo,
            'ultima': (AHORA - timedelta(hours=horas)).isoformat()}


class MotorAlertas(unittest.TestCase):
    def test_umbrales(self):
        for v, n in [(44.9, 'NORMAL'), (45, 'MODERADO'), (59.9, 'MODERADO'),
                     (60, 'ALTO'), (89.9, 'ALTO'), (90, 'CRITICO')]:
            self.assertEqual(bot.nivel_viento(v), n)

    def test_nueva_localidad_independiente(self):
        estado = {'riesgos_notificados': {'a': previo(65)}}
        with patch.object(bot, 'enviar_lista', return_value=True) as enviar:
            bot.notificar_viento({'a': entrada(65), 'b': entrada(70, 'MONTERREY', 'NUEVO LEON')}, estado, AHORA)
        self.assertEqual(enviar.call_count, 1)
        self.assertIn('Monterrey', str(enviar.call_args))
        self.assertNotIn('Veracruz', str(enviar.call_args))
        self.assertIn('b', estado['riesgos_notificados'])

    def test_critico_sin_esperar_recordatorio(self):
        self.assertEqual(bot.motivo_alerta(entrada(90), previo(89), AHORA), 'ESCALAMIENTO')

    def test_incremento_15(self):
        self.assertEqual(bot.motivo_alerta(entrada(82), previo(67), AHORA), 'AUMENTO')
        self.assertIsNone(bot.motivo_alerta(entrada(81), previo(67), AHORA))

    def test_recordatorio_3_horas(self):
        self.assertIsNone(bot.motivo_alerta(entrada(65), previo(65, 2.99), AHORA))
        self.assertEqual(bot.motivo_alerta(entrada(65), previo(65, 3), AHORA), 'RECORDATORIO')

    def test_dato_ausente_conserva_estado(self):
        estado = {'riesgos_notificados': {'a': previo(95)}}
        with patch.object(bot, 'enviar_lista') as enviar:
            bot.notificar_viento({}, estado, AHORA)
        enviar.assert_not_called()
        self.assertEqual(estado['riesgos_notificados']['a']['nivel'], 'CRITICO')

    def test_descenso_y_normalizacion(self):
        estado = {'riesgos_notificados': {'a': previo(95)}}
        with patch.object(bot, 'enviar_lista', return_value=True) as enviar:
            bot.notificar_viento({'a': entrada(52)}, estado, AHORA)
            self.assertEqual(estado['riesgos_notificados']['a']['nivel'], 'MODERADO')
            bot.notificar_viento({'a': entrada(30)}, estado, AHORA)
            self.assertNotIn('a', estado['riesgos_notificados'])
        self.assertEqual(enviar.call_count, 2)

    def test_envio_fallido_reintentable(self):
        estado = {'riesgos_notificados': {}}
        with patch.object(bot, 'enviar_lista', side_effect=[False, True]) as enviar:
            bot.notificar_viento({'a': entrada(95)}, estado, AHORA)
            self.assertNotIn('a', estado['riesgos_notificados'])
            bot.notificar_viento({'a': entrada(95)}, estado, AHORA)
            self.assertIn('a', estado['riesgos_notificados'])
        self.assertEqual(enviar.call_count, 2)

    def test_descenso_fallido_conserva_estado(self):
        estado = {'riesgos_notificados': {'a': previo(95)}}
        with patch.object(bot, 'enviar_lista', return_value=False):
            bot.notificar_viento({'a': entrada(30)}, estado, AHORA)
        self.assertEqual(estado['riesgos_notificados']['a']['nivel'], 'CRITICO')

    def test_cobertura_y_recuperacion(self):
        estado = {}
        with patch.object(bot, 'enviar_telegram', return_value=True) as enviar:
            bot.notificar_cobertura(['NHC/NOAA (ciclones)'], estado, AHORA)
            bot.notificar_cobertura(['NHC/NOAA (ciclones)'], estado, AHORA)
            self.assertEqual(enviar.call_count, 1)
            bot.notificar_cobertura([], estado, AHORA)
            self.assertEqual(enviar.call_count, 2)
            self.assertEqual(estado['fallas_notificadas'], [])

    def test_pronostico_cero_valido(self):
        h = bot.HORAS_A_FUTURO
        dato = {'hourly': {'time': ['2026-11-26T20:00'] * h,
                           'wind_speed_10m': [0] * h, 'wind_gusts_10m': [0] * h}}
        self.assertEqual(bot.extraer_peor_viento(dato)[:2], (0, 0))
        dato['hourly']['wind_gusts_10m'][0] = None
        with self.assertRaises(ValueError):
            bot.extraer_peor_viento(dato)

    def test_pronostico_incompleto(self):
        with self.assertRaises(ValueError):
            bot.extraer_peor_viento({'hourly': {}})

    def test_nhc_error_no_equivale_a_sin_ciclones(self):
        with patch.object(bot.requests, 'get', side_effect=RuntimeError):
            self.assertIsNone(bot.obtener_ciclones_activos())
        r = Mock()
        r.json.return_value = {'activeStorms': []}
        with patch.object(bot.requests, 'get', return_value=r):
            self.assertEqual(bot.obtener_ciclones_activos(), [])
        r.json.return_value = {}
        with patch.object(bot.requests, 'get', return_value=r):
            self.assertIsNone(bot.obtener_ciclones_activos())

    def test_telegram_exige_ok(self):
        r = Mock(status_code=200, text='{}')
        r.json.return_value = {'ok': False}
        with patch.object(bot.requests, 'post', return_value=r):
            self.assertFalse(bot.enviar_telegram('prueba'))
            r.json.return_value = {'ok': True}
            self.assertTrue(bot.enviar_telegram('prueba'))

    def test_resumen_sin_datos_no_dice_sin_riesgo(self):
        self.assertIn('datos incompletos', ' '.join(bot.resumen_por_zona([], {'SUR'})))

    def test_integracion_sin_cobertura_no_borra_riesgos(self):
        estado = {'riesgos_notificados': {'a': previo(95)},
                  'ciclones_activos_ids': ['AL012026'], 'viento_alto_activo': True,
                  'ultima_rutina_enviada': '9999-01-01-00'}
        municipios = {'a': {'ciudad': 'VERACRUZ', 'estado': 'VERACRUZ'}}
        with patch.object(bot, 'cargar_municipios', return_value=municipios), \
             patch.object(bot, 'procesar_lote_adaptativo', return_value={}), \
             patch.object(bot, 'obtener_ciclones_activos', return_value=None), \
             patch.object(bot, 'cargar_estado', return_value=estado), \
             patch.object(bot, 'guardar_estado') as guardar, \
             patch.object(bot, 'enviar_telegram', return_value=True) as enviar, \
             patch.object(bot.time, 'sleep'):
            bot.revisar_y_alertar()
        self.assertEqual(estado['ciclones_activos_ids'], ['AL012026'])
        self.assertEqual(estado['riesgos_notificados']['a']['nivel'], 'CRITICO')
        self.assertTrue(estado['viento_alto_activo'])
        self.assertIn('INCOMPLETOS', str(enviar.call_args))
        guardar.assert_called_once()


if __name__ == '__main__':
    unittest.main()
