"""M2 比价官 · 接口探测

摸清 query-meals / calculate-price 的**真实返回结构**，落盘到 fixtures/raw/。
写引擎前必须先看真实数据，不猜字段。

⚠️ 只调读性质的接口。**不调 create-order，不下任何单。**

用法：
    MCD_MCP_TOKEN=... python tools/probe_m2.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcd-penny-pal" / "src"))

from mcp_client import McDMcpClient  # noqa: E402

OUT = ROOT / "fixtures" / "raw"

# 2026-10-16 实测可用的门店（上海吴江路餐厅），到店自取场景
STORE = os.environ.get("MCD_PROBE_STORE", "1450118")
ORDER_TYPE = 1  # 1=到店
BE_TYPE = 1     # 1=到店自取（此场景不传 beCode）


def dump(name: str, payload) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    print(f"  [OK] {name:38s} {len(text):>8d} 字符  -> {path.name}")
    return path


def main() -> int:
    client = McDMcpClient()

    print("=" * 70)
    print("M2 探测 · 只读接口，绝不下单")
    print("=" * 70)

    print("\n[1/3] query-meals（到店自取菜单）")
    meals = client.call("query-meals", {
        "storeCode": STORE, "orderType": ORDER_TYPE, "beType": BE_TYPE,
    })
    dump("query-meals__pickup", meals)

    print("\n[2/3] 从菜单里挑几个商品，做 calculate-price")
    from parser import extract_json  # noqa: E402

    body = extract_json(meals) if isinstance(meals, str) else meals
    data = body.get("data") if isinstance(body, dict) else None

    picks: list[tuple[str, str]] = []  # (名称, code)
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                code = row.get("code") or row.get("productCode")
                name = row.get("name") or row.get("productName")
                if code and name:
                    picks.append((str(name), str(code)))
    elif isinstance(data, dict):
        for key in ("products", "list", "mealList", "items"):
            rows = data.get(key)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        code = row.get("code") or row.get("productCode")
                        name = row.get("name") or row.get("productName")
                        if code and name:
                            picks.append((str(name), str(code)))
                if picks:
                    break

    print(f"      菜单里找到 {len(picks)} 个带 code 的商品")
    for name, code in picks[:8]:
        print(f"        {name:<26} {code}")

    if not picks:
        print("\n  !! 没从菜单里解析出 code，无法继续计价探测")
        print("     先看 query-meals__pickup.json 的真实结构再调整解析")
        return 1

    # 取前 2 个商品各 1 份，做一次无券基准计价
    items = [{"productCode": picks[0][1], "quantity": 1}]
    if len(picks) > 1:
        items.append({"productCode": picks[1][1], "quantity": 1})

    print(f"\n[3/3] calculate-price（无券基准：{picks[0][0]}"
          + (f" + {picks[1][0]}" if len(items) > 1 else "") + "）")
    try:
        price = client.call("calculate-price", {
            "storeCode": STORE, "orderType": ORDER_TYPE, "beType": BE_TYPE,
            "items": items, "needTableware": False,
        })
        dump("calculate-price__baseline", price)
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] calculate-price: {exc}")
        return 1

    print("\n完成。真实返回已落盘 fixtures/raw/，下一步照真实字段写引擎。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
