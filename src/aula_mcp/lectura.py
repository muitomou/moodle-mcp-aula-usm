"""Extracción de texto de archivos descargados (PDF y formatos de texto)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ~30 000 caracteres ≈ 8-9 mil tokens: suficiente para un enunciado sin inundar el contexto.
MAX_CARACTERES = 30_000
# fmt: off
_TEXTO = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".tex", ".bib", ".html", ".htm",
    ".py", ".ipynb", ".c", ".h", ".cpp", ".hpp", ".java", ".js", ".ts", ".sql", ".r", ".m", ".sh",
    ".fasta", ".fa", ".fastq", ".gb", ".pdb",
}
# fmt: on


def _rango(paginas: str | None, total: int) -> list[int]:
    """'3' → [2]; '2-4' → [1,2,3]; '5-' → [4..total-1]; None → todas. Índices base 0."""
    if not paginas:
        return list(range(total))
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d*)\s*)?", paginas)
    if not m:
        raise ValueError("Formato de páginas inválido; usa '3', '2-5' o '4-'.")
    ini = max(1, int(m.group(1)))
    fin = int(m.group(2)) if m.group(2) else (total if "-" in paginas else ini)
    indices = list(range(ini - 1, min(fin, total)))
    if not indices and total:
        raise ValueError(f"El archivo tiene {total} páginas; pide un rango entre 1 y {total}.")
    return indices


def _recortar(texto: str, salida: dict[str, Any]) -> None:
    if len(texto) > MAX_CARACTERES:
        salida["texto"] = texto[:MAX_CARACTERES]
        salida["truncado"] = True
    else:
        salida["texto"] = texto


def extraer_texto(ruta: Path, paginas: str | None = None) -> dict[str, Any]:
    ext = ruta.suffix.lower()
    salida: dict[str, Any] = {}
    if ext == ".pdf":
        from pypdf import PdfReader

        try:
            lector = PdfReader(str(ruta))
            total = len(lector.pages)
            indices = _rango(paginas, total)
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:  # PDF dañado o cifrado
            return {
                "error": f"No pude leer el PDF ({e.__class__.__name__}). Ábrelo desde guardado_en."
            }
        partes, leidas = [], []
        largo = 0
        for i in indices:
            t = (lector.pages[i].extract_text() or "").strip()
            partes.append(f"[página {i + 1}]\n{t}")
            leidas.append(i + 1)
            largo += len(t)
            if largo > MAX_CARACTERES:
                break
        salida["paginas_totales"] = total
        salida["paginas_leidas"] = f"{leidas[0]}-{leidas[-1]}" if leidas else "ninguna"
        texto = "\n\n".join(partes)
        _recortar(texto, salida)
        if leidas and leidas[-1] < (indices[-1] + 1 if indices else 0):
            salida["truncado"] = True
        if salida.get("truncado"):
            salida["siguiente"] = f"Pide paginas='{leidas[-1] + 1}-' para seguir leyendo."
        if leidas and largo < 40 * len(leidas):
            salida["aviso"] = (
                "El PDF casi no tiene texto (¿escaneado o con fórmulas como imágenes?). "
                "Ábrelo desde guardado_en para verlo completo."
            )
        return salida
    if ext in _TEXTO:
        _recortar(ruta.read_text(encoding="utf-8", errors="replace"), salida)
        return salida
    return {
        "aviso": f"No extraigo texto de archivos {ext or 'sin extensión'}; quedó guardado en guardado_en."
    }
