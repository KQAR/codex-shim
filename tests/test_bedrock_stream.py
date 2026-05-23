"""Smoke tests for the Bedrock event-stream decoder.

We hand-craft frames that mirror what Bedrock emits for an Anthropic-family
model, then verify the decoder yields the inner Anthropic events unchanged.
"""

from __future__ import annotations

import base64
import json
import struct

import pytest

from codex_shim.bedrock_stream import BedrockStreamError, iter_anthropic_events


def _string_header(name: str, value: str) -> bytes:
    name_bytes = name.encode("utf-8")
    value_bytes = value.encode("utf-8")
    return (
        bytes([len(name_bytes)])
        + name_bytes
        + bytes([7])  # type 7 = string
        + struct.pack(">H", len(value_bytes))
        + value_bytes
    )


def _frame(event_type: str, payload: bytes) -> bytes:
    headers = _string_header(":event-type", event_type) + _string_header(
        ":content-type", "application/json"
    )
    headers_len = len(headers)
    total_len = 12 + headers_len + len(payload) + 4  # prelude + headers + payload + crc
    prelude = struct.pack(">III", total_len, headers_len, 0)
    crc = b"\x00\x00\x00\x00"
    return prelude + headers + payload + crc


def _chunk(anthropic_event: dict) -> bytes:
    inner = base64.b64encode(json.dumps(anthropic_event).encode("utf-8")).decode("ascii")
    envelope = json.dumps({"bytes": inner}).encode("utf-8")
    return _frame("chunk", envelope)


class _FakeStream:
    def __init__(self, blob: bytes, chunk_size: int = 17):
        self.blob = blob
        self.chunk_size = chunk_size

    async def iter_chunked(self, _n):
        for i in range(0, len(self.blob), self.chunk_size):
            yield self.blob[i : i + self.chunk_size]


@pytest.mark.asyncio
async def test_decodes_anthropic_chunks_across_chunk_boundaries():
    events = [
        {"type": "message_start", "message": {"id": "msg_1"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_stop"},
    ]
    blob = b"".join(_chunk(e) for e in events)

    decoded = []
    async for ev in iter_anthropic_events(_FakeStream(blob, chunk_size=11)):
        decoded.append(ev)

    assert decoded == events


@pytest.mark.asyncio
async def test_exception_event_raises():
    bad = _frame("exception", b'{"message": "throttled"}')
    with pytest.raises(BedrockStreamError):
        async for _ in iter_anthropic_events(_FakeStream(bad)):
            pass
