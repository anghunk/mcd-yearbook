"""把年鉴报告渲染成零依赖的单文件 HTML。

输出满足三个约束：
1. 单文件——内联全部 CSS 与 JS，不请求任何外部资源，离线可看、可直接截图。
2. 可溯源——每个指标旁有"查出处"开关，展开后逐条列出源订单与字段。
3. 移动端优先——故事卡按 9:16 设计，方便直接截屏分享。
"""

from __future__ import annotations

import html
import json
from typing import Any

from .models import Metric, Report

_CSS = """
:root {
  --bg: #0d0d11;
  --panel: #17171d;
  --panel-2: #1f1f27;
  --line: #2c2c36;
  --text: #f2f2f5;
  --muted: #9a9aa8;
  --gold: #ffc72c;
  --red: #da291c;
  --green: #35c26a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 720px; margin: 0 auto; padding: 24px 16px 72px; }
.banner {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  padding: 14px 16px;
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 20px;
}
.banner strong { color: var(--gold); }
header { padding: 12px 0 28px; }
.eyebrow {
  color: var(--red);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}
h1 { font-size: 30px; margin: 6px 0 8px; line-height: 1.25; }
.sub { color: var(--muted); font-size: 14px; margin: 0; }

.story { display: grid; gap: 12px; margin-bottom: 32px; }
.card {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  padding: 18px;
}
.card .k { color: var(--muted); font-size: 13px; }
.card .v { font-size: 22px; font-weight: 700; margin-top: 4px; word-break: break-word; }
.card.accent { border-color: #41341a; background: linear-gradient(180deg, #1d1a12 0%, var(--panel) 100%); }
.card.accent .v { color: var(--gold); }

section { margin-top: 36px; }
h2 {
  font-size: 15px;
  color: var(--muted);
  font-weight: 600;
  margin: 0 0 14px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
}

.metric {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  margin-bottom: 10px;
  overflow: hidden;
}
.metric-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
}
.metric-body { flex: 1; min-width: 0; }
.metric-label { font-size: 13px; color: var(--muted); }
.metric-value { font-size: 17px; font-weight: 600; margin-top: 2px; word-break: break-word; }
.metric-derivation { font-size: 12px; color: var(--muted); margin-top: 8px; }
.caveat {
  font-size: 12px;
  color: #f0b429;
  border-left: 2px solid #6b5320;
  padding-left: 8px;
  margin-top: 8px;
}
.toggle {
  flex-shrink: 0;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--gold);
  border-radius: 6px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  white-space: nowrap;
}
.toggle:hover { border-color: var(--gold); }
.toggle[aria-expanded="true"] { background: #2a2413; }
.evidence { display: none; border-top: 1px solid var(--line); background: #12121a; }
.evidence.open { display: block; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #23232c; vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
td.tool { color: var(--green); white-space: nowrap; }
td.loc { color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
.ev-more { padding: 8px 10px; color: var(--muted); font-size: 12px; }
.empty { color: var(--muted); font-size: 13px; padding: 12px 16px; }

/* 窄屏下把证据表格改为卡片式，避免多列横向溢出 */
@media (max-width: 560px) {
  .evidence { padding: 4px 0; }
  .evidence table, .evidence thead, .evidence tbody,
  .evidence tr, .evidence td { display: block; width: 100%; }
  .evidence thead { display: none; }
  .evidence tr {
    border-bottom: 1px solid #23232c;
    padding: 10px 14px;
  }
  .evidence td {
    border: 0;
    padding: 2px 0;
    display: flex;
    gap: 10px;
    align-items: baseline;
  }
  .evidence td::before {
    content: attr(data-label);
    flex: 0 0 68px;
    color: var(--muted);
    font-size: 11px;
  }
  .evidence td.loc { overflow-wrap: anywhere; }
}

details.src { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
details.src summary { padding: 12px 16px; cursor: pointer; font-size: 14px; }
details.src .src-body { padding: 0 16px 16px; font-size: 13px; color: var(--muted); }
details.src code { color: var(--text); }

footer {
  margin-top: 40px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
}
"""

_JS = """
document.querySelectorAll('.toggle').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var target = document.getElementById(btn.getAttribute('aria-controls'));
    if (!target) return;
    var open = target.classList.toggle('open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    var n = btn.getAttribute('data-count');
    btn.textContent = open ? '收起出处' : ('查出处 · ' + n);
  });
});
"""


def _esc(value: Any) -> str:
    """HTML 转义，防止数据中的尖括号破坏结构。"""
    return html.escape(str(value), quote=True)


def _evidence_block(metric: Metric, index: int) -> str:
    """渲染一个指标的证据明细表。"""
    if not metric.evidence:
        return f'<div class="evidence" id="ev-{index}"><div class="empty">该指标为派生结果，无独立证据条目。</div></div>'

    rows = []
    limit = 60
    for ev in metric.evidence[:limit]:
        rows.append(
            "<tr>"
            f'<td class="tool" data-label="Tool">{_esc(ev.tool)}</td>'
            f'<td class="loc" data-label="定位符">{_esc(ev.locator)}</td>'
            f'<td data-label="字段">{_esc(ev.field)}</td>'
            f'<td data-label="取值">{_esc(ev.value)}</td>'
            f'<td data-label="说明">{_esc(ev.note)}</td>'
            "</tr>"
        )
    more = ""
    if len(metric.evidence) > limit:
        more = (
            f'<div class="ev-more">另有 {len(metric.evidence) - limit} 条证据未在此展开，'
            "完整数据见 report.json。</div>"
        )

    return (
        f'<div class="evidence" id="ev-{index}">'
        "<table><thead><tr>"
        "<th>MCP Tool</th><th>定位符</th><th>字段</th><th>取值</th><th>说明</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + more
        + "</div>"
    )


def _metric_html(metric: Metric, index: int) -> str:
    """渲染单个指标卡片。"""
    count = len(metric.evidence)
    button = ""
    if count:
        button = (
            f'<button class="toggle" aria-expanded="false" aria-controls="ev-{index}" '
            f'data-count="{count}">查出处 · {count}</button>'
        )
    else:
        button = (
            f'<button class="toggle" aria-expanded="false" aria-controls="ev-{index}" '
            f'data-count="0">口径</button>'
        )

    caveat = f'<div class="caveat">口径提示：{_esc(metric.caveat)}</div>' if metric.caveat else ""

    return (
        '<div class="metric">'
        '<div class="metric-head">'
        '<div class="metric-body">'
        f'<div class="metric-label">{_esc(metric.label)}</div>'
        f'<div class="metric-value">{_esc(metric.display)}</div>'
        f'<div class="metric-derivation">口径：{_esc(metric.derivation)}</div>'
        f"{caveat}"
        "</div>"
        f"{button}"
        "</div>"
        f"{_evidence_block(metric, index)}"
        "</div>"
    )


def render_html(report: Report, title: str = "麦麦年鉴") -> str:
    """渲染完整报告 HTML。

    Args:
        report: 构建好的年鉴报告。
        title: 页面标题。

    Returns:
        自包含的 HTML 字符串。
    """
    banner = ""
    if report.fixture_mode:
        banner = (
            '<div class="banner">'
            "<strong>演示数据模式</strong>：当前展示的是合成演示数据，用于在无 MCP Token 的情况下"
            "体验完整链路。运行 <code>scripts/record_fixtures.py</code> 可用你账户的真实数据覆盖。"
            "</div>"
        )

    story_cards = []
    for i, (label, text, key) in enumerate(report.story):
        accent = " accent" if i == 0 else ""
        story_cards.append(
            f'<div class="card{accent}">'
            f'<div class="k">{_esc(label)}</div>'
            f'<div class="v">{_esc(text)}</div>'
            "</div>"
        )

    metrics_html = "".join(
        _metric_html(metric, i) for i, metric in enumerate(report.metrics.values())
    )

    src_rows = "".join(
        f"<li><code>{_esc(tool)}</code> — {_esc(desc)}</li>"
        for tool, desc in report.source_summary.items()
    )

    total_evidence = report.evidence_total()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
{banner}
<header>
  <div class="eyebrow">McYearbook · 可溯源年度回顾</div>
  <h1>{_esc(title)}</h1>
  <p class="sub">报告里的每一个数字，都能点开看到它来自哪几笔订单。</p>
</header>

<div class="story">
{''.join(story_cards)}
</div>

<section>
  <h2>全部指标（共 {len(report.metrics)} 项，{total_evidence} 条证据）</h2>
  {metrics_html}
</section>

<section>
  <h2>数据来源</h2>
  <details class="src">
    <summary>本次报告实际调用的 MCP Tool 与数据量</summary>
    <div class="src-body">
      <ul>{src_rows}</ul>
      <p>全部结论均由本地确定性脚本计算，未经大模型改写数字。
      报告不含真实 Token 与密钥，原始订单数据不离开本机。</p>
    </div>
  </details>
</section>

<footer>
  <p>麦麦年鉴 McYearbook · 麦当劳程序员创意开发大赛参赛作品，
  由参赛者独立开发，非麦当劳官方产品。<br>
  餐品信息、价格与供应状态以麦当劳官方渠道的实时结果为准。</p>
</footer>
</div>
<script>{_JS}</script>
</body>
</html>
"""


def render_json(report: Report) -> str:
    """把报告导出为 JSON，便于二次处理或人工核查。"""
    payload = {
        "fixture_mode": report.fixture_mode,
        "source_summary": report.source_summary,
        "evidence_total": report.evidence_total(),
        "story": [
            {"label": label, "text": text, "metric": key}
            for label, text, key in report.story
        ],
        "metrics": {
            key: {
                "label": m.label,
                "value": m.value,
                "display": m.display,
                "derivation": m.derivation,
                "caveat": m.caveat,
                "evidence": [
                    {
                        "tool": e.tool,
                        "locator": e.locator,
                        "field": e.field,
                        "value": e.value,
                        "note": e.note,
                    }
                    for e in m.evidence
                ],
            }
            for key, m in report.metrics.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
