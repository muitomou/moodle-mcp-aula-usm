import base64
import hashlib
import os

import httpx
import pytest

from aula_mcp.carpetas import elegir, puntaje
from aula_mcp.cli import decodificar_lanzamiento
from aula_mcp.config import Config, guardar_valor
from aula_mcp.moodle import MoodleClient, MoodleError, TokenInvalido, aplanar
from aula_mcp.util import (
    compactar,
    fecha,
    limpiar_html,
    limpiar_nombre_ramo,
    paralelos_ramo,
    sanear_nombre,
    sigla_ramo,
    tamano,
)

from .conftest import URL, correr


# --- util ---------------------------------------------------------------
def test_limpiar_html():
    assert limpiar_html("<p>Hola&nbsp;<b>mundo</b></p><p>2</p>") == "Hola mundo\n2"
    assert limpiar_html("x" * 50, maximo=10) == "x" * 9 + "…"
    assert limpiar_html(None) == ""


def test_sanear_nombre_no_permite_escapar():
    for malo in ["../../etc/passwd", "..", "/abs/ruta", "a\\b", "\x00x"]:
        limpio = sanear_nombre(malo)
        assert "/" not in limpio and "\\" not in limpio and limpio not in ("", ".", "..")


def test_formatos():
    assert fecha(None) is None
    assert fecha(1790000000)[:3] in {"lun", "mar", "mié", "jue", "vie", "sáb", "dom"}
    assert tamano(500) == "500 B" and tamano(2048) == "2.0 KB"
    assert compactar("Pruebas de Señales!") == "pruebasdesenales"


# --- config ---------------------------------------------------------------
def test_config_guarda_con_permisos_600(tmp_path, monkeypatch):
    monkeypatch.setenv("AULA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("AULA_TOKEN", raising=False)
    monkeypatch.delenv("AULA_URL", raising=False)
    archivo = guardar_valor("AULA_TOKEN", "abc")
    guardar_valor("AULA_DIR_BASE", str(tmp_path / "x"))
    if os.name != "nt":  # Windows no tiene permisos de tipo Unix
        assert oct(archivo.stat().st_mode & 0o777) == "0o600"
    cfg = Config.cargar()
    assert cfg.token == "abc" and cfg.url == "https://aula.usm.cl"
    assert cfg.dir_base == tmp_path / "x"
    monkeypatch.setenv("AULA_TOKEN", "del-entorno")
    assert Config.cargar().token == "del-entorno"


# --- moodle ---------------------------------------------------------------
def test_aplanar():
    assert aplanar({"courseids": [5, 6], "options": [{"name": "a", "value": True}], "x": None}) == {
        "courseids[0]": "5",
        "courseids[1]": "6",
        "options[0][name]": "a",
        "options[0][value]": "1",
    }


def test_token_invalido():
    cliente = MoodleClient(
        URL,
        "malo",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"exception": "x", "errorcode": "invalidtoken", "message": "m"}
            )
        ),
    )
    with pytest.raises(TokenInvalido, match="aula-mcp-login"):
        correr(cliente.llamar("core_webservice_get_site_info"))


def test_sin_token():
    with pytest.raises(TokenInvalido):
        correr(MoodleClient(URL, None).llamar("x"))


def test_descarga_no_envia_token_a_otro_host(cliente, tmp_path):
    with pytest.raises(MoodleError, match="no se envía el token"):
        correr(cliente.descargar("https://otro-sitio.ejemplo.com/a.pdf", tmp_path / "a.pdf"))


# --- carpetas -------------------------------------------------------------
def R(nombre, corto="202602_J1_C7_XXX000_1"):
    return {"id": 1, "shortname": corto, "fullname": f"(202602)(XXX000) {nombre}|Paralelos:200"}


def test_nombres_usm():
    completo = "(202602)(INF466) INTRODUCCIÓN A LA BIOINFORMÁTICA|Paralelos:1/200"
    assert limpiar_nombre_ramo(completo) == "INTRODUCCIÓN A LA BIOINFORMÁTICA"
    assert paralelos_ramo(completo) == "1/200"
    assert sigla_ramo({"fullname": completo}) == "INF466"
    assert (
        sigla_ramo({"fullname": "Sin sigla", "shortname": "202602_J1_C7_INF331_3732"}) == "INF331"
    )
    assert sigla_ramo({"fullname": "Otra", "shortname": "ELO-314-2026"}) == "ELO314"
    assert limpiar_nombre_ramo("Nombre normal") == "Nombre normal"


@pytest.mark.parametrize(
    "carpeta, nombre",
    [
        ("pds", "PRUEBAS DE SOFTWARE"),
        ("seminariosisges", "SEMINARIO DE SISTEMAS Y GESTION"),
        ("PROGCOMP", "Programación Competitiva"),
        ("TT1", "TRABAJO DE TÍTULO I"),
        ("bioinf", "INTRODUCCIÓN A LA BIOINFORMÁTICA"),
        ("IA", "INTELIGENCIA ARTIFICIAL"),
        ("SistemasDistribuidos", "Sistemas Distribuidos"),
    ],
)
def test_carpeta_coincide(carpeta, nombre):
    assert puntaje(carpeta, R(nombre)) >= 6


@pytest.mark.parametrize(
    "carpeta, nombre",
    [
        ("IO", "GESTIÓN DE PORTAFOLIO Y OFICINA DE PROYECTOS"),
        ("Certificaciones", "Programación Competitiva"),
        ("PROGCOMP", "Programación Avanzada"),
        ("x", "Xilófonos"),
        ("paralelos", "PRUEBAS DE SOFTWARE"),
    ],
)
def test_carpeta_no_coincide(carpeta, nombre):
    assert puntaje(carpeta, R(nombre)) == 0


def test_puntajes_ordenan_bien():
    bioinf = R("INTRODUCCIÓN A LA BIOINFORMÁTICA")
    assert puntaje("bioinf", bioinf) > puntaje("IA", bioinf) > 0
    assert puntaje("IA", bioinf) < 6  # coincidencia débil: no se elige sola
    pruebas = R("PRUEBAS DE SOFTWARE")
    assert puntaje("Pruebas de Software", pruebas) == 100
    assert puntaje("XXX000", pruebas) == 90 and puntaje("000", pruebas) == 90


def test_elegir():
    assert elegir([("a", 9), ("b", 3)]) == "a"
    assert elegir([("a", 8), ("b", 7)]) is None  # empate técnico
    assert elegir([("a", 3)]) is None  # demasiado débil
    assert elegir([]) is None


# --- login ----------------------------------------------------------------
def _url_lanzamiento(sitio, passport, token="TOK123", privado="PRIV"):
    firma = hashlib.md5(f"{sitio}{passport}".encode()).hexdigest()
    return (
        "moodlemobile://token="
        + base64.b64encode(f"{firma}:::{token}:::{privado}".encode()).decode()
    )


def test_decodificar_lanzamiento():
    pegado = _url_lanzamiento("https://aula.usm.cl", "777")
    assert (
        decodificar_lanzamiento(
            f"  Failed to launch '{pegado}' because…", "https://aula.usm.cl/", "777"
        )
        == "TOK123"
    )


def test_decodificar_lanzamiento_rechaza_otra_firma():
    with pytest.raises(ValueError, match="firma"):
        decodificar_lanzamiento(
            _url_lanzamiento("https://aula.usm.cl", "111"), "https://aula.usm.cl", "222"
        )
    with pytest.raises(ValueError):
        decodificar_lanzamiento("cualquier cosa", "https://aula.usm.cl", "1")


# --- lectura ----------------------------------------------------------------
def test_extraer_texto_pdf_y_rangos(tmp_path):
    from aula_mcp import lectura

    from .conftest import pdf_minimo

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(pdf_minimo(["uno", "dos", "tres"]))
    assert lectura.extraer_texto(pdf)["paginas_leidas"] == "1-3"
    r = lectura.extraer_texto(pdf, "2-")
    assert r["paginas_leidas"] == "2-3" and "uno" not in r["texto"]
    assert "error" in lectura.extraer_texto(pdf, "abc")
    (tmp_path / "n.py").write_text("print(1)")
    assert lectura.extraer_texto(tmp_path / "n.py")["texto"] == "print(1)"
    assert "aviso" in lectura.extraer_texto(tmp_path / "x.docx")


def test_extraer_texto_trunca(tmp_path, monkeypatch):
    from aula_mcp import lectura

    monkeypatch.setattr(lectura, "MAX_CARACTERES", 10)
    (tmp_path / "a.txt").write_text("x" * 50)
    r = lectura.extraer_texto(tmp_path / "a.txt")
    assert r["truncado"] and len(r["texto"]) == 10


@pytest.mark.parametrize(
    "error, mensaje",
    [
        (httpx.ReadTimeout, "no respondió a tiempo"),
        (httpx.ConnectTimeout, "no respondió a tiempo"),
        (httpx.ConnectError, "Revisa tu conexión a internet"),
        (httpx.RemoteProtocolError, r"Error de red .* \(RemoteProtocolError\)"),
    ],
)
def test_errores_de_red_con_mensaje_claro(tmp_path, error, mensaje):
    def falla(request):
        raise error("detalle interno", request=request)

    cliente = MoodleClient(URL, "tok", transport=httpx.MockTransport(falla))
    with pytest.raises(MoodleError, match=mensaje) as e:
        correr(cliente.llamar("core_webservice_get_site_info"))
    assert "core_webservice_get_site_info" in str(e.value)
    with pytest.raises(MoodleError, match=mensaje):
        correr(cliente.descargar(f"{URL}/webservice/pluginfile.php/1/a.pdf", tmp_path / "a.pdf"))
    assert list(tmp_path.iterdir()) == []  # no deja archivos a medias


def test_timeout_por_defecto():
    cliente = MoodleClient(URL, "tok")
    assert cliente._http.timeout.connect == 10.0 and cliente._http.timeout.read == 30.0


def _cliente_que_responde(respuesta):
    return MoodleClient(
        URL, "tok", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=respuesta))
    )


def test_token_vencido_por_accessexception():
    # Moodle lanza webservice_access_exception (errorcode accessexception) si el token venció.
    cliente = _cliente_que_responde(
        {
            "exception": "webservice_access_exception",
            "errorcode": "accessexception",
            "message": "Excepción de control de acceso",
        }
    )
    with pytest.raises(MoodleError, match="aula-mcp-login") as e:
        correr(cliente.llamar("core_webservice_get_site_info"))
    assert e.value.codigo == "accessexception" and not isinstance(e.value, TokenInvalido)


def test_sitio_en_mantencion():
    cliente = _cliente_que_responde(
        {"exception": "moodle_exception", "errorcode": "sitemaintenance", "message": "..."}
    )
    with pytest.raises(MoodleError, match="mantención"):
        correr(cliente.llamar("core_webservice_get_site_info"))


@pytest.mark.parametrize("estado", [200, 403])
def test_descarga_detecta_error_json_de_pluginfile(tmp_path, estado):
    # webservice/pluginfile.php responde los errores como {"error", "errorcode"}, sin "exception".
    error = {"error": "Token no válido", "errorcode": "invalidtoken", "stacktrace": None}
    cliente = MoodleClient(
        URL,
        "tok",
        transport=httpx.MockTransport(lambda r: httpx.Response(estado, json=error)),
    )
    with pytest.raises(TokenInvalido):
        correr(cliente.descargar(f"{URL}/webservice/pluginfile.php/1/a.pdf", tmp_path / "a.pdf"))
    assert list(tmp_path.iterdir()) == []


def test_descarga_de_un_json_legitimo(tmp_path):
    datos = {"error": 0.1, "valores": [1, 2]}  # un .json del ramo que no es un error de Moodle
    cliente = MoodleClient(
        URL, "tok", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=datos))
    )
    destino = tmp_path / "datos.json"
    correr(cliente.descargar(f"{URL}/webservice/pluginfile.php/1/datos.json", destino))
    assert destino.exists()


def test_descarga_http_error_sin_json(tmp_path):
    cliente = MoodleClient(
        URL, "tok", transport=httpx.MockTransport(lambda r: httpx.Response(404, text="<html>"))
    )
    with pytest.raises(MoodleError, match="HTTP 404 al descargar a.pdf"):
        correr(cliente.descargar(f"{URL}/webservice/pluginfile.php/1/a.pdf", tmp_path / "a.pdf"))


def test_url_que_redirige_no_parece_token_invalido():
    # http:// → https:// con 301: httpx haría un GET sin el token y Moodle diría "token inválido".
    def sitio(request):
        if request.url.scheme == "http":
            nueva = str(request.url.copy_with(scheme="https"))
            return httpx.Response(301, headers={"location": nueva})
        return httpx.Response(200, json={"exception": "x", "errorcode": "invalidtoken"})

    cliente = MoodleClient("http://moodle.ejemplo.cl", "tok", transport=httpx.MockTransport(sitio))
    with pytest.raises(MoodleError, match="redirige a https://moodle.ejemplo.cl. Corrige AULA_URL"):
        correr(cliente.llamar("core_webservice_get_site_info"))


@pytest.mark.parametrize("paginas", ["5", "4-", "3-2"])
def test_extraer_texto_paginas_fuera_de_rango(tmp_path, paginas):
    from aula_mcp import lectura

    from .conftest import pdf_minimo

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(pdf_minimo(["uno", "dos", "tres"]))
    assert lectura.extraer_texto(pdf, paginas) == {
        "error": "El archivo tiene 3 páginas; pide un rango entre 1 y 3."
    }
