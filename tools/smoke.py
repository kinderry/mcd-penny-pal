"""麦小省 · MCP 连通性冒烟测试

用途：
  1. 验证 MCP Token 是否可用
  2. 拉取工具清单
  3. 调用只读工具，把真实返回原样落盘到 fixtures/raw/

安全约束：本脚本只调用只读工具，不包含 create-order / cancel-order
等任何会产生实际后果的操作。

用法：
    set MCD_MCP_TOKEN=你的token      (Windows cmd)
    $env:MCD_MCP_TOKEN="你的token"   (PowerShell)
    python tools/smoke.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcd-penny-pal" / "src"))

from mcp_client import McDMcpClient, McpError  # noqa: E402

RAW_DIR = ROOT / "fixtures" / "raw"

# 只读工具清单：安全，不产生任何订单或金额变动
READ_ONLY_PROBES: list[tuple[str, dict]] = [
    ("now-time-info", {}),
    ("query-my-account", {}),
    ("query-my-coupons", {"page": 1, "pageSize": 20}),
    ("available-coupons", {}),
    ("campaign-calendar", {}),
    ("list-nutrition-foods", {}),
    ("query-lottery-info", {}),
    ("mall-points-products", {}),
    ("order-list", {}),
    ("delivery-query-addresses", {}),
]

# 需要参数的只读工具。参数名以 tools/list 的 inputSchema 为准，不靠猜：
# query-nearby-stores 必传 beType(1到店/5得来速) + searchType(1收藏店/2按位置)
STORE_PROBES: list[tuple[str, dict]] = [
    ("query-nearby-stores", {"beType": 1, "searchType": 1}),
    ("query-nearby-stores", {"beType": 1, "searchType": 2, "city": "上海", "keyword": "南京西路"}),
]


def main() -> int:
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if not token:
        print("[X] 未设置 MCD_MCP_TOKEN")
        print("    PowerShell: $env:MCD_MCP_TOKEN=\"你的token\"")
        print("    cmd:        set MCD_MCP_TOKEN=你的token")
        return 2

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    try:
        client = McDMcpClient(token)
    except McpError as exc:
        print(f"[X] {exc}")
        return 2

    print(f"[1/4] 握手 initialize ... (endpoint={client.endpoint})")
    try:
        info = client.initialize()
    except McpError as exc:
        print(f"[X] 握手失败：{exc}")
        return 1
    server = (info or {}).get("serverInfo", {})
    print(f"      server: {server.get('name')} {server.get('version')}")
    print(f"      protocol: {(info or {}).get('protocolVersion')}")

    print("[2/4] 拉取工具清单 tools/list ...")
    try:
        tools = client.list_tools()
    except McpError as exc:
        print(f"[X] 拉取工具清单失败：{exc}")
        return 1
    print(f"      共 {len(tools)} 个工具")
    (RAW_DIR / "_tools_list.json").write_text(
        json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("[3/4] 探测只读工具 ...")
    ok, fail = 0, 0
    for name, args in READ_ONLY_PROBES:
        try:
            payload = client.call(name, args)
            out = RAW_DIR / f"{name}.json"
            out.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            size = out.stat().st_size
            shape = type(payload).__name__
            if isinstance(payload, dict):
                shape += f" keys={list(payload.keys())[:8]}"
            print(f"      [OK]   {name:28s} {size:>7d}B  {shape}")
            ok += 1
        except McpError as exc:
            print(f"      [FAIL] {name:28s} {exc}")
            fail += 1

    print("[4/4] 探测门店维度（需 beType + searchType）...")
    for i, (name, args) in enumerate(STORE_PROBES):
        try:
            payload = client.call(name, args)
            out = RAW_DIR / f"{name}__probe{i}.json"
            out.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"      [OK]   {name:28s} {out.stat().st_size:>7d}B  {args}")
            ok += 1
        except McpError as exc:
            print(f"      [FAIL] {name:28s} {exc}")
            fail += 1

    log_path = client.dump_log(str(RAW_DIR / "_call_log.json"))
    print()
    print(f"成功 {ok} / 失败 {fail}")
    print(f"原始响应已落盘：{RAW_DIR}")
    print(f"调用日志：{log_path}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
