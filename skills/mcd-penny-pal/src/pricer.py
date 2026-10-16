"""麦小省 · M2 比价官

把每个候选方案都丢进 `calculate-price` 真金白银算一遍。
**不估算**——本模块里出现的每一个金额都来自接口返回。

四条实测口径（2026-10-16，改动前请先复验）：

1. ⚠️ **金额单位是「分」。**
   `calculate-price` 返回的 `originalPrice` / `discount` / `price` /
   `productList[].subtotal` 全是**整数分**。
   而菜单（`query-meals`）给的 `currentPrice` 是**元字符串**（"13.5"）。
   两个来源单位差 100 倍，混用会算出离谱的数。
   → 引擎内部一律用「分」运算，只在给人看的时候转元（`money()`）。

2. ⚠️ **券的 couponId / couponCode 不在券包里。**
   `query-my-coupons` 只给标题和有效期；couponId / couponCode 只在
   `query-store-coupons` 里。所以"用这张券能省多少"必须先反查门店券。

3. ⚠️ **券商品不带 couponId 时价格全为 0。**
   实测：「中薯条买一送一」的券商品 9900012387，
   不带券 → 原价/优惠/实付全 0（不是"原价"，是"没有价"）；
   带上 couponId + couponCode → 原价 2700 分、优惠 1350 分、实付 1350 分。
   → 拿 0 当"原价"去算节省额是错的。

4. ⚠️ **券面标注价 ≠ 节省额。**
   实测：「王牌小食拼」券面写着 ¥15.9，真实只省 ¥6.1。
   → 排序和播报一律以 `discount` 为准，永远不要拿券面价当省钱额。

零第三方依赖。所有接口调用都通过注入的 `caller` 完成，
因此离线回放和实时联网走的是同一份代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from models import StoreCoupon
from parser import parse

#: 调用 MCP 工具的函数签名：``caller(tool_name, arguments) -> 原始返回``
Caller = Callable[[str, dict[str, Any]], Any]

# 场景常量：到店自取。实测此场景**不传 beCode**，传了会报错。
ORDER_PICKUP = 1
BE_PICKUP = 1
# 外送 / 得来速（需要 beCode，由 caller 侧补齐）
ORDER_DELIVERY = 2
BE_DELIVERY = 2
BE_DRIVE_THRU = 5


# ------------------------------------------------------------------ 金额口径

def to_yuan(fen: int | float | None) -> float:
    """分 → 元。"""
    return round(float(fen or 0) / 100.0, 2)


def money(fen: int | float | None) -> str:
    """分 → 「¥13.5」。保留一位小数，与 SKILL.md 的金额口径一致。"""
    return f"¥{to_yuan(fen):.1f}"


# ------------------------------------------------------------------ 数据结构

@dataclass
class PriceQuote:
    """一次真实计价的结果。内部字段单位全部是**分**。"""

    original_fen: int = 0
    discount_fen: int = 0
    price_fen: int = 0
    products: list[dict[str, Any]] = field(default_factory=list)
    take_way: list[dict[str, Any]] = field(default_factory=list)

    @property
    def original_text(self) -> str:
        return money(self.original_fen)

    @property
    def saved_text(self) -> str:
        return money(self.discount_fen)

    @property
    def pay_text(self) -> str:
        return money(self.price_fen)

    @property
    def has_discount(self) -> bool:
        return self.discount_fen > 0

    @property
    def take_way_code(self) -> str:
        """下单要用的取餐方式 code（`create-order` 的 `takeWayCode`）。

        取第一个选项（实测是「堂食」eat-in），没有就返回空串。
        """
        return str(self.take_way[0].get("code") or "") if self.take_way else ""

    def __str__(self) -> str:
        return f"原价 {self.original_text} · 优惠 {self.saved_text} · 实付 {self.pay_text}"


@dataclass
class CouponDeal:
    """一张门店券的实测结果：券 + 真实计价。

    `quote.discount_fen` 才是这张券的真实价值，
    **不是**券面标注的"用券价格"。
    """

    coupon: StoreCoupon
    quote: PriceQuote

    @property
    def title(self) -> str:
        return self.coupon.title

    @property
    def saving_fen(self) -> int:
        return self.quote.discount_fen

    @property
    def saving_text(self) -> str:
        return self.quote.saved_text

    @property
    def pay_text(self) -> str:
        return self.quote.pay_text

    @property
    def original_text(self) -> str:
        return self.quote.original_text


# ------------------------------------------------------------------ 计价

def quote(
    caller: Caller,
    *,
    store_code: str,
    items: list[dict[str, Any]],
    order_type: int = ORDER_PICKUP,
    be_type: int = BE_PICKUP,
    be_code: str = "",
    need_tableware: bool = False,
    reservation_date: str = "",
) -> PriceQuote:
    """调 `calculate-price` 真实计价。

    到店自取（beType=1）**不传 beCode**；外送/得来速才传。
    """
    args: dict[str, Any] = {
        "storeCode": store_code,
        "orderType": order_type,
        "beType": be_type,
        "items": items,
        "needTableware": need_tableware,
    }
    if be_code and order_type != ORDER_PICKUP:
        args["beCode"] = be_code
    if reservation_date:
        args["reservationDate"] = reservation_date

    payload = parse("calculate-price", caller("calculate-price", args))
    data = payload.data if isinstance(payload.data, dict) else {}

    return PriceQuote(
        original_fen=int(data.get("originalPrice") or 0),
        discount_fen=int(data.get("discount") or 0),
        price_fen=int(data.get("price") or 0),
        products=[p for p in (data.get("productList") or []) if isinstance(p, dict)],
        take_way=[t for t in (data.get("takeWayList") or []) if isinstance(t, dict)],
    )


def coupon_items(coupon: StoreCoupon, quantity: int = 1) -> list[dict[str, Any]]:
    """按门店券构造计价 items。

    ⚠️ 必须**同时**带 `couponId` 和 `couponCode`。
    只带券商品 code 的话，接口返回的价格全是 0 —— 那不是"免费"，
    是"没认出来这张券"。
    """
    return [{
        "productCode": coupon.product_code,
        "quantity": quantity,
        "couponId": coupon.coupon_id,
        "couponCode": coupon.coupon_code,
    }]


def coupon_deal(
    caller: Caller,
    *,
    store_code: str,
    coupon: StoreCoupon,
    order_type: int = ORDER_PICKUP,
    be_type: int = BE_PICKUP,
    be_code: str = "",
) -> CouponDeal:
    """算一张券的真实价值。"""
    q = quote(
        caller,
        store_code=store_code,
        items=coupon_items(coupon),
        order_type=order_type,
        be_type=be_type,
        be_code=be_code,
    )
    return CouponDeal(coupon=coupon, quote=q)


def rank_deals(
    caller: Caller,
    *,
    store_code: str,
    coupons: list[StoreCoupon],
    order_type: int = ORDER_PICKUP,
    be_type: int = BE_PICKUP,
    be_code: str = "",
) -> list[CouponDeal]:
    """逐张券真实计价，按**真实优惠额**从高到低排序。

    单张券计价失败不影响其余 —— 券本身可能已失效或商品下架，
    静默跳过，但把失败的记在 `CouponDeal` 之外由调用方决定要不要提。
    """
    deals: list[CouponDeal] = []
    for c in coupons:
        if not (c.product_code and c.coupon_id and c.coupon_code):
            continue  # 字段不全，算了也是 0
        try:
            deals.append(coupon_deal(
                caller, store_code=store_code, coupon=c,
                order_type=order_type, be_type=be_type, be_code=be_code,
            ))
        except Exception:  # noqa: BLE001
            continue
    deals.sort(key=lambda d: d.saving_fen, reverse=True)
    return deals


def total_saving(deals: list[CouponDeal]) -> int:
    """多张券的优惠合计（分）。

    口径：各券单张计价后相加。**这不是"一次下单能省这么多"**——
    多数券不能叠加使用。要算叠加后的真实效果，得把所有券塞进同一次
    `calculate-price` 调用，让接口自己算。这个函数只回答"这些券各自值多少"。
    """
    return sum(d.saving_fen for d in deals)


def best_of(
    caller: Caller,
    *,
    store_code: str,
    plans: dict[str, list[dict[str, Any]]],
    order_type: int = ORDER_PICKUP,
    be_type: int = BE_PICKUP,
    be_code: str = "",
) -> list[tuple[str, PriceQuote]]:
    """对多个候选方案逐一真实计价，按**实付从低到高**排序。

    这是铁律 4 的落点：如果"为凑单加购"的方案实付反而更高，
    排序会把它顶到后面，调用方据此可以直接说破。
    """
    out: list[tuple[str, PriceQuote]] = []
    for label, items in plans.items():
        try:
            out.append((label, quote(
                caller, store_code=store_code, items=items,
                order_type=order_type, be_type=be_type, be_code=be_code,
            )))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda pair: pair[1].price_fen)
    return out
