#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/opt/xstudio-runtime/bin:$HOME/.local/bin:$PWD/.venv/bin:$PATH"
export STUDIO_POLICY_KEY_FILE="${STUDIO_POLICY_KEY_FILE:-$PWD/output/live/policy.key}"
if [[ ! -f llm.yaml || ! -f media.yaml ]]; then
  echo "Cần cấu hình llm.yaml và media.yaml trước; xem README.md."
  exit 1
fi
uv run --no-sync python - <<'PY'
from studio.llm import load_config
from studio.media import load_media_config
llm = load_config()
providers = [b.get('provider') for b in llm.backends] or [llm.provider]
assert providers and all(p in {'codex', 'claude-code'} for p in providers), 'Cần backend CLI thật trong llm.yaml'
media = load_media_config()
assert all(s.get('provider') != 'fake' for s in [media.tts, media.image, media.video]), 'Cần provider media thật'
assert media.production.get('automation') in {'manual', 'production'}, 'Launcher này tạo MP4 và giữ cửa duyệt đăng'
PY
exec uv run --no-sync python -u -m studio.console --media media.yaml --db output/live/production.sqlite "$@"
