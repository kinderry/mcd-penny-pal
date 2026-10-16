"""M1 券管家 + 简报自测。

两层：

1. **边界场景** —— 用**构造的券**跑，不含任何真实账号信息。
   重点是那几个只有真实数据才暴露得出来的坑（错位一天、同名券被吃掉、
   已领取的券被当成可领券）。

2. **端到端** —— 用 fixtures/raw 里的真实返回跑完整链路，
   断言简报里出现的每一个金额都能在输入里找到出处。

用法：
    python tools/test_briefing.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcd-penny-pal" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import briefing as B  # noqa: E402
import coupons as C  # noqa: E402
import parser as P  # noqa: E402
from fixtures_path import fixture_dir  # noqa: E402
from models import Coupon  # noqa: E402

RAW = fixture_dir()

# 固定"现在"，让测试可复现（机器时间一直在跑，不能依赖它）
NOW = datetime(2026, 10, 16, 11, 0)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<34} {detail}")


def cp(title: str, validity: str = "", price: str = "", tags: str = "",
       status: str = "") -> Coupon:
    return Coupon(title=title, validity=validity, price_text=price,
                  tags=tags, status=status)


# ------------------------------------------------------------------ 边界

print("=" * 70)
print("Part 1 · 边界场景（构造数据）")
print("=" * 70)

# —— 自然日边界。这是真实踩过的坑：10-17 10:29 到期，当时 10-16 10:59，
#    相差 23.5 小时，timedelta.days 会得 0，把"明天"说成"今天"。
tomorrow = cp("明天早餐券", "2026-10-17 05:00-2026-10-17 10:29")
check("明天 10:29 到期算作 1 天", tomorrow.days_left(now=NOW) == 1,
      f"days_left={tomorrow.days_left(now=NOW)}")

today_late = cp("今晚券", "2026-10-12 10:30-2026-10-16 23:59")
check("今天 23:59 到期算作 0 天", today_late.days_left(now=NOW) == 0,
      f"days_left={today_late.days_left(now=NOW)}")

expired = cp("昨天券", "2026-10-15 00:00-2026-10-15 23:59")
check("昨天到期算作已过期", expired.days_left(now=NOW) == -1,
      f"days_left={expired.days_left(now=NOW)}")

# —— 同名不同有效期必须都保留
d = C.digest(
    mine=[
        cp("同款套餐", "2026-10-17 05:00-2026-10-17 10:29"),
        cp("同款套餐", "2026-10-18 05:00-2026-10-18 10:29"),
    ],
    now=NOW,
)
check("同名不同有效期的券不合并", d.total == 2, f"total={d.total}")

# —— 可领池的语义：已领取的不该再被提示"可以领"
d = C.digest(claimable=[cp("占位券", status="已领取")], now=NOW)
check("「已领取」不计入可领数", d.claimable_count == 0, f"count={d.claimable_count}")

d = C.digest(claimable=[cp("占位券", status="可领取")], now=NOW)
check("「可领取」计入可领数", d.claimable_count == 1, f"count={d.claimable_count}")

# —— 券池分离：可领池不是资产，不能混进券包数量
d = C.digest(mine=[today_late], claimable=[cp("新券", status="可领取")], now=NOW)
check("可领池不混入券包总数", d.total == 1, f"total={d.total}")
check("可领池单独计数", d.claimable_count == 1, f"count={d.claimable_count}")

# —— 已在券包里的券，不再提示可领
d = C.digest(mine=[today_late], claimable=[cp("今晚券", status="可领取")], now=NOW)
check("已持有的券不重复提示可领", d.claimable_count == 0, f"count={d.claimable_count}")

# —— 情绪四态
check("空券包 -> 【空手】", B._mood(C.digest(now=NOW)) == B.MOOD_EMPTY,
      B._mood(C.digest(now=NOW)))
check("只有过期券 -> 【空手】",
      B._mood(C.digest(mine=[expired], now=NOW)) == B.MOOD_EMPTY)
check("今日到期 -> 【着急】",
      B._mood(C.digest(mine=[today_late], now=NOW)) == B.MOOD_URGENT)
check("远期券 -> 【平常】",
      B._mood(C.digest(mine=[cp("下周券", "2026-10-20 00:00-2026-10-25 23:59")], now=NOW))
      == B.MOOD_NORMAL)

# —— 上午到期的券，绝不能说成"今晚"。
#    注意视角必须是上午（09:00）：11:00 看 10:29 的券已经过期了，
#    那样测的就不是"上午到期"而是"已过期"，两个分支。
AM = datetime(2026, 10, 16, 9, 0)
text = B.build_briefing(
    C.digest(mine=[cp("早餐券", "2026-10-16 05:00-2026-10-16 10:29", "¥9.9")], now=AM),
    now=AM,
)
check("上午到期不说「今晚」", "今晚" not in text and "今天 10:29" in text,
      text.splitlines()[0])

# —— 没有有效期的券，不许编一个到期日出来
text = B.build_briefing(C.digest(mine=[cp("无期券", "", "¥5")], now=NOW), now=NOW)
check("无有效期不编到期日", "有效期未知" in str(C.digest(mine=[cp("无期券")], now=NOW).unknown)
      or "2026" not in text, "简报中无编造日期")

# —— 今日到期券时刻不一致时，不用具体时点。
#    两个时刻都必须**还没到**（11:00 视角下取 13:00 和 23:59），
#    否则其中一张会先掉进"已过期"，测不到"时刻不一致"。
mixed = B._headline(C.digest(
    mine=[cp("A券", "2026-10-16 05:00-2026-10-16 13:00"),
          cp("B券", "2026-10-12 10:30-2026-10-16 23:59")], now=NOW))
check("今日到期时刻不一致时不报具体时点",
      "23:59" not in mixed and "13:00" not in mixed, mixed)


# ------------------------------------------------------------------ 端到端

print()
print("=" * 70)
print("Part 2 · 端到端（真实 fixture）")
print("=" * 70)


def read(name: str):
    path = RAW / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


real_now = datetime(2026, 10, 16, 11, 0)

mine = P.to_coupons(P.parse("q", read("query-my-coupons")), C.POOL_MINE)
claim = P.to_coupons(P.parse("q", read("available-coupons")), C.POOL_CLAIMABLE)
acct = P.to_account(P.parse("q", read("query-my-account")))

d = C.digest(mine=mine, claimable=claim, now=real_now)
text = B.build_briefing(d, acct, now=real_now)

check("券包 6 张全部保留", d.total == 6, f"total={d.total}")
check("今日到期 2 张（不是 3 张）", len(d.today) == 2,
      "、".join(a.title for a in d.today))
check("明天到期的券被算作 T-3 档", any(a.days_left == 1 for a in d.soon),
      "、".join(f"{a.title}={a.days_left}天" for a in d.soon))
check("已领取的券不再提示可领", d.claimable_count == 0, f"count={d.claimable_count}")
check("简报带【着急】", text.startswith(B.MOOD_URGENT), text.splitlines()[0])

# —— 铁律 1 的机器验证：简报里的每个金额都必须能在输入里找到出处
allowed = set()
for c in mine + claim:
    m = re.search(r"(\d+(?:\.\d+)?)", c.price_text or "")
    if m:
        allowed.add(m.group(1))
found = set(re.findall(r"¥(\d+(?:\.\d+)?)", text))
check("简报金额全部可溯源", found <= allowed,
      f"出现={sorted(found)} 允许={sorted(allowed)}")

check("简报不含估算措辞",
      not any(w in text for w in ("大约", "估算", "约为", "大概是")), "")
check("简报不出现「本助手」等自称", "本助手" not in text and "本AI" not in text, "")
check("简报以邀请结尾而非催促",
      text.rstrip().endswith(("我来比。", "吗？", "叫你。")), text.strip().splitlines()[-1])


# ------------------------------------------------------------------ 汇总

print()
print("=" * 70)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for name in FAIL:
        print(f"  失败：{name}")
print("=" * 70)

print()
print("简报实拍：")
print("-" * 70)
print(text)

raise SystemExit(1 if FAIL else 0)
