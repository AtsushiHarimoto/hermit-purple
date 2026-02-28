"""
Hermit Purple HTML Report Generator

Generates branded HTML reports following Moyin Factory design system.
"""

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


_VERDICT_COLORS: Dict[str, str] = {
    "ADOPT": "#a855f7",   # ui-primary
    "TRIAL": "#f4abba",   # ui-sakura
    "ASSESS": "#a78bfa",  # ui-muted
}
_VERDICT_DEFAULT_COLOR = "#6d5091"  # IGNORE / unknown

_VERDICT_BADGE_BGS: Dict[str, str] = {
    "ADOPT": "rgba(168,85,247,0.2)",
    "TRIAL": "rgba(244,171,186,0.2)",
    "ASSESS": "rgba(167,139,250,0.2)",
}
_VERDICT_DEFAULT_BADGE_BG = "rgba(109,80,145,0.15)"


def _verdict_color(verdict: str) -> str:
    """Map verdict to brand color"""
    return _VERDICT_COLORS.get((verdict or "").upper(), _VERDICT_DEFAULT_COLOR)


def _verdict_badge_bg(verdict: str) -> str:
    """Map verdict to badge background color"""
    return _VERDICT_BADGE_BGS.get((verdict or "").upper(), _VERDICT_DEFAULT_BADGE_BG)


def _score_bar_color(score: int) -> str:
    """Map score to progress bar color"""
    if score >= 80:
        return "#a855f7"
    if score >= 60:
        return "#f4abba"
    if score >= 40:
        return "#a78bfa"
    return "#6d5091"


def _render_tags(tags: list) -> str:
    if not tags:
        return ""
    parts = []
    for t in tags[:5]:
        parts.append(
            f'<span class="tag">{html.escape(str(t))}</span>'
        )
    return " ".join(parts)


def _render_item_card(item: Dict[str, Any]) -> str:
    title = html.escape(item.get("title", "未命名"))
    url = html.escape(item.get("url", ""), quote=True)
    desc = html.escape(item.get("description", ""))
    analysis = item.get("analysis", {})

    verdict_raw = analysis.get("verdict", "N/A")
    score = analysis.get("score", 0)

    # Localize verdict for display
    verdict_labels = {
        "ADOPT": "採納", "TRIAL": "試用",
        "ASSESS": "評估", "IGNORE": "忽略",
    }
    verdict = verdict_labels.get(verdict_raw.upper(), verdict_raw) if verdict_raw else "未知"
    summary = html.escape(analysis.get("summary", desc or "無摘要"))
    tags = analysis.get("tags", item.get("tags", []))
    value_prop = analysis.get("value_prop", [])
    risks = analysis.get("risks", [])

    color = _verdict_color(verdict_raw)
    badge_bg = _verdict_badge_bg(verdict_raw)
    bar_color = _score_bar_color(score)

    # Title link
    raw_url = item.get("url", "")
    if raw_url and raw_url.startswith("http"):
        title_html = f'<a href="{url}" target="_blank" rel="noopener">{title}</a>'
    else:
        title_html = title

    # Value props
    vp_html = ""
    if value_prop and isinstance(value_prop, list):
        vp_items = "".join(f"<li>{html.escape(str(v))}</li>" for v in value_prop[:3])
        vp_html = f'<div class="props"><span class="props-label">核心價值</span><ul>{vp_items}</ul></div>'

    # Risks
    risk_html = ""
    if risks and isinstance(risks, list):
        r_items = "".join(f"<li>{html.escape(str(r))}</li>" for r in risks[:3])
        risk_html = f'<div class="risks"><span class="risks-label">風險提示</span><ul>{r_items}</ul></div>'

    return f"""
    <div class="card">
      <div class="card-header">
        <h3>{title_html}</h3>
        <span class="verdict-badge" style="color:{color};background:{badge_bg};border-color:{color}">
          {verdict}
        </span>
      </div>
      <div class="score-row">
        <div class="score-bar-bg">
          <div class="score-bar" style="width:{score}%;background:{bar_color}"></div>
        </div>
        <span class="score-num">{score}</span>
      </div>
      <p class="summary">{summary}</p>
      <div class="tags">{_render_tags(tags)}</div>
      {vp_html}
      {risk_html}
    </div>
    """


def generate_html_report(
    plugin_name: str,
    items: List[Dict[str, Any]],
    reports_dir: Optional[Path] = None,
) -> Path:
    """
    Generate a branded HTML report and save to reports/ directory.

    @param plugin_name: Name of the plugin (ai_trends, ai_business, etc.)
    @param items: List of processed items with 'analysis' dict
    @param reports_dir: Output directory (default: tools/hermit-purple/reports/)
    @returns: Path to the generated HTML file
    """
    if reports_dir is None:
        reports_dir = Path(__file__).parents[2] / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")
    filename = f"pipeline_{plugin_name}_{timestamp}.html"
    filepath = reports_dir / filename

    # Stats
    total = len(items)
    adopt_count = sum(1 for i in items if i.get("analysis", {}).get("verdict", "").upper() == "ADOPT")
    trial_count = sum(1 for i in items if i.get("analysis", {}).get("verdict", "").upper() == "TRIAL")
    assess_count = sum(1 for i in items if i.get("analysis", {}).get("verdict", "").upper() == "ASSESS")
    ignore_count = total - adopt_count - trial_count - assess_count

    # Plugin display name
    display_names = {
        "ai_trends": "AI 趨勢情報分析",
        "ai_business": "商業落地方案掃描",
        "social_radar": "社群輿情偵察",
        "trend_radar": "熱點雷達掃描",
    }
    report_title = display_names.get(plugin_name, plugin_name)
    date_display = now.strftime("%Y-%m-%d %H:%M")

    # Render cards
    cards_html = "\n".join(_render_item_card(item) for item in items)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>隱者之紫 - {report_title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}

  body {{
    font-family: "Noto Sans TC", "Inter", sans-serif;
    background: linear-gradient(135deg, #1a0b2e 0%, #0f051a 50%, #050b1a 100%);
    color: #f3f0ff;
    min-height: 100vh;
    padding: 0;
  }}

  /* Header */
  .header {{
    background: rgba(20,10,35,0.85);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid #3b2166;
    padding: 32px 0;
    position: sticky; top: 0; z-index: 10;
  }}
  .header-inner {{
    max-width: 1100px; margin: 0 auto; padding: 0 24px;
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 16px;
  }}
  .brand {{
    display: flex; align-items: center; gap: 12px;
  }}
  .brand-icon {{
    width: 36px; height: 36px;
    background: linear-gradient(135deg, #8b5cf6, #d946ef);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; font-weight: 700; color: #fff;
  }}
  .brand-text {{
    font-size: 14px; color: #a78bfa; font-weight: 500;
    letter-spacing: 0.5px;
  }}
  .brand-text strong {{
    color: #f3f0ff; font-size: 16px; display: block;
  }}
  .header-meta {{
    font-size: 13px; color: #6d5091; text-align: right;
  }}

  /* Stats bar */
  .stats {{
    max-width: 1100px; margin: 24px auto; padding: 0 24px;
    display: flex; gap: 12px; flex-wrap: wrap;
  }}
  .stat {{
    background: rgba(26,11,46,0.6);
    border: 1px solid #3b2166;
    border-radius: 12px; padding: 14px 20px;
    flex: 1; min-width: 120px; text-align: center;
  }}
  .stat-num {{
    font-size: 28px; font-weight: 700;
    font-family: "JetBrains Mono", monospace;
  }}
  .stat-label {{
    font-size: 12px; color: #a78bfa; margin-top: 4px;
    text-transform: uppercase; letter-spacing: 1px;
  }}

  /* Main content */
  .main {{
    max-width: 1100px; margin: 0 auto; padding: 24px;
  }}
  .section-title {{
    font-size: 20px; font-weight: 600;
    color: #a78bfa; margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(59,33,102,0.5);
  }}

  /* Cards */
  .card {{
    background: rgba(20,10,35,0.7);
    border: 1px solid #3b2166;
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 16px;
    transition: border-color 0.2s, box-shadow 0.2s;
  }}
  .card:hover {{
    border-color: rgba(168,85,247,0.5);
    box-shadow: 0 0 20px rgba(168,85,247,0.1);
  }}
  .card-header {{
    display: flex; justify-content: space-between; align-items: flex-start;
    gap: 12px; margin-bottom: 12px;
  }}
  .card-header h3 {{
    font-size: 18px; font-weight: 600; line-height: 1.4;
  }}
  .card-header h3 a {{
    color: #f3f0ff; text-decoration: none;
    border-bottom: 1px solid rgba(244,171,186,0.3);
    transition: border-color 0.2s;
  }}
  .card-header h3 a:hover {{
    border-color: #f4abba; color: #f4abba;
  }}

  .verdict-badge {{
    font-size: 12px; font-weight: 700;
    padding: 4px 12px;
    border-radius: 999px;
    border: 1px solid;
    white-space: nowrap;
    font-family: "JetBrains Mono", monospace;
    letter-spacing: 0.5px;
  }}

  /* Score bar */
  .score-row {{
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 12px;
  }}
  .score-bar-bg {{
    flex: 1; height: 6px;
    background: rgba(59,33,102,0.5);
    border-radius: 3px; overflow: hidden;
  }}
  .score-bar {{
    height: 100%; border-radius: 3px;
    transition: width 0.6s ease-out;
  }}
  .score-num {{
    font-size: 14px; font-weight: 700;
    font-family: "JetBrains Mono", monospace;
    color: #a78bfa; min-width: 28px; text-align: right;
  }}

  .summary {{
    font-size: 15px; line-height: 1.7;
    color: #c4b5fd; margin-bottom: 12px;
  }}

  /* Tags */
  .tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }}
  .tag {{
    font-size: 11px;
    background: rgba(139,92,246,0.15);
    color: #a78bfa;
    padding: 3px 10px;
    border-radius: 999px;
    border: 1px solid rgba(139,92,246,0.25);
  }}

  /* Props / Risks */
  .props, .risks {{
    margin-top: 8px; padding: 10px 14px;
    border-radius: 10px; font-size: 13px;
  }}
  .props {{
    background: rgba(168,85,247,0.08);
    border-left: 3px solid #a855f7;
  }}
  .risks {{
    background: rgba(244,63,94,0.08);
    border-left: 3px solid #f43f5e;
    margin-top: 6px;
  }}
  .props-label, .risks-label {{
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 4px; display: block;
  }}
  .props-label {{ color: #a855f7; }}
  .risks-label {{ color: #f43f5e; }}
  .props ul, .risks ul {{
    padding-left: 16px; color: #c4b5fd;
  }}
  .risks ul {{ color: #fca5a5; }}
  .props li, .risks li {{ margin: 2px 0; }}

  /* Footer */
  .footer {{
    max-width: 1100px; margin: 40px auto 20px; padding: 20px 24px;
    border-top: 1px solid #3b2166;
    font-size: 12px; color: #6d5091;
    display: flex; justify-content: space-between;
  }}

  /* Sakura accent decoration */
  .sakura-deco {{
    position: fixed; top: -60px; right: -60px;
    width: 200px; height: 200px;
    background: radial-gradient(circle, rgba(244,171,186,0.08) 0%, transparent 70%);
    border-radius: 50%;
    pointer-events: none;
  }}

  @media print {{
    body {{ background: #fff; color: #1a1625; }}
    .card {{ border-color: #ddd; background: #fafafa; }}
    .header {{ position: static; background: #f5f5f5; }}
  }}
</style>
</head>
<body>
<div class="sakura-deco"></div>

<div class="header">
  <div class="header-inner">
    <div class="brand">
      <div class="brand-icon">HP</div>
      <div class="brand-text">
        <strong>隱者之紫</strong>
        {report_title}
      </div>
    </div>
    <div class="header-meta">
      生成時間：{date_display}<br>
      分析模組：{plugin_name}
    </div>
  </div>
</div>

<div class="stats">
  <div class="stat">
    <div class="stat-num" style="color:#f3f0ff">{total}</div>
    <div class="stat-label">總計</div>
  </div>
  <div class="stat">
    <div class="stat-num" style="color:#a855f7">{adopt_count}</div>
    <div class="stat-label">採納</div>
  </div>
  <div class="stat">
    <div class="stat-num" style="color:#f4abba">{trial_count}</div>
    <div class="stat-label">試用</div>
  </div>
  <div class="stat">
    <div class="stat-num" style="color:#a78bfa">{assess_count}</div>
    <div class="stat-label">評估</div>
  </div>
  <div class="stat">
    <div class="stat-num" style="color:#6d5091">{ignore_count}</div>
    <div class="stat-label">忽略</div>
  </div>
</div>

<div class="main">
  <div class="section-title">分析結果</div>
  {cards_html}
</div>

<div class="footer">
  <span>隱者之紫 &copy; 沫引工廠 2026</span>
  <span>{filename}</span>
</div>

</body>
</html>"""

    filepath.write_text(html_content, encoding="utf-8")
    return filepath
