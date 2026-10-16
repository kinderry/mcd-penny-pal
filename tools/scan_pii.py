"""扫描 fixtures/raw 里的字段键名，找出可能的个人信息，生成脱敏依据报告。

为什么需要这一步：脱敏不能靠"看着像"就替换。门店编码、商品编码、券码
长得也像长数字，但它们**必须原样保留**，否则离线 demo 就算不出价格。
所以这里先把**所有键名**统计出来，再按"疑似 PII 的键名"逐个人工确认，
把该脱的字段锁成白名单，交给 make_demo_fixtures.py 执行。

输出：fixtures/raw/_pii_scan.md（**必须**放在 raw 下 —— 报告里含原始 PII，
而 fixtures/raw/ 已被 .gitignore 排除，不会外泄）
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "fixtures" / "raw"
OUT = RAW / "_pii_scan.md"

# "键": "值" —— 覆盖内联 JSON 与 markdown 表格里混排的 JSON
KV_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"([^"]{0,300})"')
# 疑似 PII 的键名
SUSPECT_KEY_RE = re.compile(
    r"(name|phone|mobile|tel|addr|address|contact|receiver|customer|user|token|"
    r"trace|account|member|openid|unionid|cardno|idcard|birthday|gender)",
    re.I,
)
# 手机号（含掩码形态）
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d(?:\d{9}|\*{2,6}\d{2,4})(?!\d)")
# 行政地址特征
ADDR_RE = re.compile(r"[\u4e00-\u9fa5]{2,8}(省|市)[\u4e00-\u9fa5]{2,10}(区|县|路|街|道|号)")


def unwrap(text: str) -> str:
    """raw 落盘时 json.dumps 过一层，先还原成 MCP 原始字符串。"""
    try:
        val = json.loads(text)
        if isinstance(val, str):
            return val
    except Exception:
        pass
    return text


def main() -> None:
    keys: collections.Counter[str] = collections.Counter()
    suspect: dict[str, set[str]] = collections.defaultdict(set)
    per_file: dict[str, list[str]] = {}

    for f in sorted(RAW.glob("*.json")):
        body = unwrap(f.read_text(encoding="utf-8"))
        hits: list[str] = []
        for m in KV_RE.finditer(body):
            k, v = m.group(1), m.group(2)
            keys[k] += 1
            if SUSPECT_KEY_RE.search(k) or PHONE_RE.search(v) or ADDR_RE.search(v):
                suspect[k].add(v[:90])
            if PHONE_RE.search(v) or ADDR_RE.search(v):
                hits.append(f"  - `{k}` = `{v[:90]}`")
        if hits:
            per_file[f.name] = hits

    lines = ["# fixtures/raw 敏感字段扫描报告", ""]
    lines.append(f"扫描文件数：{len(list(RAW.glob('*.json')))}")
    lines.append("")
    lines.append("## 一、疑似 PII 的键名（键名 → 出现过的值）")
    lines.append("")
    for k in sorted(suspect):
        vals = sorted(suspect[k])
        lines.append(f"### `{k}`（{len(vals)} 个不同值）")
        for v in vals[:6]:
            lines.append(f"- `{v}`")
        if len(vals) > 6:
            lines.append(f"- …另有 {len(vals) - 6} 个")
        lines.append("")
    lines.append("## 二、按文件看：命中手机号 / 行政地址的行")
    lines.append("")
    for name, hits in per_file.items():
        lines.append(f"### {name}")
        lines.extend(hits)
        lines.append("")
    lines.append("## 三、全部键名（按出现次数倒序，前 120）")
    lines.append("")
    for k, c in keys.most_common(120):
        lines.append(f"- `{k}` × {c}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"写入 {OUT}")


if __name__ == "__main__":
    main()
