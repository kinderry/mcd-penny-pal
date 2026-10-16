"""麦小省 · 今日省钱战报卡片

简报是「用起来」的，卡片是「截图发出去」的。
这个模块只做后者：把 M1 的临期结论 + M2 的真实计价，压成一张
1080×1440（3:4，小红书/朋友圈竖版）的自包含 HTML。

**自包含**：形象图以 base64 内联，单个 .html 文件随处可开、可截图，
不依赖任何外部资源，也不需要装任何东西。

三条口径，与 briefing / valuation 完全一致（这是刻意的——卡片绝不能
比简报"更好看"，好看的压力最容易变成编数字的动机）：

1. 金额只来自 `calculate-price`。估值册拿不到的券，卡片上一个数字都不给。
2. 部分可估时**不报合计**。宁可写「已核价的 2 张能省 ¥19.6」，
   也不写「今天能省 ¥19.6」——后者把 4 张没核过的券也算进去了。
3. 券面价与实省差距 ≥ ¥3 时主动说破（「券面写 ¥15.9，实际只省 ¥6.1」）。

零第三方依赖。形象图缺失时自动降级为无图案版本，不影响生成。
"""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime
from pathlib import Path

from coupons import BUCKET_LATER, BUCKET_SOON, BUCKET_TODAY, CouponDigest
from models import PointsAccount
from pricer import money
from valuation import GroupSummary, Valuation

# 卡片画布：3:4 竖版（1080×1440），主流社交平台推荐比例
CARD_W = 1080
CARD_H = 1440

# ⚠️ 高度是**下限**而不是固定值。
# 实测：券多的时候（每张券一行）内容会超过 1440，写死高度会把最后一行
# （仓库地址那行）挤出画布。所以卡片按内容自然撑开，截图后再按实际内容
# 裁切：内容少就 1080×1440，内容多就顺延加高，任何数据量都不会被裁。

# 情绪标签。与 SKILL.md 的情绪四态逐字一致。
MOOD_URGENT = "着急"
MOOD_NORMAL = "平常"
MOOD_EMPTY = "空手"
MOOD_SMUG = "得意"


# ------------------------------------------------------------------ 小工具

def _esc(text: object) -> str:
    """HTML 转义。券名里可能出现 `&`、`<` 之类。"""
    return html.escape(str(text if text is not None else ""), quote=True)


def _hhmm(expire_at: str) -> str:
    return expire_at[11:16] if len(expire_at or "") >= 16 else ""


def _face_total(summary: GroupSummary) -> float | None:
    """这一组券的**券面标注**合计（元）。有任何一张没有券面价就返回 None。

    只用于和大数字做对照，让用户自己看出"券面价虚高"。
    """
    faces: list[float] = []
    for v in summary.values:
        face = v.face_yuan
        if face is None:
            return None
        faces.append(face)
    return round(sum(faces), 1) if faces else None


# ------------------------------------------------------------------ 内容分块

def _mood(d: CouponDigest) -> str:
    if d.urgent:
        return MOOD_URGENT
    if not d.usable:
        return MOOD_EMPTY
    return MOOD_NORMAL


def _hero(d: CouponDigest) -> tuple[str, str]:
    """（大字结论, 小字补充）。卡片上换行显示，比一整句更好扫。"""
    if d.today:
        times = [t for t in (_hhmm(a.expire_at) for a in d.today) if t]
        when = f"今晚 {max(times)} 就作废" if len(set(times)) == 1 and times else "今天就作废"
        return when, f"你手上有 {d.total} 张券券，今天 {len(d.today)} 张要溜走啦"
    if d.soon:
        days = sorted({a.days_left for a in d.soon if a.days_left and a.days_left > 0})
        soonest = f"{days[0]} 天后" if days else "很快"
        first = d.soon[0]
        return f"最快 {soonest} 到期", f"{first.title} 打头，一共 {len(d.soon)} 张券券在倒计时"
    if d.usable:
        return f"手上有 {len(d.usable)} 张券券", "暂时都没有快到期的，安心安心"
    if d.audits:
        return "券券都用完了", f"{len(d.audits)} 张已经过期，新的还没来哦"
    return "券包空空的", "麦麦省有券可领的时候，小省先叫你呀"


def _number_block(d: CouponDigest, summary: GroupSummary | None) -> tuple[str, str, str]:
    """（大数字, 数字下方一句, 口径小字）。

    没有可核价金额时返回空串，卡片**不显示任何金额**——这是铁律 3。
    """
    if summary is None or not summary.any:
        return "", "", ""

    if summary.complete:
        # 这一组全核到了价，才敢说"能省"
        headline = "今天能省"
        return (
            summary.total_text,
            headline,
            f"这 {summary.count} 张券券都按实时菜单核过价哦",
        )

    # 只核到一部分：只说这一部分，并明说还有没核的
    return (
        summary.total_text,
        f"这是已核价的 {len(summary.values)} 张",
        f"另外 {len(summary.unpriced)} 张小省还没核到价，不瞎写哦",
    )


def _deal_rows(summary: GroupSummary, *, limit: int = 4) -> list[tuple[str, str, bool]]:
    """券清单行：(券名, 金额文本, 是否缺价)。

    按优惠额从大到小——用户最该用哪张，一眼看到。
    """
    rows: list[tuple[str, str, bool]] = []
    priced = sorted(summary.values, key=lambda v: v.saving_fen, reverse=True)
    for v in priced[:limit]:
        rows.append((v.title, f"省 {v.saving_text}", False))
    if len(priced) > limit:
        rest = sum(v.saving_fen for v in priced[limit:])
        rows.append((f"另外 {len(priced) - limit} 张", f"省 {money(rest)}", False))

    # 未核价的券也列出来，但明确写成「未核价」而不是留空
    for title in summary.unpriced[: max(0, limit - len(rows))]:
        rows.append((title, "未核到价", True))
    return rows


def _gap_note(summary: GroupSummary, *, threshold_yuan: float = 3.0) -> str:
    """券面虚高的提醒。差额不够大的不提，免得啰嗦。"""
    gaps = summary.mismatches(threshold_yuan=threshold_yuan)
    if not gaps:
        return ""
    worst = max(gaps, key=lambda v: v.face_gap_yuan or 0)
    face = worst.face_yuan
    if face is None:
        return ""
    return (
        f"「{worst.title}」券面写着 ¥{face:g}，实际只能省 {worst.saving_text} 哦。"
        "券面那个数字是用券后的价，不是省下来的钱"
    )


def _points_line(account: PointsAccount | None) -> str:
    if account is None:
        return ""
    parts: list[str] = []
    if account.available is not None:
        parts.append(f"积分可用 {account.available:g}")
    if getattr(account, "expired", None):
        parts.append(f"已过期 {account.expired:g}")
    return " · ".join(parts)


# ------------------------------------------------------------------ 形象图

def _data_uri(path: str | Path | None) -> str:
    """把形象图读成 data URI。读不到就返回空串（卡片自动降级）。"""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        blob = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{blob}"


# ------------------------------------------------------------------ 模板

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>麦小省 · 今日省钱战报</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html { background: #FFF9EC; }
  body {
    width: {w}px; min-height: {h}px; overflow: visible;
    font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", sans-serif;
    background: #FFF9EC; color: #2A211A;
    -webkit-font-smoothing: antialiased;
  }
  .card {
    position: relative; width: {w}px; min-height: {h}px;
    padding: 68px 72px 56px;
    display: flex; flex-direction: column;
    background:
      radial-gradient(circle at 88% 6%, #FFE7A8 0%, rgba(255,231,168,0) 46%),
      radial-gradient(circle at 4% 96%, #FFDCD2 0%, rgba(255,220,210,0) 42%),
      linear-gradient(168deg, #FFFCF3 0%, #FFF4DC 100%);
  }
  .head { display: flex; align-items: baseline; justify-content: space-between; }
  .brand { font-size: 40px; font-weight: 800; letter-spacing: 2px; color: #D8362A; }
  .brand small { font-size: 22px; font-weight: 600; color: #A8937A; letter-spacing: 0; margin-left: 12px; }
  .date { font-size: 26px; font-weight: 600; color: #A8937A; letter-spacing: 1px; }

  .hero { margin-top: 62px; }
  .mood {
    display: inline-block; padding: 10px 30px; border-radius: 999px;
    background: {mood_bg}; color: {mood_fg};
    font-size: 27px; font-weight: 800; letter-spacing: 3px;
  }
  .hero-big { margin-top: 32px; font-size: 80px; font-weight: 800; line-height: 1.16; letter-spacing: -1px; }
  .hero-sub { margin-top: 22px; font-size: 31px; font-weight: 500; color: #6B5A49; line-height: 1.45; }

  .number {
    margin-top: 54px; padding: 44px 48px; border-radius: 36px;
    background: linear-gradient(135deg, #FFFFFF 0%, #FFF6E2 100%);
    box-shadow: 0 18px 46px rgba(196, 150, 60, .16);
    border: 3px solid #FFE2A8;
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
  }
  .number .big { font-size: 94px; font-weight: 800; color: #D8362A; letter-spacing: -2px; line-height: 1; }
  .number .cap { margin-top: 14px; font-size: 28px; font-weight: 600; color: #6B5A49; }
  .number .side { text-align: right; font-size: 22px; color: #A8937A; line-height: 1.5; max-width: 340px; }

  .rows { margin-top: 48px; display: flex; flex-direction: column; gap: 24px; }
  .row { display: flex; align-items: center; gap: 18px; font-size: 32px; }
  .row .dot { width: 14px; height: 14px; border-radius: 50%; background: #FFC72C; flex: none; }
  .row .name { flex: 1; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .amt { font-weight: 800; color: #D8362A; font-variant-numeric: tabular-nums; }
  .row.dim .amt { color: #B6A793; font-weight: 600; font-size: 27px; }
  .row.dim .name { color: #8A7B6B; font-weight: 500; }

  .more { margin-top: 30px; font-size: 26px; color: #8A7B6B; font-weight: 500; }

  .note {
    margin-top: 34px; padding: 26px 28px; border-radius: 22px;
    background: rgba(216, 54, 42, .07); border-left: 8px solid #D8362A;
    font-size: 25px; line-height: 1.5; color: #8A3A2E;
  }
  .points { margin-top: 36px; font-size: 30px; font-weight: 600; color: #6B5A49; }
  .points b { color: #D8362A; font-weight: 800; }

  .spacer { flex: 1; }

  .foot { display: flex; align-items: flex-end; justify-content: space-between; gap: 28px; }
  .foot .avatar {
    width: 174px; height: 174px; border-radius: 50%; object-fit: cover; flex: none;
    border: 6px solid #FFF; box-shadow: 0 10px 26px rgba(196, 150, 60, .22);
  }
  .foot .avatar.empty {
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, #FFD24A, #FFB020);
    font-size: 64px; font-weight: 800; color: #FFF; border: 6px solid #FFF;
  }
  .foot .say { flex: 1; text-align: right; }
  .foot .say .l1 { font-size: 30px; font-weight: 700; color: #2A211A; }
  .foot .say .l2 { margin-top: 10px; font-size: 23px; color: #A8937A; line-height: 1.45; }
  .foot .say .l3 { margin-top: 14px; font-size: 22px; color: #C0973F; font-weight: 700; letter-spacing: .5px; }
</style>
</head>
<body>
<div class="card">
  <div class="head">
    <div class="brand">麦小省<small>mcd-penny-pal</small></div>
    <div class="date">{date}</div>
  </div>

  <div class="hero">
    <span class="mood">{mood}</span>
    <div class="hero-big">{hero_big}</div>
    <div class="hero-sub">{hero_sub}</div>
  </div>
{number}
{rows}
{more}
{note}
{points}
  <div class="spacer"></div>
  <div class="foot">
{avatar}
    <div class="say">
      <div class="l1">每天帮你看一眼券券，该花的花，该省的省呀</div>
      <div class="l2">每个数字都来自实时计价，不估算、不编造哦</div>
{url}
    </div>
  </div>
</div>
</body>
</html>
"""


# ------------------------------------------------------------------ 主函数

def _escape_css(template: str) -> str:
    """把 <style> 段里的花括号双写，避免与 str.format 的占位符冲突。

    ⚠️ 必须先替换掉样式段里的占位符再转义。否则 `{w}` 会被双写成 `{{w}}`，
    format 只会还原成字面的 `{w}`，宽度和最小高度**整条失效**——
    卡片尺寸悄悄退化成一堆被浏览器忽略的无效声明。这个坑踩过。
    """
    start, end = template.find("<style>"), template.find("</style>")
    if start < 0 or end < 0:
        return template
    head = template[: start + len("<style>")]
    css = template[start + len("<style>") : end]
    return head + css.replace("{", "{{").replace("}", "}}") + template[end:]


def build_card(
    d: CouponDigest,
    *,
    valuation: Valuation | None = None,
    account: PointsAccount | None = None,
    now: datetime | None = None,
    store_name: str = "",
    mascot_path: str | Path | None = None,
    project_url: str = "",
) -> str:
    """生成战报卡片 HTML（自包含，可直接截图）。

    参数
    ----
    d
        M1 券管家的体检结论。
    valuation
        M2 的估值册。传 None 时卡片**不显示任何金额**，
        退化为"临期提醒卡"——这是刻意的降级，不是 bug。
    account
        积分账户，用于底部那一行。
    now
        用于日期与临期判断的时点，默认当前时间。
    mascot_path
        形象图路径，会以 base64 内联。读不到时自动降级为纯色圆底。
    project_url
        卡片底部的仓库地址。**传播钩子**——截图发出去，看到的人能找到项目。
    """
    now = now or datetime.now()

    # ⚠️ 只聚焦**最紧急那一组**：有今天到期的就只算今天这批，否则退到 T-3。
    # 若把 today + soon 混在一起算，「2 张今天到期」和「4 条清单」就对不上了。
    focus = d.today or d.soon
    summary: GroupSummary | None = None
    if valuation is not None and focus:
        summary = valuation.summarize([a.title for a in focus])

    hero_big, hero_sub = _hero(d)
    mood = _mood(d)
    mood_bg, mood_fg = {
        MOOD_URGENT: ("#FFC9BA", "#AF2216"),
        MOOD_NORMAL: ("#FFE39B", "#82580A"),
        MOOD_EMPTY: ("#E4DCCE", "#544639"),
        MOOD_SMUG: ("#FFC9BA", "#AF2216"),
    }[mood]

    # —— 大数字块
    number_html = ""
    big, cap, side = _number_block(d, summary)
    if big:
        gap_text = ""
        if summary is not None:
            faces = _face_total(summary)
            if faces is not None and faces > 0:
                gap_text = f"券面标注合计 ¥{faces:g}<br>"
        number_html = (
            '  <div class="number">\n'
            f'    <div><div class="big">{_esc(big)}</div>'
            f'<div class="cap">{_esc(cap)}</div></div>\n'
            f'    <div class="side">{gap_text}{_esc(side)}</div>\n'
            "  </div>"
        )

    # —— 券清单
    rows_html = ""
    if summary is not None and (summary.values or summary.unpriced):
        items = [
            '  <div class="rows">',
        ]
        for name, amt, dim in _deal_rows(summary):
            cls = "row dim" if dim else "row"
            items.append(
                f'    <div class="{cls}"><span class="dot"></span>'
                f'<span class="name">{_esc(name)}</span>'
                f'<span class="amt">{_esc(amt)}</span></div>'
            )
        items.append("  </div>")
        rows_html = "\n".join(items)

    # 卡片聚焦今天到期那一批；其余临期券用一行带过，不铺开
    more_html = ""
    if summary is not None and d.today and d.soon:
        days = [a.days_left for a in d.soon if a.days_left is not None and a.days_left > 0]
        soonest = f"最快 {min(days)} 天后" if days else "稍后"
        more_html = (
            f'  <div class="more">对啦，还有 {len(d.soon)} 张券券在倒计时，{_esc(soonest)}就到期哦</div>'
        )

    note_html = ""
    if summary is not None:
        note = _gap_note(summary)
        if note:
            note_html = f'  <div class="note">{_esc(note)}</div>'

    points = _points_line(account)
    points_html = f'  <div class="points">积分：<b>{_esc(points)}</b></div>' if points else ""

    uri = _data_uri(mascot_path)
    if uri:
        avatar_html = f'    <img class="avatar" src="{uri}" alt="麦小省">'
    else:
        # 形象图缺失也要出卡：用"省"字占位，不阻塞生成
        avatar_html = '    <div class="avatar empty">省</div>'

    url_html = f'      <div class="l3">{_esc(project_url)}</div>' if project_url else ""

    store_html = ""
    if store_name:
        store_html = f'      <div class="l3">{_esc(store_name)}</div>'

    # 样式段里的占位符先落地，再整体转义花括号，最后才 format 正文占位符。
    # 顺序反了会让 width / min-height 静默失效（见 _escape_css 的说明）。
    template = (
        _TEMPLATE
        .replace("{w}", str(CARD_W))
        .replace("{h}", str(CARD_H))
        .replace("{mood_bg}", mood_bg)
        .replace("{mood_fg}", mood_fg)
    )

    out = _escape_css(template).format(
        date=now.strftime("%Y.%m.%d"),
        mood=_esc(mood),
        hero_big=_esc(hero_big),
        hero_sub=_esc(hero_sub),
        number=number_html,
        rows=rows_html,
        more=more_html,
        note=note_html,
        points=points_html,
        avatar=avatar_html,
        url=url_html or store_html,
    )
    return out


def card_plain_text(html_text: str) -> str:
    """把卡片 HTML 抽成纯文本，用于自测与无图环境下的降级展示。"""
    body = re.sub(r"(?s)<style.*?</style>", " ", html_text)
    body = re.sub(r"<br\s*/?>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    return re.sub(r"\s+", " ", body).strip()
