import json

import pytest

from aula_mcp import server
from aula_mcp.servicio import ErrorAula

from .conftest import TOKEN, correr, pdf_minimo


def test_listar_ramos_solo_vigentes(aula):
    r = correr(aula.listar_ramos())
    assert r["usuario"] == "Estudiante Prueba"
    assert [x["id"] for x in r["ramos"]] == [101, 102, 103]
    primero = r["ramos"][0]
    assert primero["sigla"] == "INF331"
    assert primero["nombre"] == "PRUEBAS DE SOFTWARE"
    assert primero["paralelos"] == "200"
    assert len(correr(aula.listar_ramos(incluir_pasados=True))["ramos"]) == 5


@pytest.mark.parametrize(
    "consulta, esperado",
    [
        ("101", 101), ("INF331", 101), ("inf-331", 101), ("331", 101), ("pruebas", 101),
        ("pds", 101), ("bioinformatica", 103), ("sisges", 102),
        ("introduccion", 103),  # coincide con 103 (vigente) y 105 (pasado) → el vigente
        ("Introducción a la Programación", 105),
    ],
)
def test_resolver_ramo(aula, consulta, esperado):
    assert correr(aula.resolver_ramo(consulta))["id"] == esperado


def test_resolver_ramo_ambiguo_y_desconocido(aula):
    with pytest.raises(ErrorAula, match="varios"):
        correr(aula.resolver_ramo("de"))
    with pytest.raises(ErrorAula, match="No encontré"):
        correr(aula.resolver_ramo("astrofísica"))


def test_resolver_ramo_por_carpeta_vinculada(aula, universidad):
    (universidad / "semi").mkdir()
    correr(aula.vincular_carpeta("102", "semi"))
    assert correr(aula.resolver_ramo("semi"))["id"] == 102


def test_proximas_entregas(aula):
    r = correr(aula.proximas_entregas(dias=7))
    assert [e["actividad"] for e in r["entregas"]] == ["Tarea 3: Pruebas unitarias", "Informe final"]
    assert r["entregas"][0]["ramo"] == "PRUEBAS DE SOFTWARE"
    assert r["entregas"][0]["pendiente"] == "Agregar entrega" and not r["entregas"][0]["atrasada"]
    assert r["entregas"][0]["recurso_id"] == 9001
    solo = correr(aula.proximas_entregas(ramo="pds"))
    assert [e["ramo_id"] for e in solo["entregas"]] == [101]


def test_contenido_ramo(aula):
    r = correr(aula.contenido_ramo("INF331"))
    assert r["ramo"] == "PRUEBAS DE SOFTWARE"
    assert [s["seccion"] for s in r["secciones"]] == ["General", "Unidad 1: Muestreo"]
    assert r["secciones"][0]["resumen"] == "Bienvenidos al ramo :)"
    programa = r["secciones"][0]["recursos"][2]
    assert programa == {"id": 9101, "tipo": "resource", "nombre": "Programa del ramo",
                        "archivos": [{"nombre": "programa.pdf", "tamano": "11 B"}]}
    video = r["secciones"][1]["recursos"][1]
    assert video["disponible"] is False and "archivos" not in video
    tarea = r["secciones"][1]["recursos"][2]
    assert tarea["tipo"] == "assign" and tarea["vence"]
    assert tarea["archivos"] == [{"nombre": "t3+enunciado.pdf", "tamano": "2.0 KB"}]


def test_ver_tarea(aula):
    r = correr(aula.ver_tarea(9001))
    assert r["tarea"] == "Tarea 3: Pruebas unitarias" and r["ramo"] == "PRUEBAS DE SOFTWARE"
    assert r["enunciado"] is None
    assert r["adjuntos"] == [{"nombre": "t3+enunciado.pdf", "tamano": "2.0 KB"}]
    assert "recurso_id=9001" in r["como_bajar_adjuntos"]
    assert r["formato_de_entrega"] == [{"tipo": "archivos", "max_archivos": 20, "tamano_maximo": "50.0 MB",
                                         "tipos_permitidos": "cualquiera"}]
    assert r["mi_entrega"]["estado"] == "sin entregar"
    assert r["mi_entrega"]["archivos_entregados"] == [] and r["mi_entrega"]["puede_editar"] is True
    assert r["url"].endswith("/mod/assign/view.php?id=9001")


def test_ver_tarea_inexistente(aula):
    with pytest.raises(ErrorAula, match="No encontré una tarea"):
        correr(aula.ver_tarea(1234))


def test_descargar_enunciado_de_tarea(aula, universidad):
    r = correr(aula.descargar_material("pds", recurso_id=9001))
    assert [g["archivo"] for g in r["guardados"]] == [
        str(universidad / "Pruebas de Software" / "Aula" / "Unidad 1_ Muestreo" / "Tarea 3_ Pruebas unitarias" / "t3+enunciado.pdf")]


def test_descargar_material_vincula_y_guarda(aula, moodle, universidad):
    r = correr(aula.descargar_material("INF331", todo=True))
    # "Pruebas de Software" (nombre exacto) le gana a "pds" (abreviatura).
    carpeta = universidad / "Pruebas de Software"
    assert r["carpeta"] == str(carpeta) and "aviso" in r
    guardados = sorted(g["archivo"] for g in r["guardados"])
    assert guardados == sorted([
        str(carpeta / "Aula" / "General" / "programa.pdf"),
        str(carpeta / "Aula" / "Unidad 1_ Muestreo" / "Guías" / "guia1.pdf"),
        str(carpeta / "Aula" / "Unidad 1_ Muestreo" / "Guías" / "_" / "_" / "_.._malicioso.sh"),
        str(carpeta / "Aula" / "Unidad 1_ Muestreo" / "Tarea 3_ Pruebas unitarias" / "t3+enunciado.pdf"),
    ])
    for g in guardados:
        assert g.startswith(str(carpeta / "Aula"))
    assert [e["archivo"] for e in r["errores"]] == ["robo.pdf"]
    assert all(req.url.params["token"] == TOKEN for req in moodle.descargas)
    assert (carpeta / "Aula" / "General" / "programa.pdf").read_bytes() == b"contenido!!"

    r2 = correr(aula.descargar_material("INF331", buscar="programa"))
    assert r2["guardados"] == [] and r2["omitidos"][0]["motivo"] == "ya existe"
    assert "aviso" not in r2


def test_descargar_material_exige_filtro(aula):
    with pytest.raises(ErrorAula, match="todo=true"):
        correr(aula.descargar_material("INF331"))


def test_descargar_material_sin_carpeta_clara(aula):
    # «Introducción a la Programación»: 'IA' coincide, pero demasiado débil para elegirla sola.
    with pytest.raises(ErrorAula, match="vincular_carpeta.*Candidatos: .*IA"):
        correr(aula.descargar_material("105", recurso_id=9101))


def test_carpetas_sugeridas(aula, universidad):
    assert correr(aula.sugerir_carpetas("103"))[0] == str(universidad / "bioinf")
    # La raíz (vigente) va antes que el semestre pasado.
    assert correr(aula.sugerir_carpetas("102")) == [
        str(universidad / "seminariosisges"), str(universidad / "2025-2" / "SISGES")]
    assert correr(aula.sugerir_carpetas("104")) == [str(universidad / "2025-2" / "DIU")]


def test_vincular_carpeta_inexistente(aula):
    with pytest.raises(ErrorAula, match="no existe"):
        correr(aula.vincular_carpeta("101", "no-existe"))


def test_ver_notas(aula):
    finales = correr(aula.ver_notas())["notas_finales"]
    assert finales[0] == {"ramo_id": 101, "ramo": "PRUEBAS DE SOFTWARE", "nota": "58,50", "vigente": True}
    detalle = correr(aula.ver_notas("pds"))
    assert [i["item"] for i in detalle["items"]] == ["Certamen 1", "Total del ramo"]
    assert detalle["items"][0]["comentario"] == "Revisar pregunta 2"


def test_avisos_y_foros(aula):
    r = correr(aula.avisos_y_foros("pds"))
    assert r["foros"][0]["tipo"] == "news"
    assert [d["asunto"] for d in r["discusiones"]] == ["Cambio de sala", "Duda tarea 3"]
    assert r["discusiones"][0]["extracto"] == "El certamen será en la sala A-210."
    solo = correr(aula.avisos_y_foros("pds", foro_id=501))
    assert [d["discusion_id"] for d in solo["discusiones"]] == [802]


def test_leer_discusion(aula):
    r = correr(aula.leer_discusion(802))
    assert [m["autor"] for m in r["mensajes"]] == ["Estudiante Dos", "Ayudante Tres"]
    assert r["mensajes"][0]["adjuntos"] == ["codigo.py"]


def test_contactos_ramo_con_lista_de_participantes(aula):
    r = correr(aula.contactos_ramo("pds"))
    assert r["personas"] == [
        {"nombre": "Profesora Uno", "rol": "Profesor/a", "correo": "profesora.uno@ejemplo.cl"},
        {"nombre": "Ayudante Tres", "rol": "Ayudante", "correo": "no visible"},
    ]
    assert r["correos_mencionados"][0]["correo"] == "ana.ayudante@ejemplo.cl"
    assert "Ayudante: Ana Ayudante" in r["correos_mencionados"][0]["contexto"]


def test_contactos_ramo_como_aula_usm(aula, moodle):
    # Aula USM: el estudiante solo se ve a sí mismo y no hay contactos de curso.
    moodle.respuestas["core_enrol_get_enrolled_users"] = [
        {"id": 42, "fullname": "Estudiante Prueba", "roles": [{"shortname": "student", "name": ""}]}]
    moodle.respuestas["core_course_get_courses_by_field"] = {"courses": [{"id": 101, "contacts": []}]}
    r = correr(aula.contactos_ramo("pds"))
    assert r["personas"] == []
    assert [c["correo"] for c in r["correos_mencionados"]] == ["ana.ayudante@ejemplo.cl"]
    assert "nota" not in r


def test_contactos_ramo_sin_nada(aula, moodle):
    moodle.fallar["core_enrol_get_enrolled_users"] = {
        "exception": "x", "errorcode": "nopermissions", "message": "sin permiso"}
    moodle.respuestas["core_course_get_courses_by_field"] = {"courses": [{"id": 102, "contacts": []}]}
    moodle.respuestas["core_course_get_contents"] = []
    r = correr(aula.contactos_ramo("sisges"))
    assert r["personas"] == [] and r["correos_mencionados"] == [] and "nota" in r


def test_cache_evita_llamadas_repetidas(aula, moodle):
    correr(aula.listar_ramos())
    correr(aula.contenido_ramo("pds"))
    correr(aula.contenido_ramo("pds"))
    assert moodle.funciones().count("core_enrol_get_users_courses") == 1
    assert moodle.funciones().count("core_course_get_contents") == 1


# --- servidor MCP ---------------------------------------------------------
def test_servidor_expone_herramientas():
    nombres = {t.name for t in correr(server.mcp.list_tools())}
    assert nombres == {
        "listar_ramos", "proximas_entregas", "contenido_ramo", "descargar_material", "ver_tarea", "leer_material", "ver_notas",
        "avisos_y_foros", "leer_discusion", "contactos_ramo", "vincular_carpeta",
    }


def test_servidor_devuelve_json_y_errores_legibles(aula, monkeypatch):
    monkeypatch.setattr(server, "_aula", aula)
    texto = correr(server.listar_ramos())
    assert json.loads(texto)["ramos"][0]["nombre"] == "PRUEBAS DE SOFTWARE"
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="No encontré"):
        correr(server.contenido_ramo("astrofísica"))


# --- leer_material ----------------------------------------------------------
def test_leer_material_descarga_y_devuelve_texto(aula, moodle, universidad):
    moodle.contenido_descarga = pdf_minimo(["Enunciado tarea 3", "Pregunta 1: probar todo"])
    r = correr(aula.leer_material("pds", recurso_id=9001))
    destino = universidad / "Pruebas de Software" / "Aula" / "Unidad 1_ Muestreo" / "Tarea 3_ Pruebas unitarias" / "t3+enunciado.pdf"
    assert r["guardado_en"] == str(destino) and destino.exists()
    assert r["paginas_totales"] == 2 and r["paginas_leidas"] == "1-2"
    assert "Enunciado tarea 3" in r["texto"] and "[página 2]" in r["texto"]
    # Segunda lectura: usa la copia local si coincide el tamaño (no vuelve a descargar).
    moodle.respuestas["mod_assign_get_assignments"]["courses"][0]["assignments"][0]["introattachments"][0]["filesize"] = destino.stat().st_size
    aula._cache.clear()
    antes = len(moodle.descargas)
    r2 = correr(aula.leer_material("pds", recurso_id=9001, paginas="2"))
    assert len(moodle.descargas) == antes
    assert r2["paginas_leidas"] == "2-2" and "Enunciado" not in r2["texto"]


def test_leer_material_varios_archivos_pide_elegir(aula):
    with pytest.raises(ErrorAula, match="indica cuál"):
        correr(aula.leer_material("pds", recurso_id=9201))


def test_leer_material_sin_carpeta_usa_cache(aula, moodle, tmp_path):
    moodle.contenido_descarga = b"hola"
    r = correr(aula.leer_material("105", recurso_id=9101))  # «Introducción a la Programación»: sin carpeta clara
    assert r["guardado_en"].startswith(str(tmp_path / "config" / "cache" / "105"))
