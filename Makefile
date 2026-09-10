.PHONY: llm sync test cov types demo lint fix golden gate eval eval-record eval-replay run watch status test-all cov-all lint-all
sync:           # một venv chung cho studio + gateway + xagents-core (vendor tại chỗ, xem pyproject.toml)
	uv sync --all-packages
llm:           # cài hồ sơ model chạy thật: make llm PROFILE=claude-gateway (không ghi đè llm.yaml đang có)
	@test -f llm.yaml && echo "llm.yaml đã có, không ghi đè (xoá nó nếu muốn cài lại)" || \
	 (uv run python -m gateway ready --quiet && \
	  cp llm.$(or $(PROFILE),claude-gateway).yaml llm.yaml && \
	  echo "đã tạo llm.yaml từ llm.$(or $(PROFILE),claude-gateway).yaml")
test:
	uv run pytest -q
cov:
	uv run pytest -q --cov
test-all:       # studio + gateway + xagents-core, ba pytest riêng (ba `fail_under=100` độc lập)
	uv run pytest -q
	cd gateway && uv run pytest -q
	cd xagents-core && uv run pytest -q
cov-all:
	uv run pytest -q --cov
	cd gateway && uv run pytest -q --cov
	cd xagents-core && uv run pytest -q --cov
lint-all:
	uv run ruff check src tests
	cd gateway && uv run ruff check src tests
	cd xagents-core && uv run ruff check src tests
	uv run mypy src/studio --ignore-missing-imports
	cd gateway && uv run mypy src --ignore-missing-imports
	cd xagents-core && uv run mypy src
types:
	uv run mypy src/studio --ignore-missing-imports
demo:
	uv run python -m studio.demo
lint:
	uv run ruff check src tests
	uv run mypy src/studio --ignore-missing-imports
fix:
	uv run ruff check --fix src tests
golden:
	UPDATE_GOLDEN=1 uv run pytest -q tests/test_golden_agents.py
gate:
	uv run python -m studio.gate_cli list
eval:
	uv run python -m studio.evals $(AGENT)
eval-record:   # RUNS=N chạy mỗi ca N lần và ghi `score` = tỉ lệ đạt (p3.3b); thời gian × N.
               # chạy model thật, lưu evals/recordings/$(AGENT).json — bắt buộc sau khi đổi prompt/skill
	uv run python -m studio.evals $(AGENT) --record --jobs $(or $(JOBS),1) $(if $(RUNS),--runs $(RUNS),)
eval-replay:   # như CI: phát lại từ bản ghi, không gọi model
	uv run python -m studio.evals all --replay --strict
run:
	uv run python -m studio.orchestrator run
watch:
	uv run python -m studio.orchestrator run --watch 5
status:
	uv run python -m studio.orchestrator status
