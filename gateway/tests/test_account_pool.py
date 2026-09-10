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


def test_round_robin_survives_coarse_clock(manager, monkeypatch):
    # Đồng hồ đứng yên (mô phỏng độ phân giải thô của Windows): LRU vẫn phải xoay vòng,
    # không được hòa mốc rồi rơi về thứ tự lưu file. Đây là nguồn của test trượt ngẫu nhiên.
    monkeypatch.setattr(gw_auth.time, "time", lambda: 1_700_000_000.0)
    manager.save_credentials(_creds("a"))
    manager.save_credentials(_creds("b"))
    seen = [manager.resolve_credential_candidates()[0].email for _ in range(4)]
    assert seen == ["a@example.com", "b@example.com", "a@example.com", "b@example.com"]


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


def test_home_dir_defaults_to_user_home_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv(gw_auth.ENV_HOME, raising=False)
    monkeypatch.setattr(gw_auth.Path, "home", classmethod(lambda cls: tmp_path))
    assert gw_auth.get_home_dir() == tmp_path / ".x-agents"


def test_gateway_dir_creates_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path / "custom"))
    d = gw_auth.get_gateway_dir()
    assert d == tmp_path / "custom" / "gateway"
    assert d.is_dir()


# ---------- AntigravityCredentials properties ----------


def test_is_expired_true_without_access_token():
    c = gw_auth.AntigravityCredentials(access_token="")
    assert c.is_expired is True


def test_is_cooling_down_reflects_unavailable_until():
    import time

    c = _creds("a")
    assert c.is_cooling_down is False
    c.unavailable_until = time.time() + 60
    assert c.is_cooling_down is True


# ---------- _read_file lỗi ----------


def test_read_file_with_corrupt_json_returns_empty(manager, caplog):
    manager.token_file.parent.mkdir(parents=True, exist_ok=True)
    manager.token_file.write_text("{not valid json", encoding="utf-8")
    assert manager.load_all_stored_credentials() == []
    assert "Không đọc được" in caplog.text


# ---------- load_stored_credentials ----------


def test_load_stored_credentials_empty_pool_returns_none(manager):
    assert manager.load_stored_credentials() is None


def test_load_stored_credentials_finds_matching_email(manager):
    manager.save_credentials(_creds("a"))
    manager.save_credentials(_creds("b"))
    manager.save_credentials(_creds("c"))
    found = manager.load_stored_credentials(email="B@Example.com")
    assert found is not None and found.email == "b@example.com"


def test_load_stored_credentials_no_email_returns_first(manager):
    manager.save_credentials(_creds("a"))
    found = manager.load_stored_credentials()
    assert found is not None and found.email == "a@example.com"


def test_load_stored_credentials_unknown_email_falls_back_to_first(manager):
    manager.save_credentials(_creds("a"))
    found = manager.load_stored_credentials(email="nobody@example.com")
    assert found is not None and found.email == "a@example.com"


# ---------- _atomic_write ----------


@pytest.mark.skipif(os.name == "nt", reason="os.chmod POSIX semantics")
def test_atomic_write_chmods_tmp_file_on_posix(manager):
    manager.save_credentials(_creds("a"))
    assert stat.S_IMODE(manager.token_file.stat().st_mode) == 0o600


def test_atomic_write_takes_posix_chmod_branch(manager, monkeypatch):
    # Forcer nhánh `if os.name != "nt": os.chmod(...)` để chạy cả trên Windows CI —
    # os.chmod vẫn hoạt động trên Windows (chỉ giới hạn hơn), không lỗi.
    monkeypatch.setattr(gw_auth.os, "name", "posix")
    manager.save_credentials(_creds("a"))
    assert manager.token_file.is_file()


def test_atomic_write_cleans_up_tmp_file_and_reraises_on_failure(manager, monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(gw_auth.json, "dump", boom)
    with pytest.raises(OSError, match="disk full"):
        manager._atomic_write(manager.token_file, {"a": 1})
    assert not manager.token_file.with_suffix(".tmp").exists()
    assert not manager.token_file.exists()


# ---------- save_credentials ----------


def test_save_credentials_migrates_legacy_flat_file(manager):
    manager.token_file.parent.mkdir(parents=True, exist_ok=True)
    manager.token_file.write_text(json.dumps(_creds("old").to_dict()), encoding="utf-8")

    manager.save_credentials(_creds("new"))

    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]
    assert set(stored) == {"old@example.com", "new@example.com"}


def test_save_credentials_logs_error_on_write_failure(manager, monkeypatch, caplog):
    monkeypatch.setattr(
        manager, "_atomic_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("no space"))
    )
    manager.save_credentials(_creds("a"))
    assert "Không ghi được" in caplog.text


# ---------- _update_account_fields ----------


def test_update_account_fields_falls_back_to_save_when_no_existing_file(manager):
    c = _creds("a")
    manager.mark_account_unavailable(c, 429, retry_after="10")
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert stored["last_failure_status"] == 429


def test_update_account_fields_logs_error_on_write_failure(manager, monkeypatch, caplog):
    c = _creds("a")
    manager.save_credentials(c)
    monkeypatch.setattr(
        manager, "_atomic_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("no space"))
    )
    manager.mark_account_unavailable(c, 429)
    assert "Không ghi được" in caplog.text


# ---------- clear_credentials ----------


def test_clear_credentials_removes_file(manager):
    manager.save_credentials(_creds("a"))
    assert manager.clear_credentials() is True
    assert not manager.token_file.exists()


def test_clear_credentials_false_when_no_file(manager):
    assert manager.clear_credentials() is False


def test_clear_credentials_logs_warning_on_failure(manager, monkeypatch, caplog):
    manager.save_credentials(_creds("a"))
    monkeypatch.setattr(
        type(manager.token_file), "unlink", lambda self, *a, **kw: (_ for _ in ()).throw(OSError("locked"))
    )
    assert manager.clear_credentials() is False
    assert "Không xóa được" in caplog.text
