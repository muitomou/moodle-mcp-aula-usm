"""Cliente mínimo de la API REST de Moodle (servicio moodle_mobile_app)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

# httpx registra cada URL en INFO; las descargas llevan ?token=…, así que no deben quedar en logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class MoodleError(Exception):
    """Error devuelto por Moodle o de conexión. El mensaje es apto para el usuario."""

    def __init__(self, mensaje: str, codigo: str | None = None):
        super().__init__(mensaje)
        self.codigo = codigo


class TokenInvalido(MoodleError):
    pass


# 30 s para leer (Moodle puede tardar en armar respuestas grandes), pero solo 10 s para conectar:
# si no hay red, conviene saberlo pronto.
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def aplanar(valor: Any, prefijo: str = "") -> dict[str, str]:
    """Convierte parámetros anidados al formato de Moodle: {'ids': [1]} → {'ids[0]': '1'}."""
    salida: dict[str, str] = {}
    if isinstance(valor, dict):
        for clave, v in valor.items():
            salida.update(aplanar(v, f"{prefijo}[{clave}]" if prefijo else str(clave)))
    elif isinstance(valor, (list, tuple)):
        for i, v in enumerate(valor):
            salida.update(aplanar(v, f"{prefijo}[{i}]"))
    elif isinstance(valor, bool):
        salida[prefijo] = "1" if valor else "0"
    elif valor is not None:
        salida[prefijo] = str(valor)
    return salida


class MoodleClient:
    def __init__(
        self,
        url: str,
        token: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | httpx.Timeout = TIMEOUT,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self._host = urlsplit(self.url).netloc
        self._http = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
            headers={"User-Agent": "aula-mcp"},
        )

    async def cerrar(self) -> None:
        await self._http.aclose()

    def _exigir_token(self) -> str:
        if not self.token:
            raise TokenInvalido(
                "No hay token configurado. Ejecuta `aula-mcp-login` en una terminal.", "notoken"
            )
        return self.token

    async def llamar(self, funcion: str, **params: Any) -> Any:
        datos = {
            "wstoken": self._exigir_token(),
            "wsfunction": funcion,
            "moodlewsrestformat": "json",
            **aplanar(params),
        }
        try:
            r = await self._http.post(f"{self.url}/webservice/rest/server.php", data=datos)
        except httpx.HTTPError as e:
            raise self._error_de_red(e, f"consultar {funcion}") from e
        if r.status_code != 200:
            raise MoodleError(f"Moodle respondió HTTP {r.status_code} en {funcion}.")
        try:
            respuesta = r.json()
        except ValueError as e:
            raise MoodleError(f"Moodle devolvió una respuesta que no es JSON en {funcion}.") from e
        self._revisar_error(respuesta, funcion)
        return respuesta

    def _error_de_red(self, e: httpx.HTTPError, accion: str) -> MoodleError:
        """Traduce un error de httpx a un mensaje que el usuario pueda entender."""
        if isinstance(e, httpx.TimeoutException):
            return MoodleError(
                f"{self.url} no respondió a tiempo al {accion}. El sitio puede estar lento o "
                "caído; intenta de nuevo en unos minutos.",
                "timeout",
            )
        if isinstance(e, httpx.ConnectError):
            return MoodleError(
                f"No se pudo conectar con {self.url} al {accion}. Revisa tu conexión a internet "
                "y la URL configurada en AULA_URL.",
                "sinconexion",
            )
        return MoodleError(
            f"Error de red con {self.url} al {accion} ({e.__class__.__name__}).", "red"
        )

    @staticmethod
    def _revisar_error(respuesta: Any, funcion: str) -> None:
        if not (isinstance(respuesta, dict) and "exception" in respuesta):
            return
        codigo = respuesta.get("errorcode")
        mensaje = respuesta.get("message") or codigo or "error desconocido"
        if codigo == "invalidtoken":
            raise TokenInvalido(
                "El token no es válido o expiró. Ejecuta `aula-mcp-login` para renovarlo.", codigo
            )
        raise MoodleError(f"Moodle rechazó {funcion}: {mensaje} ({codigo})", codigo)

    def es_url_propia(self, url: str) -> bool:
        partes = urlsplit(url)
        return partes.scheme in ("http", "https") and partes.netloc == self._host

    async def descargar(self, url_archivo: str, destino: Path) -> int:
        """Descarga un archivo de Moodle a `destino` y devuelve los bytes escritos.

        El token solo se envía al mismo host del Moodle configurado.
        """
        if not self.es_url_propia(url_archivo):
            raise MoodleError(f"URL de archivo fuera de {self.url}; no se envía el token.")
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporal = destino.with_name(destino.name + ".parcial")
        escritos = 0
        try:
            async with self._http.stream(
                "GET", url_archivo, params={"token": self._exigir_token()}
            ) as r:
                if r.status_code != 200:
                    raise MoodleError(f"HTTP {r.status_code} al descargar {destino.name}.")
                tipo = r.headers.get("content-type", "")
                with open(temporal, "wb") as f:
                    async for bloque in r.aiter_bytes():
                        f.write(bloque)
                        escritos += len(bloque)
            if tipo.startswith("application/json") and escritos < 4096:
                with contextlib.suppress(ValueError):
                    self._revisar_error(json.loads(temporal.read_bytes()), "descarga")
            os.replace(temporal, destino)
        except httpx.HTTPError as e:
            raise self._error_de_red(e, f"descargar {destino.name}") from e
        finally:
            if temporal.exists():
                temporal.unlink()
        return escritos
