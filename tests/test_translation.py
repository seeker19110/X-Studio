"""Dịch OpenAI ↔ Code Assist: model alias, schema tool, thought signature, role alternation."""

from __future__ import annotations

import json

import pytest

from gateway import client as gw


def test_map_model_name_aliases_and_unknown(monkeypatch):
    assert gw.map_model_name("gemini-3.7-flash") == "gemini-3.7-flash-high"   # alias giữ nguyên đời model, chỉ thêm mức effort
    assert gw.map_model_name("antigravity/claude-sonnet-4.6") == "claude-sonnet-4-6"
    assert gw.map_model_name("gemini-3.1-pro") == "gemini-3.1-pro-low"
    assert gw.map_model_name("") == gw.DEFAULT_CODE_ASSIST_MODEL

    # Model lạ: mặc định nổ 400, không âm thầm tụt xuống flash.
    with pytest.raises(gw.UnknownModelError) as exc:
        gw.map_model_name("totally-unknown")
    assert exc.value.status_code == 400
    assert "gemini-3.8-flash-medium" in str(exc.value)   # thông báo liệt kê model hợp lệ

    monkeypatch.setenv("GATEWAY_STRICT_MODELS", "0")
    assert gw.map_model_name("totally-unknown") == gw.DEFAULT_CODE_ASSIST_MODEL


def test_known_model_ids_covers_published_and_alias():
    ids = gw.known_model_ids()
    assert {m["id"] for m in gw.FALLBACK_MODELS} <= set(ids)
    assert set(gw.MODEL_ALIAS_MAP.values()) <= set(ids)   # mọi đích của alias phải hợp lệ
    assert "claude-sonnet-4.6" in ids          # alias cũng được chấp nhận
    assert ids == sorted(ids)


def test_nullable_schema_is_sanitized():
    params = {
        "type": "object",
        "properties": {
            "a": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "opt", "default": None},
            "b": {"type": ["integer", "null"]},
            "c": {"$ref": "#/$defs/X", "default": 1},
            "d": {"type": "string", "nullable": True},
        },
        "$defs": {"X": {"type": "object"}},
    }
    tools = gw._translate_tools_to_gemini([{"type": "function", "function": {"name": "f", "parameters": params}}])
    decl = tools[0]["functionDeclarations"][0]
    props = decl["parameters"]["properties"]
    assert props["a"] == {"type": "string", "description": "opt", "default": None}
    assert props["b"] == {"type": "integer"}
    assert props["c"] == {"$ref": "#/$defs/X"}
    assert props["d"] == {"type": "string"}


def test_tool_choice_translation():
    assert gw._translate_tool_choice_to_gemini("auto") == {"functionCallingConfig": {"mode": "AUTO"}}
    assert gw._translate_tool_choice_to_gemini("required") == {"functionCallingConfig": {"mode": "ANY"}}
    assert gw._translate_tool_choice_to_gemini({"function": {"name": "x"}}) == {
        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["x"]}
    }
    assert gw._translate_tool_choice_to_gemini(None) is None


def test_request_builder_system_tools_and_role_merge():
    payload = {
        "model": "gemini-3.7-flash",
        "temperature": 0.2,
        "max_tokens": 100,
        "messages": [
            {"role": "system", "content": "Bạn là trợ lý."},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "a"},
            {"role": "user", "content": [{"type": "text", "text": "b"}]},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{\"x\": 1}"},
                     "extra_content": {"google": {"thought_signature": "sig"}}},
                    {"id": "c2", "type": "function", "function": {"name": "g", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "f", "content": "{\"ok\": true}"},
        ],
        "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}],
    }
    env = gw.build_code_assist_request(payload, "proj")
    assert env["project"] == "proj" and env["model"] == "gemini-3.7-flash-high"
    req = env["request"]
    assert req["systemInstruction"]["parts"][0]["text"] == "Bạn là trợ lý."
    assert req["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 100}
    roles = [c["role"] for c in req["contents"]]
    # bắt đầu bằng user, xen kẽ, user liên tiếp được gộp
    assert roles == ["user", "model", "user", "model", "user"]
    assert req["contents"][2]["parts"] == [{"text": "a"}, {"text": "b"}]
    model_parts = req["contents"][3]["parts"]
    assert model_parts[0]["functionCall"]["name"] == "f" and model_parts[0]["thoughtSignature"] == "sig"
    # Không có thoughtSignature thật → vẫn là functionCall thật với chữ ký skip, giữ id.
    assert model_parts[1] == {
        "functionCall": {"name": "g", "args": {}, "id": "c2"},
        "thoughtSignature": "skip_thought_signature_validator",
    }
    fr = req["contents"][4]["parts"][0]["functionResponse"]
    assert fr == {"name": "f", "id": "c1", "response": {"result": "{\"ok\": true}"}}
    assert req["tools"][0]["functionDeclarations"][0]["name"] == "f"


def test_image_content_to_inline_data():
    parts = gw._coerce_content_to_parts(
        [{"type": "text", "text": "xem"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    )
    assert parts == [{"text": "xem"}, {"inlineData": {"mimeType": "image/png", "data": "AAAA"}}]


def test_response_translation_extracts_textual_tool_call():
    resp = {"response": {"candidates": [{"content": {"parts": [{"text": "[Tool call: search({\"q\": \"x\"})]"}]}}]}}
    out = gw.translate_gemini_to_openai_response(resp, "m")
    tc = out["choices"][0]["message"]["tool_calls"]
    assert tc[0]["function"]["name"] == "search"
    assert json.loads(tc[0]["function"]["arguments"]) == {"q": "x"}
    assert out["choices"][0]["finish_reason"] == "tool_calls"
    assert out["choices"][0]["message"]["content"] is None


def test_response_translation_reasoning_and_finish_map():
    resp = {"candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"thought": True, "text": "nghĩ"}, {"text": "ra"}]}}]}
    out = gw.translate_gemini_to_openai_response(resp, "m")
    msg = out["choices"][0]["message"]
    assert msg["content"] == "ra" and msg["reasoning_content"] == "nghĩ"
    assert out["choices"][0]["finish_reason"] == "content_filter"


def test_stream_event_translation():
    ev = {"response": {"candidates": [{"content": {"parts": [{"text": "xin"}]}}]}}
    chunk = gw.translate_gemini_stream_event(ev, "m", "id1")
    assert chunk["choices"][0]["delta"] == {"content": "xin"} and chunk["choices"][0]["finish_reason"] is None
    assert gw.translate_gemini_stream_event({"response": {"candidates": [{"content": {"parts": []}}]}}, "m", "id") is None


def test_tool_result_name_from_call_id_and_truncation_marker(monkeypatch):
    monkeypatch.setattr(gw, "TOOL_RESULT_MAX_CHARS", 10)
    payload = {
        "model": "gemini-3.7-flash",
        "messages": [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c9", "content": "x" * 50},
        ],
    }
    fr = gw.build_code_assist_request(payload, "p")["request"]["contents"][2]["parts"][0]["functionResponse"]
    assert fr["name"] == "grep" and fr["id"] == "c9"
    assert fr["response"]["result"] == "x" * 10 + gw.TOOL_RESULT_TRUNCATED_MARKER
    monkeypatch.setattr(gw, "TOOL_RESULT_MAX_CHARS", 0)
    assert gw._truncate_tool_result("y" * 5000) == "y" * 5000


def test_stream_event_with_only_finish_reason_and_usage_is_emitted():
    ev = {
        "response": {
            "candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 7, "totalTokenCount": 10},
        }
    }
    chunk = gw.translate_gemini_stream_event(ev, "m", "id1")
    assert chunk["choices"][0]["delta"] == {}
    assert chunk["choices"][0]["finish_reason"] == "length"
    assert chunk["usage"]["total_tokens"] == 10
    safety = {"response": {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}}
    assert gw.translate_gemini_stream_event(safety, "m", "id1")["choices"][0]["finish_reason"] == "content_filter"


def test_stream_tool_call_index_counts_tool_calls_not_parts():
    event = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "thinking...", "thought": True},
                        {"functionCall": {"name": "a", "args": {}}},
                        {"text": "and"},
                        {"functionCall": {"name": "b", "args": {}}},
                    ]
                }
            }
        ]
    }
    chunk = gw.translate_gemini_stream_event(event, "gemini-3.7-flash", "chatcmpl-x")
    assert [tc["index"] for tc in chunk["choices"][0]["delta"]["tool_calls"]] == [0, 1]


def test_response_translation_extracts_action_style_textual_tool_call():
    resp = {"candidates": [{"content": {"parts": [{"text": 'Action: Called search({"q": "x"})'}]}}]}
    out = gw.translate_gemini_to_openai_response(resp, "gemini-3.7-flash")
    calls = out["choices"][0]["message"]["tool_calls"]
    assert calls[0]["function"]["name"] == "search"
    assert out["choices"][0]["finish_reason"] == "tool_calls"


def test_classify_probe_catches_retired_model():
    """Model nghỉ hưu vẫn trả 200 — nội dung mới là thứ tố cáo, không phải mã HTTP."""
    retired = '{"response":{"candidates":[{"content":{"parts":[{"text":"Gemini 3.5 Flash is no longer available. Please switch to Gemini 3.7 Flash."}]}}]}}'
    verdict, note = gw.classify_probe(200, retired)
    assert verdict == gw.PROBE_RETIRED and "no longer available" in note
    assert gw.classify_probe(200, '{"response":{"candidates":[{"content":{"parts":[{"text":"Hi"}]}}]}}')[0] == gw.PROBE_OK
    assert gw.classify_probe(404, '{"error":{"code":404}}')[0] == gw.PROBE_MISSING
    assert gw.classify_probe(429, "quota")[0] == gw.PROBE_QUOTA
    assert gw.classify_probe(500, "boom")[0] == gw.PROBE_ERROR


def test_build_probe_envelope_is_minimal():
    env = gw.build_probe_envelope("gemini-3.8-flash-high", "proj")
    assert env["model"] == "gemini-3.8-flash-high" and env["project"] == "proj"
    assert env["request"]["generationConfig"]["maxOutputTokens"] == 1


def test_parse_available_models_filters_noise():
    """Catalog upstream lẫn nhiều thứ không chọn được: model nội bộ, id xấu, mục không có tên, model đã khai tử."""
    payload = {
        "models": {
            "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash (Medium)"},
            "gemini-3.8-flash-tiered": {"displayName": ""},
            "secret-one": {"displayName": "Nội bộ", "isInternal": True},
            "BAD ID": {"displayName": "Tên xấu"},
            "gemini-3-flash-agent": {"displayName": "Gemini 3.5 Flash"},
        },
        "agentModelSorts": [{"groups": [{"modelIds": ["gemini-3.8-flash-medium", "secret-one", "BAD ID"]}]}],
        "tieredModelIds": {"fast": ["gemini-3.8-flash-tiered", "gemini-3-flash-agent"]},
        "deprecatedModelIds": ["gemini-3-flash-agent"],
    }
    assert gw.parse_available_models(payload) == [
        {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)", "code_assist_model": "gemini-3.8-flash-medium"}
    ]
    assert gw.parse_available_models({"models": "sai kiểu"}) == []


def test_discovered_model_is_accepted_without_touching_static_table(monkeypatch):
    """Model upstream mới khai phải dùng được NGAY, không chờ ai sửa MODEL_ALIAS_MAP."""
    monkeypatch.setattr(gw, "_discovered", {}, raising=False)
    with pytest.raises(gw.UnknownModelError):
        gw.map_model_name("gemini-4.0-flash-high")
    gw.set_discovered_models([{"id": "gemini-4.0-flash-high", "name": "Gemini 4", "code_assist_model": "gemini-4.0-flash-high"}])
    try:
        assert gw.map_model_name("gemini-4.0-flash-high") == "gemini-4.0-flash-high"
        assert "gemini-4.0-flash-high" in gw.known_model_ids()
        assert {m["id"] for m in gw.serving_models()} == {"gemini-4.0-flash-high"}
    finally:
        gw.set_discovered_models([])
