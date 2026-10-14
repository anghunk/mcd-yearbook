"""把麦当劳官方 MCP 的原始响应归一化为领域模型。

本模块是"数据入口"的唯一通道：无论是录制的 fixture 还是实时 MCP 响应，
都先经过这里转成 `models` 中的数据结构，后续统计与渲染不再接触原始 JSON。
这样做的收益是——当官方字段调整时，只需改这一个文件。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .models import ComboItem, Nutrition, Order, OrderItem


class SchemaError(ValueError):
    """官方响应结构与预期不符时抛出。

    宁可显式失败，也不要用默认值静默产出一个错误的报告。
    """


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
        payload: `order-list` 的完整 JSON 响应。

    Returns:
        订单列表，按时间升序排列。

    Raises:
        SchemaError: 响应缺少订单数组等关键结构。
    """
    data = _require(payload, "data", "order-list 响应")
    raw_list: Sequence[Mapping[str, Any]] = _require(data, "list", "order-list.data")

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
        payload: 完整 JSON 响应。

    Returns:
        以商品编码为键的营养表。营养数据用于把订单换算成热量与营养素，
        缺少营养记录的商品会在分析阶段被显式计数，不会静默按 0 计算。
    """
    data = _require(payload, "data", "list-nutrition-foods 响应")
    raw_list: Sequence[Mapping[str, Any]] = _require(data, "list", "list-nutrition-foods.data")

    table: dict[str, Nutrition] = {}
    for raw in raw_list:
        code = str(raw.get("productCode", ""))
        if not code:
            continue
        table[code] = Nutrition(
            product_code=code,
            product_name=str(raw.get("productName", "")),
            energy=_as_float(raw.get("energy")),
            protein=_as_float(raw.get("protein")),
            fat=_as_float(raw.get("fat")),
            carbohydrate=_as_float(raw.get("carbohydrate")),
            sodium=_as_float(raw.get("sodium")),
        )
    return table


def parse_points(payload: Mapping[str, Any]) -> dict[str, Any]:
    """解析 `query-my-account` 响应中的积分字段。"""
    data = _require(payload, "data", "query-my-account 响应")
    return {
        "available": _as_int(data.get("availablePoints")),
        "total": _as_int(data.get("totalPoints")),
        "expiring": _as_int(data.get("expiringPoints")),
        "expiring_date": str(data.get("expiringDate", "")),
    }


def parse_coupons(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """解析 `query-my-coupons` 响应为券列表。"""
    data = _require(payload, "data", "query-my-coupons 响应")
    raw_list: Sequence[Mapping[str, Any]] = data.get("list") or []
    return [
        {
            "coupon_id": str(raw.get("couponId", "")),
            "name": str(raw.get("couponName", "")),
            "expire_date": str(raw.get("expireDate", "")),
            "status": str(raw.get("status", "")),
        }
        for raw in raw_list
    ]
