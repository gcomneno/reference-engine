# GiadaWare Reference Engine

Model-driven engine for deterministic extraction, validation, provenance, and querying of personal reference documents.

## Status

Architecture approved. Implementation work is tracked through small, verifiable GitHub issues.

## Documentation

- [Architecture overview](docs/architecture/overview.md)
- [Project roadmap](docs/roadmap.md)

## Development

Python 3.12 or newer is required. Create and activate a virtual environment, then install the project and development tools:

```sh
python -m pip install -e ".[dev]"
```

Run the complete initial quality checks with:

```sh
python -m pytest
ruff check .
python -m mypy
```
