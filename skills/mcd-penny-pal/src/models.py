"""麦小省 · 领域模型

所有字段名均来自 2026-10-16 对 https://mcp.mcd.cn 的**实测返回**，
不是从文档推测的。字段对不上的地方见每处注释。

零第三方依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# --------------------------------------------------------------------- 券

# 有效期原文形如：
#   「2026-10-12 10:30-2026-10-16 23:59 周一、二、三、四、五 10:30-23:59」
#   「2026-10-16 00:00-2026-10-22 23:59」
# 取最后一组「日期 时间」作为结束时间。
_DATETIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})")


@dataclass
class Coupon:
    """一张优惠券。

    三个券池返回的**排版和字段名都不一样**，由 parser 归一化到这里：
      - 麦麦省领券池（available-coupons）：bullet 条目，字段是「优惠券标题 / 状态」
      - 我的券包（query-my-coupons）：`## 券名` 作条目标题，
        字段是「优惠 / 有效期 / 领取时间 / 标签」
      - 门店券（query-store-coupons）：结构化 JSON

    券包是**唯一带真实有效期**的券池，因此临期预警只认它。
    """

    title: str
    status: str = ""
    pool: str = ""  # 券池来源，用于四路合并时去重与溯源
    image: str = ""

    # 券包专属字段（领券池没有，留空）
    price_text: str = ""   # 如 "¥13.5 (用券价格)"
    validity: str = ""     # 有效期原文，**原样保留**，不加工
    tags: str = ""         # 如 "今日到期、到店专用、外送专用"
    received_at: str = ""
    coupon_id: str = ""
    coupon_code: str = ""
    expire_at_hint: str = ""  # 结构化券接口直接给的到期时间

    # ------------------------------------------------------------ 派生
    @property
    def expire_at(self) -> str:
        """到期时刻。抽不出来就返回空串 —— 绝不臆造一个日期。

        宁可让上层显示「有效期未知」，也不编造时间去制造紧迫感。
        """
        if self.expire_at_hint:
            return self.expire_at_hint
        hits = _DATETIME_RE.findall(self.validity or "")
        if not hits:
            return ""
        y, mo, d, h, mi = hits[-1]
        return f"{y}-{mo}-{d} {h}:{mi}"

    def days_left(self, now: datetime | None = None) -> int | None:
        """距到期还有几个**自然日**。无有效期返回 None，已过期返回 -1。

        ⚠️ 必须用自然日差，不能用 ``timedelta.days``。
        实测踩过：一张券 10-17 10:29 到期，而当时是 10-16 10:59，
        相差 23.5 小时 → ``timedelta.days`` 得 0 → 券被报成「今天到期」。
        整个临期判定会因此错位一天，把明天的事说成今天的。

        返回口径：
            0  = 今天到期（按日历）
            1  = 明天到期
            -1 = 已经过期
        """
        expire = self.expire_at
        if not expire:
            return None
        try:
            deadline = datetime.strptime(expire, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        now = now or datetime.now()
        if deadline < now:
            return -1
        return (deadline.date() - now.date()).days

    @property
    def claimable(self) -> bool:
        """是否为可领取状态（麦麦省领券池口径）。"""
        return "可领取" in self.status

    @property
    def expiring_soon(self) -> bool:
        """3 天内到期。无有效期信息时一律返回 False（宁可漏报）。"""
        left = self.days_left()
        return left is not None and 0 <= left <= 3

    @property
    def tags_list(self) -> list[str]:
        return [t.strip() for t in re.split(r"[、,，]", self.tags or "") if t.strip()]

    def __str__(self) -> str:
        tail = self.status or ("今日到期" if "今日到期" in self.tags else "已领取")
        return f"{self.title}（{tail}）"


# ------------------------------------------------------------- 门店可用券

@dataclass
class StoreCoupon:
    """门店可用券 —— **唯一带 couponId / couponCode 的券结构**。

    来源 `query-store-coupons`，实测返回（2026-10-16）每条形如：

        {"title": "中薯条买一送一",
         "couponId": "8692944B045928451618BFF22F535D2A",
         "couponCode": "MCD61U9Y010008T76000N",
         "tradeDateTime": "2026-10-12 10:30:00-2026-10-16 23:59:59",
         "products": [{"productCode": "9900012387", "productName": "中薯条买一送一"}],
         "promotionId": "112458028"}

    ⚠️ 券包（`query-my-coupons`）**不给** couponId / couponCode，
    所以"用这张券能省多少钱"必须先反查门店券。这是整条省钱链路的钥匙。

    ⚠️ `products[].productCode` 是**券专属的商品编码**（如 9900012387），
    不是菜单里的普通商品（中薯条是 4810）。计价时必须用它，
    并且**必须同时带上 couponId + couponCode**，否则价格全为 0。
    """

    title: str = ""
    coupon_id: str = ""
    coupon_code: str = ""
    trade_datetime: str = ""
    promotion_id: str = ""
    product_code: str = ""
    product_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def expire_at(self) -> str:
        """券的到期时刻。`tradeDateTime` 形如
        「2026-10-12 10:30:00-2026-10-16 23:59:59」，取后一组。"""
        hits = _DATETIME_RE.findall(self.trade_datetime or "")
        if not hits:
            return ""
        y, mo, d, h, mi = hits[-1]
        return f"{y}-{mo}-{d} {h}:{mi}"


# --------------------------------------------------------------------- 活动

@dataclass
class Campaign:
    """活动日历里的一条活动。来源是纯 Markdown，按标题/正文/图片三段解析。"""

    title: str
    period: str = ""  # 所属日期分组，如「2026年10月12日 往期回顾」
    content: str = ""
    image: str = ""


# --------------------------------------------------------------------- 门店

@dataclass
class Store:
    """门店。字段来自 query-nearby-stores 实测。"""

    code: str = ""
    name: str = ""
    address: str = ""
    distance: str = ""  # 接口给的是字符串，原样保留，不做数字转换
    business_status: str = ""

    @property
    def distance_meters(self) -> int | None:
        """尽量把距离转成米，转不出来就返回 None —— 不猜。"""
        m = re.search(r"(\d+(?:\.\d+)?)", self.distance or "")
        if not m:
            return None
        value = float(m.group(1))
        if "公里" in self.distance or "km" in self.distance.lower():
            value *= 1000
        return int(value)


# --------------------------------------------------------------------- 积分

@dataclass
class PointsAccount:
    """积分账户。字段来自 query-my-account 实测。"""

    available: float = 0.0
    accumulative: float = 0.0
    expired: float = 0.0
    frozen: float = 0.0
    used: float = 0.0
    next_month_expire: float = 0.0
    current_month_expire: float = 0.0
    currency: str = "麦享会积分"

    @property
    def total_visible(self) -> float:
        return self.available + self.frozen


@dataclass
class MallProduct:
    """积分商城可兑换商品。字段来自 mall-points-products 实测。"""

    spu_id: int = 0
    name: str = ""
    point: float = 0.0
    price: float | None = None  # 接口给的参考价；无则 None，不估算
    category: str = ""
    image: str = ""
    status: str = ""
    selling: bool = False


@dataclass
class LotteryPrize:
    """抽奖奖池中的一项。原始字段保留在 raw，避免过度假设。"""

    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LotteryInfo:
    """积分抽奖活动信息。字段来自 query-lottery-info 实测。"""

    activity_name: str = ""
    activity_code: str = ""
    status_text: str = ""
    begin_time: str = ""
    end_time: str = ""
    draw_point: float = 0.0
    available_point: float = 0.0
    eligible: bool = False  # 以 drawDecision.resourceEligible 为准
    reason: str | None = None
    prizes: list[LotteryPrize] = field(default_factory=list)

    @property
    def available_times_gate(self) -> bool:
        """资格判定**只认** drawDecision.resourceEligible。

        实测可用次数 availableTimes 在纯积分模式下不返回，
        从它推断资格会得到错误结论。
        """
        return self.eligible


# --------------------------------------------------------------------- 营养

@dataclass
class Nutrition:
    """餐品营养。来源是 list-nutrition-foods 返回的紧凑表格字符串。"""

    product_name: str = ""
    description: str | None = None
    energy_kj: float = 0.0
    energy_kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carbohydrate: float = 0.0
    sodium: float = 0.0
    calcium: float = 0.0

    @property
    def energy_kcal_per_100g(self) -> float:
        return self.energy_kcal
