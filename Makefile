.PHONY: help venv install types lint type test test-unit ci clean

help:
	@echo "Heaviside developer targets:"
	@echo "  make venv       Create .venv via uv"
	@echo "  make install    Install heaviside + dev deps"
	@echo "  make types      Regenerate TypedDicts from MAS/PEAS/SAS/CAS/RAS schemas (Phase 1)"
	@echo "  make lint       ruff check + format check"
	@echo "  make type       mypy --strict"
	@echo "  make test       Full pytest suite"
	@echo "  make test-unit  Unit tests only (fast)"
	@echo "  make ci         lint + type + test-unit + BaseModel cap"
	@echo "  make clean      Remove build / cache artefacts"

venv:
	uv venv

install:
	uv pip install -e '.[dev]'

types:
	@echo "Phase 1: invokes 'npx quicktype' on schema submodules → heaviside/types/_generated/"
	@echo "Placeholder; implementation lands with Phase 1."

lint:
	ruff check .
	ruff format --check .

type:
	mypy heaviside

test:
	pytest

test-unit:
	pytest -m unit -n auto

ci: lint type test-unit
	python scripts/check_pydantic_cap.py

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache build dist *.egg-info htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
