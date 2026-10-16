"""麦小省 · 麦当劳 MCP 客户端

零第三方依赖，仅使用 Python 标准库。
实现 MCP Streamable HTTP 传输：initialize 握手 → tools/call。

用法：
    client = McDMcpClient()              # 自动读环境变量 MCD_MCP_TOKEN
    data = client.call("query-my-coupons")
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "mcd-penny-pal"
CLIENT_VERSION = "0.1.0"

# 每分钟请求上限（麦当劳 MCP 服务规则）
RATE_LIMIT_PER_MIN = 600


class McpError(RuntimeError):
    """MCP 调用失败。"""


class McDMcpClient:
    """麦当劳 MCP 的极简客户端。

    一个实例内部维护一次会话（Mcp-Session-Id），可复用。
    """

    def __init__(
        self,
        token: str | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: int = 30,
    ) -> None:
        self.token = (token or os.environ.get("MCD_MCP_TOKEN", "")).strip()
        if not self.token:
            raise McpError(
                "缺少 MCP Token。请在 https://open.mcd.cn/mcp 控制台激活并复制，"
                "然后设置环境变量 MCD_MCP_TOKEN。"
            )
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0
        self._initialized = False
        self._call_log: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- 传输层

    def _post(self, payload: dict[str, Any], expect_response: bool = True) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                ctype = resp.headers.get("Content-Type", "") or ""
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001 - 错误详情拿不到也不该二次抛出
                pass
            if exc.code == 401:
                raise McpError(
                    f"MCP Token 无效、已过期或未提供（401）。{detail}"
                ) from exc
            if exc.code == 429:
                raise McpError(
                    f"触发限流（429）：每分钟上限 {RATE_LIMIT_PER_MIN} 次，请降低请求频率。"
                ) from exc
            raise McpError(f"HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"网络不可达：{exc.reason}") from exc

        if not expect_response:
            return None
        if "text/event-stream" in ctype:
            return self._parse_sse(raw)
        return json.loads(raw) if raw.strip() else None

    @staticmethod
    def _parse_sse(raw: str) -> Any:
        """从 SSE 流里取出最后一条 data: 的有效 JSON-RPC 响应。"""
        result = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
                result = parsed
        return result

    def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        if notify:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, False)
            return None
        self._next_id += 1
        resp = self._post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        )
        if not isinstance(resp, dict):
            raise McpError(f"{method} 未返回有效响应：{resp!r}")
        if "error" in resp:
            err = resp["error"]
            raise McpError(f"{method} 失败：{err.get('message', err)}")
        return resp.get("result")

    # ---------------------------------------------------------------- 协议层

    def initialize(self) -> dict[str, Any]:
        """完成 MCP 握手。重复调用会被忽略。"""
        if self._initialized:
            return {"already": True}
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True
        return result or {}

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list") or {}
        return result.get("tools", [])

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """调用一个 MCP 工具，返回解析后的业务数据。

        麦当劳 MCP 把业务数据放在 content[0].text 里（JSON 字符串），
        这里做统一拆包，调用方直接拿到 dict / list。
        """
        self.initialize()
        args = arguments or {}
        started = time.time()
        result = self._rpc("tools/call", {"name": tool, "arguments": args})
        elapsed = time.time() - started

        if result is None:
            payload: Any = None
        elif isinstance(result, dict) and result.get("isError"):
            text = self._extract_text(result)
            self._log(tool, args, elapsed, ok=False)
            raise McpError(f"{tool} 返回错误：{text}")
        else:
            payload = self._unwrap(result)

        self._log(tool, args, elapsed, ok=True, payload=payload)
        return payload

    @staticmethod
    def _extract_text(result: dict[str, Any]) -> str:
        parts = result.get("content") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
        return "\n".join(t for t in texts if t)

    @classmethod
    def _unwrap(cls, result: Any) -> Any:
        """把 MCP 的 content 包装拆成纯业务数据。"""
        if isinstance(result, dict) and "content" in result:
            text = cls._extract_text(result)
            if not text:
                return result.get("structuredContent", result)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return result

    # ---------------------------------------------------------------- 可观测性

    def _log(self, tool: str, args: dict[str, Any], elapsed: float, ok: bool, payload: Any = None) -> None:
        self._call_log.append(
            {
                "tool": tool,
                "arguments": args,
                "seconds": round(elapsed, 3),
                "ok": ok,
                "payload_type": type(payload).__name__,
            }
        )

    @property
    def call_log(self) -> list[dict[str, Any]]:
        """本次会话的工具调用记录，用于 MCP_INTEGRATION.md 佐证。"""
        return list(self._call_log)

    def dump_log(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._call_log, fh, ensure_ascii=False, indent=2)
        return path
