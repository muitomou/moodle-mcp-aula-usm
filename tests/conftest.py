"""Moodle falso para los tests: responde con tests/fixtures/moodle.json vía httpx.MockTransport."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from aula_mcp.carpetas import Carpetas
from aula_mcp.moodle import MoodleClient
from aula_mcp.servicio import Aula

URL = "https://moodle.ejemplo.cl"
TOKEN = "token-de-prueba"
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "moodle.json").read_text(encoding="utf-8"))


def _con_fechas(valor):
    """Reemplaza marcadores __AHORA_MAS_N_DIAS__ por timestamps relativos a hoy."""
    if isinstance(valor, dict):
        return {k: _con_fechas(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_con_fechas(v) for v in valor]
    if isinstance(valor, str) and valor.startswith("__AHORA_MAS_"):
        dias = int(valor.removeprefix("__AHORA_MAS_").removesuffix("_DIAS__"))
        return int(time.time()) + dias * 86400
    return valor


class FakeMoodle:
    def __init__(self):
        self.respuestas = _con_fechas(copy.deepcopy(FIXTURES))
        self.llamadas: list[tuple[str, dict[str, str]]] = []
        self.descargas: list[httpx.Request] = []
        self.fallar: dict[str, dict] = {}
        self.contenido_descarga = b"contenido!!"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "/webservice/pluginfile.php/" in request.url.path:
            self.descargas.append(request)
            return httpx.Response(200, content=self.contenido_descarga, headers={"content-type": "application/pdf"})
        datos = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        funcion = datos["wsfunction"]
        self.llamadas.append((funcion, datos))
        if datos.get("wstoken") != TOKEN:
            return httpx.Response(200, json={"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Token no válido"})
        if funcion in self.fallar:
            return httpx.Response(200, json=self.fallar[funcion])
        r = self.respuestas[funcion]
        if funcion == "mod_forum_get_forum_discussions":
            r = r[datos["forumid"]]
        return httpx.Response(200, json=r)

    def funciones(self) -> list[str]:
        return [f for f, _ in self.llamadas]


@pytest.fixture
def moodle() -> FakeMoodle:
    return FakeMoodle()


@pytest.fixture
def cliente(moodle) -> MoodleClient:
    return MoodleClient(URL, TOKEN, transport=httpx.MockTransport(moodle))


@pytest.fixture
def universidad(tmp_path) -> Path:
    """Estructura parecida a ~/Escritorio/UNIVERSIDAD."""
    base = tmp_path / "UNIVERSIDAD"
    for nombre in ["pds", "Pruebas de Software", "seminariosisges", "bioinf", "IA", "Certificaciones", "2025-2/DIU", "2025-2/SISGES"]:
        (base / nombre).mkdir(parents=True)
    return base


@pytest.fixture
def aula(cliente, universidad, tmp_path) -> Aula:
    return Aula(cliente, Carpetas(tmp_path / "config" / "carpetas.json", universidad))


def pdf_minimo(paginas: list[str]) -> bytes:
    """PDF válido con una línea de texto por página (para probar la extracción)."""
    objetos = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(paginas)))
    objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(paginas)} >>")
    fuente = 3 + 2 * len(paginas)
    for i, texto in enumerate(paginas):
        objetos.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {4 + 2 * i} 0 R "
                       f"/Resources << /Font << /F1 {fuente} 0 R >> >> >>")
        flujo = f"BT /F1 12 Tf 20 100 Td ({texto}) Tj ET"
        objetos.append(f"<< /Length {len(flujo)} >>\nstream\n{flujo}\nendstream")
    objetos.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    salida = b"%PDF-1.4\n"
    offsets = []
    for n, obj in enumerate(objetos, start=1):
        offsets.append(len(salida))
        salida += f"{n} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    salida += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    salida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return salida


def correr(coro):
    return asyncio.run(coro)
