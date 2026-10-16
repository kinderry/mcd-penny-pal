"""M2 比价官 · 「哪张券最值」报告

两种模式：

    python tools/run_price.py            # 离线：回放 fixtures/demo 的脱敏返回
    MCD_MCP_TOKEN=... python tools/run_price.py --live   # 实时直连

两边走**同一份引擎代码**（`src/pricer.py`），只是 caller 换了实现。
所以离线看到的结果和联网时一致。

⚠️ 只调只读接口。**不下单。**

用法：
    python tools/run_price.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills" / "mcd-penny-pal" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "tools"))

import parser as P  # noqa: E402
import pricer as PR  # noqa: E402
from fixtures_path import fixture_dir  # noqa: E402
from replay import FixtureCaller  # noqa: E402

RAW = fixture_dir()
STORE = "1450118"  # 上海吴江路餐厅（2026-10-16 实测可用）


def make_caller(live: bool):
    if not live:
        return FixtureCaller(RAW)

    from mcp_client import McDMcpClient

    client = McDMcpClient()
    return lambda tool, args: client.call(tool, args)


def main() -> int:
    live = "--live" in sys.argv
    if live and not os.environ.get("MCD_MCP_TOKEN"):
        print("--live 模式需要环境变量 MCD_MCP_TOKEN", file=sys.stderr)
        return 2

    caller = make_caller(live)

    # 门店券 —— 只有这里能拿到 couponId / couponCode
    store_raw = caller("query-store-coupons", {
        "storeCode": STORE, "orderType": PR.ORDER_PICKUP, "beType": PR.BE_PICKUP,
    })
    coupons = P.to_store_coupons(P.parse("query-store-coupons", store_raw))

    print("=" * 72)
    print(f"数据来源：{'实时 MCP' if live else '离线 fixtures（真实返回原样落盘）'}")
    print(f"门店：{STORE} · 场景：到店自取")
    print("=" * 72)
    print()

    if not coupons:
        print("该门店当前没有可用券。")
        return 0

    deals = PR.rank_deals(caller, store_code=STORE, coupons=coupons)

    if not deals:
        print("抓到了券，但逐张计价都没算出来（可能已失效或商品下架）。")
        return 0

    top = deals[0]
    mood = "【得意】" if top.saving_fen >= 1000 else "【平常】"
    total = PR.total_saving(deals)

    print(f"{mood}这几张券都能用，真金白银能省 {PR.money(total)}。")
    print()
    print("按**真实优惠**排（不是按券面价）：")
    print()
    for i, d in enumerate(deals, 1):
        print(f"  {i}. {d.title:<16} 省 {d.saving_text:<7} "
              f"原价 {d.original_text} → 实付 {d.pay_text}")

    print()
    print("提示一句：上面「省」的金额全部来自 calculate-price 当场试算，")
    print("不是券面标的那个数。券面上写的是**用券后的价格**，")
    print("跟「省下多少」是两回事 —— 实测就有券面写 ¥15.9、实际只省 ¥6.1 的。")
    print()
    print("-" * 72)
    print(f"合计「各自价值」{PR.money(total)}。注意：这是逐张单独算的加总，")
    print("多数券不能叠加，实际一次下单省不到这个数。")
    print("要看真实的一单，得把所有券塞进同一次 calculate-price 让接口自己算。")

    if isinstance(caller, FixtureCaller):
        print()
        print(f"离线回放命中：{caller.hit_rate}")
        if caller.missed:
            for tool, args in caller.missed:
                print(f"  未命中：{tool} {args}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
