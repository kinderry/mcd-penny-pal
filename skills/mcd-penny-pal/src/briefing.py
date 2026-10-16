"""麦小省 · 今日省钱简报

把 `coupons.digest()` 的体检结论渲染成麦小省的**主动播报**。

这是「搭子」和「计算器」的分界线：计算器等用户提问，
小省在用户开口之前就把今天要处理的事报出来。

文案口径（与 SKILL.md 的「说话方式」逐条对齐）：
  - 先给结论，再给理由
  - 金额带单位、保留一位小数
  - 不出现「首先/其次/最后」，不出现「本助手」
  - 不催单：结尾是邀请，不是催促

**金额的来源必须可追溯。** 本模块自己不计算任何金额，只做两件事：

  1. 从 `valuation.Valuation`（M2 用 `calculate-price` 实测算出来的）取真实节省额；
  2. 没有实测值时，退回到券面标注并**明说这是券面标注**。

券面包里的「用券价格」是券面标注值，不等于省下的钱
（实测「王牌小食拼」券面 ¥15.9、实省 ¥6.1）。
所以在拿到实测值之前，简报宁可留白也不报数——这比编一个好看的数字重要。

零第三方依赖。
"""

from __future__ import annotations

import re
from datetime import datetime

from coupons import BUCKET_SOON, CouponAudit, CouponDigest
from models import PointsAccount
from valuation import GroupSummary, Valuation

# 情绪标签。与 SKILL.md「情绪状态」表逐字一致。
MOOD_URGENT = "【着急】"
MOOD_NORMAL = "【平常】"
MOOD_EMPTY = "【空手】"
MOOD_SMUG = "【得意】"

#: 「得意」的触发门槛（分）。SKILL.md 写的是"本次为用户省下 ≥ 10 元"，
#: 改这里必须同步改 SKILL.md。
SMUG_THRESHOLD_FEN = 1000

#: 「券面价 vs 实测省额」差额达到这个数（元）才值得说破（实测命中：王牌小食拼差 ¥9.8）。
FACE_GAP_THRESHOLD_YUAN = 3.0

_MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _money(text: str) -> str:
    """从「¥13.5 (用券价格)」里取出「¥13.5」。取不到就返回空串。"""
    m = _MONEY_RE.search(text or "")
    return f"¥{m.group(1)}" if m else ""


def _num(value: float) -> str:
    """金额格式化：保留一位小数，去掉无意义的尾零。"""
    return f"{value:.1f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.1f}"


def _hhmm(expire_at: str) -> str:
    return expire_at[11:16] if len(expire_at) >= 16 else ""


def _mood(d: CouponDigest, valuation: Valuation | None = None) -> str:
    """情绪四态。紧迫性优先于金额——今天不用就归零，比"能省 20 块"更要紧。"""
    if d.urgent:
        return MOOD_URGENT
    # 券包里只剩过期的，等于手上没券 —— 不能还摆出"平常"的脸
    if not d.usable:
        return MOOD_EMPTY
    if valuation is not None:
        s = valuation.summarize([a.title for a in d.usable])
        if s.total_fen >= SMUG_THRESHOLD_FEN:
            return MOOD_SMUG
    return MOOD_NORMAL


def _headline(d: CouponDigest) -> str:
    """第一句永远是结论。"""
    today, soon = d.today, d.soon

    if today:
        # 只有所有今日到期的券**同一时刻**归零时，才敢说"今晚 23:59 就没了"。
        # 时刻不一致就退回到"今天就到期了"——笼统一点，但不误导。
        times = {t for t in (_hhmm(a.expire_at) for a in today) if t}
        if len(times) == 1:
            when = times.pop()
            # 10:29 说成"今晚"是错的；按实际时刻选词
            prefix = "今晚" if when >= "18:00" else "今天"
            tail = f"{prefix} {when} 就到期啦"
        else:
            tail = "今天就到期啦"
        return f"哎呀哎呀！你有 {len(today)} 张券券，{tail}！"

    if soon:
        days = min(a.days_left for a in soon if a.days_left is not None)
        return f"有 {len(soon)} 张券券，{days} 天内就到期了哦。快看看呀！"

    if d.audits:
        return f"券包里有 {len(d.audits)} 张券券，暂时都还安全哦。"

    return "券包空空的，一张都没有呢。"


def _item_line(a: CouponAudit, valuation: Valuation | None) -> str:
    """清单里的一行。有实测价就写实测价，没有就只写券名。"""
    v = valuation.get(a.title) if valuation else None
    if v is None:
        return f"· {a.title}"
    return f"· {a.title}（省 {v.saving_text}）"


def _untally_line(valuation: Valuation | None, audits: list[CouponAudit]) -> list[str]:
    """部分券核不到价时的说明。**绝不把没核过的那部分算进合计。**"""
    if valuation is None:
        return []
    missing = [a.title for a in audits if not valuation.can_price(a.title)]
    if not missing or len(missing) == len(audits):
        return []  # 全都能估 或 全都不能估，这里不用解释
    names = "、".join(f"「{t}」" for t in missing)
    return [f"{names}这几张小省还没算出来哦，等你要点了我再去算哈～", ""]


def _value_line(summary: GroupSummary, count: int) -> str:
    """合计句。只有**全部核到价**时才报总账。

    口径：能估的券凑一起说"加起来能省 X"；估不全就先说能估的那部分，
    剩下多少张明确告知（铁律 3：宁可少报，不可编造）。
    """
    if summary.complete:
        if count == 1:
            return f"这张能省 {summary.total_text} 哦。是拿实时菜单算过的，真的！"
        return f"这 {count} 张加起来能省 {summary.total_text} 哦。是拿实时菜单算过的，真的！"
    return (
        f"{len(summary.values)} 张核过价啦，能省 {summary.total_text}；"
        f"还有 {len(summary.unpriced)} 张小省没算出来，先不写进来，不骗你哦。"
    )


def _face_note(summary: GroupSummary) -> list[str]:
    """券面价明显高于实测节省额时，主动说破。

    这是铁律 1 最容易被违反的地方：券面那个数字是"用券**后**的价"，
    不是省下的钱。不说破，用户会以为省了 ¥15.9，实际只有 ¥6.1。
    """
    bad = summary.mismatches(threshold_yuan=FACE_GAP_THRESHOLD_YUAN)
    if not bad:
        return []
    lines = []
    for v in bad:
        lines.append(
            f"「{v.title}」小省要小小声跟你说哦：券面写着 ¥{v.face_yuan:g}，"
            f"可是实际只能省 {v.saving_text}。"
            f"券面那个数字是用券后的价，不是省下来的钱，不要被它骗到啦！"
        )
    lines.append("")
    return lines


def _urgent_block(d: CouponDigest, valuation: Valuation | None = None) -> list[str]:
    """临期清单。有实测价就给实测价，没有才退到券面标注。"""
    today, soon = d.today, d.soon
    lines: list[str] = []

    if today:
        summary = (
            valuation.summarize([a.title for a in today]) if valuation else None
        )
        head = f"券包里有 {d.total} 张券券哦，今天就没了的是这 {len(today)} 张"
        if summary is not None and summary.any:
            head += "（钱都按实时菜单算过，不是券面那个数）"
        lines.append(head + "：")
        lines.append("")
        for a in today:
            lines.append(_item_line(a, valuation))
        lines.append("")

        if summary is not None and summary.any:
            lines.append(_value_line(summary, len(today)))
            lines.append("")
            lines.extend(_face_note(summary))
            lines.extend(_untally_line(valuation, today))
        else:
            # 没有实测值：只报券面标注，并明说这是券面标注（旧行为，保留）
            priced = [(a.title, _money(a.price_text)) for a in today if _money(a.price_text)]
            if priced:
                amounts = "、".join(p[1] for p in priced)
                lines.append(
                    f"券面标的是 {amounts} 哦。能不能真省下这些，"
                    f"小省得拿实时菜单试一遍才算数。先不写进来，不骗你呀。"
                )
                lines.append("")

    elif soon:
        summary = (
            valuation.summarize([a.title for a in soon]) if valuation else None
        )
        lines.append(f"券包里有 {d.total} 张券券哦，这 {len(soon)} 张在 {BUCKET_SOON}：")
        lines.append("")
        for a in soon:
            lines.append(f"{_item_line(a, valuation)}（{a.expiry_label}）")
        lines.append("")
        if summary is not None and summary.any:
            lines.append(_value_line(summary, len(soon)))
            lines.append("")
            lines.extend(_face_note(summary))
            lines.extend(_untally_line(valuation, soon))

    return lines


def _soon_mention(d: CouponDigest, valuation: Valuation | None = None) -> list[str]:
    """「今天到期」已经列过之后，再顺嘴提一句稍晚到期的券。"""
    if not d.today or not d.soon:
        return []
    # 3 张以内就逐个点名 —— 只说"还有 2 张"用户没法行动
    if len(d.soon) <= 3:
        parts = []
        for a in d.soon:
            days = a.days_left
            when = "明天" if days == 1 else (
                f"{days} 天后" if days is not None and days > 1 else "稍后"
            )
            v = valuation.get(a.title) if valuation else None
            tag = f"（省 {v.saving_text}）" if v is not None else ""
            parts.append(f"{when}「{a.title}」{tag}")
        head = f"对啦对啦，这 {len(d.soon)} 张券券也在倒计时哦："
        # 全部可估价时补一句总账；估不全就只给单张，不给合计
        tail = ""
        if valuation is not None:
            s = valuation.summarize([a.title for a in d.soon])
            if s.complete and len(d.soon) > 1:
                tail = f"，加起来还能省 {s.total_text}"
        return [head + "、".join(parts) + tail + "。", ""]
    return [f"另外还有 {len(d.soon)} 张券券，这几天里会陆续到期哦。", ""]


def _expired_block(d: CouponDigest) -> list[str]:
    """已经过期的券。救不回来了，但该让用户知道——这叫记性好，不叫说教。"""
    if not d.expired:
        return []
    names = "、".join(f"「{a.title}」" for a in d.expired)
    return [f"呜呜，还有 {len(d.expired)} 张已经过期了：{names}，用不上了呢。", ""]


def _points_block(account: PointsAccount | None) -> list[str]:
    """积分是另一种会过期的钱 —— 用户没问也要说。"""
    if account is None:
        return []

    lines: list[str] = []
    if account.expired > 0:
        lines.append(
            f"积分也要说一句哦：能用的只剩 {_num(account.available)} 了，"
            f"已经过期掉 {_num(account.expired)} 分。攒着不用，它是真的会没的哦。"
        )
        lines.append("")
    elif account.available > 0:
        lines.append(f"积分还能用 {_num(account.available)} 哦。")
        lines.append("")

    return lines


def _claimable_block(d: CouponDigest) -> list[str]:
    """券包不厚的时候，提醒一句麦麦省还有得领。"""
    if d.claimable_count <= 0:
        return []
    if d.urgent:
        return []  # 已经在救火，就别再塞新任务了
    return [
        f"对啦对啦，麦麦省里还有 {d.claimable_count} 张可以领哦，"
        f"要不要小省一起收进券包呀？",
        "",
    ]


def _closing(d: CouponDigest) -> str:
    if d.usable:
        return "要小省帮你算算今天这一单怎么点最省吗？给个预算，我来比。"
    if d.claimable_count > 0:
        return "要小省看看麦麦省现在有什么能领的吗？"
    return "现在没有可用的券哦。等有新券到手，小省第一时间叫你。"


def build_briefing(
    d: CouponDigest,
    account: PointsAccount | None = None,
    *,
    valuation: Valuation | None = None,
    now: datetime | None = None,
) -> str:
    """生成「今日省钱简报」全文。

    骨架固定为：状态标签 → 一句话结论 → 临期清单 → 积分 → 收尾邀请。
    换调性只改这个函数里的措辞，不动骨架。

    `valuation` 传 M2 的实测结果（`valuation.value_book()`），
    传了简报里才会出现真实节省额；不传则退回券面标注口径——
    两条路都**不编数字**，区别只是"核过的"和"没核过的"。
    """
    blocks: list[list[str]] = [
        [_mood(d, valuation) + _headline(d), ""],
        _urgent_block(d, valuation),
        _soon_mention(d, valuation),
        _expired_block(d),
        _points_block(account),
        _claimable_block(d),
        [_closing(d)],
    ]

    body = "\n".join(line for block in blocks for line in block)
    # 压掉连续空行，保持简报紧凑
    return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
