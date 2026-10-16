"""麦小省 · M1 券管家

四路券池合并 → 去重 → 临期分级 → 给出「今天该先动哪张」。

三条口径来自 2026-10-16 的**实测**，不是从文档推的，改动前请先复验：

1. **只有「我的券包」带真实有效期。**
   `query-my-coupons` 每张券都给了「有效期」原文和「用券价格」；
   而 `available-coupons`（麦麦省可领池）只有标题和状态，没有到期时间。
   → 所以临期预警**只认券包**。拿领券池去算临期，只能靠编。

2. **领券成功 ≠ 进卡包。**
   实测 `auto-bind-coupons` 返回「总计 7 张、成功 7 张」，
   但券包只到账 6 张（「免费脆薯饼」缺席）。
   → 引擎一律以券包为资产口径，**绝不把领券结果当已有资产**。

3. **没有原价就不能说省了多少。**
   券面包里的 ¥13.5 是「用券价格」，不是节省额。真实节省额要拿
   `query-meals` 取原价、再 `calculate-price` 试算才知道。
   → 本模块**不产出任何金额结论**，只负责券的分类与排序。

零第三方依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from models import Coupon

# ------------------------------------------------------------------ 券池标识

POOL_MINE = "我的券包"        # query-my-coupons，唯一带有效期
POOL_CLAIMABLE = "麦麦省待领"  # available-coupons，可领取列表
POOL_STORE = "门店专属"       # query-store-coupons
POOL_PROMO = "活动券"         # query-promotions 等

# 去重时的优先级：数字越小越权威（已有资产 > 门店券 > 可领券）
_POOL_RANK = {POOL_MINE: 0, POOL_STORE: 1, POOL_PROMO: 2, POOL_CLAIMABLE: 3}

# ------------------------------------------------------------------ 临期分档

BUCKET_EXPIRED = "已过期"
BUCKET_TODAY = "今日到期"
BUCKET_SOON = "T-3 内到期"
BUCKET_LATER = "有效期内"
BUCKET_UNKNOWN = "有效期未知"

#: 临期阈值（天）。与 SKILL.md 的「情绪四态」共用同一个数字，
#: 改这里必须同步改 SKILL.md，否则人格和逻辑会对不上。
SOON_THRESHOLD_DAYS = 3


def name_key(title: str) -> str:
    """券名归一化：抹平全半角、空格、标点差异，只留文字和数字。

    被 `_dedup_key`（去重）和 `valuation`（跨券池匹配计价结果）共用。
    两处必须用同一套规则，否则会出现"去重认得出、估值匹配不上"的错配。
    """
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", title or "").lower()


def _dedup_key(coupon: Coupon) -> str:
    """去重键：券名 + 到期时刻。

    ⚠️ 到期时刻**必须**参与去重。实测就有两张同名的
    「巧克力味厚松饼猪柳蛋套餐」，但一张 10-17 到期、一张 10-18 到期。
    只按券名去重会把其中一张悄悄吃掉，用户凭空少一张券。

    券名为空时返回空串（跳过该条）；没有有效期时退化为单券名键
    ——宁可重复显示，不可漏计。
    """
    name = name_key(coupon.title)
    return f"{name}|{coupon.expire_at}" if name else ""


def merge_pools(*groups: tuple[str, list[Coupon]]) -> list[Coupon]:
    """合并多路券池并按券名去重。

    同一张券可能同时出现在「麦麦省待领」和「我的券包」里
    （领完之后两边都能查到）。此时保留优先级更高的那一份，
    并把可读到的信息合并过来，避免因为选错来源丢掉有效期。

    参数形如 ``merge_pools((POOL_MINE, mine), (POOL_CLAIMABLE, claim))``。
    """
    best: dict[str, Coupon] = {}

    for pool_name, coupons in groups:
        for coupon in coupons or []:
            coupon.pool = pool_name
            key = _dedup_key(coupon)
            if not key:
                continue
            prev = best.get(key)
            if prev is None:
                best[key] = coupon
                continue
            # 同券多源：留下更权威的一份，但把补不到的字段捡回来
            winner, loser = sorted(
                (prev, coupon), key=lambda c: _POOL_RANK.get(c.pool, 9)
            )
            for f in ("price_text", "validity", "tags", "image", "status",
                      "received_at", "coupon_id", "coupon_code", "expire_at_hint"):
                if not getattr(winner, f) and getattr(loser, f):
                    setattr(winner, f, getattr(loser, f))
            best[key] = winner

    return sorted(best.values(), key=lambda c: (_POOL_RANK.get(c.pool, 9), c.title))


@dataclass
class CouponAudit:
    """单张券的体检结论。只描述事实，不给金额判断。"""

    coupon: Coupon
    days_left: int | None = None
    bucket: str = BUCKET_UNKNOWN
    notes: list[str] = field(default_factory=list)

    @property
    def urgent(self) -> bool:
        """是否需要在简报里高亮。"""
        return self.bucket in (BUCKET_TODAY, BUCKET_SOON)

    @property
    def title(self) -> str:
        return self.coupon.title

    @property
    def price_text(self) -> str:
        return self.coupon.price_text

    @property
    def expire_at(self) -> str:
        return self.coupon.expire_at

    @property
    def expiry_label(self) -> str:
        """给人看的到期描述。无有效期时**直说不知道**，不编一个日期。"""
        if not self.expire_at:
            return "有效期未知"
        hhmm = self.expire_at[11:16]
        left = self.days_left
        if left == -1:
            return f"已过期（{self.expire_at[:10]} {hhmm}）"
        if left == 0:
            return f"今天 {hhmm} 到期"
        if left == 1:
            return f"明天 {hhmm} 到期"
        if left is not None and left > 1:
            return f"{left} 天后到期（{self.expire_at[:10]}）"
        return self.expire_at


def _bucket_of(days_left: int | None) -> str:
    if days_left is None:
        return BUCKET_UNKNOWN
    if days_left < 0:
        return BUCKET_EXPIRED
    if days_left == 0:
        return BUCKET_TODAY
    if days_left <= SOON_THRESHOLD_DAYS:
        return BUCKET_SOON
    return BUCKET_LATER


#: 标签里的场景词 → 人话。原样保留原文，只在备注里翻译。
_CHANNEL_WORDS = ("到店专用", "外送专用", "外送", "到店")


def audit(coupons: list[Coupon], now: datetime | None = None) -> list[CouponAudit]:
    """给每张券体检。结果按「紧急度 → 剩余天数 → 券名」排序。

    紧急度排序把「今日到期」顶到最前，因为那是唯一有可能今天归零的东西。
    """
    out: list[CouponAudit] = []
    for c in coupons:
        left = c.days_left(now=now)
        a = CouponAudit(
            coupon=c,
            days_left=left,
            bucket=_bucket_of(left),
            notes=_notes_for(c, left),
        )
        out.append(a)

    _BUCKET_ORDER = {
        BUCKET_TODAY: 0,
        BUCKET_SOON: 1,
        BUCKET_LATER: 2,
        BUCKET_UNKNOWN: 3,
        BUCKET_EXPIRED: 4,  # 已经救不回来了，排在后面，只作告知
    }
    out.sort(key=lambda a: (
        _BUCKET_ORDER.get(a.bucket, 9),
        a.days_left if a.days_left is not None else 999,
        a.title,
    ))
    return out


def _notes_for(coupon: Coupon, days_left: int | None) -> list[str]:
    """生成**事实性**备注。这里不出现任何金额推断。"""
    notes: list[str] = []

    channels = [w for w in _CHANNEL_WORDS if w in (coupon.tags or "")]
    if "到店专用" in channels and "外送专用" in channels:
        notes.append("到店和外送都能用")
    elif channels:
        notes.append(f"限{channels[0]}")

    if "买一送一" in coupon.title:
        # 买一送一必须凑两份才有意义，这不是推断，是券的玩法
        notes.append("买一送一，要凑两份才划算")

    if coupon.price_text:
        notes.append(f"券面标注：{coupon.price_text}")

    if days_left is None:
        notes.append("接口没给有效期，不猜到期日")

    return notes


@dataclass
class CouponDigest:
    """券包体检总览。简报和卡片都从这里取数。

    ``total`` 数的是**券包里的券**（query-my-coupons + 门店券），
    不包含可领池——可领池是机会，不是资产，混进来会把数字说大。
    """

    audits: list[CouponAudit] = field(default_factory=list)
    total: int = 0
    claimable_count: int = 0

    @property
    def urgent(self) -> list[CouponAudit]:
        return [a for a in self.audits if a.urgent]

    @property
    def today(self) -> list[CouponAudit]:
        return [a for a in self.audits if a.bucket == BUCKET_TODAY]

    @property
    def soon(self) -> list[CouponAudit]:
        return [a for a in self.audits if a.bucket == BUCKET_SOON]

    @property
    def later(self) -> list[CouponAudit]:
        return [a for a in self.audits if a.bucket == BUCKET_LATER]

    @property
    def unknown(self) -> list[CouponAudit]:
        return [a for a in self.audits if a.bucket == BUCKET_UNKNOWN]

    @property
    def expired(self) -> list[CouponAudit]:
        return [a for a in self.audits if a.bucket == BUCKET_EXPIRED]

    @property
    def usable(self) -> list[CouponAudit]:
        """还在有效期内、真能拿去用的券。"""
        return [a for a in self.audits if a.bucket != BUCKET_EXPIRED]

    @property
    def has_assets(self) -> bool:
        """券包里到底有没有东西。空券包不该假装有资产。"""
        return bool(self.audits)


def digest(
    mine: list[Coupon] | None = None,
    claimable: list[Coupon] | None = None,
    store: list[Coupon] | None = None,
    now: datetime | None = None,
) -> CouponDigest:
    """合成券包体检总览。

    ⚠️ 「券包」和「可领池」是两种东西，绝不能混成一个数字：

      - **券包**（query-my-coupons / query-store-coupons）是**已有的钱**，
        带真实有效期，临期预警只认它 → 进 ``audits``
      - **可领池**（available-coupons）是**领取机会**，没有有效期

    而且实测领完券之后，可领池里那批券的 ``status`` 会变成「已领取」，
    此时它们**不该**再被提示成"可以领"——所以 ``claimable_count``
    只数 ``claimable`` 为真的那些。

    另有一处实测数据不一致：可领池把「免费脆薯饼」标成「已领取」，
    但券包里根本没有它。**以券包为准**——引擎只信券包。
    """
    holdings = merge_pools((POOL_MINE, mine or []), (POOL_STORE, store or []))

    held_names = {name_key(c.title) for c in holdings}
    fresh = [
        c for c in (claimable or [])
        if c.claimable and name_key(c.title) not in held_names
    ]

    return CouponDigest(
        audits=audit(holdings, now=now),
        total=len(holdings),
        claimable_count=len(fresh),
    )
