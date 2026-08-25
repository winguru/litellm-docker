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

from custom_hooks.litellm_vision_proxy import VisionHook


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
