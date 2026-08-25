# custom_hooks/litellm_vision_proxy.py
"""
Transparent vision for text-only GLM via the Z.AI vision MCP sidecar.

Per request: image_url parts -> base64 decoded + sha256'd (cache check)
-> written to /tmp, served at https://<litellm>/public/images/<id>
-> SSE MCP call -> transcript replaces the image part -> file deleted.

Coding-agent clients also emit an `<attached_files>` placeholder next to each
image; once the image is transcribed in place that block references a file id
that no longer exists, so it is stripped from the same message (saves the dead
tokens from being re-sent on every history turn).

Images rejected by the upstream content filter are replaced with an honest
`<vision_unavailable>` note (no error internals, no "authoritative evidence"
framing) and the block is cached per digest so history re-sends don't re-trigger
the MCP call. Transient failures (outages, timeouts, 5xx) get the same honest
treatment with a user-safe reason string; they are never cached, so the next
send retries automatically.
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

# Sentinel stored in _transcripts for images the provider's content filter
# rejected: re-sends of the same bytes must not re-attempt the MCP call.
# A NUL byte can never occur in a real transcript, so it cannot collide.
_FILTERED = "\x00content-filtered"

# Substrings that stably identify Z.AI/GLM moderation rejections (HTTP 400,
# error code 1301) in whatever form the MCP tool error detail embeds them.
_CONTENT_FILTER_MARKERS = ("contentFilter", "unsafe or sensitive content")

_VISION_PROMPT = (
    "You are the vision subsystem of a coding agent. Describe this image with high "
    "fidelity for a text-only coding model: transcribe visible text and error messages "
    "verbatim, describe UI elements and layout, reproduce any code exactly, and note "
    "anything visually unusual. Be comprehensive but compact: write a dense factual "
    "summary, not an essay — omit filler sections, meta-commentary, and analysis of "
    "your own process. Length is only justified by verbatim text or code that must "
    "be reproduced exactly."
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


_ATTACHED_FILES_RE = re.compile(
    r"[ \t]*<attached_files>.*?</attached_files>[ \t]*", re.DOTALL
)


def _strip_attached_files(text: str) -> str:
    """Remove `<attached_files>` blocks left behind once images are transcribed.

    There is no id linkage between a data:-URL image part and the placeholder's
    `<file id=...>` entries, so the only safe scope is "same message". Returns
    "" when the part carried nothing but the placeholder.
    """
    if "<attached_files>" not in text:
        return text
    cleaned = _ATTACHED_FILES_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# Replacement text for a content-filtered image. Deliberately NOT wrapped in
# _wrap(): the anti-leak preamble there ("use it as if you inspected the image")
# would force the model to bluff through a block. Here we want the opposite —
# an honest, compact note it can relay to the user.
_VISION_BLOCKED = (
    "<vision_unavailable>\n"
    "The attached image was blocked by the vision provider's content filter and was "
    "not analyzed. Tell the user their image was blocked by a content safety filter, "
    "do not guess at or describe its contents, and continue with the rest of their "
    "request.\n"
    "</vision_unavailable>"
)


def _is_content_filter_error(exc: BaseException) -> bool:
    text = str(exc)
    return any(marker in text for marker in _CONTENT_FILTER_MARKERS)


# Template for transient failures (MCP outage, timeout, upstream 5xx). Same
# honest framing as _VISION_BLOCKED, plus a retry hint: these are never cached,
# so the next send re-attempts transcription.
_VISION_FAILED = (
    "<vision_unavailable>\n"
    "The attached image could not be analyzed: {reason}. Tell the user their "
    "image was not processed and why in plain terms, do not guess at or describe "
    "its contents, and suggest they try again — it will be re-analyzed on their "
    "next message.\n"
    "</vision_unavailable>"
)


def _failure_reason(exc: BaseException) -> str:
    """Map a transcription failure to a short, user-safe reason.

    Exception text can contain internal hostnames and provider payloads, which
    belong in the proxy logs — not in a note the model relays to the user.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "the vision service timed out"
    if "connect" in text or "connection" in text or "refused" in text:
        return "the vision service was unreachable"
    if "http 5" in text or any(s in text for s in ("502", "503", "504")):
        return "the vision service returned a server error"
    if "429" in text or "rate" in text:
        return "the vision service is rate-limited"
    if "http 4" in text or "400" in text or "401" in text or "403" in text:
        return "the vision service rejected the request"
    if "no vision-like tool" in text:
        return "the vision service is misconfigured"
    return "an unexpected vision service error occurred"


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
        transcribed_msgs = {mi for mi, _, _ in jobs}

        for mi, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            rebuilt = []
            for pi, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "image_url":
                    rebuilt.append(lookup.get((mi, pi), part))
                elif (
                    mi in transcribed_msgs
                    and isinstance(part, dict)
                    and part.get("type") == "text"
                ):
                    cleaned = _strip_attached_files(str(part.get("text") or ""))
                    if cleaned:
                        rebuilt.append({**part, "text": cleaned})
                    # else: part carried only the placeholder -> drop it
                else:
                    rebuilt.append(part)
            msg["content"] = rebuilt
        return data

    # ---- internals ----

    async def _transcribe(self, url: str) -> dict:
        hosted_name = None
        digest = None
        try:
            if url.startswith("data:"):
                raw, digest, media_type = self._decode(url)
                cached = _transcripts.get(digest)
                if cached == _FILTERED:               # blocked earlier in this convo
                    return {"type": "text", "text": _VISION_BLOCKED}
                if cached is not None:                 # conversation history re-sends
                    return {"type": "text", "text": _wrap(cached)}
                hosted_name, public_url = self._write(raw, digest, media_type)
            else:
                public_url = url

            from custom_hooks.zai_mcp_client import describe_image
            try:
                transcript = await describe_image(
                    MCP_BASE, public_url, _VISION_PROMPT, MCP_TOOL
                )
            except Exception as exc:
                if _is_content_filter_error(exc):      # moderation, not a fault
                    logger.warning(
                        "image blocked by upstream content filter (digest=%s)",
                        (digest or "?")[:16],
                    )
                    if digest:
                        _cache_put(digest, _FILTERED)  # don't re-attempt re-sends
                    return {"type": "text", "text": _VISION_BLOCKED}
                raise
            if digest:
                _cache_put(digest, transcript)
            return {"type": "text", "text": _wrap(transcript)}
        except Exception as exc:                       # never fail the whole request
            logger.exception("vision transcription failed")
            return {
                "type": "text",
                "text": _VISION_FAILED.format(reason=_failure_reason(exc)),
            }
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