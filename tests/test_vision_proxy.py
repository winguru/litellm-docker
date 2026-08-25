import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports optional runtime packages that are not available in a bare
# Python test environment. Stub only the minimum surface that this hook needs.
fastapi = types.ModuleType("fastapi")
class Response:
    def __init__(self, content=None, media_type=None, status_code=200, headers=None):
        self.content = content
        self.media_type = media_type
        self.status_code = status_code
        self.headers = headers or {}
fastapi.Response = Response
sys.modules.setdefault("fastapi", fastapi)

litellm = types.ModuleType("litellm")
integrations = types.ModuleType("litellm.integrations")
custom_logger = types.ModuleType("litellm.integrations.custom_logger")
class CustomLogger:
    pass
custom_logger.CustomLogger = CustomLogger
integrations.custom_logger = custom_logger
litellm.integrations = integrations
proxy = types.ModuleType("litellm.proxy")
proxy.proxy_server = types.SimpleNamespace(app=types.SimpleNamespace(get=lambda *args, **kwargs: (lambda f: f)))

sys.modules.setdefault("litellm", litellm)
sys.modules.setdefault("litellm.integrations", integrations)
sys.modules.setdefault("litellm.integrations.custom_logger", custom_logger)
sys.modules.setdefault("litellm.proxy", proxy)

import custom_hooks.litellm_vision_proxy as vision_proxy
from custom_hooks.litellm_vision_proxy import (
    VisionHook,
    _strip_attached_files,
    _VISION_BLOCKED,
    _is_content_filter_error,
)


def _install_fake_mcp(calls, error=None, transcript="ok transcript"):
    """Patch the deferred-imported MCP client; returns a reset function."""
    fake_mod = types.ModuleType("custom_hooks.zai_mcp_client")

    async def describe_image(base_url, image_url, prompt, tool_name=None, timeout=180.0):
        calls.append(image_url)
        if error is not None:
            raise error
        return transcript

    fake_mod.describe_image = describe_image
    sys.modules["custom_hooks.zai_mcp_client"] = fake_mod

    def reset():
        sys.modules.pop("custom_hooks.zai_mcp_client", None)
        vision_proxy._transcripts.clear()

    return reset


class DummyResponse:
    def __init__(self, text):
        self.text = text


async def _run_string_url_case():
    hook = VisionHook.__new__(VisionHook)
    async def fake_transcribe(url):
        assert url == "data:image/png;base64,abcd"
        return {"type": "text", "text": "transcript text"}

    hook._transcribe = fake_transcribe
    data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "data:image/png;base64,abcd"}
                ],
            }
        ]
    }
    result = await hook.async_pre_call_hook(None, None, data, "completion")
    assert result["messages"][0]["content"][0]["type"] == "text"
    assert "transcript text" in result["messages"][0]["content"][0]["text"]


def test_async_pre_call_hook_handles_string_image_url():
    asyncio.run(_run_string_url_case())


# ---- <attached_files> placeholder cleanup ----

PLACEHOLDER = (
    '<attached_files>\n<file id="file-abc123" name="screenshot.png" />\n</attached_files>'
)


def test_strip_removes_block_and_surrounding_whitespace():
    cleaned = _strip_attached_files(f"{PLACEHOLDER}\n\nDescribe this image")
    assert cleaned == "Describe this image"


def test_strip_placeholder_only_part_returns_empty():
    assert _strip_attached_files(PLACEHOLDER) == ""


def test_strip_no_placeholder_is_noop():
    text = "plain text without any placeholder"
    assert _strip_attached_files(text) == text


async def _run_hook_with_placeholder():
    hook = VisionHook.__new__(VisionHook)

    async def fake_transcribe(url):
        return {"type": "text", "text": "transcript text"}

    hook._transcribe = fake_transcribe
    data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PLACEHOLDER},
                    {"type": "text", "text": "Describe this image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abcd"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{PLACEHOLDER} but keep me"},
                ],
            },
        ]
    }
    result = await hook.async_pre_call_hook(None, None, data, "completion")

    # message 0: placeholder part dropped, prompt kept, image replaced in place
    msg0 = result["messages"][0]["content"]
    assert [p["type"] for p in msg0] == ["text", "text"]
    assert msg0[0]["text"] == "Describe this image"
    assert msg0[1]["text"] == "transcript text"

    # message 1: no image transcribed -> untouched, placeholder preserved
    msg1 = result["messages"][1]["content"]
    assert msg1[0]["text"] == f"{PLACEHOLDER} but keep me"


def test_hook_strips_placeholder_only_in_transcribed_messages():
    asyncio.run(_run_hook_with_placeholder())


async def _run_hook_placeholder_in_mixed_part():
    hook = VisionHook.__new__(VisionHook)

    async def fake_transcribe(url):
        return {"type": "text", "text": "transcript text"}

    hook._transcribe = fake_transcribe
    data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{PLACEHOLDER}\nDescribe this image"},
                    {"type": "image_url", "image_url": "data:image/png;base64,abcd"},
                ],
            }
        ]
    }
    result = await hook.async_pre_call_hook(None, None, data, "completion")
    msg = result["messages"][0]["content"]
    assert [p["type"] for p in msg] == ["text", "text"]
    assert msg[0]["text"] == "Describe this image"   # condensed, part kept
    assert msg[1]["text"] == "transcript text"       # image replaced in place


def test_hook_condenses_part_that_had_placeholder_plus_text():
    asyncio.run(_run_hook_placeholder_in_mixed_part())


# ---- content-filter handling ----

ZAI_1301_ERROR = Exception(
    "tool 'analyze_image' returned an error result: Error: Unexpected error: "
    "analyze-image analysis failed: HTTP 400: {\"contentFilter\":[{\"level\":2,"
    "\"role\":\"user\"}],\"error\":{\"code\":\"1301\",\"message\":\"System "
    "detected potentially unsafe or sensitive content in input or generation. "
    "Please avoid using prompts that may generate sensitive content. Thank you "
    "for your cooperation.\"}}"
)

DATA_URL = "data:image/png;base64,abcd"


def test_is_content_filter_error_matches_zai_1301():
    assert _is_content_filter_error(ZAI_1301_ERROR)


class McpTimeoutish(Exception):
    pass


def test_is_content_filter_error_rejects_other_failures():
    assert not _is_content_filter_error(Exception("HTTP 500: upstream exploded"))
    assert not _is_content_filter_error(Exception("connection refused"))
    assert not _is_content_filter_error(McpTimeoutish())


async def _run_filtered_image_case():
    calls = []
    reset = _install_fake_mcp(calls, error=ZAI_1301_ERROR)
    try:
        hook = VisionHook.__new__(VisionHook)
        part = await hook._transcribe(DATA_URL)
        assert part == {"type": "text", "text": _VISION_BLOCKED}
        # honest note, not the authoritative-evidence wrapper, no error internals
        assert "vision subsystem unavailable" not in part["text"]
        assert "contentFilter" not in part["text"]
        assert "1301" not in part["text"]
        assert calls, "MCP should have been attempted once"

        # re-send of the same image: served from the negative cache, no MCP call
        before = len(calls)
        part2 = await hook._transcribe(DATA_URL)
        assert part2 == {"type": "text", "text": _VISION_BLOCKED}
        assert len(calls) == before
    finally:
        reset()


def test_filtered_image_returns_blocked_note_and_caches():
    asyncio.run(_run_filtered_image_case())


async def _run_non_filter_error_case():
    calls = []
    reset = _install_fake_mcp(calls, error=Exception("HTTP 500: upstream exploded"))
    try:
        hook = VisionHook.__new__(VisionHook)
        part = await hook._transcribe(DATA_URL)
        assert part["type"] == "text"
        assert "<vision_unavailable>" in part["text"]
        assert "server error" in part["text"]           # friendly reason, ...
        assert "HTTP 500" not in part["text"]           # ...without internals
        assert "vision subsystem unavailable" not in part["text"]
        assert not vision_proxy._transcripts           # nothing cached: next send retries
    finally:
        reset()


def test_non_filter_errors_get_friendly_unavailable_note():
    asyncio.run(_run_non_filter_error_case())


def test_failure_reason_categories():
    f = vision_proxy._failure_reason
    assert f(Exception("Read timed out")) == "the vision service timed out"
    assert (
        f(Exception("Connect call failed to http://zai-vision-mcp:8000"))
        == "the vision service was unreachable"
    )
    assert f(Exception("HTTP 502: Bad Gateway")) == "the vision service returned a server error"
    assert f(Exception("HTTP 429: rate limit exceeded")) == "the vision service is rate-limited"
    assert f(Exception("HTTP 400: bad request")) == "the vision service rejected the request"
    assert (
        f(Exception("no vision-like tool exposed by MCP server"))
        == "the vision service is misconfigured"
    )
    assert f(Exception("weird unknown failure")) == "an unexpected vision service error occurred"
