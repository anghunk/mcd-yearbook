"""领域模型与证据链结构。

本模块定义年鉴的核心数据结构。设计上刻意让每一个统计结论都携带
`Evidence` 列表——这是本项目与同类"年度报告"项目的核心差异：
报告里的每个数字都能点开看到它来自哪几条订单、哪个字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class OrderItem:
    """订单中的单个商品行。

    Attributes:
        product_code: 商品编码。
        product_name: 商品名称。
        quantity: 购买数量。
        combo_items: 套餐内的子项；非套餐商品为空列表。
    """

    product_code: str
    product_name: str
    quantity: int
    combo_items: tuple["ComboItem", ...] = ()

    def leaf_products(self) -> list[tuple[str, str, int]]:
        """展开出可计入统计的最小商品单元。

        套餐按其子项展开（子项数量按套餐份数放大），非套餐商品返回自身。
        这样做是为了让"最爱单品"这类结论贴近用户直觉——点了一份巨无霸套餐，
        统计的是里面的巨无霸、薯条和可乐，而不是"巨无霸套餐"这个壳。

        Returns:
            (商品编码, 商品名称, 数量) 三元组列表。
        """
        if not self.combo_items:
            return [(self.product_code, self.product_name, self.quantity)]
        return [
            (child.product_code, child.name, child.quantity * self.quantity)
            for child in self.combo_items
        ]


@dataclass(frozen=True)
class ComboItem:
    """套餐内的子项。"""

    product_code: str
    name: str
    quantity: int


@dataclass(frozen=True)
class Order:
    """一笔历史订单。"""

    order_id: str
    created_at: str
    store_code: str
    store_name: str
    status: str
    items: tuple[OrderItem, ...]
    total_amount: float

    @property
    def is_fully_refunded(self) -> bool:
        """整单退款：不计入消费统计。"""
        return "整单退款" in self.status

    @property
    def is_partially_refunded(self) -> bool:
        """部分退款：仍计入消费，但报告中单独标注。"""
        return "部分退款" in self.status

    @property
    def date(self) -> str:
        """订单日期，格式 YYYY-MM-DD。"""
        return self.created_at[:10]

    @property
    def hour(self) -> int:
        """下单小时（0-23）；时间格式异常时回退为 0。"""
        try:
            return int(self.created_at[11:13])
        except (ValueError, IndexError):
            return 0


@dataclass(frozen=True)
class Nutrition:
    """餐品营养数据。"""

    product_code: str
    product_name: str
    energy: float
    protein: float
    fat: float
    carbohydrate: float
    sodium: float


@dataclass(frozen=True)
class Evidence:
    """单条证据：一个结论到一处原始数据的映射。

    Attributes:
        tool: 证据来自哪个 MCP Tool。
        locator: 定位符，通常是 orderId。
        field: 原始响应中的字段路径。
        value: 该字段的取值。
        note: 补充说明，用于解释这条证据为何被计入。
    """

    tool: str
    locator: str
    field: str
    value: str
    note: str = ""


@dataclass
class Metric:
    """可溯源的统计结论。

    Attributes:
        key: 机器可读的指标标识。
        label: 展示名称。
        value: 数值结果。
        display: 展示用文本。
        derivation: 计算口径的自然语言描述，供用户核对。
        evidence: 支撑该结论的证据列表。
        caveat: 口径限制说明，例如数据覆盖率不足。
    """

    key: str
    label: str
    value: Any
    display: str
    derivation: str
    evidence: list[Evidence] = field(default_factory=list)
    caveat: Optional[str] = None


@dataclass
class Report:
    """完整年鉴报告。"""

    metrics: dict[str, Metric]
    story: list[tuple[str, str, str]]  # (标题, 主文案, 指标 key 或空)
    source_summary: dict[str, str]
    fixture_mode: bool = False

    def metric(self, key: str) -> Optional[Metric]:
        """按 key 取指标，不存在时返回 None。"""
        return self.metrics.get(key)

    def evidence_total(self) -> int:
        """报告中全部证据条数。"""
        return sum(len(m.evidence) for m in self.metrics.values())


def expand_leaf_products(orders: Iterable[Order]) -> list[tuple[str, str, int, str]]:
    """把所有订单展开为最小商品单元流。

    Args:
        orders: 订单序列。

    Returns:
        (商品编码, 商品名称, 数量, 来源订单号) 四元组列表，已剔除整单退款。
    """
    stream: list[tuple[str, str, int, str]] = []
    for order in orders:
        if order.is_fully_refunded:
            continue
        for item in order.items:
            for code, name, qty in item.leaf_products():
                stream.append((code, name, qty, order.order_id))
    return stream
