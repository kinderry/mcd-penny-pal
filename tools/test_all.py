"""一键跑完全部自测。

    python tools/test_all.py

给评委和路人用的入口：**不需要任何 Token**，因为所有断言都跑在
`fixtures/demo/` 里那批 2026-10-16 的真实返回上（脱敏版，只换了个人信息字段）。

四层自测，越往下越靠近用户：
  1. test_parser    —— 四种返回形态（json / markdown / mixed / 紧凑表格）解析
  2. test_price     —— M2 真实计价口径（单位是分、券的钥匙在门店券里…）
  3. test_briefing  —— M1 券管家 + 简报渲染
  4. test_valuation —— M1 × M2 缝合：券价值核算 + 接上真实金额的简报
  5. test_card      —— 战报卡片：口径、金额溯源、合规红线
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

SUITES = [
    ("test_parser", "解析层 · 四种返回形态"),
    ("test_price", "M2 比价官 · 真实计价口径"),
    ("test_briefing", "M1 券管家 · 简报渲染"),
    ("test_valuation", "M1×M2 · 券价值核算"),
    ("test_card", "战报卡片 · 口径/溯源/合规"),
]


def main() -> int:
    results: list[tuple[str, int, str]] = []

    for name, label in SUITES:
        path = TOOLS / f"{name}.py"
        print()
        print("=" * 72)
        print(f"  {label}   ({name}.py)")
        print("=" * 72)
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = proc.stdout or ""
        # 只回显断言行和结论，不刷屏
        for line in out.splitlines():
            stripped = line.strip()
            if (stripped.startswith("[PASS]") or stripped.startswith("[FAIL]")
                    or stripped.startswith("通过 ") or stripped.startswith("全部通过")
                    or stripped.startswith("  失败：") or stripped.startswith("发现 ")):
                print("  " + stripped)
        tail = next((l.strip() for l in reversed(out.splitlines())
                     if l.strip().startswith(("通过 ", "全部通过"))), "")
        results.append((label, proc.returncode, tail))

    print()
    print("=" * 72)
    print("  汇总")
    print("=" * 72)
    failed = 0
    for label, code, tail in results:
        mark = "OK  " if code == 0 else "FAIL"
        if code:
            failed += 1
        print(f"  [{mark}] {label:<28} {tail}")
    print("=" * 72)
    if failed:
        print(f"{failed} 个自测套件未通过。")
    else:
        print("全部自测通过。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
