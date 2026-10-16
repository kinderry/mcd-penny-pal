"""M2 探测 · 用门店券真实计价

2026-10-16 实测确认的链路（关键，写引擎前必须记住）：

    query-store-coupons  →  拿到 couponId / couponCode / 券商品 productCode
    calculate-price      →  items 里带上 couponId + couponCode，才算出优惠

⚠️ 两个反直觉的点：
  1. **券的 couponId / couponCode 不在券包里**（query-my-coupons 只给标题和有效期），
     只在 query-store-coupons 里。所以"用券省钱"这条线必须先查门店券。
  2. **券商品不带 couponId 时价格全为 0** —— 不是"原价"，是"没价"。
     想拿原价做对比，得用菜单里的普通商品（如中薯条 4810），或读 originalSubtotal。

⚠️ 只调只读接口，不下单。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcd-penny-pal" / "src"))

from mcp_client import McDMcpClient  # noqa: E402
from parser import extract_json  # noqa: E402

RAW = ROOT / "fixtures" / "raw"
STORE = "1450118"


def main() -> int:
    client = McDMcpClient()
    base = {"storeCode": STORE, "orderType": 1, "beType": 1, "needTableware": False}

    # ⚠️ fixtures 里存的是 **JSON 编码后的字符串**（MCP 返回就是 str，
    #    落盘时 json.dumps 了一层）。所以必须先 json.loads 还原成原文，
    #    少了这一层，extract_json 拿到的就是带引号转义的整段字面量，
    #    会解析出错的东西（实测会得到一个 list）。
    raw_text = (RAW / "query-store-coupons.json").read_text(encoding="utf-8")
    store = extract_json(json.loads(raw_text))
    coupons = store.get("data") or []

    print("=" * 74)
    print("M2 探测 · 门店券真实计价（只读，不下单）")
    print("=" * 74)

    rows = []
    for cp in coupons:
        prod = (cp.get("products") or [{}])[0]
        pcode = prod.get("productCode")
        if not pcode:
            continue
        tag = f"coupon__{cp['couponCode']}"
        try:
            raw = client.call("calculate-price", dict(base, items=[{
                "productCode": pcode,
                "quantity": 1,
                "couponId": cp.get("couponId"),
                "couponCode": cp.get("couponCode"),
            }]))
            (RAW / f"calculate-price__{tag}.json").write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

            d = extract_json(raw).get("data") or {}
            orig, disc, pay = d.get("originalPrice"), d.get("discount"), d.get("price")
            rows.append((cp["title"], orig, disc, pay, cp["couponCode"]))
            print(f"\n### {cp['title']}")
            print(f"    原价 {orig} 分 (¥{orig/100:.2f})  "
                  f"优惠 {disc} 分 (¥{disc/100:.2f})  "
                  f"实付 {pay} 分 (¥{pay/100:.2f})")
            for p in (d.get("productList") or []):
                print(f"    -> {p.get('productName')} x{p.get('quantity')} "
                      f"原价小计 {p.get('originalSubtotal')} 实付小计 {p.get('subtotal')}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n### {cp['title']}  失败: {exc}")

    print()
    print("=" * 74)
    print(f"{'券名':<22}{'原价':>9}{'优惠':>9}{'实付':>9}")
    print("-" * 74)
    for title, orig, disc, pay, _ in rows:
        print(f"{title:<22}{(orig or 0)/100:>8.2f}元{(disc or 0)/100:>8.2f}元"
              f"{(pay or 0)/100:>8.2f}元")
    total_disc = sum((r[2] or 0) for r in rows)
    print("-" * 74)
    print(f"{'合计可省':<22}{'':>9}{total_disc/100:>8.2f}元")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
