"""把 `fixtures/raw/` 里的真实返回脱敏成可公开的 `fixtures/demo/`。

**为什么要做这一步**

`fixtures/raw/` 是 2026-10-16 对 mcp.mcd.cn 的真实返回原样落盘，里面必然带着
账号 ID、收货地址、联系人、手机号。它已经被 `.gitignore` 排除，但公开 demo
又需要一份能跑通的数据 —— 于是脱敏一份出去。

**脱敏原则**

1. **只动个人信息字段**，业务字段（券码、门店编码、商品编码、价格、券面文字）
   **一个字符都不改**。否则离线 demo 跑出来的金额就和联网时不一样了，
   "真实返回原样落盘"这个卖点也就没了。
2. **确定性替换**：同一个原值永远映射到同一个假值，跑多少次结果都一样。
3. 门店名、门店地址是**公开经营信息**，不是个人信息，保留原样。
4. 替换后**自检**：全量扫一遍，任何手机号 / 原始值残留直接报错。

用法：

    python tools/make_demo_fixtures.py           # 生成 fixtures/demo/
    python tools/make_demo_fixtures.py --check   # 只自检已生成的 demo，不重写
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "fixtures" / "raw"
DEMO = ROOT / "fixtures" / "demo"

# ---------------------------------------------------------------- 脱敏白名单
# 键名 -> 假值生成函数。只列个人信息字段；
# productCode / couponId / couponCode / storeCode / storeAdress 一律不碰。
PII_FIELDS = ("accountId", "addressId", "contactName", "phone", "fullAddress", "traceId")

# 手机号（含掩码形态），自检用
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d(?:\d{9}|\*{2,6}\d{2,4})(?!\d)")
KV_FIELD_RE = re.compile(r'"%s"\s*:\s*"([^"]*)"')


def fake_value(field: str, value: str, index: int) -> str:
    """按字段类型造一个确定性假值。index 让同字段的不同原值互不撞车。"""
    if field == "accountId":
        return "00000000-0000-0000-0000-000000000001"
    if field == "addressId":
        # 原值 28 位纯数字，假值保持同长度同形态
        return f"1{'0' * 25}{index + 1:02d}"
    if field == "contactName":
        return "示例"
    if field == "phone":
        # 原值是 `1xx****xxxx` 这种掩码形态。假值刻意把号段换成 XX，
        # 一是让人一眼看出这是占位，二是不会和"真手机号"的正则撞车
        return f"1XX****{index + 1:04d}"
    if field == "fullAddress":
        return f"XX省XX市XX区示例路{index + 1}号（脱敏示例地址）"
    if field == "traceId":
        # 调用指纹，无个人信息，但没必要带出去 —— 换成一个稳定的假指纹
        return hashlib.md5(value.encode("utf-8")).hexdigest()
    raise KeyError(field)


def unwrap(text: str) -> tuple[str, bool]:
    """raw 落盘时 json.dumps 过一层，先还原成 MCP 原始字符串。"""
    try:
        val = json.loads(text)
    except Exception:
        return text, False
    if isinstance(val, str):
        return val, True
    return text, False


def collect_mapping() -> dict[tuple[str, str], str]:
    """先扫全量、建立 `(字段, 原值) -> 假值` 映射，保证跨文件一致。"""
    mapping: dict[tuple[str, str], str] = {}
    for field in PII_FIELDS:
        seen: list[str] = []
        pattern = re.compile(KV_FIELD_RE.pattern % field)
        for f in sorted(RAW.glob("*.json")):
            body, _ = unwrap(f.read_text(encoding="utf-8"))
            for m in pattern.finditer(body):
                v = m.group(1)
                if v not in seen:
                    seen.append(v)
        for i, v in enumerate(seen):
            mapping[(field, v)] = fake_value(field, v, i)
    return mapping


def redact(text: str, mapping: dict[tuple[str, str], str]) -> tuple[str, int]:
    """把文本里所有命中的个人信息字段替换掉，返回 (新文本, 替换次数)。"""
    count = 0
    for (field, old), new in sorted(mapping.items(), key=lambda kv: -len(kv[0][1])):
        pattern = re.compile(r'("%s"\s*:\s*")%s(")' % (field, re.escape(old)))
        text, n = pattern.subn(lambda m: m.group(1) + new + m.group(2), text)
        count += n
    return text, count


def verify(mapping: dict[tuple[str, str], str]) -> int:
    """脱敏自检：demo 里不该再出现手机号，也不该出现任何原始 PII 值。"""
    fake_values = set(mapping.values())
    files = sorted(DEMO.glob("*.json"))
    leaked: list[str] = []
    for f in files:
        body, _ = unwrap(f.read_text(encoding="utf-8"))
        for m in PHONE_RE.finditer(body):
            if m.group(0) not in fake_values:
                leaked.append(f"{f.name}: 疑似手机号 {m.group(0)}")
        for field, old in mapping:
            if f'"{field}":"{old}"' in body:
                leaked.append(f"{f.name}: {field} 原始值残留")
    if leaked:
        print("[FAIL] 脱敏自检未通过：")
        for item in leaked:
            print(f"  - {item}")
        return 1
    print(f"自检通过：{len(files)} 个文件，扫不到手机号，也扫不到任何原始 PII 值。")
    return 0


def main() -> int:
    check_only = "--check" in sys.argv

    if not RAW.is_dir():
        print(f"找不到 {RAW}，先跑 tools/smoke.py 落盘真实返回", file=sys.stderr)
        return 2

    mapping = collect_mapping()
    if not mapping:
        print("没有扫到任何个人信息字段，请确认 fixtures/raw 是否完整", file=sys.stderr)
        return 2

    print("脱敏映射（原值 -> 假值）")
    print("-" * 66)
    for (field, old), new in sorted(mapping.items()):
        shown = old if len(old) <= 40 else old[:37] + "…"
        print(f"  {field:<12} {shown:<42} -> {new}")
    print()

    if check_only:
        if not DEMO.is_dir():
            print("fixtures/demo 还没生成", file=sys.stderr)
            return 2
        return verify(mapping)

    print("脱敏映射（原值 -> 假值）")
    print("-" * 66)
    for (field, old), new in sorted(mapping.items()):
        shown = old if len(old) <= 40 else old[:37] + "…"
        print(f"  {field:<12} {shown:<44} -> {new}")
    print()

    DEMO.mkdir(parents=True, exist_ok=True)
    total = 0
    raw_files = sorted(RAW.glob("*.json"))
    for f in raw_files:
        body, wrapped = unwrap(f.read_text(encoding="utf-8"))
        new_body, n = redact(body, mapping)
        # 保持落盘形态一致（外层仍是一层 json.dumps 后的字符串）
        payload = json.dumps(new_body, ensure_ascii=False) if wrapped else new_body
        (DEMO / f.name).write_text(payload, encoding="utf-8")
        total += n
        print(f"  {f.name:<46} {f'{n} 处' if n else '无个人信息'}")

    print()
    print(f"完成：{len(raw_files)} 个文件、{total} 处个人信息已替换 -> {DEMO}")
    return verify(mapping)


if __name__ == "__main__":
    raise SystemExit(main())
