"""Recibe el enlace `moodlemobile://token=…` directo del navegador.

Al terminar el inicio de sesión, Moodle redirige a `moodlemobile://token=…` para que lo
abra la app móvil. Si `aula-mcp-enlace` está registrado como la aplicación de ese esquema,
el navegador se lo entrega y este lo deja en un archivo de intercambio que
`aula-mcp-login` está esperando. Así el estudiante no tiene que abrir la consola del
navegador ni copiar nada.

El archivo de intercambio no es un secreto útil por sí solo: `aula-mcp-login` solo acepta
un enlace firmado con el `passport` aleatorio de ese intento.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ESQUEMA = "moodlemobile"
NOMBRE_DESKTOP = "aula-mcp-enlace.desktop"
NOMBRE_APP_MAC = "Aula MCP Login.app"
ID_MAC = "io.github.muitomou.aula-mcp-login"
_CLAVE_WINDOWS = rf"Software\Classes\{ESQUEMA}"
_MAX_LARGO = 8192


def archivo_intercambio() -> Path:
    """Dónde el receptor deja el enlace. No depende de AULA_CONFIG_DIR, porque el navegador
    lanza el receptor sin las variables de entorno de la terminal."""
    return Path.home() / ".config" / "aula-mcp" / "enlace-pendiente"


# --- receptor (lo ejecuta el sistema al abrir moodlemobile://) ---------------
def recibir(url: str) -> bool:
    """Guarda el enlace para que lo lea aula-mcp-login. Devuelve False si no es válido."""
    url = url.strip()
    if not url.startswith(f"{ESQUEMA}://") or len(url) > _MAX_LARGO:
        return False
    destino = archivo_intercambio()
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(destino.name + ".tmp")
    fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(url)
    os.replace(temporal, destino)
    return True


def main() -> None:
    """Punto de entrada de `aula-mcp-enlace` (sin ventana en Windows)."""
    ok = len(sys.argv) > 1 and recibir(sys.argv[1])
    sys.exit(0 if ok else 1)


# --- espera (lado de aula-mcp-login) ----------------------------------------
def limpiar() -> None:
    archivo_intercambio().unlink(missing_ok=True)


def esperar(limite_segundos: float, cancelado: threading.Event) -> str | None:
    """Espera a que llegue un enlace. Devuelve None si se cancela o se acaba el tiempo."""
    archivo = archivo_intercambio()
    hasta = time.monotonic() + limite_segundos
    while time.monotonic() < hasta and not cancelado.is_set():
        try:
            texto = archivo.read_text(encoding="utf-8")
        except FileNotFoundError:
            time.sleep(0.3)
            continue
        archivo.unlink(missing_ok=True)
        return texto
    return None


# --- registro como aplicación del esquema moodlemobile:// --------------------
def comando_receptor() -> Path | None:
    """Ruta absoluta de aula-mcp-enlace, junto al intérprete actual o en el PATH."""
    nombre = "aula-mcp-enlace.exe" if os.name == "nt" else "aula-mcp-enlace"
    junto = Path(sys.executable).absolute().parent / nombre
    if junto.exists():
        return junto
    encontrado = shutil.which("aula-mcp-enlace")
    return Path(encontrado).absolute() if encontrado else None


def registrar() -> tuple[bool, str]:
    """Registra aula-mcp-enlace para abrir moodlemobile://. Devuelve (ok, mensaje).

    No reemplaza a otra aplicación que ya tenga el esquema (por ejemplo, la app de
    escritorio de Moodle).
    """
    receptor = comando_receptor()
    if receptor is None:
        return False, "No encontré el comando aula-mcp-enlace; reinstala aula-mcp."
    try:
        if sys.platform.startswith("win"):
            return _registrar_windows(receptor)
        if sys.platform == "darwin":
            return _registrar_mac(receptor)
        return _registrar_linux(receptor)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"No pude registrar el receptor de enlaces ({e.__class__.__name__}: {e})."


def quitar() -> str:
    """Deshace registrar()."""
    if sys.platform.startswith("win"):
        import winreg

        for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _CLAVE_WINDOWS + sub)
        return "Quité el registro de moodlemobile:// en Windows."
    if sys.platform == "darwin":
        app = _app_mac()
        if app.exists():
            _lsregister("-u", str(app))
            shutil.rmtree(app)
        return f"Borré {app}."
    escritorio = _dir_aplicaciones_linux() / NOMBRE_DESKTOP
    escritorio.unlink(missing_ok=True)
    return f"Borré {escritorio}."


# Linux ------------------------------------------------------------------------
def _dir_aplicaciones_linux() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


def _comillas_desktop(ruta: Path) -> str:
    """Argumento entre comillas según la especificación de archivos .desktop."""
    texto = str(ruta)
    for c in ("\\", '"', "`", "$"):
        texto = texto.replace(c, "\\" + c)
    texto = texto.replace("%", "%%")
    # El valor de la clave también interpreta '\': hay que duplicarlas.
    return '"' + texto.replace("\\", "\\\\") + '"'


def _registrar_linux(receptor: Path) -> tuple[bool, str]:
    actual = _consultar(["xdg-mime", "query", "default", f"x-scheme-handler/{ESQUEMA}"])
    if actual and actual != NOMBRE_DESKTOP:
        return False, f"{actual} ya abre los enlaces {ESQUEMA}://; no lo reemplazo."
    carpeta = _dir_aplicaciones_linux()
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / NOMBRE_DESKTOP).write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Aula MCP (inicio de sesión)\n"
        f"Exec={_comillas_desktop(receptor)} %u\n"
        "NoDisplay=true\n"
        "Terminal=false\n"
        f"MimeType=x-scheme-handler/{ESQUEMA};\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["xdg-mime", "default", NOMBRE_DESKTOP, f"x-scheme-handler/{ESQUEMA}"],
        check=True,
        capture_output=True,
    )
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(carpeta)], capture_output=True)
    return True, f"Registré {carpeta / NOMBRE_DESKTOP} para abrir los enlaces {ESQUEMA}://."


def _consultar(comando: list[str]) -> str:
    if not shutil.which(comando[0]):
        raise FileNotFoundError(f"falta {comando[0]}")
    r = subprocess.run(comando, capture_output=True, text=True)
    return r.stdout.strip()


# Windows ------------------------------------------------------------------------
def _registrar_windows(receptor: Path) -> tuple[bool, str]:
    import winreg

    comando = f'"{receptor}" "%1"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_WINDOWS + r"\shell\open\command") as k:
            actual = winreg.QueryValueEx(k, "")[0]
    except FileNotFoundError:
        actual = None
    if actual and "aula-mcp-enlace" not in actual:
        return (
            False,
            f"Otra aplicación ya abre los enlaces {ESQUEMA}:// ({actual}); no la reemplazo.",
        )
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CLAVE_WINDOWS) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f"URL:{ESQUEMA}")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CLAVE_WINDOWS + r"\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, comando)
    return True, f"Registré aula-mcp-enlace para abrir los enlaces {ESQUEMA}:// en Windows."


# macOS --------------------------------------------------------------------------
def _app_mac() -> Path:
    return Path.home() / "Applications" / NOMBRE_APP_MAC


def _cadena_applescript(texto: str) -> str:
    return '"' + texto.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lsregister(*args: str) -> None:
    lsregister = (
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister"
    )
    subprocess.run([lsregister, *args], check=True, capture_output=True)


def _registrar_mac(receptor: Path) -> tuple[bool, str]:
    # Una app AppleScript mínima: macOS solo entrega URLs a aplicaciones (.app).
    app = _app_mac()
    app.parent.mkdir(parents=True, exist_ok=True)
    if app.exists():
        shutil.rmtree(app)
    script = (
        "on open location enlace\n"
        f"  do shell script quoted form of {_cadena_applescript(str(receptor))}"
        ' & " " & quoted form of enlace\n'
        "end open location\n"
    )
    subprocess.run(["osacompile", "-o", str(app), "-e", script], check=True, capture_output=True)
    info = app / "Contents" / "Info.plist"
    datos = plistlib.loads(info.read_bytes())
    datos.update(
        {
            "CFBundleIdentifier": ID_MAC,
            "CFBundleName": "Aula MCP Login",
            "LSUIElement": True,  # sin ícono en el Dock
            "CFBundleURLTypes": [
                {"CFBundleURLName": "Inicio de sesión de Moodle", "CFBundleURLSchemes": [ESQUEMA]}
            ],
        }
    )
    info.write_bytes(plistlib.dumps(datos))
    _lsregister("-f", str(app))
    return True, f"Registré {app} para abrir los enlaces {ESQUEMA}://."
