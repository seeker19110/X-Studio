.PHONY: test cov lint types fix login start stop status setup models
test:
	uv run pytest -q
cov:
	uv run pytest -q --cov
types:
	uv run mypy src/gateway --ignore-missing-imports
lint:
	uv run ruff check src tests
	uv run mypy src/gateway --ignore-missing-imports
fix:
	uv run ruff check --fix src tests
login:      # thêm một tài khoản Google vào pool (chạy nhiều lần để thêm nhiều tài khoản)
	uv run python -m gateway login
start:
	uv run python -m gateway start
stop:
	uv run python -m gateway stop
status:
	uv run python -m gateway status
setup:      # ghi software-company/llm.yaml trỏ vào gateway
	uv run python -m gateway setup
models:     # model gateway hỗ trợ + đối chiếu llm.yaml của các công ty (exit 1 nếu lệch)
	uv run python -m gateway models
