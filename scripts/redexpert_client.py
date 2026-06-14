#!/usr/bin/env python3
"""Minimal reusable client for the Würth REDEXPERT MCP server
(streamable-HTTP/SSE). Used to fetch real Würth parametric data for the
librarian. No credentials needed (public MCP).
"""
from __future__ import annotations

import contextlib
import json

import httpx

URL = "https://redexpert.we-online.com/mcp"
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _parse_sse(text: str):
    for line in text.splitlines():
        if line.startswith("data:"):
            with contextlib.suppress(json.JSONDecodeError):
                yield json.loads(line[5:].strip())


class RedexpertClient:
    def __init__(self) -> None:
        self._client = httpx.Client(follow_redirects=True, timeout=60.0)
        self._headers = dict(_HEADERS)
        self._init()

    def _rpc(self, method: str, params: dict | None = None, rpc_id=None):
        body: dict = {"jsonrpc": "2.0", "method": method}
        if rpc_id is not None:
            body["id"] = rpc_id
        if params is not None:
            body["params"] = params
        r = self._client.post(URL, json=body, headers=self._headers)
        sid = r.headers.get("mcp-session-id")
        if sid:
            self._headers["mcp-session-id"] = sid
        return list(_parse_sse(r.text))

    def _init(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "heaviside-librarian", "version": "0.1"}}, rpc_id=1)
        self._rpc("notifications/initialized")

    def call_tool(self, name: str, arguments: dict) -> object:
        """Call an MCP tool; return the parsed JSON content (REDEXPERT
        returns its payload as text content holding JSON)."""
        objs = self._rpc("tools/call", {"name": name, "arguments": arguments}, rpc_id=2)
        for o in objs:
            res = o.get("result")
            if not res:
                if "error" in o:
                    raise RuntimeError(f"REDEXPERT {name} error: {o['error']}")
                continue
            content = res.get("content", [])
            for c in content:
                if c.get("type") == "text":
                    txt = c.get("text", "")
                    try:
                        return json.loads(txt)
                    except json.JSONDecodeError:
                        return txt
            return res
        return None

    def family_ids(self) -> object:
        return self.call_tool("get_product_family_ids", {})

    def products(self, module: str, **kw) -> object:
        args = {"module": module}
        args.update(kw)
        return self.call_tool("get_products", args)

    def close(self) -> None:
        self._client.close()


if __name__ == "__main__":
    import sys
    c = RedexpertClient()
    fams = c.family_ids()
    print(json.dumps(fams, indent=2)[:4000] if not isinstance(fams, str) else fams[:4000])
    c.close()
    sys.exit(0)
