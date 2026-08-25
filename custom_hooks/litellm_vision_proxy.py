# custom_hooks/litellm_vision_proxy.py
"""
Transparent vision for text-only GLM via the Z.AI vision MCP sidecar.

Per request: image_url parts -> base64 decoded + sha256'd (cache check)
-> written to /tmp, served at https://<litellm>/public/images/<id>
-> SSE MCP call -> transcript replaces the image part -> file deleted.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import os
import re
import time
import uuid

from fastapi import Response
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger(__name__)

IMAGE_DIR = os.environ.get("VISION_IMAGE_DIR", "/tmp/litellm_vision_images")
PUBLIC_BASE = os.environ.get(
    "LITELLM_PUBLIC_BASE_URL", "https://litellm.thewinguru.com"
).rstrip("/")
MCP_BASE = os.environ.get("ZAI_MCP_BASE_URL", "http://zai-vision-mcp:8000")
MCP_TOOL = os.environ.get("ZAI_VISION_TOOL") or None       # None = auto-discover
IMAGE_TTL = int(os.environ.get("VISION_IMAGE_TTL", "600"))
CACHE_MAX = int(os.environ.get("VISION_CACHE_MAX", "256"))

os.makedirs(IMAGE_DIR, exist_ok=True)

_transcripts: dict[str, str] = {}   # sha256(image bytes) -> transcript

_VISION_PROMPT = (
    "You are the vision subsystem of a coding agent. Describe this image with high "
    "fidelity for a text-only coding model: transcribe visible text and error messages "
    "verbatim, describe UI elements and layout, reproduce any code exactly, and note "
    "anything visually unusual."
)


def _wrap(text: str) -> str:
    return (
        "<vision_transcript>\n"
        "The following is authoritative visual evidence from an image attached by the user. "
        "Use it as if you inspected the image directly. Do not say that no image was provided, "
        "do not mention this transcript or the vision subsystem, and do not ask the user to "
        "re-upload the image.\n"
        f"{text}\n"
        "</vision_transcript>"
    )


def _cache_put(digest: str, transcript: str) -> None:
    _transcripts[digest] = transcript
    while len(_transcripts) > CACHE_MAX:
        _transcripts.pop(next(iter(_transcripts)))


def _sweep_expired() -> None:
    now = time.time()
    for name in os.listdir(IMAGE_DIR):
        p = os.path.join(IMAGE_DIR, name)
        try:
            if now - os.path.getmtime(p) > IMAGE_TTL:
                os.remove(p)
        except OSError:
            pass


class VisionHook(CustomLogger):
    def __init__(self) -> None:
        super().__init__()
        self._register_routes()

    # ---- route injected into the already-running LiteLLM FastAPI app ----

    def _register_routes(self) -> None:
        from litellm.proxy import proxy_server   # deferred: avoids import cycles

        @proxy_server.app.get("/public/images/{image_id}")
        async def serve_image(image_id: str) -> Response:
            if image_id != os.path.basename(image_id):          # traversal guard
                return Response(status_code=400)
            path = os.path.join(IMAGE_DIR, image_id)
            if not os.path.isfile(path):
                return Response(status_code=404, content=b"image expired")
            with open(path, "rb") as f:
                data = f.read()
            media = mimetypes.guess_type(image_id)[0] or "application/octet-stream"
            return Response(
                content=data,
                media_type=media,
                headers={"Cache-Control": "no-store"},   # stop CDNs caching secrets
            )

    # ---- the hook ----

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        messages = data.get("messages") or []
        jobs = []
        for mi, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for pi, part in enumerate(content):
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue

                image_url = part.get("image_url")
                if isinstance(image_url, str):
                    url = image_url
                elif isinstance(image_url, dict):
                    url = image_url.get("url", "")
                else:
                    url = ""

                if url:
                    jobs.append((mi, pi, asyncio.create_task(self._transcribe(url))))

        if not jobs:
            return data

        results = await asyncio.gather(*(j[2] for j in jobs))  # parallel MCP calls
        lookup = {(mi, pi): res for (mi, pi, _), res in zip(jobs, results)}

        for mi, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            msg["content"] = [
                lookup.get((mi, pi), part)
                if isinstance(part, dict) and part.get("type") == "image_url"
                else part
                for pi, part in enumerate(content)
            ]
        return data

    # ---- internals ----

    async def _transcribe(self, url: str) -> dict:
        hosted_name = None
        try:
            if url.startswith("data:"):
                raw, digest, media_type = self._decode(url)
                cached = _transcripts.get(digest)
                if cached is not None:                 # conversation history re-sends
                    return {"type": "text", "text": _wrap(cached)}
                hosted_name, public_url = self._write(raw, digest, media_type)
            else:
                public_url, digest = url, None

            from custom_hooks.zai_mcp_client import describe_image
            transcript = await describe_image(MCP_BASE, public_url, _VISION_PROMPT, MCP_TOOL)
            if digest:
                _cache_put(digest, transcript)
            return {"type": "text", "text": _wrap(transcript)}
        except Exception as exc:                       # never fail the whole request
            logger.exception("vision transcription failed")
            return {"type": "text", "text": _wrap(f"(vision subsystem unavailable: {exc})")}
        finally:
            if hosted_name:
                try:
                    os.remove(os.path.join(IMAGE_DIR, hosted_name))
                except OSError:
                    pass
                _sweep_expired()

    @staticmethod
    def _decode(data_url: str) -> tuple[bytes, str, str]:
        header, _, b64 = data_url.partition(",")
        raw = base64.b64decode(b64)
        media_type = header[5:].split(";", 1)[0] or "image/png"
        if media_type not in {"image/png", "image/jpeg"}:
            media_type = "image/png"
        return raw, hashlib.sha256(raw).hexdigest(), media_type

    @staticmethod
    def _write(raw: bytes, digest: str, media_type: str) -> tuple[str, str]:
        extension = ".jpg" if media_type == "image/jpeg" else ".png"
        name = f"{digest[:16]}.{uuid.uuid4().hex[:8]}{extension}"
        with open(os.path.join(IMAGE_DIR, name), "wb") as f:
            f.write(raw)
        return name, f"{PUBLIC_BASE}/public/images/{name}"


hook = VisionHook()