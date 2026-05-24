from __future__ import annotations

from codex_shim.translate import (
    anthropic_to_chat_response,
    anthropic_usage_to_openai,
    chat_completion_to_response,
    responses_to_anthropic,
    responses_to_chat,
)


def test_responses_to_chat_text_input():
    body = {"model": "slug", "instructions": "System", "input": "Hello", "stream": True, "max_output_tokens": 99}
    out = responses_to_chat(body, "real-model")
    assert out["model"] == "real-model"
    assert out["stream"] is True
    assert out["max_tokens"] == 99
    assert out["messages"] == [{"role": "system", "content": "System"}, {"role": "user", "content": "Hello"}]


def test_responses_function_tools_convert_to_chat_shape():
    body = {
        "model": "slug",
        "input": "Hi",
        "tools": [{"type": "function", "name": "do_work", "description": "Do work", "parameters": {"type": "object"}}],
    }
    out = responses_to_chat(body, "real-model")
    assert out["tools"] == [
        {
            "type": "function",
            "function": {"name": "do_work", "description": "Do work", "parameters": {"type": "object"}},
        }
    ]


def test_responses_to_anthropic_messages():
    body = {"model": "slug", "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hi"}]}]}
    out = responses_to_anthropic(body, "claude-real", 123)
    assert out["model"] == "claude-real"
    assert out["max_tokens"] == 123
    assert out["messages"] == [{"role": "user", "content": "Hi"}]


def test_chat_completion_to_response_strips_think():
    payload = {
        "id": "chatcmpl_1",
        "choices": [{"message": {"role": "assistant", "content": "<think>secret</think>Hello"}}],
    }
    out = chat_completion_to_response(payload, "slug")
    assert out["model"] == "slug"
    assert out["output"][0]["content"][0]["text"] == "Hello"


def test_anthropic_usage_translation_folds_cache_tokens_into_prompt():
    """Cache tokens are billed as part of the input budget. The result must
    expose Anthropic-style fields (Codex Desktop's Responses parser hard-
    requires input_tokens) and the OpenAI legacy aliases."""
    out = anthropic_usage_to_openai({
        "input_tokens": 100,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 25,
        "output_tokens": 200,
    })
    assert out == {
        "input_tokens": 175,  # 100 + 50 + 25
        "output_tokens": 200,
        "prompt_tokens": 175,
        "completion_tokens": 200,
        "total_tokens": 375,
    }


def test_anthropic_to_chat_response_emits_usage():
    """Without this, Codex Desktop's context-window meter goes blank when
    it talks to an Anthropic / Bedrock backend."""
    payload = {
        "id": "msg_x",
        "content": [{"type": "text", "text": "hi"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    out = anthropic_to_chat_response(payload, "slug")
    assert out["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_responses_to_chat_streaming_requests_usage_chunk():
    """Without stream_options.include_usage upstreams won't emit the final
    usage chunk and the response.completed event won't carry token counts."""
    body = {"model": "slug", "input": "hi", "stream": True}
    out = responses_to_chat(body, "real-model")
    assert out["stream_options"] == {"include_usage": True}


def test_responses_to_chat_non_streaming_omits_stream_options():
    body = {"model": "slug", "input": "hi"}
    out = responses_to_chat(body, "real-model")
    assert "stream_options" not in out
