"""Configuración: variables de entorno con prioridad sobre ~/.config/aula-mcp/config.env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

URL_POR_DEFECTO = "https://aula.usm.cl"


def directorio_config() -> Path:
    base = os.environ.get("AULA_CONFIG_DIR")
    return Path(base).expanduser() if base else Path.home() / ".config" / "aula-mcp"


def leer_env(archivo: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    if not archivo.is_file():
        return valores
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def guardar_valor(clave: str, valor: str, directorio: Path | None = None) -> Path:
    """Escribe o actualiza CLAVE=valor en config.env, con permisos 600."""
    directorio = directorio or directorio_config()
    directorio.mkdir(parents=True, exist_ok=True)
    os.chmod(directorio, 0o700)
    archivo = directorio / "config.env"
    valores = leer_env(archivo)
    valores[clave] = valor
    contenido = "# Generado por aula-mcp. No compartas este archivo: contiene tu token.\n"
    contenido += "".join(f"{k}={v}\n" for k, v in valores.items())
    # Crear con permisos restringidos antes de escribir el secreto.
    fd = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(contenido)
    os.chmod(archivo, 0o600)
    return archivo


@dataclass
class Config:
    url: str
    token: str | None
    dir_base: Path | None
    directorio: Path

    @classmethod
    def cargar(cls) -> Config:
        directorio = directorio_config()
        archivo = leer_env(directorio / "config.env")

        def obtener(clave: str) -> str | None:
            return os.environ.get(clave) or archivo.get(clave) or None

        url = (obtener("AULA_URL") or URL_POR_DEFECTO).rstrip("/")
        dir_base = obtener("AULA_DIR_BASE")
        return cls(
            url=url,
            token=obtener("AULA_TOKEN"),
            dir_base=Path(dir_base).expanduser() if dir_base else None,
            directorio=directorio,
        )
