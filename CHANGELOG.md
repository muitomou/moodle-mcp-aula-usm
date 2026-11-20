# Registro de cambios

Todos los cambios importantes de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto
usa [versionado semántico](https://semver.org/lang/es/).

## [Sin publicar]

## [0.1.0] - 2026-11-20

Primera versión pública.

### Añadido

- Servidor MCP por stdio con 11 herramientas de solo lectura sobre Moodle: `listar_ramos`,
  `proximas_entregas`, `ver_tarea`, `leer_material`, `contenido_ramo`, `descargar_material`,
  `ver_notas`, `avisos_y_foros`, `leer_discusion`, `contactos_ramo` y `vincular_carpeta`.
- Uso de la API REST oficial con el servicio de la app móvil (`moodle_mobile_app`), sin
  *scraping*.
- `aula-mcp-login`: obtiene el token con el flujo de la app móvil (sirve con inicio de sesión
  de Microsoft) y lo guarda con permisos `600`.
- `aula-mcp-check` (prueba de humo) y `aula-mcp-desktop` (registro en Claude Desktop).
- Detección de la carpeta local de cada ramo por nombre, sigla o abreviatura.
- Lectura de PDFs y archivos de texto con rangos de páginas.
- Documentación para Cursor, VS Code (Copilot en modo agente) y Gemini CLI.
- CI con ruff (lint y formato), pytest en Python 3.10 a 3.13 y reporte de cobertura.

### Corregido

- Mensajes claros para timeouts y fallas de conexión, en consultas y descargas.
- Un token vencido (`accessexception`) ahora indica que hay que renovarlo con
  `aula-mcp-login`, y se avisa cuando el sitio está en mantención.
- Las descargas detectan los errores en formato JSON de `webservice/pluginfile.php`, que
  antes podían guardarse como si fueran el archivo pedido.
- Un token vencido ya no queda oculto detrás de las consultas de respaldo (vigencia por
  fechas, contactos del curso, estado de entrega) ni se repite como error en cada descarga.
- Los errores de disco al guardar una descarga se informan por archivo sin cortar el resto.
- Si `AULA_URL` redirige (por ejemplo, `http://` → `https://`), se indica la URL correcta en
  vez de responder "token inválido".
- `leer_material` explica cuando se piden páginas que no existen en el PDF.

[Sin publicar]: https://github.com/muitomou/moodle-mcp-aula-usm/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/muitomou/moodle-mcp-aula-usm/releases/tag/v0.1.0
