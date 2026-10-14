"""麦当劳官方 MCP Server 的最小 Streamable HTTP 客户端。

只用标准库实现，避免为了几个只读调用而引入依赖。协议细节：
  接入地址  https://mcp.mcd.cn
  传输协议  Streamable HTTP
  鉴权      Authorization: Bearer <MCD_MCP_TOKEN>

本模块只调用只读 Tool，不包含任何下单、领券、抽奖等写操作。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

MCP_ENDPOINT = "https://mcp.mcd.cn"
TOKEN_ENV = "MCD_MCP_TOKEN"
PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    """MCP 调用失败。"""


class MCPClient:
    """只读的 MCP 客户端。

    每个实例持有一个会话；Token 从环境变量读取，绝不写入代码或配置文件。

    Attributes:
        endpoint: MCP Server 地址。
        token: 访问令牌。
        _session_id: 服务端返回的会话标识。
    """

    def __init__(self, token: str | None = None, endpoint: str = MCP_ENDPOINT) -> None:
        self.endpoint = endpoint
        self.token = token or os.environ.get(TOKEN_ENV, "")
        self._session_id: str | None = None
        self._next_id = 1

    def _headers(self) -> dict[str, str]:
        """构造请求头。"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送一次 JSON-RPC 请求并解析响应。"""
        if not self.token:
            raise MCPError(
                f"未设置 {TOKEN_ENV}。请先在 https://open.mcd.cn/mcp 申请 MCP Token，"
                f"然后 export {TOKEN_ENV}=你的token"
            )

        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self._session_id = session
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise MCPError(f"MCP 请求失败 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"无法连接 MCP Server：{exc.reason}") from exc

        return self._parse_body(body)

    @staticmethod
    def _parse_body(body: str) -> dict[str, Any]:
        """解析响应体，兼容 SSE 与纯 JSON 两种返回形式。"""
        stripped = body.strip()
        if not stripped:
            return {}
        if stripped.startswith("{"):
            return json.loads(stripped)

        # 服务端可能以 text/event-stream 返回，逐行取 data: 负载。
        for line in stripped.splitlines():
            if line.startswith("data:"):
                candidate = line[len("data:"):].strip()
                if candidate.startswith("{"):
                    return json.loads(candidate)
        raise MCPError(f"无法解析 MCP 响应：{stripped[:200]}")

    def initialize(self) -> dict[str, Any]:
        """建立会话并完成协议握手。"""
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mcd-yearbook", "version": "1.0.0"},
                },
            }
        )
        self._next_id += 1
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """列出服务端可用 Tool。"""
        result = self._post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/list", "params": {}}
        )
        self._next_id += 1
        return result.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """调用一个 Tool 并返回其文本内容。

        Args:
            name: Tool 名称。
            arguments: 调用参数。

        Returns:
            Tool 返回的文本内容；若为 JSON 则返回已解析对象。
        """
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        self._next_id += 1

        if "error" in result:
            raise MCPError(f"调用 {name} 失败：{result['error']}")

        content = result.get("result", {}).get("content", [])
        if not content:
            return None
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
