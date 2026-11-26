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


class MotorLluvia(unittest.TestCase):
    def dato(self):
        t = datetime(2026, 11, 25, 18)
        return {'hourly': {'time': [(t + timedelta(hours=i)).isoformat() for i in range(49)],
                           'wind_speed_10m': [20]*49, 'wind_gusts_10m': [30]*49,
                           'precipitation': [100]+[0.5]*24+[1]*24}}

    def lluvia(self, previa, futura, viento=65):
        return {'ciudad': 'VERACRUZ', 'estado': 'VERACRUZ',
                'lluvia_previa': previa, 'lluvia_futura': futura, 'maximo': viento}

    def test_ventanas_independientes(self):
        m = bot.extraer_meteorologia(self.dato())
        self.assertEqual(m['lluvia_previa'], 12)
        self.assertEqual(m['lluvia_futura'], 24)

    def test_viento_conserva_18_horas(self):
        dato = self.dato()
        dato['hourly']['wind_gusts_10m'][0] = 200
        dato['hourly']['wind_gusts_10m'][42] = 180
        dato['hourly']['wind_gusts_10m'][41] = 70
        self.assertEqual(bot.extraer_meteorologia(dato)['viento'][1], 70)

    def test_umbral_estricto(self):
        for mm, nivel in [(0,0),(10,0),(10.01,1),(24.99,1),(25,2),(49.99,2),(50,3)]:
            self.assertEqual(bot.nivel_lluvia(mm), nivel)

    def test_no_suma_48_horas(self):
        self.assertEqual(bot.clasificar_lluvia(self.lluvia(7, 7)), 0)

    def test_dato_ausente_no_equivale_a_cero(self):
        dato = self.dato()
        dato['hourly']['precipitation'][30] = None
        m = bot.extraer_meteorologia(dato)
        self.assertIsNone(m['lluvia_futura'])
        self.assertEqual(m['lluvia_previa'], 12)
        self.assertIsNone(bot.clasificar_lluvia(self.lluvia(5, None)))
        self.assertEqual(bot.clasificar_lluvia(self.lluvia(12, None)), 1)

    def test_lluvia_independiente_del_viento(self):
        dato = self.dato()
        dato['hourly']['wind_gusts_10m'][30] = None
        m = bot.extraer_meteorologia(dato)
        self.assertIsNone(m['viento'])
        self.assertEqual(m['lluvia_futura'], 24)

    def test_combinacion(self):
        self.assertEqual(bot.clasificar_combinado(self.lluvia(11, 0, 60)), 2)
        self.assertEqual(bot.clasificar_combinado(self.lluvia(11, 0, 90)), 3)
        self.assertEqual(bot.clasificar_combinado(self.lluvia(11, 0, 59)), 0)
        self.assertEqual(bot.clasificar_combinado(self.lluvia(0, 10, 90)), 0)
        self.assertIsNone(bot.clasificar_combinado(self.lluvia(None, 0, 90)))

    def test_lluvia_aviso_y_recordatorio(self):
        estado = {}
        with patch.object(bot, 'enviar_lista', return_value=True) as envio:
            obs = {'a': self.lluvia(11, 0, 20)}
            bot.notificar_lluvia(obs, estado, AHORA)
            bot.notificar_lluvia(obs, estado, AHORA + timedelta(minutes=90))
            bot.notificar_lluvia(obs, estado, AHORA + timedelta(hours=3))
            self.assertEqual(envio.call_count, 2)
            self.assertEqual(estado['lluvia_notificada']['a']['nivel'], 1)

    def test_lluvia_fallo_envio_y_datos_ausentes(self):
        estado = {}
        with patch.object(bot, 'enviar_lista', side_effect=[False, True]) as envio:
            obs = {'a': self.lluvia(50, 0)}
            bot.notificar_lluvia(obs, estado, AHORA)
            self.assertNotIn('a', estado['lluvia_notificada'])
            bot.notificar_lluvia(obs, estado, AHORA)
            bot.notificar_lluvia({'a': self.lluvia(None, 0)}, estado, AHORA)
            bot.notificar_lluvia({'a': self.lluvia(None, 12)}, estado, AHORA)
            self.assertEqual(estado['lluvia_notificada']['a']['nivel'], 3)
            self.assertEqual(envio.call_count, 2)

    def test_lluvia_normalizacion_confirmada(self):
        estado = {'lluvia_notificada': {'a': {'nivel': 2, 'ultima': AHORA.isoformat()}}}
        with patch.object(bot, 'enviar_lista', return_value=True) as envio:
            bot.notificar_lluvia({'a': self.lluvia(0, 10)}, estado, AHORA)
            self.assertNotIn('a', estado['lluvia_notificada'])
            envio.assert_called_once()

    def test_integracion_lluvia_y_viento(self):
        estado = {'riesgos_notificados': {}, 'ultima_rutina_enviada': '9999-01-01-00'}
        municipios = {'a': {'ciudad': 'VERACRUZ', 'estado': 'VERACRUZ'}}
        meteo = {'a': {'viento': (40, 75, '2026-11-26T20:00'), 'lluvia_previa': 18, 'lluvia_futura': 32}}
        with patch.object(bot, 'cargar_municipios', return_value=municipios), \
             patch.object(bot, 'procesar_lote_adaptativo', return_value=meteo), \
             patch.object(bot, 'obtener_ciclones_activos', return_value=[]), \
             patch.object(bot, 'cargar_estado', return_value=estado), \
             patch.object(bot, 'guardar_estado'), \
             patch.object(bot, 'enviar_lista', return_value=True), \
             patch.object(bot.time, 'sleep'):
            bot.revisar_y_alertar()
        self.assertEqual(estado['lluvia_notificada']['a']['nivel'], 2)
        self.assertEqual(estado['combinado_notificado']['a']['nivel'], 2)
        self.assertEqual(estado['riesgos_notificados']['a']['nivel'], 'ALTO')


if __name__ == '__main__':
    unittest.main()
