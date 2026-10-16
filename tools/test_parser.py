"""解析层自测 —— 拿 fixtures/raw 里的**真实返回**逐个跑解析。

这是本项目的核心可信度来源：解析器不是照着文档猜的，
是照着 2026-10-16 对 mcp.mcd.cn 的实测返回写的，
所以也必须用同一批真实返回证明它跑得通。

用法：
    python tools/test_parser.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcd-penny-pal" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import coupons as C  # noqa: E402
import parser as P  # noqa: E402
from fixtures_path import fixture_dir  # noqa: E402

RAW = fixture_dir()

# 每个 fixture 期望被识别成什么形态、能抽出多少条记录。
#
# 形态说明（实测，非推测）：
#   json     — 带 `## Original Response` 标记，标记后是纯 JSON
#   mixed    — JSON 与说明文字混排。注意 query-nearby-stores /
#              delivery-query-addresses **从不**带该标记，即使成功也是 mixed
#   markdown — 完全没有 JSON，只有中文列表
#   table    — data 字段是自描述紧凑表格字符串
EXPECT: dict[str, tuple[str, str]] = {
    "now-time-info.json": ("json", "datetime"),
    "query-my-account.json": ("json", "account"),
    "query-lottery-info.json": ("json", "lottery"),
    "mall-points-products.json": ("json", "mall"),
    "order-list.json": ("json", "data"),
    "list-nutrition-foods.json": ("table", "nutrition"),
    # 下面两个没有 Original Response 标记，成功时也是 mixed
    "query-nearby-stores__probe1.json": ("mixed", "stores"),
    "query-nearby-stores__probe0.json": ("mixed", "data"),
    "query-nearby-stores.json": ("mixed", "data"),
    "delivery-query-addresses.json": ("mixed", "data"),
    # 纯 Markdown
    "available-coupons.json": ("markdown", "coupons"),
    "query-my-coupons.json": ("markdown", "coupons"),
    "campaign-calendar.json": ("markdown", "campaigns"),
}


def extract(payload: P.Payload, kind: str):
    if kind == "coupons":
        return P.to_coupons(payload, pool="未知")
    if kind == "campaigns":
        return P.to_campaigns(payload)
    if kind == "stores":
        return P.to_stores(payload)
    if kind == "nutrition":
        return P.to_nutrition(payload)
    if kind == "mall":
        return P.to_mall_products(payload)
    if kind == "account":
        return P.to_account(payload)
    if kind == "lottery":
        return P.to_lottery(payload)
    if kind == "datetime":
        return payload.data
    return payload.data


def main() -> int:
    files = sorted(p for p in RAW.glob("*.json") if not p.name.startswith("_"))
    if not files:
        print("找不到 fixtures/raw/*.json，请先跑 tools/smoke.py")
        return 1

    print(f"{'fixture':34s} {'形态':10s} {'code':>7s} {'抽取结果'}")
    print("-" * 100)

    problems: list[str] = []
    total_items = 0

    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        payload = P.parse(path.stem, raw)
        kind, what = EXPECT.get(path.name, ("?", "data"))
        got = extract(payload, what)

        if isinstance(got, list):
            detail = f"list[{len(got)}]"
            total_items += len(got)
            if got:
                first = got[0]
                sample = getattr(first, "title", None) or getattr(first, "name", None) \
                    or getattr(first, "product_name", None) or getattr(first, "store_name", None)
                if sample:
                    detail += f"  首条={sample}"
        elif got is None:
            detail = "None"
        elif isinstance(got, dict):
            detail = f"dict({len(got)} 字段)"
        else:
            detail = str(got)[:40]

        code = payload.code if payload.code is not None else "-"
        print(f"{path.name:34s} {payload.kind:10s} {str(code):>7s} {detail}")

        # 形态断言
        if kind != "?" and payload.kind != kind:
            problems.append(f"{path.name}: 期望形态 {kind}，实际 {payload.kind}")

    print("-" * 100)
    print(f"共 {len(files)} 个 fixture，抽出记录合计 {total_items} 条")

    # 关键断言：成功返回必须能抽出东西，失败返回必须被识别为失败
    checks: list[tuple[str, bool, str]] = []
    try:
        acc = P.to_account(P.parse("q", json.loads((RAW / "query-my-account.json").read_text("utf-8"))))
        checks.append(("积分账户解析", acc is not None and acc.available == 9.9,
                       f"available={acc.available if acc else None}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("积分账户解析", False, repr(exc)))

    try:
        cps = P.to_coupons(P.parse("q", json.loads((RAW / "available-coupons.json").read_text("utf-8"))), "麦麦省")
        checks.append(("麦麦省券池非空", len(cps) > 0, f"{len(cps)} 张，首张={cps[0].title if cps else '-'}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("麦麦省券池非空", False, repr(exc)))

    try:
        nut = P.to_nutrition(P.parse("q", json.loads((RAW / "list-nutrition-foods.json").read_text("utf-8"))))
        checks.append(("营养紧凑表格解析", len(nut) >= 20, f"{len(nut)} 条，首条={nut[0].product_name if nut else '-'}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("营养紧凑表格解析", False, repr(exc)))

    try:
        bad = P.parse("q", json.loads((RAW / "query-nearby-stores.json").read_text("utf-8")))
        checks.append(("混合型失败识别", (not bad.ok) and bad.code == 400, f"ok={bad.ok} code={bad.code}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("混合型失败识别", False, repr(exc)))

    # 券包：`## 券名` 作条目标题 + `- **键**: 值` 作字段，是第三种 Markdown 排版
    try:
        wallet_payload = P.parse("q", json.loads((RAW / "query-my-coupons.json").read_text("utf-8")))
        wallet = P.to_coupons(wallet_payload, "我的券包")
        checks.append(("券包按二级标题拆条目", len(wallet) == 6,
                       f"{len(wallet)} 张，首张={wallet[0].title if wallet else '-'}"))
        checks.append(("券包总数声明解析", wallet_payload.total == 6,
                       f"total={wallet_payload.total}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("券包按二级标题拆条目", False, repr(exc)))
        checks.append(("券包总数声明解析", False, repr(exc)))

    # 临期预警依赖**真实有效期**，不是靠猜
    try:
        first = wallet[0]
        checks.append(("有效期抽取", first.expire_at == "2026-10-16 23:59",
                       f"{first.title} -> expire_at={first.expire_at!r}"))
        ref = datetime(2026, 10, 16, 10, 0)
        checks.append(("剩余天数计算", first.days_left(now=ref) == 0,
                       f"days_left={first.days_left(now=ref)} -> 今日到期"))
        checks.append(("临期预警触发", first.expiring_soon is True and "今日到期" in first.tags,
                       f"expiring_soon={first.expiring_soon} tags={first.tags}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("有效期抽取", False, repr(exc)))
        checks.append(("剩余天数计算", False, repr(exc)))
        checks.append(("临期预警触发", False, repr(exc)))

    # ⚠️ 回归断言：门店券返回**不带** `## Original Response` 标记，
    # 前言里的 `## Response Structure` / `## 字段说明` 等文档标题
    # 曾被误当成券名 —— 结果是真券一条没出来、垃圾条目混进券池。
    # 修法是"结构化数据优先于 markdown 条目"，这里锁住它。
    try:
        store_payload = P.parse("q", json.loads((RAW / "query-store-coupons.json").read_text("utf-8")))
        store_list = P.to_coupons(store_payload, "门店专属")
        noise = [c.title for c in store_list
                 if "{" in c.title or c.title.strip() in
                 ("Response Structure", "字段说明", "输出格式")]
        checks.append(("门店券不被字段说明污染", len(store_list) == 3 and not noise,
                       f"{len(store_list)} 张，噪声={noise or '无'}"))

        # ⚠️ 回归断言：门店券的到期字段是 `tradeDateTime`，不是 expireTime。
        # 漏掉它 → 门店券转出来没有有效期 → 与券包里同一张券**去重匹配不上**，
        # 于是「中薯条买一送一」被算成两张，券包总数从 6 涨到 9。
        store_c = P.to_store_coupons(store_payload)
        via_coupon = [c for c in store_list if c.title == "中薯条买一送一"][0]
        wallet_same = [c for c in wallet if c.title == "中薯条买一送一"][0]
        checks.append(("门店券有效期可解析", via_coupon.expire_at == "2026-10-16 23:59",
                       f"expire_at={via_coupon.expire_at!r}"))
        checks.append(("门店券与券包同券去重键一致",
                       C._dedup_key(via_coupon) == C._dedup_key(wallet_same),
                       f"门店={C._dedup_key(via_coupon)!r}"))
        checks.append(("门店券 couponId/couponCode 可取",
                       bool(store_c and store_c[0].coupon_id and store_c[0].coupon_code),
                       f"{store_c[0].title if store_c else '-'}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("门店券不被字段说明污染", False, repr(exc)))
        checks.append(("门店券有效期可解析", False, repr(exc)))
        checks.append(("门店券与券包同券去重键一致", False, repr(exc)))
        checks.append(("门店券 couponId/couponCode 可取", False, repr(exc)))

    loot = P.to_lottery(P.parse("q", json.loads((RAW / "query-lottery-info.json").read_text("utf-8"))))
    checks.append(("抽奖资格读 drawDecision", loot is not None and loot.eligible is False,
                   f"eligible={loot.eligible if loot else None} drawPoint={loot.draw_point if loot else None}"))

    print()
    print("关键断言：")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:22s} {detail}")
        if not ok:
            problems.append(f"断言失败：{name} — {detail}")

    print()
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for p in problems:
            print("  -", p)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
