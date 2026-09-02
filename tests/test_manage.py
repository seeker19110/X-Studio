"""CLI: setup ghi llm.yaml; reset/logout thao tác pool."""

from __future__ import annotations

import yaml

from gateway import auth as gw_auth
from gateway import manage


def test_setup_writes_llm_yaml_preserving_other_keys(tmp_path):
    target = tmp_path / "llm.yaml"
    target.write_text("provider: anthropic\nmodels:\n  strong: claude-opus-5\nextra: {temperature: 0}\n", encoding="utf-8")
    assert manage.main(["setup", "--target", str(target), "--port", "9000"]) == 0
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["provider"] == "openai"
    assert data["base_url"] == "http://127.0.0.1:9000/v1"
    assert data["models"] == {"strong": manage.DEFAULT_STRONG_MODEL, "standard": manage.DEFAULT_STANDARD_MODEL}
    assert data["extra"] == {"temperature": 0}


def test_reset_and_logout(tmp_path, monkeypatch):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    mgr = gw_auth.AntigravityAuthManager()
    c = gw_auth.AntigravityCredentials(access_token="t", email="a@example.com", project_id="p")
    mgr.save_credentials(c)
    mgr.mark_account_unavailable(c, 429)
    assert manage.main(["reset", "a@example.com"]) == 0
    assert mgr.load_stored_credentials().unavailable_until == 0
    assert manage.main(["logout", "a@example.com"]) == 0
    assert manage.main(["logout", "a@example.com"]) == 1
    assert manage.main(["reset"]) == 1
