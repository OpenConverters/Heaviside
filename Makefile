.PHONY: help venv install types types-check probe gen-topologies lint type test test-unit ci clean

help:
	@echo "Heaviside developer targets:"
	@echo "  make venv       Create .venv via uv"
	@echo "  make install    Install heaviside + dev deps"
	@echo "  make types         Regenerate schema classes from MAS/PEAS/SAS/CAS/RAS (requires quicktype)"
	@echo "  make types-check   Verify schema submodules are present"
	@echo "  make probe         Empirical PyOpenMagnetics topology probe → docs/probe-report.md"
	@echo "  make gen-topologies  Regenerate heaviside/topologies/<name>.py from registry"
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
	python scripts/gen_types.py

types-check:
	python scripts/gen_types.py --check

probe:
	python scripts/probe_topologies.py

gen-topologies:
	python scripts/gen_topology_modules.py

lint:
	ruff check .
	ruff format --check .

type: types
	mypy heaviside

test: types
	pytest

test-unit: types
	pytest -m unit -n auto

ci: lint type test-unit
	python scripts/check_pydantic_cap.py

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache build dist *.egg-info htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
