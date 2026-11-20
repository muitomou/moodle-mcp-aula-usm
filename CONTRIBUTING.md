# Cómo contribuir

¡Gracias por el interés! Los reportes de errores, ideas y *pull requests* son bienvenidos.

## Preparar el entorno

```bash
git clone https://github.com/muitomou/moodle-mcp-aula-usm.git
cd moodle-mcp-aula-usm
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Antes de abrir un PR

```bash
.venv/bin/ruff check .          # lint
.venv/bin/ruff format .         # formato
.venv/bin/pytest --cov          # tests con cobertura
```

CI corre lo mismo en Python 3.10 a 3.13.

- **Tests sin red**: usan el Moodle falso de `tests/conftest.py` y las respuestas de
  `tests/fixtures/moodle.json`. Si agregas una herramienta o un caso nuevo, suma la respuesta
  de ejemplo ahí, con datos inventados.
- **Nada de datos reales**: no subas tokens, `config.env` ni respuestas reales de Moodle
  (`tests/fixtures/real/` está en `.gitignore` por eso).
- **Solo lectura**: las herramientas no modifican nada en Moodle. Cualquier cosa que escriba
  (por ejemplo, subir entregas) tiene que pedir confirmación explícita y discutirse antes en
  un issue.
- **Commits**: mensajes en español con el estilo de
  [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/)
  (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`), un cambio por commit.
- **Registro de cambios**: anota los cambios visibles en la sección «Sin publicar» de
  [CHANGELOG.md](CHANGELOG.md).

## Reportar errores

Usa la [plantilla de error](https://github.com/muitomou/moodle-mcp-aula-usm/issues/new/choose)
e incluye la salida de `aula-mcp-check`, **sin** tu token ni datos personales.
