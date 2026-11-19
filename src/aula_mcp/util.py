"""Utilidades de formato: HTML → texto, fechas, tamaños, normalización y nombres seguros."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime

_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_TAG = re.compile(r"<[^>]+>")
_BLOQUE = re.compile(r"</?(p|div|br|li|tr|h[1-6])\b[^>]*>", re.IGNORECASE)
_ESPACIOS = re.compile(r"[ \t\r\f\v]+")
_SALTOS = re.compile(r"\n\s*\n+")
_INVALIDOS = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def limpiar_html(texto: str | None, maximo: int | None = None) -> str:
    """Convierte HTML de Moodle en texto plano compacto."""
    if not texto:
        return ""
    t = _BLOQUE.sub("\n", texto)
    t = _TAG.sub("", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = _ESPACIOS.sub(" ", t)
    t = _SALTOS.sub("\n", t)
    t = "\n".join(linea.strip() for linea in t.splitlines()).strip()
    if maximo and len(t) > maximo:
        t = t[: maximo - 1].rstrip() + "…"
    return t


def fecha(ts: int | float | None) -> str | None:
    """Timestamp Unix → 'jue 20-11-2026 23:59' en la hora local del sistema."""
    if not ts:
        return None
    d = datetime.fromtimestamp(int(ts)).astimezone()
    return f"{_DIAS[d.weekday()]} {d:%d-%m-%Y %H:%M}"


def tamano(bytes_: int | None) -> str:
    if bytes_ is None:
        return "?"
    n = float(bytes_)
    for unidad in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidad == "GB":
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024
    return f"{n:.1f} GB"  # pragma: no cover


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios simples."""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


def compactar(texto: str) -> str:
    """Solo letras y dígitos, normalizado: 'Pruebas de Software' → 'pruebasdesoftware'."""
    return re.sub(r"[^a-z0-9]", "", normalizar(texto))


_PREFIJO_PARENTESIS = re.compile(r"^\s*(\([^)]*\)\s*)+")
_PARALELOS = re.compile(r"paralelos?\s*:\s*([\w/ ,-]+)", re.IGNORECASE)
_SIGLA = re.compile(r"(?<![a-z])([a-z]{2,4})[-\s]?(\d{3,4})(?!\d)")


def limpiar_nombre_ramo(nombre: str) -> str:
    """'(202602)(INF466) INTRODUCCIÓN A LA BIOINFORMÁTICA|Paralelos:1' → 'INTRODUCCIÓN A LA BIOINFORMÁTICA'."""
    t = _PREFIJO_PARENTESIS.sub("", nombre or "")
    t = t.split("|")[0].strip()
    return t or (nombre or "").strip()


def paralelos_ramo(nombre: str) -> str | None:
    m = _PARALELOS.search(nombre or "")
    return m.group(1).strip() if m else None


def sigla_ramo(ramo: dict) -> str | None:
    """Sigla tipo 'INF331', buscada en el nombre completo y luego en el nombre corto."""
    for texto in (ramo.get("fullname") or "", ramo.get("shortname") or ""):
        m = _SIGLA.search(normalizar(texto.replace("_", " ")))
        if m:
            return (m.group(1) + m.group(2)).upper()
    return None


def sanear_nombre(nombre: str, maximo: int = 150) -> str:
    """Nombre de archivo o carpeta seguro: sin separadores, sin '..' y no vacío."""
    t = _INVALIDOS.sub("_", nombre or "").strip().strip(".").strip()
    if not t:
        t = "_"
    if len(t) > maximo:
        raiz, punto, ext = t.rpartition(".")
        if punto and 0 < len(ext) <= 10:
            t = raiz[: maximo - len(ext) - 1] + "." + ext
        else:
            t = t[:maximo]
    return t
