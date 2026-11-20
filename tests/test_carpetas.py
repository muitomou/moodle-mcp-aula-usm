"""Casos borde de Carpetas: vínculos rotos, sin AULA_DIR_BASE y carpetas que no coinciden."""

import pytest

from aula_mcp.carpetas import CarpetaNoResuelta, Carpetas, claves_sigla, puntaje_abreviatura

PDS = {"id": 101, "fullname": "(202602)(INF331) PRUEBAS DE SOFTWARE|Paralelos:200"}


def test_abreviatura_vacia_y_ramo_sin_sigla():
    assert puntaje_abreviatura("", "Pruebas de Software") == 0
    assert claves_sigla({"fullname": "Taller sin sigla", "shortname": "taller"}) == set()


def test_vincular_ruta_relativa_sin_dir_base(tmp_path):
    carpetas = Carpetas(tmp_path / "carpetas.json", None)
    with pytest.raises(CarpetaNoResuelta, match="ruta absoluta"):
        carpetas.vincular(PDS, tmp_path.relative_to(tmp_path.parent))


def test_vincular_ruta_absoluta_sin_dir_base(tmp_path):
    destino = tmp_path / "pds"
    destino.mkdir()
    carpetas = Carpetas(tmp_path / "config" / "carpetas.json", None)
    assert carpetas.vincular(PDS, destino) == destino.resolve()
    assert carpetas.resolver(PDS) == (destino.resolve(), False)


def test_resolver_sin_dir_base_ni_vinculo(tmp_path):
    carpetas = Carpetas(tmp_path / "carpetas.json", None)
    assert carpetas.candidatos(PDS) == []
    with pytest.raises(CarpetaNoResuelta, match="AULA_DIR_BASE"):
        carpetas.resolver(PDS)


def test_resolver_vinculo_a_carpeta_borrada(tmp_path):
    base = tmp_path / "UNIVERSIDAD"
    (base / "pds").mkdir(parents=True)
    vieja = base / "vieja"
    vieja.mkdir()
    carpetas = Carpetas(tmp_path / "carpetas.json", base)
    carpetas.vincular(PDS, vieja)
    vieja.rmdir()
    with pytest.raises(CarpetaNoResuelta, match="ya no existe") as e:
        carpetas.resolver(PDS)
    # Sugiere las candidatas para volver a vincular.
    assert e.value.candidatos == [base / "pds"]


def test_resolver_sin_candidatas(tmp_path):
    base = tmp_path / "UNIVERSIDAD"
    (base / "Certificaciones").mkdir(parents=True)
    with pytest.raises(CarpetaNoResuelta, match="No encontré una carpeta") as e:
        Carpetas(tmp_path / "carpetas.json", base).resolver(PDS)
    assert e.value.candidatos == []


def test_subcarpetas_ignora_archivos_y_ocultas(tmp_path):
    base = tmp_path / "UNIVERSIDAD"
    (base / ".pds").mkdir(parents=True)
    (base / "2025-2" / ".pds").mkdir(parents=True)
    (base / "pds.txt").write_text("no soy carpeta")
    carpetas = Carpetas(tmp_path / "carpetas.json", base)
    assert carpetas.candidatos(PDS) == []
    (base / "pds").mkdir()
    assert [p for p, _ in carpetas.candidatos(PDS)] == [base / "pds"]


def test_carpetas_json_danado_se_trata_como_vacio(tmp_path):
    archivo = tmp_path / "carpetas.json"
    archivo.write_text("{no es json")
    assert Carpetas(archivo, None).vinculos() == {}
