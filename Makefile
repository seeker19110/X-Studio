.PHONY: test demo lint fix golden gate eval eval-record eval-replay run watch status
test:
	uv run pytest -q
demo:
	PYTHONPATH=src uv run python -m studio.demo
lint:
	uv run ruff check src tests
fix:
	uv run ruff check --fix src tests
golden:
	UPDATE_GOLDEN=1 uv run pytest -q tests/test_golden_agents.py
gate:
	PYTHONPATH=src uv run python -m studio.gate_cli list
eval:
	PYTHONPATH=src uv run python -m studio.evals $(AGENT)
eval-record:   # chạy model thật, lưu evals/recordings/$(AGENT).json — bắt buộc sau khi đổi prompt/skill
	PYTHONPATH=src uv run python -m studio.evals $(AGENT) --record
eval-replay:   # như CI: phát lại từ bản ghi, không gọi model
	PYTHONPATH=src uv run python -m studio.evals all --replay
run:
	PYTHONPATH=src uv run python -m studio.orchestrator run
watch:
	PYTHONPATH=src uv run python -m studio.orchestrator run --watch 5
status:
	PYTHONPATH=src uv run python -m studio.orchestrator status
