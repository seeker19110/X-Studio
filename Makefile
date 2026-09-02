.PHONY: test lint fix login start stop status setup
test:
	uv run pytest -q
lint:
	uv run ruff check src tests
fix:
	uv run ruff check --fix src tests
login:      # thêm một tài khoản Google vào pool (chạy nhiều lần để thêm nhiều tài khoản)
	PYTHONPATH=src uv run python -m gateway login
start:
	PYTHONPATH=src uv run python -m gateway start
stop:
	PYTHONPATH=src uv run python -m gateway stop
status:
	PYTHONPATH=src uv run python -m gateway status
setup:      # ghi software-company/llm.yaml trỏ vào gateway
	PYTHONPATH=src uv run python -m gateway setup
