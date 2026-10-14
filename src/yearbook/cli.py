"""命令行入口：一条命令生成年鉴报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import build_report
from .normalize import parse_coupons, parse_nutrition, parse_orders, parse_points
from .render import render_html, render_json

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "fixtures"


def _load(path: Path) -> dict:
    """读取 JSON 文件，失败时给出可读的错误信息。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"找不到数据文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} 不是合法 JSON：{exc}") from exc


def build_from_fixtures() -> tuple:
    """从 fixtures 目录读取演示数据并生成报告。"""
    orders = parse_orders(_load(FIXTURE_DIR / "orders.json"))
    nutrition = parse_nutrition(_load(FIXTURE_DIR / "nutrition.json"))
    points = parse_points(_load(FIXTURE_DIR / "account.json"))
    coupons = parse_coupons(_load(FIXTURE_DIR / "coupons.json"))
    report = build_report(orders, nutrition, points, coupons, fixture_mode=True)
    return report, orders


def build_from_live() -> tuple:
    """从麦当劳官方 MCP 实时拉取数据并生成报告。"""
    from .mcp_client import MCPClient, MCPError

    client = MCPClient()
    try:
        client.initialize()
        orders_payload = client.call_tool("order-list")
        nutrition_payload = client.call_tool("list-nutrition-foods")
        account_payload = client.call_tool("query-my-account")
        coupons_payload = client.call_tool("query-my-coupons")
    except MCPError as exc:
        raise SystemExit(f"实时取数失败：{exc}") from exc

    orders = parse_orders(orders_payload)
    nutrition = parse_nutrition(nutrition_payload)
    points = parse_points(account_payload)
    coupons = parse_coupons(coupons_payload)
    report = build_report(orders, nutrition, points, coupons, fixture_mode=False)
    return report, orders


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行。

    Returns:
        进程退出码，0 表示成功。
    """
    parser = argparse.ArgumentParser(
        prog="mcd-yearbook",
        description="麦麦年鉴：把麦当劳订单变成可溯源的年度回顾。",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="从麦当劳官方 MCP 实时取数（需要环境变量 MCD_MCP_TOKEN）",
    )
    parser.add_argument(
        "--out",
        default="out/yearbook.html",
        help="HTML 输出路径，默认 out/yearbook.html",
    )
    parser.add_argument(
        "--json-out",
        default="out/report.json",
        help="JSON 输出路径，默认 out/report.json",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出文件路径")
    args = parser.parse_args(argv)

    report, orders = build_from_live() if args.live else build_from_fixtures()

    html_path = Path(args.out)
    json_path = Path(args.json_out)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")

    if args.quiet:
        print(html_path)
        return 0

    mode = "实时 MCP" if args.live else "演示数据"
    print(f"数据模式：{mode}")
    print(f"订单数：{len(orders)}")
    print(f"指标数：{len(report.metrics)}")
    print(f"证据数：{report.evidence_total()}")
    print()
    for label, text, _ in report.story:
        print(f"  {label}：{text}")
    print()
    print(f"已生成 {html_path}")
    print(f"已生成 {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
