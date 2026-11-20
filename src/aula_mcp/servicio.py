"""Lógica de las herramientas: llama a Moodle y devuelve datos compactos."""

from __future__ import annotations

import re
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .carpetas import CarpetaNoResuelta, Carpetas, claves_sigla, elegir, puntaje_abreviatura
from .lectura import extraer_texto
from .moodle import MoodleClient, MoodleError
from .util import (
    compactar,
    fecha,
    limpiar_html,
    limpiar_nombre_ramo,
    normalizar,
    paralelos_ramo,
    sanear_nombre,
    sigla_ramo,
    tamano,
)

_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


_ESTADOS_ENTREGA = {
    "new": "sin entregar",
    "draft": "borrador (aún no enviado)",
    "submitted": "enviada",
    "reopened": "reabierta",
}


def _id_de_url(url: str | None) -> int | None:
    m = re.search(r"[?&]id=(\d+)", url or "")
    return int(m.group(1)) if m else None


def _archivos_modulo(m: dict[str, Any], tareas: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Archivos descargables de un recurso, incluidos los adjuntos del enunciado de una tarea."""
    archivos = [a for a in m.get("contents") or [] if a.get("type") == "file" and a.get("fileurl")]
    tarea = tareas.get(m.get("id")) if m.get("modname") == "assign" else None
    if tarea:
        archivos += [a for a in tarea.get("introattachments") or [] if a.get("fileurl")]
    return archivos


def nombre_ramo(c: dict[str, Any]) -> str:
    return limpiar_nombre_ramo(c.get("fullname") or c.get("shortname") or str(c.get("id")))


_TTL = 600  # segundos de caché para datos que cambian poco
_ROLES = {
    "editingteacher": "Profesor/a",
    "teacher": "Profesor/a sin edición",
    "manager": "Gestor/a",
    "coursecreator": "Creador/a de cursos",
}
_ROLES_ESTUDIANTE = {"student", "guest", "user", "frontpage"}
MAX_ARCHIVOS_SIN_TODO = 30


class ErrorAula(Exception):
    """Error esperado con un mensaje apto para el usuario."""


class Aula:
    def __init__(self, cliente: MoodleClient, carpetas: Carpetas, cache_dir: Path | None = None):
        self.cliente = cliente
        self.carpetas = carpetas
        self.cache_dir = cache_dir or carpetas.archivo.parent / "cache"
        self._cache: dict[str, tuple[float, Any]] = {}

    # --- caché y datos base -------------------------------------------
    async def _cacheado(self, clave: str, funcion: str, **params: Any) -> Any:
        ahora = time.monotonic()
        guardado = self._cache.get(clave)
        if guardado and ahora - guardado[0] < _TTL:
            return guardado[1]
        valor = await self.cliente.llamar(funcion, **params)
        self._cache[clave] = (ahora, valor)
        return valor

    async def sitio(self) -> dict[str, Any]:
        return await self._cacheado("sitio", "core_webservice_get_site_info")

    async def ramos(self) -> list[dict[str, Any]]:
        usuario = (await self.sitio())["userid"]
        return await self._cacheado("ramos", "core_enrol_get_users_courses", userid=usuario)

    async def ids_vigentes(self) -> set[int]:
        try:
            r = await self._cacheado(
                "vigentes",
                "core_course_get_enrolled_courses_by_timeline_classification",
                classification="inprogress",
                limit=0,
                offset=0,
            )
            return {c["id"] for c in r.get("courses", [])}
        except MoodleError:
            # Respaldo: sin fecha de término o con término futuro.
            ahora = time.time()
            return {
                c["id"] for c in await self.ramos() if not c.get("enddate") or c["enddate"] > ahora
            }

    async def resolver_ramo(self, ramo: str | int) -> dict[str, Any]:
        lista = await self.ramos()
        texto = str(ramo).strip()
        if texto.isdigit():
            for c in lista:
                if c["id"] == int(texto):
                    return c
        consulta = normalizar(texto)
        compacta = compactar(texto)
        vinculos = {i: p.name for i, p in self.carpetas.vinculos().items()}

        exactos = [
            c
            for c in lista
            if compacta
            and (
                compacta == compactar(c.get("shortname", ""))
                or compacta in claves_sigla(c)
                or consulta == normalizar(nombre_ramo(c))
                or (c["id"] in vinculos and compactar(vinculos[c["id"]]) == compacta)
            )
        ]
        if len(exactos) == 1:
            return exactos[0]
        candidatos = exactos or [
            c
            for c in lista
            if consulta in normalizar(nombre_ramo(c))
            or (len(compacta) >= 3 and compacta in compactar(nombre_ramo(c)))
        ]
        if not candidatos and len(compacta) >= 2:
            # Abreviaturas: "pds" → Pruebas De Software, "sisges" → SIStemas y GEStión.
            puntajes = [(c, puntaje_abreviatura(compacta, c.get("fullname", ""))) for c in lista]
            elegido = elegir(puntajes)
            if elegido:
                return elegido
            candidatos = [c for c, p in puntajes if p > 0]
        if len(candidatos) == 1:
            return candidatos[0]
        if len(candidatos) > 1:
            vigentes = await self.ids_vigentes()
            solo_vigentes = [c for c in candidatos if c["id"] in vigentes]
            if len(solo_vigentes) == 1:
                return solo_vigentes[0]
            opciones = "; ".join(
                f"{c['id']}: {nombre_ramo(c)}" for c in (solo_vigentes or candidatos)
            )
            raise ErrorAula(f"«{texto}» coincide con varios ramos. Usa el id. Opciones: {opciones}")
        raise ErrorAula(
            f"No encontré el ramo «{texto}». Usa listar_ramos para ver los disponibles."
        )

    # --- herramientas ---------------------------------------------------
    async def listar_ramos(self, incluir_pasados: bool = False) -> dict[str, Any]:
        sitio = await self.sitio()
        vigentes = await self.ids_vigentes()
        vinculos = self.carpetas.vinculos()
        salida = []
        for c in await self.ramos():
            es_vigente = c["id"] in vigentes
            if not incluir_pasados and not es_vigente:
                continue
            salida.append(
                {
                    "id": c["id"],
                    "sigla": sigla_ramo(c),
                    "nombre": nombre_ramo(c),
                    "paralelos": paralelos_ramo(c.get("fullname") or ""),
                    "vigente": es_vigente,
                    "inicio": fecha(c.get("startdate")),
                    "termino": fecha(c.get("enddate")),
                    "carpeta_local": str(vinculos[c["id"]]) if c["id"] in vinculos else None,
                }
            )
        return {"usuario": sitio.get("fullname"), "ramos": salida}

    async def proximas_entregas(
        self, dias: int = 14, ramo: str | None = None, incluir_atrasadas: bool = True
    ) -> dict[str, Any]:
        dias = max(1, min(int(dias), 180))
        ahora = int(time.time())
        desde = ahora - 14 * 86400 if incluir_atrasadas else ahora
        r = await self.cliente.llamar(
            "core_calendar_get_action_events_by_timesort",
            timesortfrom=desde,
            timesortto=ahora + dias * 86400,
            limitnum=50,
            limittononsuspendedevents=True,
        )
        filtro = (await self.resolver_ramo(ramo))["id"] if ramo else None
        eventos = []
        for e in r.get("events", []):
            curso = e.get("course") or {}
            if filtro and curso.get("id") != filtro:
                continue
            accion = e.get("action") or {}
            eventos.append(
                {
                    "actividad": e.get("activityname") or e.get("name"),
                    "tipo": e.get("modulename"),
                    "ramo": nombre_ramo(curso) if curso else None,
                    "ramo_id": curso.get("id"),
                    "fecha": fecha(e.get("timesort")),
                    "atrasada": bool(e.get("overdue")) or (e.get("timesort") or 0) < ahora,
                    "pendiente": accion.get("name"),
                    "recurso_id": _id_de_url(e.get("url")),
                    "url": e.get("url"),
                }
            )
        return {"desde": fecha(desde), "hasta": fecha(ahora + dias * 86400), "entregas": eventos}

    async def _contenidos(self, ramo_id: int) -> list[dict[str, Any]]:
        return await self._cacheado(
            f"contenido:{ramo_id}", "core_course_get_contents", courseid=ramo_id
        )

    async def _tareas(self, ramo_id: int) -> dict[int, dict[str, Any]]:
        """Tareas del ramo por cmid. Traen enunciado y adjuntos, que core_course_get_contents omite."""
        r = await self._cacheado(
            f"tareas:{ramo_id}", "mod_assign_get_assignments", courseids=[ramo_id]
        )
        return {
            t["cmid"]: t for curso in r.get("courses", []) for t in curso.get("assignments", [])
        }

    async def _tareas_si_hay(
        self, ramo_id: int, secciones: list[dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        if not any(m.get("modname") == "assign" for s in secciones for m in s.get("modules", [])):
            return {}
        try:
            return await self._tareas(ramo_id)
        except MoodleError:
            return {}

    async def contenido_ramo(self, ramo: str) -> dict[str, Any]:
        c = await self.resolver_ramo(ramo)
        contenidos = await self._contenidos(c["id"])
        tareas = await self._tareas_si_hay(c["id"], contenidos)
        secciones = []
        for s in contenidos:
            recursos = []
            for m in s.get("modules", []):
                archivos = [
                    {"nombre": a.get("filename"), "tamano": tamano(a.get("filesize"))}
                    for a in _archivos_modulo(m, tareas)
                ]
                recurso = {
                    "id": m.get("id"),
                    "tipo": m.get("modname"),
                    "nombre": limpiar_html(m.get("name"), 120),
                }
                tarea = tareas.get(m.get("id"))
                if tarea and tarea.get("duedate"):
                    recurso["vence"] = fecha(tarea["duedate"])
                if archivos:
                    recurso["archivos"] = archivos
                if not m.get("uservisible", True):
                    recurso["disponible"] = False
                recursos.append(recurso)
            resumen = limpiar_html(s.get("summary"), 300)
            if recursos or resumen:
                secciones.append(
                    {
                        "seccion": s.get("name") or f"Sección {s.get('section')}",
                        "resumen": resumen or None,
                        "recursos": recursos,
                    }
                )
        return {"ramo": nombre_ramo(c), "ramo_id": c["id"], "secciones": secciones}

    async def _archivos_ramo(
        self, c: dict[str, Any], raiz: Path, recurso_id: int | None, buscar: str | None
    ) -> list[tuple[Path, dict[str, Any]]]:
        """Archivos del ramo que coinciden, con su ruta de destino dentro de `raiz`."""
        consulta = normalizar(buscar) if buscar else None
        pendientes: list[tuple[Path, dict[str, Any]]] = []
        contenidos = await self._contenidos(c["id"])
        tareas = await self._tareas_si_hay(c["id"], contenidos)
        for s in contenidos:
            nombre_seccion = sanear_nombre(s.get("name") or f"Sección {s.get('section')}", 80)
            for m in s.get("modules", []):
                if recurso_id is not None and m.get("id") != recurso_id:
                    continue
                for a in _archivos_modulo(m, tareas):
                    if consulta and consulta not in normalizar(
                        f"{m.get('name', '')} {a.get('filename', '')}"
                    ):
                        continue
                    subruta = [
                        sanear_nombre(p, 80)
                        for p in PurePosixPath(a.get("filepath") or "/").parts
                        if p != "/"
                    ]
                    if m.get("modname") in ("folder", "assign"):
                        subruta.insert(0, sanear_nombre(m.get("name") or "carpeta", 80))
                    destino = raiz.joinpath(
                        nombre_seccion, *subruta, sanear_nombre(a.get("filename") or "archivo")
                    )
                    # Defensa extra: el destino debe quedar dentro de la carpeta del ramo.
                    if not destino.resolve().is_relative_to(raiz.resolve()):
                        continue
                    pendientes.append((destino, a))
        return pendientes

    async def leer_material(
        self,
        ramo: str,
        recurso_id: int | None = None,
        archivo: str | None = None,
        paginas: str | None = None,
    ) -> dict[str, Any]:
        """Descarga (si hace falta) un archivo del ramo y devuelve su texto."""
        if recurso_id is None and not archivo:
            raise ErrorAula(
                "Indica recurso_id (de contenido_ramo o ver_tarea) o parte del nombre en `archivo`."
            )
        c = await self.resolver_ramo(ramo)
        try:
            carpeta, _ = self.carpetas.resolver(c)
            raiz = carpeta / "Aula"
        except CarpetaNoResuelta:
            # Sin carpeta del ramo: se guarda en la caché; igual queda una copia local.
            raiz = self.cache_dir / str(c["id"])
        candidatos = await self._archivos_ramo(c, raiz, recurso_id, archivo)
        if not candidatos:
            raise ErrorAula(
                "No encontré ese archivo. Revisa los nombres con contenido_ramo o ver_tarea."
            )
        if len(candidatos) > 1:
            nombres = ", ".join(a.get("filename") or "?" for _, a in candidatos[:15])
            raise ErrorAula(f"Hay {len(candidatos)} archivos; indica cuál con `archivo`: {nombres}")
        destino, a = candidatos[0]
        if not (destino.exists() and destino.stat().st_size == a.get("filesize")):
            await self.cliente.descargar(a["fileurl"], destino)
        salida: dict[str, Any] = {"archivo": a.get("filename"), "guardado_en": str(destino)}
        salida.update(extraer_texto(destino, paginas))
        return salida

    async def descargar_material(
        self,
        ramo: str,
        recurso_id: int | None = None,
        buscar: str | None = None,
        todo: bool = False,
        sobrescribir: bool = False,
    ) -> dict[str, Any]:
        if recurso_id is None and not buscar and not todo:
            raise ErrorAula("Indica recurso_id o buscar, o usa todo=true para bajar todo el ramo.")
        c = await self.resolver_ramo(ramo)
        try:
            carpeta, recien = self.carpetas.resolver(c)
        except CarpetaNoResuelta as e:
            candidatos = ", ".join(str(p) for p in e.candidatos) or "ninguno"
            raise ErrorAula(f"{e} Candidatos: {candidatos}") from e

        pendientes = await self._archivos_ramo(c, carpeta / "Aula", recurso_id, buscar)
        if not pendientes:
            raise ErrorAula("No encontré archivos que coincidan. Revisa con contenido_ramo.")
        if not todo and len(pendientes) > MAX_ARCHIVOS_SIN_TODO:
            raise ErrorAula(
                f"Coinciden {len(pendientes)} archivos; acota la búsqueda o usa todo=true."
            )

        guardados, omitidos, errores = [], [], []
        for destino, a in pendientes:
            if destino.exists() and not sobrescribir:
                igual = destino.stat().st_size == a.get("filesize")
                omitidos.append(
                    {
                        "archivo": str(destino),
                        "motivo": "ya existe"
                        if igual
                        else "ya existe con otro tamaño (usa sobrescribir=true)",
                    }
                )
                continue
            try:
                n = await self.cliente.descargar(a["fileurl"], destino)
                guardados.append({"archivo": str(destino), "tamano": tamano(n)})
            except MoodleError as e:
                errores.append({"archivo": a.get("filename"), "error": str(e)})
        salida: dict[str, Any] = {
            "ramo": nombre_ramo(c),
            "carpeta": str(carpeta),
            "guardados": guardados,
            "omitidos": omitidos,
            "errores": errores,
        }
        if recien:
            salida["aviso"] = (
                f"Vinculé automáticamente el ramo a {carpeta}. Cámbialo con vincular_carpeta si no es correcta."
            )
        return salida

    async def ver_tarea(self, tarea_id: int, ramo: str | None = None) -> dict[str, Any]:
        """Enunciado, adjuntos, fechas, formato de entrega y estado de una tarea (por su id de recurso)."""
        ramos = (
            [await self.resolver_ramo(ramo)]
            if ramo
            else [c for c in await self.ramos() if c["id"] in await self.ids_vigentes()]
        )
        tarea, curso = None, None
        for c in ramos:
            tarea = (await self._tareas(c["id"])).get(int(tarea_id))
            if tarea:
                curso = c
                break
        if not tarea:
            donde = f"en {nombre_ramo(ramos[0])}" if ramo else "en tus ramos vigentes"
            raise ErrorAula(
                f"No encontré una tarea con id {tarea_id} {donde}. Si es de un ramo pasado, indica `ramo`."
            )

        config = {
            (x.get("plugin"), x.get("subtype"), x.get("name")): x.get("value")
            for x in tarea.get("configs", [])
        }
        formatos = []
        if config.get(("file", "assignsubmission", "enabled")) == "1":
            maximo = int(config.get(("file", "assignsubmission", "maxsubmissionsizebytes")) or 0)
            formatos.append(
                {
                    "tipo": "archivos",
                    "max_archivos": int(
                        config.get(("file", "assignsubmission", "maxfilesubmissions")) or 0
                    )
                    or None,
                    "tamano_maximo": tamano(maximo) if maximo else "límite del sitio",
                    "tipos_permitidos": config.get(("file", "assignsubmission", "filetypeslist"))
                    or "cualquiera",
                }
            )
        if config.get(("onlinetext", "assignsubmission", "enabled")) == "1":
            limite = config.get(("onlinetext", "assignsubmission", "wordlimit"))
            formatos.append(
                {
                    "tipo": "texto en línea",
                    "limite_palabras": int(limite) if limite and limite != "0" else None,
                }
            )

        salida: dict[str, Any] = {
            "tarea": tarea.get("name"),
            "recurso_id": tarea.get("cmid"),
            "ramo": nombre_ramo(curso),
            "disponible_desde": fecha(tarea.get("allowsubmissionsfromdate")),
            "vence": fecha(tarea.get("duedate")),
            "fecha_de_corte": fecha(tarea.get("cutoffdate")),
            "enunciado": limpiar_html(tarea.get("intro"), 6000) or None,
            "adjuntos": [
                {"nombre": a.get("filename"), "tamano": tamano(a.get("filesize"))}
                for a in tarea.get("introattachments") or []
            ],
            "formato_de_entrega": formatos,
            "url": f"{self.cliente.url}/mod/assign/view.php?id={tarea.get('cmid')}",
        }
        if salida["adjuntos"]:
            salida["como_bajar_adjuntos"] = (
                f"descargar_material(ramo='{curso['id']}', recurso_id={tarea.get('cmid')})"
            )

        try:
            estado = await self.cliente.llamar(
                "mod_assign_get_submission_status", assignid=tarea["id"]
            )
        except MoodleError as e:
            salida["mi_entrega"] = {"error": str(e)}
            return salida
        ultimo = estado.get("lastattempt") or {}
        envio = ultimo.get("submission") or ultimo.get("teamsubmission") or {}
        entregados = [
            f.get("filename")
            for plugin in envio.get("plugins") or []
            for area in plugin.get("fileareas") or []
            for f in area.get("files") or []
        ]
        texto = next(
            (
                limpiar_html(ed.get("text"), 500)
                for plugin in envio.get("plugins") or []
                if plugin.get("type") == "onlinetext"
                for ed in plugin.get("editorfields") or []
                if ed.get("text")
            ),
            None,
        )
        mi_entrega: dict[str, Any] = {
            "estado": _ESTADOS_ENTREGA.get(
                envio.get("status"), envio.get("status") or "sin entregar"
            ),
            "ultima_modificacion": fecha(envio.get("timemodified"))
            if envio.get("status") not in (None, "new")
            else None,
            "archivos_entregados": entregados,
            "puede_editar": ultimo.get("canedit"),
            "calificada": ultimo.get("graded"),
        }
        if texto:
            mi_entrega["texto_entregado"] = texto
        if ultimo.get("extensionduedate"):
            mi_entrega["prorroga_hasta"] = fecha(ultimo["extensionduedate"])
        retro = estado.get("feedback") or {}
        if retro:
            mi_entrega["nota"] = limpiar_html(retro.get("gradefordisplay")) or None
            comentarios = [
                limpiar_html(ed.get("text"), 2000)
                for plugin in retro.get("plugins") or []
                for ed in plugin.get("editorfields") or []
                if ed.get("text")
            ]
            if comentarios:
                mi_entrega["comentarios"] = comentarios
        salida["mi_entrega"] = mi_entrega
        return salida

    async def ver_notas(self, ramo: str | None = None) -> dict[str, Any]:
        if not ramo:
            r = await self.cliente.llamar("gradereport_overview_get_course_grades")
            nombres = {c["id"]: nombre_ramo(c) for c in await self.ramos()}
            vigentes = await self.ids_vigentes()
            notas = [
                {
                    "ramo_id": g.get("courseid"),
                    "ramo": nombres.get(g.get("courseid"), str(g.get("courseid"))),
                    "nota": g.get("grade"),
                    "vigente": g.get("courseid") in vigentes,
                }
                for g in r.get("grades", [])
            ]
            notas.sort(key=lambda n: not n["vigente"])
            return {"notas_finales": notas}
        c = await self.resolver_ramo(ramo)
        usuario = (await self.sitio())["userid"]
        r = await self.cliente.llamar(
            "gradereport_user_get_grade_items", courseid=c["id"], userid=usuario
        )
        items = []
        for ug in r.get("usergrades", []):
            for it in ug.get("gradeitems", []):
                if it.get("gradeishidden"):
                    continue
                if it.get("itemtype") == "course":
                    nombre = "Total del ramo"
                elif it.get("itemtype") == "category":
                    nombre = f"Total categoría: {it.get('itemname') or ''}".strip()
                else:
                    nombre = it.get("itemname")
                item = {
                    "item": nombre,
                    "nota": it.get("gradeformatted") or None,
                    "rango": it.get("rangeformatted") or None,
                    "porcentaje": it.get("percentageformatted") or None,
                    "ponderacion": it.get("weightformatted") or None,
                }
                comentario = limpiar_html(it.get("feedback"), 500)
                if comentario:
                    item["comentario"] = comentario
                items.append(item)
        return {"ramo": nombre_ramo(c), "items": items}

    async def avisos_y_foros(
        self, ramo: str, foro_id: int | None = None, limite: int = 10
    ) -> dict[str, Any]:
        limite = max(1, min(int(limite), 50))
        c = await self.resolver_ramo(ramo)
        foros = await self.cliente.llamar("mod_forum_get_forums_by_courses", courseids=[c["id"]])
        if foro_id is not None:
            foros = [f for f in foros if f.get("id") == foro_id]
            if not foros:
                raise ErrorAula(f"El ramo no tiene un foro con id {foro_id}.")
        # Avisos primero; como máximo 6 foros para no hacer demasiadas consultas.
        foros = sorted(foros, key=lambda f: f.get("type") != "news")[:6]
        discusiones = []
        for f in foros:
            r = await self.cliente.llamar(
                "mod_forum_get_forum_discussions", forumid=f["id"], page=0, perpage=limite
            )
            for d in r.get("discussions", []):
                discusiones.append(
                    {
                        "discusion_id": d.get("discussion"),
                        "foro": f.get("name"),
                        "asunto": d.get("subject") or d.get("name"),
                        "autor": d.get("userfullname"),
                        "fecha": fecha(d.get("timemodified") or d.get("modified")),
                        "_orden": d.get("timemodified") or d.get("modified") or 0,
                        "respuestas": d.get("numreplies"),
                        "extracto": limpiar_html(d.get("message"), 300),
                    }
                )
        discusiones.sort(key=lambda d: d["_orden"], reverse=True)
        for d in discusiones:
            d.pop("_orden")
        return {
            "ramo": nombre_ramo(c),
            "foros": [
                {"id": f.get("id"), "nombre": f.get("name"), "tipo": f.get("type")} for f in foros
            ],
            "discusiones": discusiones[:limite],
        }

    async def leer_discusion(self, discusion_id: int) -> dict[str, Any]:
        r = await self.cliente.llamar("mod_forum_get_discussion_posts", discussionid=discusion_id)
        mensajes = sorted(r.get("posts", []), key=lambda p: p.get("timecreated") or 0)
        return {
            "mensajes": [
                {
                    "autor": (p.get("author") or {}).get("fullname"),
                    "fecha": fecha(p.get("timecreated")),
                    "asunto": p.get("subject"),
                    "mensaje": limpiar_html(p.get("message"), 4000),
                    "adjuntos": [a.get("filename") for a in p.get("attachments") or []] or None,
                }
                for p in mensajes
            ]
        }

    async def contactos_ramo(self, ramo: str) -> dict[str, Any]:
        """Mejor esfuerzo: muchos Moodle (como Aula USM) no dejan a estudiantes ver participantes."""
        c = await self.resolver_ramo(ramo)
        yo = (await self.sitio())["userid"]
        personas: list[dict[str, Any]] = []
        try:
            usuarios = await self.cliente.llamar(
                "core_enrol_get_enrolled_users",
                courseid=c["id"],
                options=[{"name": "userfields", "value": "id,fullname,email,roles"}],
            )
        except MoodleError:
            usuarios = []
        for u in usuarios:
            roles = [r for r in u.get("roles", []) if r.get("shortname") not in _ROLES_ESTUDIANTE]
            if u.get("id") == yo or not roles:
                continue
            personas.append(
                {
                    "nombre": u.get("fullname"),
                    "rol": ", ".join(
                        r.get("name") or _ROLES.get(r.get("shortname"), r.get("shortname"))
                        for r in roles
                    ),
                    "correo": u.get("email") or "no visible",
                }
            )
        if not personas:
            try:
                r = await self.cliente.llamar(
                    "core_course_get_courses_by_field", field="id", value=c["id"]
                )
                for x in (r.get("courses") or [{}])[0].get("contacts", []):
                    personas.append(
                        {"nombre": x.get("fullname"), "rol": "Profesor/a", "correo": "no visible"}
                    )
            except MoodleError:
                pass

        # Correos escritos en el contenido del ramo (resúmenes, etiquetas, descripciones).
        mencionados: dict[str, str] = {}
        textos = [c.get("summary") or ""]
        for s in await self._contenidos(c["id"]):
            textos.append(s.get("summary") or "")
            textos.extend(
                (m.get("description") or "") + " " + (m.get("name") or "")
                for m in s.get("modules", [])
            )
        for t in textos:
            plano = limpiar_html(t)
            for m in _CORREO.finditer(plano):
                correo = m.group(0).rstrip(".").lower()
                if correo not in mencionados:
                    contexto = (
                        plano[max(0, m.start() - 80) : m.end() + 20].replace("\n", " ").strip()
                    )
                    mencionados[correo] = contexto
        salida: dict[str, Any] = {
            "ramo": nombre_ramo(c),
            "personas": personas,
            "correos_mencionados": [{"correo": k, "contexto": v} for k, v in mencionados.items()],
        }
        if not personas and not mencionados:
            salida["nota"] = (
                "Este Moodle no muestra participantes a estudiantes y el ramo no menciona correos "
                "en su contenido. Revisa los avisos (avisos_y_foros) o el programa del ramo."
            )
        return salida

    async def vincular_carpeta(self, ramo: str, carpeta: str) -> dict[str, Any]:
        c = await self.resolver_ramo(ramo)
        try:
            ruta = self.carpetas.vincular(c, Path(carpeta))
        except CarpetaNoResuelta as e:
            raise ErrorAula(str(e)) from e
        return {"ramo": nombre_ramo(c), "carpeta": str(ruta)}

    async def sugerir_carpetas(self, ramo: str) -> list[str]:
        c = await self.resolver_ramo(ramo)
        return [str(p) for p, _ in self.carpetas.candidatos(c)]
