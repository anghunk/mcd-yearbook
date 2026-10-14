"""命令行入口：一条命令生成年鉴报告。"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from .analyze import build_report
from .normalize import (
    extract_payload,
    parse_coupons,
    parse_nutrition,
    parse_orders,
    parse_points,
)
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
    """从 fixtures 目录读取演示数据并生成报告。

    fixtures 中的文件同时兼容两种形态：结构化 JSON，或录制下来的
    Markdown 包裹响应（带 `raw` 字段）。统一经 `extract_payload` 处理。

    Returns:
        (报告, 订单列表)。演示数据始终来自仓库内提交的 `fixtures/`，
        与用户本机录制的 `fixtures/real/` 无关，保证任何人 clone 后结果一致。
    """
    return _build_from_dir(FIXTURE_DIR, fixture_mode=True)


def build_from_real_fixtures() -> tuple:
    """从本机录制的真实响应生成报告。

    数据来自 `scripts/record_fixtures.py` 写出的 `fixtures/real/`，
    该目录已在 .gitignore 中，不会被提交。

    Returns:
        (报告, 订单列表)。
    """
    real_dir = FIXTURE_DIR / "real"
    if not (real_dir / "orders.json").exists():
        raise SystemExit(
            "找不到 fixtures/real/。请先运行：\n"
            "  export MCD_MCP_TOKEN=你的token\n"
            "  python3 scripts/record_fixtures.py"
        )
    return _build_from_dir(real_dir, fixture_mode=False)


def _build_from_dir(base: Path, fixture_mode: bool) -> tuple:
    """从指定目录读取四个数据文件并生成报告。"""
    orders = parse_orders(extract_payload(_payload(base / "orders.json"), "order-list"))
    nutrition = parse_nutrition(
        extract_payload(_payload(base / "nutrition.json"), "list-nutrition-foods")
    )
    points = parse_points(extract_payload(_payload(base / "account.json"), "query-my-account"))
    coupons = parse_coupons(extract_payload(_payload(base / "coupons.json"), "query-my-coupons"))
    report = build_report(orders, nutrition, points, coupons, fixture_mode=fixture_mode)
    return report, orders


def _payload(path: Path):
    """读取 fixture 并取出其中的载荷部分。

    录制脚本会把原始 Markdown 文本放在 `raw` 字段中，这里做一个拆包，
    让后续解析函数拿到的始终是"待解析的内容"。
    """
    data = _load(path)
    return data.get("raw", data) if isinstance(data, dict) else data


def build_from_live() -> tuple:
    """从麦当劳官方 MCP 实时拉取数据并生成报告。"""
    from .mcp_client import MCPClient, MCPError

    client = MCPClient()
    try:
        client.initialize()
        orders_payload = extract_payload(client.call_tool("order-list"), "order-list")
        nutrition_payload = extract_payload(
            client.call_tool("list-nutrition-foods"), "list-nutrition-foods"
        )
        account_payload = extract_payload(
            client.call_tool("query-my-account"), "query-my-account"
        )
        coupons_payload = extract_payload(
            client.call_tool("query-my-coupons"), "query-my-coupons"
        )
    except MCPError as exc:
        raise SystemExit(f"实时取数失败：{exc}") from exc
    except Exception as exc:  # noqa: BLE001 - 归一化失败需给出可读提示
        raise SystemExit(f"响应解析失败：{exc}") from exc

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
        "--from-real",
        action="store_true",
        help="使用本机录制的真实响应 fixtures/real/（先跑 scripts/record_fixtures.py）",
    )
    parser.add_argument(
        "--out",
        default="out/yearbook.html",
        help="HTML 输出路径，默认 out/yearbook.html",
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="生成后用默认浏览器打开报告",
    )
    parser.add_argument(
        "--json-out",
        default="out/report.json",
        help="JSON 输出路径，默认 out/report.json",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出文件路径")
    args = parser.parse_args(argv)

    if args.live and args.from_real:
        raise SystemExit("--live 与 --from-real 只能选一个。")
    if args.live:
        report, orders = build_from_live()
    elif args.from_real:
        report, orders = build_from_real_fixtures()
    else:
        report, orders = build_from_fixtures()

    html_path = Path(args.out)
    json_path = Path(args.json_out)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")

    if args.open_browser:
        # 用标准库 webbrowser，天然跨平台（macOS / Linux / Windows）。
        webbrowser.open(html_path.resolve().as_uri())

    if args.quiet:
        print(html_path)
        return 0

    if args.live:
        mode = "实时 MCP"
    elif args.from_real:
        mode = "本机录制数据"
    else:
        mode = "演示数据"
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
