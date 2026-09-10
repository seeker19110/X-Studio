# Lõi chung của mọi công ty (ADR gốc 0001). `types` KHÔNG có --ignore-missing-imports như bốn package kia:
# core chạy mypy `strict` (K6.1), bỏ qua import thiếu là mất đúng phần bảo đảm vừa bật.
.PHONY: test cov lint types fix
test:
	uv run pytest -q
cov:
	uv run pytest -q --cov
types:
	uv run mypy src/xagents_core
lint:
	uv run ruff check src tests
	uv run mypy src/xagents_core
fix:
	uv run ruff check --fix src tests
