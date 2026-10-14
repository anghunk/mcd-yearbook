#!/usr/bin/env python3
"""用你账户的真实 MCP 响应覆盖演示数据。

这是把「演示模式」切换到「真实数据」的唯一脚本。运行后，fixtures/ 目录下的
四个 JSON 会被你账户的真实响应替换，之后 `python -m yearbook.cli` 产出的就是
你自己的年鉴。

用法：
    export MCD_MCP_TOKEN=你的token
    python3 scripts/record_fixtures.py

注意：
    - 录制结果包含你的订单信息，请勿提交到公开仓库（fixtures/real/ 已在 .gitignore 中）。
    - 脚本调用全部为只读 Tool，不会下单、领券或抽奖。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from yearbook.mcp_client import MCPClient, MCPError  # noqa: E402

TOOLS = ("order-list", "list-nutrition-foods", "query-my-account", "query-my-coupons")
OUTPUT_NAMES = {
    "order-list": "orders.json",
    "list-nutrition-foods": "nutrition.json",
    "query-my-account": "account.json",
    "query-my-coupons": "coupons.json",
}


def main() -> int:
    """录制全部只读响应到 fixtures/real/。

    Returns:
        进程退出码。
    """
    out_dir = REPO_ROOT / "fixtures" / "real"
    out_dir.mkdir(parents=True, exist_ok=True)

    client = MCPClient()
    try:
        client.initialize()
    except MCPError as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 1

    recorded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for tool in TOOLS:
        try:
            payload = client.call_tool(tool)
        except MCPError as exc:
            print(f"{tool} 调用失败：{exc}", file=sys.stderr)
            return 1

        if isinstance(payload, dict):
            payload["_recorded_at"] = recorded_at
            payload["_source_tool"] = tool
            payload["_note"] = "由 scripts/record_fixtures.py 从真实 MCP 响应录制"
        else:
            payload = {
                "_source_tool": tool,
                "_recorded_at": recorded_at,
                "_note": "该 Tool 返回非 JSON 文本，原文保留在 raw 字段",
                "raw": payload,
            }

        path = out_dir / OUTPUT_NAMES[tool]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已录制 {tool} -> {path.relative_to(REPO_ROOT)}")

    print()
    print("录制完成。把你的真实数据用于报告：")
    print("  1) 将 fixtures/real/*.json 复制到 fixtures/ 覆盖演示数据（仅在本地）")
    print("  2) python3 -m yearbook.cli --live  # 或直接读取本地副本")
    print()
    print("提醒：fixtures/real/ 含个人订单信息，已在 .gitignore 中，请勿提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
