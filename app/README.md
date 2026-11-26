# FS Alerta Climática · piloto local

Abrir index.html en un navegador compatible con archivos locales. Los seis registros iniciales son ficticios. Cargar un JSON privado de forma manual mediante el selector. No subir el catálogo real al repositorio ni a un sitio público. El archivo se mantiene en memoria; al recargar se pierde. Los filtros no son autenticación. Los escenarios son simulados, no pronósticos. El mapa es una cuadrícula esquemática sin cartografía. El seguimiento es local y temporal. No se realizan consultas ni se envían alertas.

Pruebas: node app/test_app.cjs. La interfaz web aún no tiene hosting HTTPS, manifiesto, service worker ni push; se incorporarán después de validar el piloto y definir almacenamiento y autenticación privados.
