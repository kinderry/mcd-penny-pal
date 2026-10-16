"""M2 比价官自测。

两层：

1. **边界场景**（构造数据）：单位换算、金额格式、券 items 构造。
2. **端到端**（真实 fixture）：逐张券真实计价，验排序与金额。

用法：
    python tools/test_price.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcd-penny-pal" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import parser as P  # noqa: E402
import pricer as PR  # noqa: E402
from fixtures_path import fixture_dir  # noqa: E402
from replay import FixtureCaller  # noqa: E402

RAW = fixture_dir()
STORE = "1450118"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<36} {detail}")


# ------------------------------------------------------------------ 边界

print("=" * 72)
print("Part 1 · 边界场景（构造数据）")
print("=" * 72)

check("分→元换算", PR.to_yuan(1350) == 13.5, f"1350 分 -> {PR.to_yuan(1350)} 元")
check("元→金额文本", PR.money(2700) == "¥27.0", PR.money(2700))
check("金额保留一位小数", PR.money(2870) == "¥28.7", PR.money(2870))
check("零金额不炸", PR.money(0) == "¥0.0" and PR.money(None) == "¥0.0",
      f"{PR.money(0)} / {PR.money(None)}")

q = PR.PriceQuote(take_way=[{"code": "eat-in", "title": "堂食"},
                            {"code": "take-in-store", "title": "外带"}])
check("取餐方式取第一个", q.take_way_code == "eat-in", q.take_way_code)
check("无取餐方式不炸", PR.PriceQuote().take_way_code == "", "''")

# 券 items 必须同时带两个字段 —— 少一个价格就是 0
coupon_stub = P.to_store_coupons(P.parse(
    "query-store-coupons",
    FixtureCaller(RAW)("query-store-coupons", {
        "storeCode": STORE, "orderType": 1, "beType": 1}),
))[0]
items = PR.coupon_items(coupon_stub)
check("计价 items 带 couponId", bool(items[0].get("couponId")), items[0].get("couponId", "")[:16])
check("计价 items 带 couponCode", bool(items[0].get("couponCode")), items[0].get("couponCode"))
check("计价 items 用券商品编码", items[0]["productCode"] == coupon_stub.product_code,
      f"{items[0]['productCode']}")


# ------------------------------------------------------------------ 端到端

print()
print("=" * 72)
print("Part 2 · 端到端（真实 fixture，逐张券计价）")
print("=" * 72)

caller = FixtureCaller(RAW)
store_raw = caller("query-store-coupons", {
    "storeCode": STORE, "orderType": PR.ORDER_PICKUP, "beType": PR.BE_PICKUP})
coupons = P.to_store_coupons(P.parse("query-store-coupons", store_raw))

check("门店券解析成功", len(coupons) == 3, f"{len(coupons)} 张")
check("券带 couponId 和 couponCode",
      all(c.coupon_id and c.coupon_code for c in coupons), "")
check("券带到期时刻", bool(coupons[0].expire_at), coupons[0].expire_at)

deals = PR.rank_deals(caller, store_code=STORE, coupons=coupons)
check("三张券全部计价成功", len(deals) == 3, f"{len(deals)} 张")

by_title = {d.title: d for d in deals}

# 中薯条买一送一：两份 ¥13.5 的中薯条，花一份的钱
d = by_title.get("中薯条买一送一")
if d:
    check("买一送一原价 = 两份", d.quote.original_fen == 2700, d.quote.original_text)
    check("买一送一省一份", d.quote.discount_fen == 1350, d.saving_text)
    check("买一送一实付", d.quote.price_fen == 1350, d.pay_text)
else:
    check("买一送一券存在", False, "未找到")

# ⚠️ 最关键的一条：券面标注价 ≠ 真实节省额
d = by_title.get("王牌小食拼")
if d:
    check("王牌小食拼真实只省 ¥6.1（非券面 ¥15.9）",
          d.saving_fen == 610, f"真实 {d.saving_text}")
else:
    check("王牌小食拼券存在", False, "未找到")

# 排序依据必须是真实优惠额
order = [d.title for d in deals]
check("按真实优惠降序排列",
      order == ["中薯条买一送一", "9.9元中杯冰美式", "王牌小食拼"],
      " > ".join(f"{d.title}({d.saving_text})" for d in deals))

check("合计优惠", PR.total_saving(deals) == 2870, PR.money(PR.total_saving(deals)))

# 到店自取不传 beCode（传了会报错）
calc_calls = [a for t, a in caller.calls if t == "calculate-price"]
check("到店自取不传 beCode",
      bool(calc_calls) and all("beCode" not in a for a in calc_calls),
      f"{len(calc_calls)} 次计价调用")

# 券商品原价 == 等量普通商品价（用菜单里的中薯条验证口径一致）
plain = PR.quote(caller, store_code=STORE, items=[{"productCode": "4810", "quantity": 2}])
check("券商品原价 = 两份普通中薯条", plain.original_fen == 2700, plain.original_text)
check("不用券就没有优惠", plain.discount_fen == 0 and plain.price_fen == 2700,
      f"{plain.saved_text} / {plain.pay_text}")

check("离线回放全命中", not caller.missed, caller.hit_rate)


# ------------------------------------------------------------------ 汇总

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for name in FAIL:
    print(f"  失败：{name}")
print("=" * 72)

raise SystemExit(1 if FAIL else 0)
