"""确定性统计：每个结论都附带可回溯的证据链。

设计原则（也是本项目的立身之本）：

1. 不做猜测。所有数字来自订单、营养表、账户三类输入之一。
2. 每个结论都带 `Evidence`，指向具体的 orderId 与字段名。
3. 口径限制显式暴露在 `caveat` 里，而不是藏在脚注。

本模块不依赖任何随机数，同样的输入必然得到同样的输出。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Mapping

from .models import Evidence, Metric, Nutrition, Order, Report, expand_leaf_products
from .normalize import _normalize_product_name

# 时段划分依据订单下单时间，与麦当劳常规用餐时段对齐。
DAY_PARTS: tuple[tuple[str, int, int], ...] = (
    ("早餐", 5, 10),
    ("午餐", 10, 14),
    ("下午茶", 14, 17),
    ("晚餐", 17, 22),
)
NIGHT_PART = "深夜"


def _day_part(hour: int) -> str:
    """把下单小时映射到用餐时段。"""
    for name, start, end in DAY_PARTS:
        if start <= hour < end:
            return name
    return NIGHT_PART


def _fmt_money(amount: float) -> str:
    """金额展示，保留两位小数。"""
    return f"¥{amount:,.2f}"


def _top(counter: Counter[str], limit: int | None = None) -> list[tuple[str, int]]:
    """按出现次数降序取前若干项，同频时按名称排序以保证输出稳定。"""
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return items[:limit] if limit else items


def build_report(
    orders: list[Order],
    nutrition: Mapping[str, Nutrition],
    points: Mapping[str, object],
    coupons: list[dict[str, str]],
    fixture_mode: bool = False,
) -> Report:
    """构建完整年鉴报告。

    Args:
        orders: 归一化后的订单列表。
        nutrition: 商品编码到营养数据的映射。
        points: 积分账户信息。
        coupons: 券列表。
        fixture_mode: 是否运行在演示数据模式下，用于在报告中标注数据来源。

    Returns:
        含全部指标、故事线与数据来源摘要的 `Report`。
    """
    valid_orders = [o for o in orders if not o.is_fully_refunded]
    refunded = [o for o in orders if o.is_fully_refunded]
    partially_refunded = [o for o in valid_orders if o.is_partially_refunded]

    metrics: dict[str, Metric] = {}

    # ---- 空状态：账户暂无订单时，给出明确的引导而不是一串零 ----
    if not valid_orders:
        return _empty_report(nutrition, points, coupons, fixture_mode, len(orders), len(refunded))

    # ---- 数据覆盖度：先声明样本边界，后续口径的诚实前提 ----
    span_start = valid_orders[0].date if valid_orders else "-"
    span_end = valid_orders[-1].date if valid_orders else "-"
    store_names = {o.store_name for o in valid_orders if o.store_name}
    metrics["coverage"] = Metric(
        key="coverage",
        label="数据覆盖范围",
        value=len(valid_orders),
        display=f"{len(valid_orders)} 笔订单，{span_start} ~ {span_end}",
        derivation=(
            "统计口径为 order-list 返回的全部历史订单，剔除「整单退款」。"
            "该接口只返回近期订单，因此本报告的覆盖范围以实际返回的时间跨度为准，"
            "不等同于自然年。"
        ),
        evidence=[
            Evidence(
                tool="order-list",
                locator=o.order_id,
                field="data.list[].createTime",
                value=o.created_at,
                note="订单时间，用于确定覆盖范围",
            )
            for o in valid_orders
        ],
        caveat=(
            f"order-list 返回 {len(valid_orders)} 笔订单，"
            f"覆盖 {len(store_names)} 家门店。若你的历史订单更多，"
            "本报告只覆盖接口实际返回的部分。"
        ),
    )

    # ---- 消费总额 ----
    total_spend = sum(o.total_amount for o in valid_orders)
    metrics["total_spend"] = Metric(
        key="total_spend",
        label="消费总额",
        value=round(total_spend, 2),
        display=_fmt_money(total_spend),
        derivation="对每笔订单的 realTotalAmount 求和，单位元。",
        evidence=[
            Evidence(
                tool="order-list",
                locator=o.order_id,
                field="realTotalAmount",
                value=_fmt_money(o.total_amount),
                note="订单实付金额",
            )
            for o in valid_orders
        ],
        caveat=(
            f"其中 {len(partially_refunded)} 笔为部分退款订单，仍按实付金额计入。"
            if partially_refunded
            else None
        ),
    )

    # ---- 订单数、客单价 ----
    metrics["order_count"] = Metric(
        key="order_count",
        label="订单数",
        value=len(valid_orders),
        display=f"{len(valid_orders)} 单",
        derivation="剔除整单退款后的订单条数。",
        evidence=[
            Evidence(
                tool="order-list",
                locator=o.order_id,
                field="orderId",
                value=o.order_id,
                note=f"{o.date} {o.store_name}",
            )
            for o in valid_orders
        ],
    )

    if valid_orders:
        avg = total_spend / len(valid_orders)
        metrics["avg_ticket"] = Metric(
            key="avg_ticket",
            label="客单价",
            value=round(avg, 2),
            display=_fmt_money(avg),
            derivation="消费总额 ÷ 订单数。",
            evidence=[
                Evidence(
                    tool="order-list",
                    locator="derived",
                    field="realTotalAmount ÷ count(orderId)",
                    value=f"{_fmt_money(total_spend)} ÷ {len(valid_orders)} = {_fmt_money(avg)}",
                    note="派生指标，分子分母均来自上方订单明细",
                )
            ],
        )

    # ---- 展开为最小商品单元 ----
    leaf_stream = expand_leaf_products(valid_orders)

    # ---- 常点单品 TOP ----
    if leaf_stream:
        code_counter: Counter[str] = Counter()
        name_by_code: dict[str, str] = {}
        evidence_by_code: defaultdict[str, list[Evidence]] = defaultdict(list)
        for code, name, qty, order_id in leaf_stream:
            code_counter[code] += qty
            name_by_code.setdefault(code, name)
            evidence_by_code[code].append(
                Evidence(
                    tool="order-list",
                    locator=order_id,
                    field="orderProductList[].comboItemList[].quantity",
                    value=f"{name} × {qty}",
                    note="套餐已按子项展开计数",
                )
            )

        ranked = _top(code_counter, 5)
        top_code, top_qty = ranked[0]
        top_name = name_by_code.get(top_code, top_code)
        metrics["top_item"] = Metric(
            key="top_item",
            label="最常点的单品",
            value={"code": top_code, "name": top_name, "count": top_qty},
            display=f"{top_name} × {top_qty}",
            derivation=(
                "把所有订单（含套餐）展开成最小商品单元后按份数计数。"
                "套餐里的巨无霸、薯条、可乐各自计一次，而不是整份套餐计一次。"
            ),
            evidence=evidence_by_code[top_code],
        )
        metrics["top_items_ranked"] = Metric(
            key="top_items_ranked",
            label="常点单品 TOP5",
            value=[{"code": c, "name": name_by_code.get(c, c), "count": n} for c, n in ranked],
            display="、".join(f"{name_by_code.get(c, c)}×{n}" for c, n in ranked),
            derivation="同上，取前五名。",
            evidence=[
                ev
                for code, _ in ranked
                for ev in evidence_by_code[code][:3]
            ],
        )

    # ---- 用餐时段分布 ----
    part_counter: Counter[str] = Counter()
    part_evidence: defaultdict[str, list[Evidence]] = defaultdict(list)
    for order in valid_orders:
        part = _day_part(order.hour)
        part_counter[part] += 1
        part_evidence[part].append(
            Evidence(
                tool="order-list",
                locator=order.order_id,
                field="createTime 的时字段",
                value=f"{order.created_at} → {part}",
                note="按时段归类",
            )
        )

    if part_counter:
        peak_part, peak_count = _top(part_counter)[0]
        metrics["day_part"] = Metric(
            key="day_part",
            label="最常下单时段",
            value={"part": peak_part, "count": peak_count},
            display=f"{peak_part} · {peak_count} 单",
            derivation=(
                "按订单下单时间分档：早餐 05-10、午餐 10-14、"
                "下午茶 14-17、晚餐 17-22、深夜 22-05。"
            ),
            evidence=part_evidence[peak_part],
        )
        metrics["day_part_distribution"] = Metric(
            key="day_part_distribution",
            label="时段分布",
            value=dict(part_counter),
            display=" / ".join(f"{k} {v}" for k, v in _top(part_counter)),
            derivation="同上，展示全部时段。",
            evidence=[ev for evs in part_evidence.values() for ev in evs],
        )

    night_orders = part_evidence.get(NIGHT_PART, [])
    metrics["night_orders"] = Metric(
        key="night_orders",
        label="深夜订单",
        value=len(night_orders),
        display=f"{len(night_orders)} 单",
        derivation="下单时间落在 22:00 至次日 04:59 的订单数。",
        evidence=night_orders,
        caveat="深夜时段按本机时间解析 createTime，未做时区换算。" if night_orders else None,
    )

    # ---- 常去门店 ----
    store_counter: Counter[str] = Counter()
    store_evidence: defaultdict[str, list[Evidence]] = defaultdict(list)
    for order in valid_orders:
        if not order.store_name:
            continue
        store_counter[order.store_name] += 1
        store_evidence[order.store_name].append(
            Evidence(
                tool="order-list",
                locator=order.order_id,
                field="storeName",
                value=order.store_name,
                note=order.date,
            )
        )

    if store_counter:
        store_name, store_count = _top(store_counter)[0]
        metrics["top_store"] = Metric(
            key="top_store",
            label="常去门店",
            value={"store": store_name, "count": store_count},
            display=f"{store_name} · {store_count} 次",
            derivation="按 storeName 统计订单次数。",
            evidence=store_evidence[store_name],
        )
        metrics["store_count"] = Metric(
            key="store_count",
            label="足迹门店数",
            value=len(store_counter),
            display=f"{len(store_counter)} 家",
            derivation="去重后的 storeName 数量。",
            evidence=[
                Evidence(
                    tool="order-list",
                    locator=ev.locator,
                    field="storeName",
                    value=name,
                    note=f"该门店共 {store_counter[name]} 单",
                )
                for name in sorted(store_counter)
                for ev in store_evidence[name][:1]
            ],
        )

    # ---- 逐月出勤 ----
    month_counter: Counter[str] = Counter(o.date[:7] for o in valid_orders if o.date)
    if month_counter:
        metrics["monthly"] = Metric(
            key="monthly",
            label="逐月订单数",
            value=dict(sorted(month_counter.items())),
            display=" / ".join(f"{m} {month_counter[m]}" for m in sorted(month_counter)),
            derivation="按 createTime 的年月分组计数。",
            evidence=[
                Evidence(
                    tool="order-list",
                    locator=o.order_id,
                    field="createTime",
                    value=f"{o.date} → {o.date[:7]}",
                    note="按月份归组",
                )
                for o in valid_orders
                if o.date
            ],
        )

    # ---- 营养折算 ----
    total_energy = 0.0
    total_protein = 0.0
    total_sodium = 0.0
    nutrition_evidence: list[Evidence] = []
    unmatched_codes: Counter[str] = Counter()
    matched_items = 0
    for code, name, qty, order_id in leaf_stream:
        # 营养表以商品名称为键（该 Tool 不返回商品编码），因此按名称匹配。
        record = nutrition.get(_normalize_product_name(name)) or nutrition.get(code)
        if record is None:
            unmatched_codes[name or code] += qty
            continue
        matched_items += qty
        total_energy += record.energy * qty
        total_protein += record.protein * qty
        total_sodium += record.sodium * qty
        nutrition_evidence.append(
            Evidence(
                tool="list-nutrition-foods",
                locator=order_id,
                field=f"{record.product_name}.energyKcal",
                value=f"{name} × {qty} × {record.energy:g} kcal",
                note="营养值乘以份数",
            )
        )

    if nutrition_evidence:
        metrics["energy"] = Metric(
            key="energy",
            label="累计热量",
            value=round(total_energy, 1),
            display=f"{total_energy:,.0f} kcal",
            derivation="每个商品单元的 energy 乘以其份数后求和。",
            evidence=nutrition_evidence,
            caveat=(
                f"有 {sum(unmatched_codes.values())} 份商品未匹配到营养数据"
                f"（{'、'.join(sorted(unmatched_codes)[:5])}"
                f"{' 等' if len(unmatched_codes) > 5 else ''}），"
                "未计入热量合计（不按 0 估）。套餐内的组合项若名称与营养表不一致，也会落入此类。"
                if unmatched_codes
                else None
            ),
        )
        metrics["protein"] = Metric(
            key="protein",
            label="累计蛋白质",
            value=round(total_protein, 1),
            display=f"{total_protein:,.0f} g",
            derivation="每个商品单元的 protein 乘以其份数后求和。",
            evidence=nutrition_evidence[:20],
            caveat="证据列表展示前 20 条，合计口径覆盖全部已匹配商品。" if len(nutrition_evidence) > 20 else None,
        )
        metrics["sodium"] = Metric(
            key="sodium",
            label="累计钠",
            value=round(total_sodium, 1),
            display=f"{total_sodium:,.0f} mg",
            derivation="每个商品单元的 sodium 乘以其份数后求和。",
            evidence=nutrition_evidence[:20],
            caveat="钠摄入仅作数据展示，不构成健康建议。" ,
        )

    # ---- 券与积分 ----
    expired = [c for c in coupons if "过期" in c.get("status", "")]
    active = [c for c in coupons if c not in expired]
    metrics["coupons"] = Metric(
        key="coupons",
        label="券包状态",
        value={"active": len(active), "expired": len(expired)},
        display=f"可用 {len(active)} 张 / 已过期 {len(expired)} 张",
        derivation="按 query-my-coupons 返回的 status 字段分类。",
        evidence=[
            Evidence(
                tool="query-my-coupons",
                locator=c.get("coupon_id", ""),
                field="status",
                value=f"{c.get('name', '')} → {c.get('status', '')}",
                note=f"有效期至 {c.get('expire_date', '')}",
            )
            for c in coupons
        ],
    )

    metrics["points"] = Metric(
        key="points",
        label="积分账户",
        value=dict(points),
        display=(f"可用 {points.get('available', 0):,} 分，累计获得 {points.get('total', 0):,} 分"
                 + (f"，已使用 {points.get('used', 0):,} 分" if points.get("used") else "")),
        derivation="直接取自 query-my-account 的 availablePoints / expiringPoints。",
        evidence=[
            Evidence(
                tool="query-my-account",
                locator="account",
                field="availablePoints",
                value=str(points.get("available", 0)),
                note="可用积分",
            ),
            Evidence(
                tool="query-my-account",
                locator="account",
                field="expiringPoints",
                value=f"{points.get('expiring', 0)}",
                note="本月即将过期积分",
            ),
        ],
    )

    # ---- 退款与异常：显式说明被排除的数据 ----
    if refunded:
        metrics["refunds"] = Metric(
            key="refunds",
            label="已剔除的整单退款",
            value=len(refunded),
            display=f"{len(refunded)} 笔",
            derivation="orderStatus 含「整单退款」的订单不计入任何消费统计。",
            evidence=[
                Evidence(
                    tool="order-list",
                    locator=o.order_id,
                    field="orderStatus",
                    value=o.status,
                    note=f"{o.date} 金额 {_fmt_money(o.total_amount)}，已剔除",
                )
                for o in refunded
            ],
        )

    story = _build_story(metrics, valid_orders)

    source_summary = {
        "order-list": f"{len(orders)} 条订单（含 {len(refunded)} 条整单退款）",
        "list-nutrition-foods": f"{len(nutrition)} 条营养记录，命中 {matched_items} 份商品",
        "query-my-account": "积分账户 1 条",
        "query-my-coupons": f"{len(coupons)} 张券",
    }

    return Report(
        metrics=metrics,
        story=story,
        source_summary=source_summary,
        fixture_mode=fixture_mode,
    )


def _empty_report(
    nutrition: Mapping[str, Nutrition],
    points: Mapping[str, object],
    coupons: list[dict[str, str]],
    fixture_mode: bool,
    raw_order_count: int,
    refunded_count: int,
) -> Report:
    """账户暂无有效订单时的报告。

    麦当劳 `order-list` 只返回本账号的历史订单。新账号或从未下过单的账号
    会得到空列表——这是合法状态。此处不渲染一串无意义的零，而是明确说明
    "没有可统计的订单"，并保留已能取到的账户与营养数据。
    """
    metrics: dict[str, Metric] = {}

    metrics["coverage"] = Metric(
        key="coverage",
        label="数据覆盖范围",
        value=0,
        display="暂无可统计的订单",
        derivation=(
            "统计口径为 order-list 返回的历史订单，剔除「整单退款」。"
            "当前账号没有可用订单。"
        ),
        evidence=[],
        caveat=(
            f"接口返回 {raw_order_count} 条记录，其中 {refunded_count} 条为整单退款，"
            "因此没有可计入统计的订单。"
            if raw_order_count
            else (
                "order-list 未返回任何订单。可能原因：该账号尚未下过单；"
                "或下单时使用的手机号与申请 MCP Token 的账号不是同一个。"
            )
        ),
    )

    metrics["points"] = Metric(
        key="points",
        label="积分账户",
        value=dict(points),
        display=f"可用 {points.get('available', 0):,} 分，累计获得 {points.get('total', 0):,} 分",
        derivation="直接取自 query-my-account 的 availablePoint / accumulativePoint。",
        evidence=[
            Evidence(
                tool="query-my-account",
                locator="account",
                field="availablePoint",
                value=str(points.get("available", 0)),
                note="可用积分",
            )
        ],
    )

    metrics["coupons"] = Metric(
        key="coupons",
        label="券包状态",
        value={"active": len([c for c in coupons if "过期" not in c.get("status", "")]),
               "expired": len([c for c in coupons if "过期" in c.get("status", "")])},
        display=f"可用 {len(coupons)} 张券" if coupons else "暂无可用券",
        derivation="来自 query-my-coupons；无券时该 Tool 返回文案而非 JSON。",
        evidence=[
            Evidence(
                tool="query-my-coupons",
                locator=c.get("coupon_id", ""),
                field="status",
                value=f"{c.get('name', '')} → {c.get('status', '')}",
                note=f"有效期至 {c.get('expire_date', '')}",
            )
            for c in coupons
        ],
        caveat=None if coupons else "当前账号没有可用优惠券。",
    )

    metrics["nutrition_table"] = Metric(
        key="nutrition_table",
        label="营养数据库",
        value=len(nutrition),
        display=f"已加载 {len(nutrition)} 条餐品营养数据",
        derivation="来自 list-nutrition-foods，用于在有订单时把商品换算成热量与营养素。",
        evidence=[
            Evidence(
                tool="list-nutrition-foods",
                locator=name,
                field="energyKcal",
                value=f"{rec.energy:g} kcal",
                note="样例数据，完整表见 fixtures",
            )
            for name, rec in list(nutrition.items())[:10]
        ],
    )

    story = [
        ("当前状态", "这个账号还没有可统计的麦当劳订单", "coverage"),
        ("营养数据库", f"已加载 {len(nutrition)} 条餐品营养数据，随时可用", "nutrition_table"),
        ("积分账户", metrics["points"].display, "points"),
    ]

    return Report(
        metrics=metrics,
        story=story,
        source_summary={
            "order-list": "0 条订单",
            "list-nutrition-foods": f"{len(nutrition)} 条营养记录",
            "query-my-account": "积分账户 1 条",
            "query-my-coupons": f"{len(coupons)} 张券",
        },
        fixture_mode=fixture_mode,
    )


def _build_story(metrics: Mapping[str, Metric], orders: list[Order]) -> list[tuple[str, str, str]]:
    """把指标组织成报告叙事。

    Returns:
        (小标题, 主文案, 关联指标 key) 三元组列表。
    """
    story: list[tuple[str, str, str]] = []

    coverage = metrics.get("coverage")
    if coverage:
        story.append(("这段时间", coverage.display, "coverage"))

    spend = metrics.get("total_spend")
    count = metrics.get("order_count")
    if spend and count:
        story.append(
            (
                "一共花了",
                f"{spend.display}，{count.display}",
                "total_spend",
            )
        )

    top_item = metrics.get("top_item")
    if top_item:
        story.append(("点得最多的是", top_item.display, "top_item"))

    day_part = metrics.get("day_part")
    if day_part:
        story.append(("你的麦麦生物钟", day_part.display, "day_part"))

    night = metrics.get("night_orders")
    if night and night.value:
        story.append(("深夜的那几单", night.display, "night_orders"))

    store = metrics.get("top_store")
    if store:
        story.append(("最常去", store.display, "top_store"))

    energy = metrics.get("energy")
    if energy:
        km = energy.value / 60.0  # 以 60 kcal/km 粗略折算，仅作趣味换算
        story.append(
            (
                "折算成热量",
                f"{energy.display}，大约相当于慢跑 {km:,.0f} 公里",
                "energy",
            )
        )

    points = metrics.get("points")
    if points:
        story.append(("积分账户", points.display, "points"))

    return story
