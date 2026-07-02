.PHONY: help venv install types types-check probe gen-topologies lint type test test-unit test-smoke test-full ci clean

# Curated, hermetic, fast key tests — the PR smoke gate. No TAS data / PyOM /
# ngspice / LLM, so it runs in seconds and is deterministic. Keep this list to
# the highest-signal correctness guards (parse, stress/loss physics, the
# realism gate, guardrails, review wiring, API security).
SMOKE_TESTS := \
	tests/unit/test_value_parse.py \
	tests/unit/test_pipeline_analyst.py \
	tests/unit/test_pipeline_stress.py \
	tests/unit/test_realism.py \
	tests/unit/test_guardrails_g8_g9.py \
	tests/unit/test_no_invented_values.py \
	tests/unit/test_match_score_keys.py \
	tests/unit/test_review_verdicts_surfaced.py \
	tests/unit/test_otto_reguard.py \
	tests/unit/test_gate_isat_failloud.py \
	tests/unit/test_index_no_partial_cache.py \
	tests/unit/test_api_security.py \
	tests/unit/test_design_spec.py

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
	@echo "  make test-smoke Curated fast key tests — the PR gate (seconds)"
	@echo "  make test-full  All unit + regression tests (needs PyOM + TAS LFS)"
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

# Fast PR gate: a selected few key tests, hermetic, ~seconds.
test-smoke:
	pytest $(SMOKE_TESTS) -q

# Everything: all unit + regression tests. Needs PyOpenMagnetics built and the
# TAS Git-LFS data smudged (git lfs pull in TAS/). Slower (~20 min).
test-full: types
	pytest tests/unit tests/regression

ci: lint type test-unit
	python scripts/check_pydantic_cap.py

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache build dist *.egg-info htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
