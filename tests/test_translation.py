"""Dịch OpenAI ↔ Code Assist: model alias, schema tool, thought signature, role alternation."""

from __future__ import annotations

import json

from gateway import client as gw


def test_map_model_name_aliases_and_unknown():
    assert gw.map_model_name("gemini-3.7-flash") == "gemini-3-flash-agent"
    assert gw.map_model_name("antigravity/claude-sonnet-4.6") == "claude-sonnet-4-6"
    assert gw.map_model_name("gemini-3.1-pro") == "gemini-3.1-pro-low"
    assert gw.map_model_name("totally-unknown") == "gemini-3-flash-agent"
    assert gw.map_model_name("") == "gemini-3-flash-agent"


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
    assert env["project"] == "proj" and env["model"] == "gemini-3-flash-agent"
    req = env["request"]
    assert req["systemInstruction"]["parts"][0]["text"] == "Bạn là trợ lý."
    assert req["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 100}
    roles = [c["role"] for c in req["contents"]]
    # bắt đầu bằng user, xen kẽ, user liên tiếp được gộp
    assert roles == ["user", "model", "user", "model", "user"]
    assert req["contents"][2]["parts"] == [{"text": "a"}, {"text": "b"}]
    model_parts = req["contents"][3]["parts"]
    assert model_parts[0]["functionCall"]["name"] == "f" and model_parts[0]["thoughtSignature"] == "sig"
    assert model_parts[1] == {"text": "[Tool call: g({})]"}
    assert req["contents"][4]["parts"][0]["text"].startswith("[Tool result from f:")
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
