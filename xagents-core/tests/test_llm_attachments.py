"""Đầu vào ảnh cho reviewer (studio ADR-0020, DANH-GIA §5 C1): `attachments` là ĐẦU VÀO, không phải tool.

Model vẫn không làm gì ngoài trả JSON (ADR-0003 của studio); ảnh chỉ đi cùng lượt user đầu tiên. Mỗi provider một
định dạng: Anthropic `image` block base64, OpenAI `image_url` data URL, CLI `claude -p` nhận `stream-json` trên stdin.
Codex không nhận → lỗi nói rõ, và routing bỏ qua backend không nhận ảnh. Khoá eval băm `sha256` của FILE NGUỒN để
phát lại không phụ thuộc phiên bản Pillow đã thu nhỏ ảnh.
"""
from __future__ import annotations

import base64
import json

import pytest

from xagents_core.evals import ReplayClient, prompt_key
from xagents_core.llm import (
    AnthropicClient,
    Attachment,
    CodexClient,
    Completion,
    FakeClient,
    LLMConfig,
    LLMError,
    OpenAICompatClient,
    claude_stream_input,
    stream_result,
)
from xagents_core.routing import Backend, RoutingClient

PNG = Attachment(media_type="image/png", data=b"\x89PNG-bytes", sha256="a" * 64, label="lưới cảnh S1–S4")
JPG = Attachment(media_type="image/jpeg", data=b"\xff\xd8jpeg", sha256="b" * 64, label="thumbnail A")


def test_attachment_encodes_and_rejects_non_images():
    assert PNG.b64() == base64.b64encode(b"\x89PNG-bytes").decode()
    assert JPG.data_url() == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpeg").decode()
    with pytest.raises(LLMError, match="image/"):
        Attachment(media_type="application/pdf", data=b"x", sha256="c" * 64)
    with pytest.raises(LLMError, match="rỗng"):
        Attachment(media_type="image/png", data=b"", sha256="c" * 64)


def test_anthropic_puts_images_before_text_in_the_first_user_turn(monkeypatch):
    from test_llm_clients import _Block, _cfg, _fake_sdk, _Msg, _Usage
    seen = _fake_sdk(monkeypatch, final=_Msg([_Block("text", text="{}")], _Usage()))
    AnthropicClient(_cfg()).complete(system="s", user="xem ảnh", schema={"type": "object"}, model_tier="strong",
                                     attachments=[PNG, JPG])
    content = seen["kwargs"]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"]
    assert content[0]["source"] == {"type": "base64", "media_type": "image/png", "data": PNG.b64()}
    assert content[2]["text"] == "xem ảnh"
    # vòng tool: chỉ lượt user ĐẦU mang ảnh, lượt sau giữ nguyên
    AnthropicClient(_cfg()).complete(system="s", user="u", schema={}, model_tier="strong", attachments=[PNG],
                                     messages=[{"role": "user", "content": "u"},
                                               {"role": "assistant", "content": "x"},
                                               {"role": "user", "content": "tiếp"}])
    msgs = seen["kwargs"]["messages"]
    assert msgs[0]["content"][0]["type"] == "image" and msgs[2]["content"] == "tiếp"


def test_openai_sends_image_url_parts(monkeypatch):
    c = OpenAICompatClient(LLMConfig(provider="openai", base_url="http://x/v1", api_key="k",
                                     models={"strong": "m", "standard": "m"}))
    seen = []
    monkeypatch.setattr(c, "_post_cacheable", lambda body: (seen.append(body), {
        "model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}})[1])
    c.complete(system="s", user="xem", schema={"type": "object"}, model_tier="strong", attachments=[JPG])
    user = seen[0]["messages"][1]
    assert user["content"] == [{"type": "text", "text": "xem"},
                               {"type": "image_url", "image_url": {"url": JPG.data_url()}}]
    # nhánh dự phòng json_object vẫn nối được gợi ý schema vào phần chữ
    c2 = OpenAICompatClient(LLMConfig(provider="openai", base_url="http://x/v1", api_key="k",
                                      models={"strong": "m", "standard": "m"}))
    c2._json_schema_ok = False
    seen2 = []
    monkeypatch.setattr(c2, "_post_cacheable", lambda body: (seen2.append(body), {
        "model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}})[1])
    c2.complete(system="s", user="xem", schema={"type": "object"}, model_tier="strong", attachments=[JPG])
    parts = seen2[0]["messages"][1]["content"]
    assert parts[0]["text"].startswith("xem") and "JSON Schema" in parts[0]["text"] and parts[1]["type"] == "image_url"


def test_codex_refuses_images_clearly():
    c = CodexClient(LLMConfig(provider="codex", models={"strong": "m"}), runner=lambda *a: "")
    with pytest.raises(LLMError, match=r"codex.*ảnh"):
        c.complete(system="s", user="u", schema={}, model_tier="strong", attachments=[PNG])


def test_fake_client_records_attachment_hashes():
    f = FakeClient(responses=[{"ok": 1}, {"ok": 2}])
    f.complete(system="s", user="u", schema={}, model_tier="strong", attachments=[PNG, JPG])
    f.complete(system="s", user="u", schema={}, model_tier="strong")
    assert f.calls[0]["attachments"] == [PNG.sha256, JPG.sha256] and f.calls[1]["attachments"] == []


def test_claude_stream_input_and_result_parsing():
    line = json.loads(claude_stream_input("đọc ảnh", [PNG]))
    assert line["type"] == "user" and line["message"]["role"] == "user"
    blocks = line["message"]["content"]
    assert blocks[0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG.b64()}}
    assert blocks[-1] == {"type": "text", "text": "đọc ảnh"}
    out = "\n".join([json.dumps({"type": "system", "subtype": "init"}), "không phải json",
                     json.dumps({"type": "assistant", "message": {}}),
                     json.dumps({"type": "result", "subtype": "success", "result": "{}"}), "{hỏng"])
    assert json.loads(stream_result(out)) == {"type": "result", "subtype": "success", "result": "{}"}
    assert stream_result('{"subtype": "success", "result": "{}"}') == '{"subtype": "success", "result": "{}"}'


class _Client:
    def __init__(self, name):
        self.name, self.seen = name, []

    def complete(self, *, system, user, schema, model_tier, cache_key=None, tools=None, messages=None, workdir=None,
                 attachments=None):
        self.seen.append(attachments)
        return Completion(text="{}", input_tokens=1, output_tokens=1, model=self.name)


def test_routing_skips_backends_that_cannot_see_images():
    codex, claude = _Client("codex"), _Client("claude")
    r = RoutingClient([Backend("codex", codex, supports_images=False), Backend("claude", claude)])
    assert r.complete(system="s", user="u", schema={}, model_tier="strong", attachments=[PNG]).model == "claude"
    assert claude.seen == [[PNG]] and codex.seen == []
    assert r.complete(system="s", user="u", schema={}, model_tier="strong").model == "codex"
    only = RoutingClient([Backend("codex", _Client("codex"), supports_images=False)])
    with pytest.raises(LLMError, match="ảnh"):
        only.complete(system="s", user="u", schema={}, model_tier="strong", attachments=[PNG])


def test_prompt_key_hashes_source_images_and_replay_matches(tmp_path):
    assert prompt_key("s", "u") == prompt_key("s", "u", None) == prompt_key("s", "u", [])
    k1, k2 = prompt_key("s", "u", [PNG]), prompt_key("s", "u", [JPG])
    assert len({prompt_key("s", "u"), k1, k2}) == 3
    # cùng FILE NGUỒN (sha256) thì cùng khoá dù bytes đã thu nhỏ khác nhau (Pillow khác phiên bản)
    resized = Attachment(media_type="image/jpeg", data=b"\xff\xd8other-bytes", sha256=PNG.sha256)
    assert prompt_key("s", "u", [resized]) == k1

    class Suite:
        def load_recording(self, agent):
            return {"cases": {k1: {"text": '{"ok": true}', "model": "m"}}}
    rep = ReplayClient("x", Suite())  # type: ignore[arg-type]
    assert rep.complete(system="s", user="u", schema={}, model_tier="strong", attachments=[PNG]).text == '{"ok": true}'
    with pytest.raises(LLMError, match="lệch prompt"):
        rep.complete(system="s", user="u", schema={}, model_tier="strong")
