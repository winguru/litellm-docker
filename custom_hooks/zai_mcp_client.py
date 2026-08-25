# custom_hooks/zai_mcp_client.py
"""
Minimal async MCP client for the SSE transport (GET /sse + POST /message?sessionId=).
httpx only — no packages beyond what the LiteLLM image already ships.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx


class McpSseError(RuntimeError):
    pass


class McpSseSession:
    def __init__(self, base_url: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._resp = None
        self._reader: asyncio.Task | None = None
        self._post_url: str | None = None
        self._queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "McpSseSession":
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0))
        req = self._client.build_request(
            "GET", f"{self.base_url}/sse", headers={"Accept": "text/event-stream"}
        )
        self._resp = await self._client.send(req, stream=True)
        self._resp.raise_for_status()
        self._reader = asyncio.create_task(self._read_stream())
        self._post_url = await asyncio.wait_for(self._handshake(), 10.0)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._reader:
            self._reader.cancel()
        if self._resp is not None:
            await self._resp.aclose()
        if self._client is not None:
            await self._client.aclose()

    async def _read_stream(self) -> None:
        event, data_lines = None, []
        try:
            async for raw in self._resp.aiter_lines():
                line = raw.rstrip("\r")
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())
                elif line == "" and data_lines:          # blank line = frame boundary
                    payload = "\n".join(data_lines)
                    if event == "endpoint":
                        self._queue.put_nowait(("endpoint", payload))
                    elif event in (None, "message"):     # JSON-RPC responses
                        self._queue.put_nowait(("message", payload))
                    event, data_lines = None, []
        except (httpx.StreamClosed, httpx.HTTPError, asyncio.CancelledError):
            pass

    async def _handshake(self) -> str:
        kind, endpoint = await self._queue.get()
        if kind != "endpoint":
            raise McpSseError(f"unexpected first SSE event: {kind}")
        return endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"

    async def _next_message(self) -> str:
        while True:
            kind, payload = await self._queue.get()
            if kind == "message":
                return payload

    async def _rpc(self, method: str, params=None, notify: bool = False):
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = uuid.uuid4().hex
        r = await self._client.post(self._post_url, json=msg)
        r.raise_for_status()                              # 202 Accepted for POSTs
        if notify:
            return None
        while True:
            try:
                data = json.loads(await self._next_message())
            except json.JSONDecodeError:
                continue
            if data.get("id") == msg["id"]:
                if "error" in data:
                    raise McpSseError(f"{method} failed: {data['error']}")
                return data.get("result", {})

    async def initialize(self) -> None:
        await self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "litellm-vision-hook", "version": "1.0"},
        })
        await self._rpc("notifications/initialized", notify=True)

    async def list_tools(self) -> list[dict]:
        return (await self._rpc("tools/list")).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpSseError(f"tool '{name}' returned an error result")
        return "\n".join(
            b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"
        )


async def describe_image(
    base_url: str, image_url: str, prompt: str,
    tool_name: str | None = None, timeout: float = 180.0,
) -> str:
    """One-shot: open an SSE session, transcribe, close. Session-per-call keeps
    this stateless across your 2 uvicorn workers."""
    async with McpSseSession(base_url, timeout) as session:
        await session.initialize()
        if tool_name is None:
            for t in await session.list_tools():
                n = t.get("name", "").lower()
                if "vision" in n or "image" in n:
                    tool_name = t["name"]
                    break
            if tool_name is None:
                raise McpSseError("no vision-like tool exposed by MCP server")
        return await session.call_tool(tool_name, {"url": image_url, "prompt": prompt})