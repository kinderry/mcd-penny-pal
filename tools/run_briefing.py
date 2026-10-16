"""生成「今日省钱简报」。

两种模式：

    python tools/run_briefing.py            # 离线：读 fixtures/demo 里的脱敏返回
    python tools/run_briefing.py --live     # 实时：直连 mcp.mcd.cn（需 MCD_MCP_TOKEN）
    python tools/run_briefing.py --raw      # 离线但读 fixtures/raw 的原始返回

离线模式是刻意保留的——评委和路人不需要任何 Token 就能自己跑一遍，
看到的结果和联网时**完全一致**，因为 fixtures 就是真实返回的原样落盘
（demo 版只把账号 ID、收货地址、手机号换成了占位值，业务字段一字未改）。
这也是榜单头部项目的做法。

**链路**：四路券池 → 券包体检（M1） → 门店券真实计价（M2）
        → 券价值核算 → 简报渲染。
简报里出现的每一个金额，都能追到某一次 `calculate-price` 返回。

**本脚本只调只读工具。** 不领券、不下单。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills" / "mcd-penny-pal" / "src"
sys.path.insert(0, str(SRC))

import briefing as B  # noqa: E402
import coupons as C  # noqa: E402
import parser as P  # noqa: E402
import pricer as PR  # noqa: E402
import valuation as V  # noqa: E402
from fixtures_path import fixture_dir, label as fixtures_label  # noqa: E402
from replay import FixtureCaller  # noqa: E402

RAW = fixture_dir()


def _read(name: str):
    path = RAW / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 数据采集

def collect_offline() -> dict:
    """从 fixtures 读四路券池 + 积分账户 + 门店券，并回放真实计价。"""
    mine_p = P.parse("query-my-coupons", _read("query-my-coupons"))
    claim_p = P.parse("available-coupons", _read("available-coupons"))
    store_p = P.parse("query-store-coupons", _read("query-store-coupons"))
    acct_p = P.parse("query-my-account", _read("query-my-account"))
    stores_p = P.parse("query-nearby-stores", _read("query-nearby-stores__probe1"))

    mine = P.to_coupons(mine_p, C.POOL_MINE)
    store_coupons = P.to_store_coupons(store_p)
    stores = P.to_stores(stores_p)
    store_code = stores[0].code if stores else ""

    # 用真实落盘的返回回放计价，走的是和联网完全相同的引擎代码
    caller = FixtureCaller(RAW)
    deals = PR.rank_deals(caller, store_code=store_code, coupons=store_coupons)

    return {
        "mine": mine,
        "claimable": P.to_coupons(claim_p, C.POOL_CLAIMABLE),
        "store": P.to_coupons(store_p, C.POOL_STORE),
        "store_coupons": store_coupons,
        "account": P.to_account(acct_p),
        "store_code": store_code,
        "store_name": stores[0].name if stores else "",
        "deals": deals,
        "caller": caller,
    }


def collect_live() -> dict:
    """直连 MCP 实时拉取，并真实计价。只读工具，绝不下单。"""
    from mcp_client import McDMcpClient

    client = McDMcpClient()

    account = client.call("query-my-account", {})
    mine_raw = client.call("query-my-coupons", {})
    claim_raw = client.call("available-coupons", {})

    mine = P.to_coupons(P.parse("query-my-coupons", mine_raw), C.POOL_MINE)

    # 门店：先试收藏店，不给就退回按城市+关键字搜
    store_code, store_name, store_coupons = "", "", []
    for args in (
        {"beType": PR.BE_PICKUP, "searchType": 1},
        {"beType": PR.BE_PICKUP, "searchType": 2, "city": "上海", "keyword": "南京西路"},
    ):
        try:
            nearby = client.call("query-nearby-stores", args)
        except Exception:  # noqa: BLE001
            continue
        stores = P.to_stores(P.parse("query-nearby-stores", nearby))
        if stores:
            store_code, store_name = stores[0].code, stores[0].name
            break

    if store_code:
        try:
            store_raw = client.call(
                "query-store-coupons",
                {"storeCode": store_code, "orderType": PR.ORDER_PICKUP, "beType": PR.BE_PICKUP},
            )
            store_coupons = P.to_store_coupons(P.parse("query-store-coupons", store_raw))
        except Exception:  # noqa: BLE001
            store_coupons = []

    deals = PR.rank_deals(
        client.call, store_code=store_code, coupons=store_coupons
    ) if store_code else []

    return {
        "mine": mine,
        "claimable": P.to_coupons(P.parse("available-coupons", claim_raw), C.POOL_CLAIMABLE),
        "store": [c for c in (store_coupons or [])],
        "store_coupons": store_coupons,
        "account": P.to_account(P.parse("query-my-account", account)),
        "store_code": store_code,
        "store_name": store_name,
        "deals": deals,
        "caller": None,
    }


# ------------------------------------------------------------------ 主流程

def main() -> int:
    live = "--live" in sys.argv
    if live and not os.environ.get("MCD_MCP_TOKEN"):
        print("--live 模式需要环境变量 MCD_MCP_TOKEN", file=sys.stderr)
        return 2

    data = collect_live() if live else collect_offline()

    d = C.digest(
        mine=data["mine"],
        claimable=data["claimable"],
        store=data["store"],
    )

    # M1 × M2：把真实计价结果按券名映射回券包
    book = V.value_book(data["deals"], data["mine"])

    print("=" * 66)
    print(f"数据来源：{'实时 MCP' if live else fixtures_label()}")
    if data["store_name"]:
        print(f"门店：{data['store_name']}（{data['store_code']}）")
    print("=" * 66)
    print()
    print(B.build_briefing(d, data["account"], valuation=book))

    # 附一份明细，便于核对简报里的每句话有没有出处
    print("-" * 66)
    print("券包明细（简报里的每一条都来自这里）")
    print("-" * 66)
    if not d.audits:
        print("  （券包为空）")
    for a in d.audits:
        mark = {"今日到期": "!!", "T-3 内到期": "! ", }.get(a.bucket, "  ")
        print(f"  {mark} {a.title:<22} {a.bucket:<10} {a.expiry_label}")
        v = book.get(a.title)
        if v is not None:
            print(f"       · 实测：原价 {v.original_text} → 实付 {v.pay_text} "
                  f"（省 {v.saving_text}）")
            if v.face_gap_yuan:
                print(f"       · 券面标注 {v.face_text or '—'} "
                      f"（券面比实省多 ¥{v.face_gap_yuan:g}）")
        for n in a.notes:
            print(f"       · {n}")

    print()
    print(f"券池来源：我的券包 {len(data['mine'])} 张 / "
          f"麦麦省可领 {len(data['claimable'])} 张 / "
          f"门店专属 {len(data['store'])} 张")
    print(f"合并去重后：{d.total} 张；临期待处理：{len(d.urgent)} 张")

    print()
    print("-" * 66)
    print("券价值核算（M2 真实计价，单位：分 → 元）")
    print("-" * 66)
    if not data["store_coupons"]:
        print("  （没拿到门店券，无法核价 —— 简报已退回券面标注口径）")
    for deal in data["deals"]:
        print(f"  {deal.title:<22} 省 {deal.saving_text:<8} "
              f"（原价 {deal.original_text} → 实付 {deal.pay_text}）")
    if data["deals"]:
        print(f"  可核价券合计：{PR.money(PR.total_saving(data['deals']))}")
    if book.unpriced:
        # 券包里有、门店券里没有 —— 引擎明确说不出它值多少，不猜
        names = [c.title for c in data["mine"] if C.name_key(c.title) in book.unpriced]
        print(f"  无法核价（门店券里没有对应条目）：{'、'.join(dict.fromkeys(names))}")

    if not live:
        print()
        print(f"离线回放命中率：{data['caller'].hit_rate}")
        if data["caller"].missed:
            for tool, args in data["caller"].missed:
                print(f"  未命中：{tool}({args})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
