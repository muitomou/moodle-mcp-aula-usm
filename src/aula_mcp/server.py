"""Servidor MCP (stdio) con las herramientas de Aula."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .carpetas import Carpetas
from .config import Config
from .moodle import MoodleClient, MoodleError
from .servicio import Aula, ErrorAula

INSTRUCCIONES = """\
Herramientas para consultar el Moodle del estudiante (por defecto, Aula USM).
- `ramo` acepta el id numérico, el nombre corto, parte del nombre o la carpeta local vinculada.
- Empieza por listar_ramos si no sabes qué ramos hay.
- Para el enunciado de una tarea usa ver_tarea: muchas lo traen solo como PDF adjunto, que se
  baja con descargar_material(ramo, recurso_id) o se lee con leer_material(ramo, recurso_id).
- descargar_material guarda archivos en el computador del usuario: confirma con el usuario
  antes de usar todo=true.
- Las notas y los correos son datos personales: muéstralos solo al usuario.
"""

mcp = MCPServer(name="aula", instructions=INSTRUCCIONES)

LECTURA = ToolAnnotations(read_only_hint=True, open_world_hint=True)
ESCRITURA_LOCAL = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)

_aula: Aula | None = None


def aula() -> Aula:
    global _aula
    if _aula is None:
        cfg = Config.cargar()
        _aula = Aula(
            MoodleClient(cfg.url, cfg.token),
            Carpetas(cfg.directorio / "carpetas.json", cfg.dir_base),
        )
    return _aula


async def _ejecutar(tarea: Awaitable[Any]) -> str:
    """Convierte errores esperados en ToolError para que el modelo lea el motivo."""
    try:
        resultado = await tarea
    except (MoodleError, ErrorAula) as e:
        raise ToolError(str(e)) from e
    return json.dumps(_sin_vacios(resultado), ensure_ascii=False, separators=(",", ":"))


def _sin_vacios(valor: Any) -> Any:
    """Quita null, "" y colecciones vacías: ahorra tokens sin perder información."""
    if isinstance(valor, dict):
        return {k: _sin_vacios(v) for k, v in valor.items() if v not in (None, "", [], {})}
    if isinstance(valor, list):
        return [_sin_vacios(v) for v in valor]
    return valor


@mcp.tool(annotations=LECTURA)
async def listar_ramos(incluir_pasados: bool = False) -> str:
    """Lista los ramos del estudiante (por defecto solo los vigentes), con su id y la carpeta local vinculada."""
    return await _ejecutar(aula().listar_ramos(incluir_pasados))


@mcp.tool(annotations=LECTURA)
async def proximas_entregas(
    dias: int = 14, ramo: str | None = None, incluir_atrasadas: bool = True
) -> str:
    """Entregas y actividades pendientes con fecha límite en los próximos `dias` días.

    Con incluir_atrasadas también muestra las pendientes vencidas en las últimas 2 semanas.
    `ramo` filtra por un ramo.
    """
    return await _ejecutar(aula().proximas_entregas(dias, ramo, incluir_atrasadas))


@mcp.tool(annotations=LECTURA)
async def contenido_ramo(ramo: str) -> str:
    """Secciones y recursos de un ramo (id de recurso, tipo, nombre y archivos con su tamaño)."""
    return await _ejecutar(aula().contenido_ramo(ramo))


@mcp.tool(annotations=ESCRITURA_LOCAL)
async def descargar_material(
    ramo: str,
    recurso_id: int | None = None,
    buscar: str | None = None,
    todo: bool = False,
    sobrescribir: bool = False,
) -> str:
    """Descarga archivos de un ramo a su carpeta local, en <carpeta del ramo>/Aula/<sección>/.

    Filtra por recurso_id (de contenido_ramo) o por texto en `buscar`; todo=true baja todo el ramo.
    No sobrescribe archivos existentes salvo con sobrescribir=true. Si el ramo no tiene carpeta
    vinculada y no hay una única coincidencia, responde con candidatos para usar vincular_carpeta.
    """
    return await _ejecutar(aula().descargar_material(ramo, recurso_id, buscar, todo, sobrescribir))


@mcp.tool(annotations=LECTURA)
async def ver_tarea(tarea_id: int, ramo: str | None = None) -> str:
    """Detalle de una tarea: enunciado, archivos adjuntos del enunciado, fechas (apertura, vencimiento,
    corte), formato de entrega permitido y el estado de tu entrega (archivos enviados, nota y comentarios).

    tarea_id es el recurso_id que dan proximas_entregas o contenido_ramo. Muchas tareas traen el
    enunciado solo como PDF adjunto: bájalo con descargar_material(ramo, recurso_id=tarea_id).
    """
    return await _ejecutar(aula().ver_tarea(tarea_id, ramo))


@mcp.tool(annotations=ESCRITURA_LOCAL)
async def leer_material(
    ramo: str, recurso_id: int | None = None, archivo: str | None = None, paginas: str | None = None
) -> str:
    """Lee el contenido de un archivo del ramo (PDF o texto) y además lo deja guardado en su carpeta local.

    Úsalo para entender un enunciado, una guía o un apunte. Identifica el archivo por recurso_id
    (de contenido_ramo o ver_tarea) y, si el recurso tiene varios, por parte del nombre en `archivo`.
    Devuelve hasta ~30 000 caracteres; para PDFs largos pide un rango con paginas='3-6'.
    No extrae imágenes ni fórmulas dibujadas: si hacen falta, abre el archivo de guardado_en.
    """
    return await _ejecutar(aula().leer_material(ramo, recurso_id, archivo, paginas))


@mcp.tool(annotations=LECTURA)
async def ver_notas(ramo: str | None = None) -> str:
    """Notas. Sin ramo: nota final de cada ramo. Con ramo: cada evaluación con nota, rango, % y comentarios."""
    return await _ejecutar(aula().ver_notas(ramo))


@mcp.tool(annotations=LECTURA)
async def avisos_y_foros(ramo: str, foro_id: int | None = None, limite: int = 10) -> str:
    """Últimos avisos y discusiones de los foros de un ramo (los avisos primero). Usa foro_id para un solo foro."""
    return await _ejecutar(aula().avisos_y_foros(ramo, foro_id, limite))


@mcp.tool(annotations=LECTURA)
async def leer_discusion(discusion_id: int) -> str:
    """Mensajes completos de una discusión de foro (discusion_id viene de avisos_y_foros)."""
    return await _ejecutar(aula().leer_discusion(discusion_id))


@mcp.tool(annotations=LECTURA)
async def contactos_ramo(ramo: str) -> str:
    """Profesores y ayudantes de un ramo, con su rol y correo (si Moodle lo deja visible)."""
    return await _ejecutar(aula().contactos_ramo(ramo))


@mcp.tool(annotations=ESCRITURA_LOCAL)
async def vincular_carpeta(ramo: str, carpeta: str | None = None) -> str:
    """Vincula un ramo con su carpeta local (ruta absoluta o relativa a AULA_DIR_BASE).

    Sin `carpeta`, solo sugiere carpetas candidatas. No crea carpetas.
    """
    if carpeta is None:

        async def sugerir() -> dict[str, Any]:
            return {"candidatas": await aula().sugerir_carpetas(ramo)}

        return await _ejecutar(sugerir())
    return await _ejecutar(aula().vincular_carpeta(ramo, carpeta))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
