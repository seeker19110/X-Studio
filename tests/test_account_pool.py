"""Pool tài khoản: lưu nhiều tài khoản, cooldown, refresh, ưu tiên theo bearer."""

from __future__ import annotations

import json
import os
import stat
import threading
import urllib.error

import pytest

from gateway import auth as gw_auth


def _creds(name: str, **kw) -> gw_auth.AntigravityCredentials:
    base = {"access_token": f"token-{name}", "email": f"{name}@example.com", "project_id": f"project-{name}"}
    base.update(kw)
    return gw_auth.AntigravityCredentials(**base)


@pytest.fixture
def manager(tmp_path):
    return gw_auth.AntigravityAuthManager(auth_file=tmp_path / "tokens.json")


def test_save_keeps_all_accounts(manager):
    manager.save_credentials(_creds("a"))
    manager.save_credentials(_creds("b"))
    emails = sorted(c.email for c in manager.load_all_stored_credentials())
    assert emails == ["a@example.com", "b@example.com"]
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))
    assert set(stored["accounts"]) == {"a@example.com", "b@example.com"}


def test_legacy_flat_file_is_read(manager):
    manager.token_file.parent.mkdir(parents=True, exist_ok=True)
    manager.token_file.write_text(json.dumps(_creds("solo").to_dict()), encoding="utf-8")
    assert [c.email for c in manager.load_all_stored_credentials()] == ["solo@example.com"]


def test_cooldown_excludes_rate_limited_account(manager, caplog):
    first, second = _creds("a"), _creds("b")
    manager.save_credentials(first)
    manager.save_credentials(second)
    manager.mark_account_unavailable(first, 429, retry_after="60")

    reloaded = gw_auth.AntigravityAuthManager(auth_file=manager.token_file)
    assert [c.email for c in reloaded.resolve_credential_candidates()] == ["b@example.com"]
    limited = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert limited["last_failure_status"] == 429
    assert limited["unavailable_until"] > 0


def test_retry_after_overrides_default_cooldown(manager):
    c = _creds("a")
    manager.save_credentials(c)
    manager.mark_account_unavailable(c, 429, retry_after="5")
    import time

    assert 0 < c.unavailable_until - time.time() <= 5.5


def test_reset_clears_cooldown(manager):
    c = _creds("a")
    manager.save_credentials(c)
    manager.mark_account_unavailable(c, 403)
    manager.mark_account_healthy(c)
    assert [x.email for x in manager.resolve_credential_candidates()] == ["a@example.com"]


def test_concurrent_cooldowns_both_persist(manager):
    first, second = _creds("a"), _creds("b")
    manager.save_credentials(first)
    manager.save_credentials(second)
    for i in range(300):
        manager.save_credentials(_creds(f"filler{i}"))

    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def mark(creds, status):
        try:
            start.wait()
            manager.mark_account_unavailable(creds, status)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=mark, args=(first, 401)), threading.Thread(target=mark, args=(second, 429))]
    for t in threads:
        t.start()
    start.wait()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]
    assert stored["a@example.com"]["last_failure_status"] == 401
    assert stored["b@example.com"]["last_failure_status"] == 429


def test_refresh_rejected_by_google_cools_account(manager, caplog):
    manager.save_credentials(_creds("a", refresh_token="bad", expires_at=1))
    manager.save_credentials(_creds("b"))

    def fail(_c):
        raise urllib.error.HTTPError("https://oauth2.googleapis.com/token", 400, "invalid_grant", {}, None)

    manager.refresh_access_token = fail
    assert [c.email for c in manager.resolve_credential_candidates()] == ["b@example.com"]
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert stored["last_failure_status"] == 401
    assert "token-a" not in caplog.text and "bad" not in caplog.text.split("refresh token")[0]


def test_transient_refresh_error_skips_without_cooldown(manager):
    # Một nhịp mạng chập chờn không được làm nguội cả pool.
    manager.save_credentials(_creds("a", refresh_token="ok", expires_at=1))
    manager.save_credentials(_creds("b"))

    def fail(_c):
        raise TimeoutError("network timed out")

    manager.refresh_access_token = fail
    assert [c.email for c in manager.resolve_credential_candidates()] == ["b@example.com"]
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert stored.get("unavailable_until", 0) == 0


def test_expired_without_refresh_token_is_skipped(manager):
    manager.save_credentials(_creds("a", expires_at=1))
    manager.save_credentials(_creds("b"))
    assert [c.email for c in manager.resolve_credential_candidates()] == ["b@example.com"]


def test_all_cooled_accounts_raise_429_with_wait_hint(manager):
    for name in ("a", "b"):
        c = _creds(name)
        manager.save_credentials(c)
        manager.mark_account_unavailable(c, 429, retry_after="60")
    with pytest.raises(gw_auth.UpstreamError, match="cooldown") as exc:
        manager.resolve_credential_candidates()
    assert exc.value.status_code == 429
    assert "Thử lại sau" in str(exc.value)


def test_empty_pool_raises_runtime_error(manager):
    with pytest.raises(RuntimeError, match="login"):
        manager.resolve_credential_candidates()


def test_bearer_token_prioritizes_matching_account(manager):
    for name in ("a", "b"):
        manager.save_credentials(_creds(name))
    by_token = manager.resolve_credential_candidates(bearer_token="token-b")
    assert [c.email for c in by_token] == ["b@example.com", "a@example.com"]
    by_email = manager.resolve_credential_candidates(bearer_token="B@example.com")
    assert by_email[0].email == "b@example.com"


def test_bearer_email_prefix_does_not_match(manager):
    # "b" từng khớp "b@example.com" qua startswith → giờ phải so bằng chính xác (không phân biệt hoa thường).
    manager.save_credentials(_creds("a"))
    manager.save_credentials(_creds("b"))
    manager.resolve_credential_candidates()  # a được dùng → LRU đẩy b lên đầu dù không khớp bearer
    assert [c.email for c in manager.resolve_credential_candidates(bearer_token="b")] == ["b@example.com", "a@example.com"]
    manager.resolve_credential_candidates(bearer_token="a@example.com")
    assert manager.resolve_credential_candidates(bearer_token="b")[0].email == "b@example.com"


def test_round_robin_least_recently_used(manager):
    manager.save_credentials(_creds("a"))
    manager.save_credentials(_creds("b"))
    first = manager.resolve_credential_candidates()[0].email
    second = manager.resolve_credential_candidates()[0].email
    third = manager.resolve_credential_candidates()[0].email
    assert first != second and third == first
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]
    assert stored[first]["last_used_at"] > 0 and stored[second]["last_used_at"] > 0


def test_mark_unavailable_keeps_newer_token_from_other_request(manager):
    stale = _creds("a")
    manager.save_credentials(stale)
    fresh = _creds("a", access_token="token-a-NEW", refresh_token="r2", expires_at=9e9)
    manager.save_credentials(fresh)  # request khác vừa refresh
    manager.mark_account_unavailable(stale, 429, retry_after="30")
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert stored["access_token"] == "token-a-NEW" and stored["refresh_token"] == "r2"
    assert stored["last_failure_status"] == 429 and stored["unavailable_until"] > 0
    manager.mark_account_healthy(stale)
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert stored["access_token"] == "token-a-NEW" and stored["unavailable_until"] == 0


@pytest.mark.skipif(os.name == "nt", reason="chmod POSIX")
def test_token_file_is_owner_only_after_save_and_remove(manager):
    manager.save_credentials(_creds("a"))
    manager.save_credentials(_creds("b"))
    assert stat.S_IMODE(manager.token_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(manager.token_file.parent.stat().st_mode) == 0o700
    assert manager.remove_account("a@example.com")
    assert stat.S_IMODE(manager.token_file.stat().st_mode) == 0o600


def test_remove_account(manager):
    manager.save_credentials(_creds("a"))
    manager.save_credentials(_creds("b"))
    assert manager.remove_account("a@example.com")
    assert [c.email for c in manager.load_all_stored_credentials()] == ["b@example.com"]
    assert manager.remove_account("b@example.com")
    assert not manager.token_file.exists()
    assert not manager.remove_account("zzz@example.com")


def test_home_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path / "custom"))
    assert gw_auth.get_home_dir() == tmp_path / "custom"
    assert gw_auth.default_token_file() == tmp_path / "custom" / "auth" / "antigravity_tokens.json"
