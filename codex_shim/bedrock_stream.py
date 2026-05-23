"""AWS event-stream frame decoder for Bedrock invoke-with-response-stream.

Bedrock streams responses as a sequence of binary event-stream frames. Each
frame wraps a JSON envelope whose `bytes` field is base64-encoded — and for
Anthropic-family models, the decoded payload is exactly the same SSE event
(message_start, content_block_delta, …) that the Anthropic public API emits.

We don't validate CRCs: TLS already covers transport integrity, and there's
nothing actionable we can do on a failure here. The decoder yields decoded
Anthropic events directly so the existing _stream_anthropic state machine
can consume them unchanged.

Frame format (big-endian):

    +0   uint32   total_length         (whole message including this field)
    +4   uint32   headers_length
    +8   uint32   prelude_crc          (skipped)
    +12  bytes    headers (variable)
    +N   bytes    payload (JSON)
    -4   uint32   message_crc          (skipped)
"""

from __future__ import annotations

import base64
import json
import struct
from typing import AsyncIterator


_PRELUDE_LEN = 12  # total_len + headers_len + prelude_crc
_TRAILING_CRC_LEN = 4


class BedrockStreamError(RuntimeError):
    """Raised when an upstream `exception` event is encountered."""


async def iter_anthropic_events(stream) -> AsyncIterator[dict]:
    """Decode a Bedrock invoke-with-response-stream body into Anthropic events.

    `stream` is anything with `.iter_chunked(n) -> async iterator[bytes]`,
    matching aiohttp's StreamReader. We accumulate bytes, parse one frame at
    a time, and yield the inner Anthropic JSON event for each successful
    chunk message. Non-`chunk` events (`exception`, `error`) raise.
    """
    buf = bytearray()
    async for chunk in stream.iter_chunked(8192):
        buf.extend(chunk)
        while True:
            frame = _try_take_frame(buf)
            if frame is None:
                break
            event_type, payload = frame
            if event_type == "chunk":
                envelope = json.loads(payload)
                blob = envelope.get("bytes")
                if not blob:
                    continue
                event = json.loads(base64.b64decode(blob))
                yield event
            elif event_type in {"exception", "error", "modelStreamErrorException"}:
                # Surface upstream errors with their JSON body for the caller
                # to translate into an HTTP error response.
                raise BedrockStreamError(payload.decode("utf-8", errors="replace"))
            # Other event types (internalServerException etc.) — ignore
            # silently; Bedrock occasionally emits keep-alive style frames
            # we don't need.


def _try_take_frame(buf: bytearray) -> tuple[str, bytes] | None:
    """Pull one complete frame from `buf`, mutating it in place.

    Returns (event_type, payload_bytes) or None if buf doesn't yet hold a
    full frame.
    """
    if len(buf) < _PRELUDE_LEN:
        return None
    total_len, headers_len, _prelude_crc = struct.unpack(">III", bytes(buf[:_PRELUDE_LEN]))
    if total_len < _PRELUDE_LEN + _TRAILING_CRC_LEN + headers_len:
        # Malformed; drop one byte and let the caller try to resync. In
        # practice this should never happen with well-formed Bedrock output.
        del buf[0]
        return None
    if len(buf) < total_len:
        return None

    headers_start = _PRELUDE_LEN
    headers_end = headers_start + headers_len
    payload_end = total_len - _TRAILING_CRC_LEN
    headers_blob = bytes(buf[headers_start:headers_end])
    payload = bytes(buf[headers_end:payload_end])
    del buf[:total_len]

    headers = _parse_headers(headers_blob)
    event_type = headers.get(":event-type") or headers.get(":exception-type") or ""
    return event_type, payload


def _parse_headers(blob: bytes) -> dict[str, str]:
    """Parse AWS event-stream headers. We only need string-typed headers
    (type 7); other types are skipped over correctly so the offset advances."""
    out: dict[str, str] = {}
    i = 0
    n = len(blob)
    while i < n:
        name_len = blob[i]
        i += 1
        name = blob[i : i + name_len].decode("utf-8", errors="replace")
        i += name_len
        if i >= n:
            break
        type_id = blob[i]
        i += 1
        if type_id == 7:  # string
            (slen,) = struct.unpack(">H", blob[i : i + 2])
            i += 2
            value = blob[i : i + slen].decode("utf-8", errors="replace")
            i += slen
            out[name] = value
        elif type_id == 6:  # uuid
            i += 16
        elif type_id == 0:  # bool true
            pass
        elif type_id == 1:  # bool false
            pass
        elif type_id == 2:  # byte
            i += 1
        elif type_id == 3:  # int16
            i += 2
        elif type_id == 4:  # int32
            i += 4
        elif type_id == 5:  # int64
            i += 8
        elif type_id == 8:  # byte buffer
            (blen,) = struct.unpack(">H", blob[i : i + 2])
            i += 2 + blen
        elif type_id == 9:  # timestamp
            i += 8
        else:
            # Unknown type; bail out rather than misalign.
            break
    return out
