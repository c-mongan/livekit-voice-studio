.PHONY: studio studio-build studio-check test

studio:
	uv run --no-sync python -m examples.studio

studio-build:
	npm --prefix web run build

studio-check:
	uv run --no-sync python -m examples.studio --check

test:
	uv run --no-sync ruff check .
	uv run --no-sync ruff format --check .
	uv run --no-sync mypy
	uv run --no-sync pytest -m "not integration"
	npm --prefix web run typecheck
	npm --prefix web test
