"""Login sin consola: el navegador entrega moodlemobile://token=… a aula-mcp-enlace."""

import base64
import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlsplit

import pytest

from aula_mcp import cli, enlace

SITIO = "https://aula.usm.cl"


def enlace_firmado(passport: str, token: str = "TOK123") -> str:
    firma = hashlib.md5(f"{SITIO}{passport}".encode()).hexdigest()
    return "moodlemobile://token=" + base64.b64encode(f"{firma}:::{token}:::PRIV".encode()).decode()


@pytest.fixture(autouse=True)
def intercambio(tmp_path, monkeypatch):
    archivo = tmp_path / "intercambio" / "enlace-pendiente"
    monkeypatch.setattr(enlace, "archivo_intercambio", lambda: archivo)
    return archivo


# --- receptor y espera --------------------------------------------------------
def test_recibir_guarda_el_enlace(intercambio):
    assert enlace.recibir("  moodlemobile://token=abc  ")
    assert intercambio.read_text() == "moodlemobile://token=abc"
    if os.name != "nt":
        assert oct(intercambio.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize(
    "url", ["https://aula.usm.cl", "javascript:alert(1)", "moodlemobile://" + "x" * 9000]
)
def test_recibir_rechaza_otros_enlaces(intercambio, url):
    assert not enlace.recibir(url)
    assert not intercambio.exists()


def test_main_usa_el_argumento(intercambio, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["aula-mcp-enlace", "moodlemobile://token=x"])
    with pytest.raises(SystemExit) as e:
        enlace.main()
    assert e.value.code == 0 and intercambio.exists()
    monkeypatch.setattr(sys, "argv", ["aula-mcp-enlace"])
    with pytest.raises(SystemExit) as e:
        enlace.main()
    assert e.value.code == 1


def test_esperar_devuelve_y_consume_el_enlace(intercambio):
    threading.Timer(0.2, enlace.recibir, args=["moodlemobile://token=x"]).start()
    assert enlace.esperar(5, threading.Event()) == "moodlemobile://token=x"
    assert not intercambio.exists()


def test_esperar_se_puede_cancelar_y_tiene_limite():
    cancelado = threading.Event()
    cancelado.set()
    assert enlace.esperar(5, cancelado) is None
    assert enlace.esperar(0.1, threading.Event()) is None


# --- flujo de aula-mcp-login -----------------------------------------------------
def _passport(url_lanzamiento: str) -> str:
    return parse_qs(urlsplit(url_lanzamiento).query)["passport"][0]


def _lanzamiento(passport: str) -> str:
    return f"{SITIO}/admin/tool/mobile/launch.php?passport={passport}&urlscheme=moodlemobile"


def test_login_recibe_el_token_del_navegador(monkeypatch, capsys):
    monkeypatch.setattr(enlace, "registrar", lambda: (True, "registrado"))

    def navegador(url):
        # Primero llega un enlace de un intento anterior, luego el bueno.
        enlace.recibir(enlace_firmado("otro-passport"))
        threading.Timer(0.5, enlace.recibir, args=[enlace_firmado(_passport(url))]).start()

    monkeypatch.setattr(cli.webbrowser, "open", navegador)
    token = cli._token_desde_navegador(SITIO, "777", _lanzamiento("777"), abrir=True, a_mano=False)
    assert token == "TOK123"
    salida = capsys.readouterr().out
    assert "el token se guarda solo" in salida and "Sigo esperando" in salida


def test_login_a_mano_si_no_se_puede_registrar(monkeypatch, capsys):
    monkeypatch.setattr(enlace, "registrar", lambda: (False, "xdg-mime no está instalado"))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(cli, "getpass", lambda _: enlace_firmado("777"))
    token = cli._token_desde_navegador(SITIO, "777", _lanzamiento("777"), abrir=True, a_mano=False)
    assert token == "TOK123"
    assert "F12" in capsys.readouterr().out


def test_login_a_mano_si_se_acaba_la_espera(monkeypatch, capsys):
    monkeypatch.setattr(enlace, "registrar", lambda: (True, "registrado"))
    monkeypatch.setattr(cli, "ESPERA_MAXIMA", 0.1)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(cli, "getpass", lambda _: enlace_firmado("777"))
    assert cli._token_desde_navegador(SITIO, "777", _lanzamiento("777"), True, False) == "TOK123"
    assert "Hagámoslo a mano" in capsys.readouterr().out


def test_login_opcion_a_mano_no_registra(monkeypatch):
    monkeypatch.setattr(enlace, "registrar", lambda: pytest.fail("no debía registrar"))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(cli, "getpass", lambda _: enlace_firmado("777"))
    assert cli._token_desde_navegador(SITIO, "777", _lanzamiento("777"), True, True) == "TOK123"


# --- registro por sistema -----------------------------------------------------------
def test_registro_linux(tmp_path, monkeypatch):
    llamadas = []

    def run(cmd, **kw):
        llamadas.append(cmd)
        salida = "" if cmd[:2] == ["xdg-mime", "query"] else None
        return subprocess.CompletedProcess(cmd, 0, stdout=salida, stderr="")

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(enlace.shutil, "which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setattr(enlace.subprocess, "run", run)
    ok, _ = enlace._registrar_linux(PurePosixPath("/opt/mi app/100%/aula-mcp-enlace"))
    assert ok
    desktop = (tmp_path / "applications" / enlace.NOMBRE_DESKTOP).read_text()
    assert 'Exec="/opt/mi app/100%%/aula-mcp-enlace" %u' in desktop
    assert "MimeType=x-scheme-handler/moodlemobile;" in desktop
    assert [
        "xdg-mime",
        "default",
        enlace.NOMBRE_DESKTOP,
        "x-scheme-handler/moodlemobile",
    ] in llamadas


def test_registro_linux_no_reemplaza_otra_app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(enlace.shutil, "which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setattr(
        enlace.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="moodle-desktop.desktop\n"),
    )
    ok, mensaje = enlace._registrar_linux(Path("/x/aula-mcp-enlace"))
    assert not ok and "moodle-desktop.desktop" in mensaje
    assert not (tmp_path / "applications").exists()


def test_registrar_sin_receptor(monkeypatch):
    monkeypatch.setattr(enlace, "comando_receptor", lambda: None)
    ok, mensaje = enlace.registrar()
    assert not ok and "reinstala" in mensaje


@pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")
def test_registro_windows():  # pragma: no cover - corre en CI con Windows
    import winreg

    receptor = Path(r"C:\Program Files\aula\aula-mcp-enlace.exe")
    try:
        ok, _ = enlace._registrar_windows(receptor)
        assert ok
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, enlace._CLAVE_WINDOWS) as k:
            assert winreg.QueryValueEx(k, "URL Protocol")[0] == ""
        clave = enlace._CLAVE_WINDOWS + r"\shell\open\command"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
            assert winreg.QueryValueEx(k, "")[0] == f'"{receptor}" "%1"'
        assert enlace._registrar_windows(receptor)[0]  # volver a registrar no falla
    finally:
        enlace.quitar()
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, enlace._CLAVE_WINDOWS)


@pytest.mark.skipif(sys.platform != "darwin", reason="solo macOS")
def test_registro_mac(tmp_path, monkeypatch):  # pragma: no cover - corre en CI con macOS
    import plistlib

    monkeypatch.setattr(enlace.Path, "home", lambda: tmp_path)
    registrados = []
    monkeypatch.setattr(enlace, "_lsregister", lambda *a: registrados.append(a))
    ok, _ = enlace._registrar_mac(Path('/Users/a b/"raro"/aula-mcp-enlace'))
    assert ok
    app = tmp_path / "Applications" / enlace.NOMBRE_APP_MAC
    datos = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert datos["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["moodlemobile"]
    assert datos["CFBundleIdentifier"] == enlace.ID_MAC
    assert registrados == [("-f", str(app))]


def test_comando_receptor_junto_al_interprete():
    receptor = enlace.comando_receptor()
    assert receptor is not None and receptor.name.startswith("aula-mcp-enlace")


def test_registrar_convierte_errores_en_mensaje(monkeypatch):
    def falla(_):
        raise FileNotFoundError("falta xdg-mime")

    monkeypatch.setattr(enlace, "comando_receptor", lambda: Path("/x/aula-mcp-enlace"))
    for nombre in ("_registrar_linux", "_registrar_windows", "_registrar_mac"):
        monkeypatch.setattr(enlace, nombre, falla)
    ok, mensaje = enlace.registrar()
    assert not ok and "falta xdg-mime" in mensaje


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="registro de Linux")
def test_quitar_linux(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    archivo = tmp_path / "applications" / enlace.NOMBRE_DESKTOP
    archivo.parent.mkdir()
    archivo.write_text("[Desktop Entry]\n")
    assert str(archivo) in enlace.quitar()
    assert not archivo.exists()
