# Moodle MCP (Aula USM) — Especificación v0.1

Servidor MCP que permite a un asistente (Claude Code, Claude Desktop, …) consultar
un Moodle como estudiante. Pensado para Aula USM, pero sirve para cualquier Moodle
que tenga habilitado el servicio de la app móvil (`moodle_mobile_app`).

## Principios

- **Solo lectura** en v0.1. Las acciones que escriben en Moodle (subir entregas)
  llegan después y siempre con confirmación explícita.
- **Sin secretos en el repo.** El token vive en `~/.config/aula-mcp/config.env`
  (permisos 600) o en variables de entorno.
- **Genérico.** Nada específico de una persona. La URL del Moodle y la carpeta
  local son configurables.
- **Respuestas compactas.** Las herramientas devuelven datos resumidos (sin HTML,
  fechas legibles) para no gastar contexto del modelo.

## Configuración

| Variable | Por defecto | Uso |
|---|---|---|
| `AULA_URL` | `https://aula.usm.cl` | URL base del Moodle |
| `AULA_TOKEN` | — | Token del servicio `moodle_mobile_app` |
| `AULA_DIR_BASE` | — | Carpeta local con una subcarpeta por ramo (opcional) |
| `AULA_CONFIG_DIR` | `~/.config/aula-mcp` | Dónde se guardan `config.env` y `carpetas.json` |

Las variables de entorno tienen prioridad sobre `config.env`.

## Obtener el token: `aula-mcp-login`

1. Genera un `passport` aleatorio y abre
   `AULA_URL/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=…&urlscheme=moodlemobile`.
2. El usuario inicia sesión (Microsoft, en el caso de la USM). Moodle redirige a
   `moodlemobile://token=<base64>`; el usuario copia esa URL y la pega en la terminal.
3. Se decodifica `firma:::token[:::tokenprivado]` y se verifica `firma == md5(AULA_URL + passport)`.
4. Se valida con `core_webservice_get_site_info` y se guarda solo el `token`
   (el token privado se descarta).

Alternativa: `aula-mcp-login --token`, para pegar un token ya existente
(por ejemplo, desde *Preferencias → Claves de seguridad*).

## Identificar un ramo

Las herramientas que reciben `ramo` aceptan el id numérico, el nombre corto, parte
del nombre completo (sin importar tildes ni mayúsculas) o el nombre de la carpeta
local vinculada. Si hay ambigüedad, la herramienta responde con los candidatos.

## Herramientas

| Herramienta | Parámetros | Devuelve | API de Moodle |
|---|---|---|---|
| `listar_ramos` | `incluir_pasados=false` | id, sigla, nombre limpio, paralelos, fechas, carpeta local vinculada | `core_webservice_get_site_info`, `core_enrol_get_users_courses`, `core_course_get_enrolled_courses_by_timeline_classification` |
| `proximas_entregas` | `dias=14`, `ramo?`, `incluir_atrasadas=true` | actividad, ramo, fecha límite, acción pendiente, `recurso_id`, url | `core_calendar_get_action_events_by_timesort` |
| `ver_tarea` | `tarea_id`, `ramo?` | enunciado, adjuntos, fechas (apertura, vencimiento, corte), formato de entrega y estado de la entrega propia (archivos, nota, comentarios) | `mod_assign_get_assignments`, `mod_assign_get_submission_status` |
| `leer_material` | `ramo`, `recurso_id?`, `archivo?`, `paginas?` | texto del archivo (PDF por páginas, máx. ~30 000 caracteres) y ruta local donde quedó guardado | igual que `descargar_material` + extracción con `pypdf` |
| `contenido_ramo` | `ramo` | secciones → recursos (id, tipo, nombre, vencimiento de tareas, archivos con tamaño, incluidos los adjuntos de enunciados) | `core_course_get_contents`, `mod_assign_get_assignments` |
| `descargar_material` | `ramo`, `recurso_id?`, `buscar?`, `todo=false`, `sobrescribir=false` | rutas guardadas y archivos omitidos | `core_course_get_contents` + `webservice/pluginfile.php` |
| `ver_notas` | `ramo?` | sin ramo: nota final por ramo; con ramo: ítems de calificación, nota, rango, % y feedback | `gradereport_overview_get_course_grades`, `gradereport_user_get_grade_items` |
| `avisos_y_foros` | `ramo`, `foro_id?`, `limite=10` | foros del ramo y últimas discusiones (asunto, autor, fecha, extracto) | `mod_forum_get_forums_by_courses`, `mod_forum_get_forum_discussions` |
| `leer_discusion` | `discusion_id` | mensajes completos del hilo | `mod_forum_get_discussion_posts` |
| `contactos_ramo` | `ramo` | mejor esfuerzo: participantes con rol docente (si el Moodle los muestra), contactos del curso y correos mencionados en el contenido del ramo, con contexto | `core_enrol_get_enrolled_users`, `core_course_get_courses_by_field`, `core_course_get_contents` |
| `vincular_carpeta` | `ramo`, `carpeta?` | sin carpeta: candidatas ordenadas por puntaje; con carpeta: confirma el vínculo | — (guarda en `carpetas.json`) |

### Carpetas dinámicas (`descargar_material`)

- Si el ramo tiene una carpeta vinculada, se usa esa.
- Si no, cada subcarpeta de `AULA_DIR_BASE` (y de las carpetas de semestre `AAAA-S`) recibe
  un puntaje: nombre exacto = 100, sigla = 90; si no, abreviatura del nombre formada por
  prefijos de sus palabras en orden (+3 por palabra significativa, −2 por palabra vacía,
  + largo de la clave). Se prefieren las carpetas de la raíz.
- Se elige sola solo si el mejor puntaje es ≥ 6 y supera al segundo por ≥ 4; se recuerda.
- Si no, no se descarga nada: se responden los candidatos para que el usuario elija con
  `vincular_carpeta`. **Nunca se crean carpetas sin pedirlo.**
- Dentro de la carpeta del ramo, los archivos se guardan en
  `Aula/<sección>/<archivo>`, para no mezclarse con los archivos propios.
- Los nombres se sanean (no se permite `..` ni rutas absolutas). Por defecto no se
  sobrescriben archivos existentes del mismo tamaño.
- Sin `recurso_id` ni `buscar`, hay que pedir `todo=true` explícitamente.

## Transporte

- v0.1: `stdio` (Claude Code y Claude Desktop).
- Futuro (iPhone): HTTP *streamable* desplegado con autenticación OAuth, porque los
  conectores remotos de la app móvil lo requieren. Fuera de alcance de v0.1.

## Fuera de alcance (v0.1)

Subir entregas, enviar mensajes, rendir cuestionarios y cualquier acción que escriba en Moodle.

## Tests

- Las respuestas de Moodle se guardan como fixtures JSON (anonimizadas) en `tests/fixtures/`.
- Los tests usan `httpx.MockTransport`: no requieren red ni token.
- La prueba de humo contra el Moodle real es manual: `aula-mcp-check`.
