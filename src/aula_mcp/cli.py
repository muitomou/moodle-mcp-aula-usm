"""Comandos de terminal: aula-mcp-login (obtener token) y aula-mcp-check (prueba de humo)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import webbrowser
from getpass import getpass
from pathlib import Path
from urllib.parse import unquote

from .carpetas import Carpetas
from .config import Config, guardar_valor
from .moodle import MoodleClient, MoodleError
from .servicio import Aula

INSTRUCCIONES_LOGIN = """
1. Se abrirá Aula en tu navegador. ANTES de iniciar sesión, abre las herramientas
   de desarrollo (F12) en la pestaña «Consola».
2. Inicia sesión normalmente (Microsoft).
3. Al terminar, Moodle intenta abrir la app y el navegador muestra un error tipo:
     Failed to launch 'moodlemobile://token=…'
   Copia esa dirección completa, desde moodlemobile:// hasta el final.
   (Si no aparece en la consola: pestaña «Red», activa «Conservar registro» y
    busca la petición launch.php → cabecera «Location».)
4. Pégala aquí abajo. La entrada no se muestra en pantalla.

Si el navegador no se abre, visita esta dirección:
  {url}
"""


def decodificar_lanzamiento(texto: str, url_sitio: str, passport: str) -> str:
    """Extrae el token de `moodlemobile://token=<base64>` y verifica la firma del sitio."""
    m = re.search(r"token=([A-Za-z0-9+/=%_\-]+)", texto.strip())
    if not m:
        raise ValueError("No encontré 'token=' en lo que pegaste.")
    codificado = unquote(m.group(1))
    codificado += "=" * (-len(codificado) % 4)
    try:
        decodificado = base64.b64decode(codificado, altchars=b"-_" if "-" in codificado or "_" in codificado else None).decode()
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("El token pegado no es base64 válido; ¿se copió completo?") from e
    partes = decodificado.split(":::")
    if len(partes) < 2:
        raise ValueError("Formato inesperado: se esperaba firma:::token.")
    firma, token = partes[0], partes[1]
    esperado = hashlib.md5(f"{url_sitio.rstrip('/')}{passport}".encode()).hexdigest()
    if firma != esperado:
        raise ValueError(
            "La firma no coincide con este intento de inicio de sesión. "
            "Asegúrate de pegar la URL de ESTE intento (no de uno anterior)."
        )
    return token  # el token privado (partes[2]) se descarta a propósito


async def _verificar(url: str, token: str) -> dict:
    cliente = MoodleClient(url, token)
    try:
        return await cliente.llamar("core_webservice_get_site_info")
    finally:
        await cliente.cerrar()


def login() -> None:
    p = argparse.ArgumentParser(prog="aula-mcp-login", description="Obtiene y guarda el token de Moodle.")
    p.add_argument("--url", help="URL del Moodle (por defecto, la configurada o https://aula.usm.cl)")
    p.add_argument("--token", action="store_true", help="Pegar un token existente en vez de iniciar sesión")
    p.add_argument("--dir-base", help="Carpeta con una subcarpeta por ramo (para descargas)")
    p.add_argument("--no-abrir", action="store_true", help="No abrir el navegador automáticamente")
    a = p.parse_args()

    cfg = Config.cargar()
    url = (a.url or cfg.url).rstrip("/")

    try:
        if a.token:
            token = getpass("Pega tu token (no se mostrará): ").strip()
        else:
            passport = str(secrets.randbelow(10**9))
            lanzamiento = (
                f"{url}/admin/tool/mobile/launch.php?service=moodle_mobile_app"
                f"&passport={passport}&urlscheme=moodlemobile"
            )
            print(INSTRUCCIONES_LOGIN.format(url=lanzamiento))
            if not a.no_abrir:
                webbrowser.open(lanzamiento)
            pegado = getpass("URL moodlemobile://… : ")
            token = decodificar_lanzamiento(pegado, url, passport)
    except (ValueError, KeyboardInterrupt, EOFError) as e:
        sys.exit(f"\n✗ {e}" if str(e) else "\nCancelado.")

    try:
        sitio = asyncio.run(_verificar(url, token))
    except MoodleError as e:
        sys.exit(f"✗ El token no funcionó: {e}")

    archivo = guardar_valor("AULA_TOKEN", token)
    if a.url:
        guardar_valor("AULA_URL", url)
    if a.dir_base:
        guardar_valor("AULA_DIR_BASE", str(Path(a.dir_base).expanduser().resolve()))
    print(f"✓ Conectado como {sitio.get('fullname')} en «{sitio.get('sitename')}».")
    print(f"  Token guardado en {archivo} (permisos 600).")


def _config_desktop() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _desktop_abierto() -> bool:
    """Claude Desktop reescribe su configuración al cerrarse: hay que editarla con la app cerrada."""
    if sys.platform.startswith("win"):
        return False  # no hay un pgrep confiable; se avisa en el mensaje
    patron = "Claude.app/Contents/MacOS/Claude" if sys.platform == "darwin" else "claude-desktop"
    try:
        return subprocess.run(["pgrep", "-f", patron], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def desktop() -> None:
    """Registra aula-mcp en Claude Desktop (claude_desktop_config.json)."""
    p = argparse.ArgumentParser(prog="aula-mcp-desktop", description=desktop.__doc__)
    p.add_argument("--nombre", default="aula", help="Nombre del servidor en Claude Desktop")
    p.add_argument("--forzar", action="store_true", help="Escribir aunque Claude Desktop esté abierto")
    a = p.parse_args()

    if _desktop_abierto() and not a.forzar:
        sys.exit(
            "✗ Claude Desktop está abierto. Ciérralo por completo (también desde la bandeja del sistema),\n"
            "  vuelve a ejecutar este comando y luego abre la app. Si no, la app sobrescribe el cambio."
        )
    archivo = _config_desktop()
    datos = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}
    if archivo.exists():
        respaldo = archivo.with_name(archivo.name + ".respaldo")
        shutil.copy2(archivo, respaldo)
    # El ejecutable junto al intérprete actual (p. ej. .venv/bin/aula-mcp), siempre como ruta absoluta.
    junto = Path(sys.executable).absolute().parent / ("aula-mcp.exe" if os.name == "nt" else "aula-mcp")
    comando = str(junto) if junto.exists() else str(Path(shutil.which("aula-mcp") or "aula-mcp").absolute())
    datos.setdefault("mcpServers", {})[a.nombre] = {"command": comando}
    archivo.parent.mkdir(parents=True, exist_ok=True)
    temporal = archivo.with_name(archivo.name + ".tmp")
    temporal.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    if archivo.exists():
        os.chmod(temporal, archivo.stat().st_mode & 0o777)
    os.replace(temporal, archivo)
    print(f"✓ «{a.nombre}» → {comando}\n  registrado en {archivo}\n  Ahora abre Claude Desktop.")


def check() -> None:
    """Prueba de humo contra el Moodle real."""
    cfg = Config.cargar()
    cliente = MoodleClient(cfg.url, cfg.token)
    aula = Aula(cliente, Carpetas(cfg.directorio / "carpetas.json", cfg.dir_base))

    async def correr() -> None:
        try:
            ramos = await aula.listar_ramos()
            print(f"✓ {ramos['usuario']} — {len(ramos['ramos'])} ramos vigentes:")
            for r in ramos["ramos"]:
                carpeta = r["carpeta_local"] or "sin carpeta vinculada"
                sugeridas = await aula.sugerir_carpetas(str(r["id"])) if not r["carpeta_local"] else []
                extra = f" (sugeridas: {', '.join(Path(s).name for s in sugeridas)})" if sugeridas else ""
                print(f"  [{r['id']}] {r['sigla'] or '—'} {r['nombre']} → {carpeta}{extra}")
            entregas = await aula.proximas_entregas()
            print(f"✓ {len(entregas['entregas'])} entregas pendientes en las próximas 2 semanas.")
        finally:
            await cliente.cerrar()

    try:
        asyncio.run(correr())
    except MoodleError as e:
        sys.exit(f"✗ {e}")
