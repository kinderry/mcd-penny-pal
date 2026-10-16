"""麦小省 · 券价值核算（M1 × M2 缝合层）

M1 知道「哪张券快过期」，M2 知道「哪张券值多少钱」，
本模块按券名把两者缝起来，回答用户真正关心的那句话：

    「我这几张快过期的券，加起来值多少钱？」

三条口径（2026-10-16 实测，改动前请先复验）：

1. ⚠️ **能估价的券必须先在门店券里落地。**
   券包（`query-my-coupons`）**不给** `couponId` / `couponCode`，
   而没有这两个字段，`calculate-price` 认不出券、价格全返回 0。
   所以「券包里有、门店券里没有」的券一律**无法估价**，
   只记进 `unpriced`，**绝不拿券面价冒充实测节省额**（铁律 3）。

2. ⚠️ **券面价 ≠ 节省额。**
   实测「王牌小食拼」券面写 ¥15.9，真实只省 ¥6.1 ——
   券面那个数字是"用券**后**的价"，不是省下的钱。
   本模块只认 `discount`，券面价仅作对照展示。

3. ⚠️ **部分可估时不许报合计。**
   一组券里只有一部分能估价，就只说这一部分的数字，并明确告知
   剩下的还没核过。把「¥19.6」说成「这 5 张券值 ¥19.6」是撒谎。

零第三方依赖，纯函数，不碰网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from coupons import name_key
from models import Coupon
from pricer import CouponDeal, money, to_yuan


# ------------------------------------------------------------------ 单张券

@dataclass
class CouponValue:
    """一张券的**实测**价值。金额单位是分。"""

    title: str = ""            # 券包里的券名（原样）
    saving_fen: int = 0        # 真实优惠额，来自 calculate-price 的 discount
    original_fen: int = 0
    pay_fen: int = 0
    store_title: str = ""      # 命中的门店券名
    face_text: str = ""        # 券面包里的「¥13.5 (用券价格)」，仅作对照

    @property
    def saving_text(self) -> str:
        return money(self.saving_fen)

    @property
    def original_text(self) -> str:
        return money(self.original_fen)

    @property
    def pay_text(self) -> str:
        return money(self.pay_fen)

    @property
    def face_yuan(self) -> float | None:
        """券面标注的金额（元）。解析不出来返回 None —— 不猜。"""
        import re

        m = re.search(r"(\d+(?:\.\d+)?)", self.face_text or "")
        return float(m.group(1)) if m else None

    @property
    def face_gap_yuan(self) -> float | None:
        """券面价与实测节省额的差额（元）。

        正数表示券面数字**比实际省下的钱大**——最容易误导用户的地方。
        实测「王牌小食拼」这里是 9.8（券面 ¥15.9，实省 ¥6.1）。
        """
        face = self.face_yuan
        if face is None:
            return None
        return round(face - to_yuan(self.saving_fen), 1)


# ------------------------------------------------------------------ 分组合计

@dataclass
class GroupSummary:
    """一组券（比如"今天到期的这几张"）的估值汇总。"""

    values: list[CouponValue] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)  # 券名原样

    @property
    def total_fen(self) -> int:
        return sum(v.saving_fen for v in self.values)

    @property
    def total_text(self) -> str:
        return money(self.total_fen)

    @property
    def any(self) -> bool:
        """至少有一张核到了价。"""
        return bool(self.values)

    @property
    def complete(self) -> bool:
        """这一组券**全部**核到了价 —— 只有此时才敢报合计。"""
        return bool(self.values) and not self.unpriced

    @property
    def count(self) -> int:
        return len(self.values) + len(self.unpriced)

    def mismatches(self, *, threshold_yuan: float = 3.0) -> list[CouponValue]:
        """券面价明显高于实测节省额的券（差额 ≥ threshold 元）。

        这些是最该被说破的：「券面写 ¥15.9，实际只能省 ¥6.1」。
        差得太小的（比如几毛钱）不提，免得啰嗦。
        """
        out = []
        for v in self.values:
            gap = v.face_gap_yuan
            if gap is not None and gap >= threshold_yuan:
                out.append(v)
        return out


# ------------------------------------------------------------------ 估值册

@dataclass
class Valuation:
    """一批真实计价结果，按券名索引后的查询册。"""

    values: dict[str, CouponValue] = field(default_factory=dict)
    unpriced: set[str] = field(default_factory=set)

    def get(self, title: str) -> CouponValue | None:
        return self.values.get(name_key(title))

    def can_price(self, title: str) -> bool:
        return name_key(title) in self.values

    def summarize(self, titles: list[str]) -> GroupSummary:
        """给一组券名出汇总。同名券出现几次就计几次（券包里确实可能有两张）。"""
        values: list[CouponValue] = []
        unpriced: list[str] = []
        for title in titles or []:
            v = self.get(title)
            if v is None:
                unpriced.append(title)
            else:
                values.append(v)
        return GroupSummary(values=values, unpriced=unpriced)

    @property
    def total_fen(self) -> int:
        """所有**已估价**券的合计（分）。不含未估价的，不虚报。"""
        return sum(v.saving_fen for v in self.values.values())

    @property
    def empty(self) -> bool:
        return not self.values


def value_book(deals: list[CouponDeal], wallet: list[Coupon] | None = None) -> Valuation:
    """把 M2 的计价结果按券名映射到券包。

    参数
    ----
    deals
        `pricer.rank_deals()` 的产出，每条都带着**真实**优惠额。
    wallet
        券包里的券（`query-my-coupons` 解析结果）。
        **必须传**：只有券包里的券才需要显示有效期与临期分级，
        门店券列表本身不足以代表"用户手上有什么"。

    只有「券包里存在 且 门店券里也能找到」的券会进 `values`；
    券包里有但门店券里没有的，进 `unpriced`——这类券引擎明确
    说不出它值多少钱，简报里会如实标注，不编数。
    """
    by_name: dict[str, CouponDeal] = {}
    for deal in deals or []:
        key = name_key(deal.title)
        if key and key not in by_name:
            by_name[key] = deal  # 同名取第一条；门店券按 couponId 唯一，重复概率极低

    values: dict[str, CouponValue] = {}
    unpriced: set[str] = set()
    seen: set[str] = set()

    for coupon in wallet or []:
        key = name_key(coupon.title)
        if not key or key in seen:
            continue
        seen.add(key)

        deal = by_name.get(key)
        if deal is None:
            unpriced.add(key)
            continue

        values[key] = CouponValue(
            title=coupon.title,
            saving_fen=deal.saving_fen,
            original_fen=deal.quote.original_fen,
            pay_fen=deal.quote.price_fen,
            store_title=deal.title,
            face_text=coupon.price_text or "",
        )

    return Valuation(values=values, unpriced=unpriced)
