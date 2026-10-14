"""把麦当劳官方 MCP 的原始响应归一化为领域模型。

本模块是"数据入口"的唯一通道：无论是录制的 fixture 还是实时 MCP 响应，
都先经过这里转成 `models` 中的数据结构，后续统计与渲染不再接触原始响应。
这样做的收益是——当官方字段调整时，只需改这一个文件。

关于真实响应格式（实测 2026-10-14，与公开文档存在差异，此处按实测实现）：

1. Tools 返回的 `content[0].text` 是一段 Markdown，结构为：

       # API Response Information
       ...（字段说明）...
       ## Original Response
       {"success":true,...,"data":{...}}

   真正可用的载荷在 `## Original Response` 之后。

2. `data` 的类型**因 Tool 而异**：
   - `order-list`：`data.list` 为订单数组；无订单时 `data` 为 `{}`。
   - `query-my-account`：`data` 是对象，字段为 `availablePoint`
     （单数 Point）等字符串型数字。
   - `list-nutrition-foods`：`data` 是**一段文本表格**，不是 JSON 数组，
     形如 `[160]{productName,...,calcium}:\n  巨无霸,null,...`。
   - `query-my-coupons`：可能直接返回 Markdown 文案（如"暂无可用优惠券"）。

3. 营养表只有 `productName`，**没有商品编码**。因此营养匹配以商品名称为键，
   并对套餐子项名称做规范化处理（见 `_normalize_product_name`）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .models import ComboItem, Nutrition, Order, OrderItem


class SchemaError(ValueError):
    """官方响应结构与预期不符时抛出。

    宁可显式失败，也不要用默认值静默产出一个错误的报告。
    """


_ORIGINAL_RESPONSE_RE = re.compile(r"^##\s*Original Response\s*$", re.MULTILINE)
_NUTRITION_HEADER_RE = re.compile(r"^\[(\d+)\]\{(.+?)\}:\s*$")


def extract_payload(text: Any, tool: str) -> Any:
    """从 MCP Tool 返回的 Markdown 说明中取出真正的载荷。

    Args:
        text: `content[0].text` 原文，可能已经是 dict（fixture 直接存对象的情况）。
        tool: Tool 名称，仅用于错误信息。

    Returns:
        解析后的 JSON 对象；若原文本身就是对象则原样返回。

    Raises:
        SchemaError: 文本中找不到 `## Original Response` 段且无法整体解析为 JSON。
    """
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        raise SchemaError(f"{tool} 返回了非文本、非对象内容：{type(text).__name__}")

    match = _ORIGINAL_RESPONSE_RE.search(text)
    if match:
        candidate = text[match.end():].strip()
        # 载荷可能被 ``` 代码块包裹，一并去掉。
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{tool} 的 Original Response 不是合法 JSON：{exc}") from exc

    # 没有 Markdown 包装时，尝试整体按 JSON 解析。
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # 少数 Tool 在"没有数据"时直接返回纯文案（实测 query-my-coupons
        # 无券时返回"# 您的优惠券列表\n\n暂无可用优惠券"）。
        # 这类文案不是错误，按空载荷处理，由各解析函数给出空结果。
        if tool == "query-my-coupons":
            return {"success": True, "code": 200, "data": {}}
        raise SchemaError(
            f"{tool} 响应既不含 '## Original Response' 段，也不是合法 JSON。"
            f"原文开头：{text[:120]!r}"
        ) from exc


def _sanitize_number(token: str) -> float:
    """把表格里的数字字段转成 float。

    Args:
        token: 形如 `308`、`null`、`-` 的字段。

    Returns:
        数值；`null`、`-`、空串等无法解析时返回 0.0。
    """
    cleaned = token.strip()
    if cleaned.lower() in {"null", "none", "-", "", "na", "n/a"}:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


_NAME_NOISE_RE = re.compile(r"[\s\u3000]+")


def _normalize_product_name(name: str) -> str:
    """把商品名规范化，用于跨 Tool 的名称匹配。

    订单明细与营养表来自不同接口，名称上存在空白字符、全半角差异。
    这里只做保守的规范化（去空白、统一括号），不做模糊匹配，
    避免把两个不同商品错误地关联到同一条营养记录。

    Args:
        name: 原始商品名。

    Returns:
        规范化后的名称。
    """
    return _NAME_NOISE_RE.sub("", name).replace("（", "(").replace("）", ")").strip()


def _require(payload: Mapping[str, Any], key: str, context: str) -> Any:
    """读取必需字段，缺失时抛出 SchemaError。"""
    if key not in payload:
        raise SchemaError(f"{context} 缺少必需字段 '{key}'")
    return payload[key]


def _as_int(value: Any, default: int = 0) -> int:
    """尽力把字段转成 int，失败时返回默认值。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    """尽力把金额字段转成 float（单位：元）。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_orders(payload: Mapping[str, Any]) -> list[Order]:
    """解析 `order-list` 响应。

    Args:
        payload: 已由 `extract_payload` 取出的响应对象。

    Returns:
        订单列表，按时间升序排列。

    Raises:
        SchemaError: 响应缺少订单数组等关键结构。
    """
    data = _require(payload, "data", "order-list 响应")
    if not data:
        # 实测：账户无历史订单时 data 为 {}，这是合法状态而非错误。
        return []
    raw_list: Sequence[Mapping[str, Any]] = data.get("list") or []

    orders: list[Order] = []
    for index, raw in enumerate(raw_list):
        context = f"order-list.data.list[{index}]"
        items: list[OrderItem] = []
        for raw_item in raw.get("orderProductList") or []:
            combo = tuple(
                ComboItem(
                    product_code=str(child.get("productCode", "")),
                    name=str(child.get("name", "")),
                    quantity=_as_int(child.get("quantity"), 1),
                )
                for child in raw_item.get("comboItemList") or []
            )
            items.append(
                OrderItem(
                    product_code=str(raw_item.get("productCode", "")),
                    product_name=str(raw_item.get("productName", "")),
                    quantity=_as_int(raw_item.get("quantity"), 1),
                    combo_items=combo,
                )
            )

        orders.append(
            Order(
                order_id=str(_require(raw, "orderId", context)),
                created_at=str(raw.get("createTime", "")),
                store_code=str(raw.get("storeCode", "")),
                store_name=str(raw.get("storeName", "")),
                status=str(raw.get("orderStatus", "")),
                items=tuple(items),
                total_amount=_as_float(raw.get("realTotalAmount")),
            )
        )

    orders.sort(key=lambda o: o.created_at)
    return orders


def parse_nutrition(payload: Mapping[str, Any]) -> dict[str, Nutrition]:
    """解析 `list-nutrition-foods` 响应。

    Args:
        payload: 已由 `extract_payload` 取出的响应对象。

    Returns:
        以规范化商品名为键的营养表。

    Note:
        该 Tool 的 `data` 是文本表格而非 JSON 数组（实测），格式为：

            [160]{productName,nutritionDescription,energyKj,energyKcal,...}:
              巨无霸,null,2300,550,26,29,45,1000,200

        表头给出了列顺序，因此解析按表头动态定位字段，不硬编码列号。
    """
    data = _require(payload, "data", "list-nutrition-foods 响应")
    table: dict[str, Nutrition] = {}

    # 形态 A：标准 JSON 数组（保留兼容，便于 fixture 使用结构化数据）
    if isinstance(data, dict) and isinstance(data.get("list"), list):
        for raw in data["list"]:
            name = str(raw.get("productName", ""))
            if not name:
                continue
            table[_normalize_product_name(name)] = Nutrition(
                product_name=name,
                energy=_as_float(raw.get("energyKcal", raw.get("energy"))),
                protein=_as_float(raw.get("protein")),
                fat=_as_float(raw.get("fat")),
                carbohydrate=_as_float(raw.get("carbohydrate")),
                sodium=_as_float(raw.get("sodium")),
                energy_kj=_as_float(raw.get("energyKj")),
            )
        return table

    # 形态 B：文本表格（实测形态）
    if isinstance(data, str):
        lines = [ln for ln in data.splitlines() if ln.strip()]
        if not lines:
            return table
        header_match = _NUTRITION_HEADER_RE.match(lines[0].strip())
        if not header_match:
            raise SchemaError(
                "list-nutrition-foods 的文本表格缺少形如 "
                "'[N]{col1,col2,...}:' 的表头，无法确定列顺序。"
            )
        columns = [c.strip() for c in header_match.group(2).split(",")]
        for line in lines[1:]:
            cells = [c.strip() for c in line.split(",")]
            if len(cells) != len(columns):
                # 列数不符的行跳过，避免错位解析出错误数值。
                continue
            row = dict(zip(columns, cells))
            name = row.get("productName", "")
            if not name:
                continue
            table[_normalize_product_name(name)] = Nutrition(
                product_name=name,
                energy=_sanitize_number(row.get("energyKcal", "0")),
                protein=_sanitize_number(row.get("protein", "0")),
                fat=_sanitize_number(row.get("fat", "0")),
                carbohydrate=_sanitize_number(row.get("carbohydrate", "0")),
                sodium=_sanitize_number(row.get("sodium", "0")),
                energy_kj=_sanitize_number(row.get("energyKj", "0")),
            )
        return table

    raise SchemaError(f"list-nutrition-foods 的 data 类型无法识别：{type(data).__name__}")


def parse_points(payload: Mapping[str, Any]) -> dict[str, Any]:
    """解析 `query-my-account` 响应中的积分字段。

    Note:
        实测字段为 `availablePoint` / `accumulativePoint` / `frozenPoint` /
        `currentMouthExpirePoint`（首字母单数，且官方把 Month 拼成了 Mouth）。
        同时兼容文档里出现的复数形式，避免官方修正字段名后失效。
    """
    data = _require(payload, "data", "query-my-account 响应")
    data = data or {}

    def pick(*keys: str) -> int:
        for key in keys:
            if key in data:
                return _as_int(data[key])
        return 0

    return {
        "available": pick("availablePoint", "availablePoints"),
        "total": pick("accumulativePoint", "totalPoints"),
        "frozen": pick("frozenPoint", "frozenPoints"),
        "expiring": pick("currentMouthExpirePoint", "expiringPoints"),
        "expired": pick("expiredPoint", "expiredPoints"),
        "used": pick("usedPoint", "usedPoints"),
    }


def parse_coupons(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """解析 `query-my-coupons` 响应为券列表。

    Note:
        该 Tool 在无券时可能不返回 JSON，而是直接返回 Markdown 文案
        （实测："# 您的优惠券列表\\n\\n暂无可用优惠券"）。此时返回空列表。
    """
    data = _require(payload, "data", "query-my-coupons 响应")
    if isinstance(data, dict):
        raw_list = data.get("list") or []
        return [
            {
                "coupon_id": str(raw.get("couponId", "")),
                "name": str(raw.get("couponName", raw.get("name", ""))),
                "expire_date": str(raw.get("expireDate", raw.get("endTime", ""))),
                "status": str(raw.get("status", "")),
            }
            for raw in raw_list
        ]
    # 文本形态（无券或说明文案）：视为空券包。
    return []


def parse_coupons_text(text: str) -> list[dict[str, str]]:
    """从非 JSON 的 Markdown 文案中解析券信息。

    实测无券时返回纯文案，这里保守地返回空列表，并把原文留给调用方展示。

    Args:
        text: Tool 返回的原始文本。

    Returns:
        券列表；无法识别时为空列表。
    """
    if "暂无" in text or "无可用" in text:
        return []
    return []
