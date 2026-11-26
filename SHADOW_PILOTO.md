# Piloto shadow · 36 horas (preparacion)

## Corte inicial
- Ejecutar wind_alert.yml manualmente en piloto/shadow-36h, primero sin shadow_real.
- Solo despues de validar las pruebas, habilitar shadow_real para un corte real de evaluacion tecnica.
- Si la interfaz no presenta el parametro, desde una CLI GitHub autenticada:
  gh workflow run wind_alert.yml --repo GeraSanBel/Viento_FS_MEX --ref piloto/shadow-36h -f shadow_real=true
- Descargar el artefacto shadow-corte-<id> y extraer shadow_corte.json. Contiene solo las localidades publicas del catalogo original, datos meteorologicos y resultados de comparacion, no el inventario de sucursales ni secretos.
- Abrir docs/shadow.html en el piloto web y cargar los JSON. No se transmiten archivos ni se conservan tras recarga.

## Alcance y limites
El extractor legado se reproduce sobre las mismas 18 horas del pronostico de Open-Meteo. No es una captura del proceso de produccion ni de sus envios, no usa el respaldo OWM, no compara ciclones. Se excluyen del denominador las filas sin ambos resultados. Los acumulados de lluvia son estimaciones del modelo para 24 h previas y futuras independientes. Se rechazan respuestas con hora distinta al corte para no mezclar ventanas; se registra la falta de datos.

No se publican catálogos privados ni se envian notificaciones. El piloto es evaluacion tecnica, no uso operativo. Open-Meteo atribucion: https://open-meteo.com/ ; datos CC BY 4.0. Revisar limites y licencia antes de un uso empresarial.

## Periodo 36 h (pendiente)
La rama no ejecuta cron automaticamente. Para 24 cortes planeados se requiere un workflow independiente en main o un programador autorizado. Definir inicio, fin estricto y detener ejecuciones al vencer. Comparar cobertura, cortes omitidos, coincidencia en puntos comunes, alertas nuevas y entregas reales de Telegram con hora UTC. La cobertura ampliada y las sucursales requieren una fase privada posterior. No asumir que 100% de coincidencia en una muestra implica 100% de fiabilidad.
