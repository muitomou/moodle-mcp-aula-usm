"""Vínculo ramo de Moodle → carpeta local, con detección automática por nombre."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .util import compactar, limpiar_nombre_ramo, normalizar, sigla_ramo

# Palabras que no aportan a abreviaturas ("Seminario de Sistemas y Gestión" → sem+sis+ges).
_VACIAS = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a", "al", "para", "por", "con"}
_ROMANOS = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}
_SEMESTRE = re.compile(r"^\d{4}-\d$")
_SIN_COINCIDENCIA = -(10**6)

PUNTAJE_NOMBRE_EXACTO = 100
PUNTAJE_SIGLA = 90
MARGEN_AUTOMATICO = 4  # diferencia mínima con el 2.º candidato para elegir sin preguntar
MINIMO_AUTOMATICO = 6  # 'IA' para «Introducción a la …» (3) no basta; para «Inteligencia Artificial» (8) sí


class CarpetaNoResuelta(Exception):
    def __init__(self, mensaje: str, candidatos: list[Path]):
        super().__init__(mensaje)
        self.candidatos = candidatos


def palabras_ramo(nombre: str) -> list[tuple[str, bool]]:
    """Palabras del nombre limpio como (palabra, es_vacía). 'Título I' → ('titulo'), ('1')."""
    palabras = re.findall(r"[a-z0-9]+", normalizar(limpiar_nombre_ramo(nombre)))
    if palabras and palabras[-1] in _ROMANOS:
        palabras[-1] = _ROMANOS[palabras[-1]]
    return [(p, p in _VACIAS) for p in palabras]


def puntaje_abreviatura(clave: str, nombre: str) -> int:
    """Qué tan bien `clave` (compactada) abrevia `nombre`; 0 si no lo abrevia.

    La clave debe poder escribirse concatenando prefijos de las palabras, en orden y pudiendo
    saltar palabras: 'pds' ← Pruebas De Software, 'seminariosisges' ← Seminario SIStemas GEStión.
    Cada palabra significativa usada suma 3, cada palabra vacía resta 2 y se suma el largo de
    la clave (claves más largas son más específicas).
    """
    palabras = tuple(palabras_ramo(nombre))

    @lru_cache(maxsize=None)
    def mejor(i: int, j: int) -> int:
        if i == len(clave):
            return 0
        resultado = _SIN_COINCIDENCIA
        for k in range(j, len(palabras)):
            palabra, vacia = palabras[k]
            largo = 0
            while largo < len(palabra) and i + largo < len(clave) and palabra[largo] == clave[i + largo]:
                largo += 1
                resto = mejor(i + largo, k + 1)
                if resto > _SIN_COINCIDENCIA:
                    resultado = max(resultado, resto + (-2 if vacia else 3))
        return resultado

    if not clave:
        return 0
    r = mejor(0, 0)
    return max(1, r + len(clave)) if r > _SIN_COINCIDENCIA else 0


def claves_sigla(ramo: dict[str, Any]) -> set[str]:
    """'INF331' → {'inf331', '331'}."""
    sigla = sigla_ramo(ramo)
    if not sigla:
        return set()
    clave = compactar(sigla)
    return {clave, re.sub(r"^[a-z]+", "", clave)}


def puntaje(nombre_carpeta: str, ramo: dict[str, Any]) -> int:
    """Puntaje de que una carpeta (o un texto) se refiera al ramo; 0 = no coincide."""
    clave = compactar(nombre_carpeta)
    if len(clave) < 2:
        return 0
    if clave == compactar(limpiar_nombre_ramo(ramo.get("fullname") or "")):
        return PUNTAJE_NOMBRE_EXACTO
    if clave in claves_sigla(ramo):
        return PUNTAJE_SIGLA
    return puntaje_abreviatura(clave, ramo.get("fullname") or "")


def elegir(puntajes: list[tuple[Any, int]]) -> Any | None:
    """El mejor candidato si es único o supera claramente al segundo; si no, None."""
    ordenados = sorted((p for p in puntajes if p[1] > 0), key=lambda p: -p[1])
    if not ordenados or ordenados[0][1] < MINIMO_AUTOMATICO:
        return None
    if len(ordenados) == 1 or ordenados[0][1] - ordenados[1][1] >= MARGEN_AUTOMATICO:
        return ordenados[0][0]
    return None


class Carpetas:
    def __init__(self, archivo: Path, dir_base: Path | None):
        self.archivo = archivo
        self.dir_base = dir_base

    # --- persistencia -------------------------------------------------
    def _leer(self) -> dict[str, dict[str, str]]:
        try:
            return json.loads(self.archivo.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _escribir(self, datos: dict[str, dict[str, str]]) -> None:
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        self.archivo.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")

    def obtener(self, ramo_id: int) -> Path | None:
        entrada = self._leer().get(str(ramo_id))
        return Path(entrada["carpeta"]) if entrada else None

    def vinculos(self) -> dict[int, Path]:
        return {int(k): Path(v["carpeta"]) for k, v in self._leer().items()}

    def vincular(self, ramo: dict[str, Any], carpeta: Path) -> Path:
        carpeta = carpeta.expanduser()
        if not carpeta.is_absolute():
            if not self.dir_base:
                raise CarpetaNoResuelta("Indica una ruta absoluta o configura AULA_DIR_BASE.", [])
            carpeta = self.dir_base / carpeta
        carpeta = carpeta.resolve()
        if not carpeta.is_dir():
            raise CarpetaNoResuelta(f"La carpeta {carpeta} no existe (no se crean carpetas solas).", [])
        datos = self._leer()
        datos[str(ramo["id"])] = {
            "carpeta": str(carpeta),
            "ramo": limpiar_nombre_ramo(ramo.get("fullname") or ramo.get("shortname") or ""),
        }
        self._escribir(datos)
        return carpeta

    # --- detección ----------------------------------------------------
    def _subcarpetas(self) -> list[Path]:
        if not self.dir_base or not self.dir_base.is_dir():
            return []
        salida: list[Path] = []
        for p in sorted(self.dir_base.iterdir()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            if _SEMESTRE.match(p.name):
                # Semestres pasados: buscar un nivel más adentro.
                salida.extend(q for q in sorted(p.iterdir()) if q.is_dir() and not q.name.startswith("."))
            else:
                salida.append(p)
        return salida

    def candidatos(self, ramo: dict[str, Any]) -> list[tuple[Path, int]]:
        """Carpetas que coinciden, de mejor a peor (las de la raíz antes que las de semestres)."""
        con_puntaje = [(p, puntaje(p.name, ramo)) for p in self._subcarpetas()]
        return sorted(
            ((p, s) for p, s in con_puntaje if s > 0),
            key=lambda x: (x[0].parent != self.dir_base, -x[1]),
        )

    def resolver(self, ramo: dict[str, Any]) -> tuple[Path, bool]:
        """Devuelve (carpeta, recien_vinculada). Lanza CarpetaNoResuelta si no hay una opción clara."""
        nombre = limpiar_nombre_ramo(ramo.get("fullname") or "")
        vinculada = self.obtener(ramo["id"])
        if vinculada:
            if vinculada.is_dir():
                return vinculada, False
            raise CarpetaNoResuelta(
                f"La carpeta vinculada {vinculada} ya no existe. Vincula otra con vincular_carpeta.",
                [p for p, _ in self.candidatos(ramo)],
            )
        if not self.dir_base:
            raise CarpetaNoResuelta(
                "No hay carpeta vinculada para este ramo ni AULA_DIR_BASE configurado. "
                "Usa vincular_carpeta con una ruta absoluta.",
                [],
            )
        cands = self.candidatos(ramo)
        # Preferir carpetas en la raíz (ramos vigentes) sobre las de semestres pasados.
        en_raiz = [c for c in cands if c[0].parent == self.dir_base]
        elegida = elegir(en_raiz or cands)
        if elegida:
            return self.vincular(ramo, elegida), True
        if not cands:
            raise CarpetaNoResuelta(
                f"No encontré una carpeta para «{nombre}» en {self.dir_base}. "
                "Usa vincular_carpeta para elegirla.",
                [],
            )
        raise CarpetaNoResuelta(
            f"No hay una carpeta clara para «{nombre}». Usa vincular_carpeta para elegir.",
            [p for p, _ in (en_raiz or cands)],
        )
