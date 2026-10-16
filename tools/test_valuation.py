"""M1 × M2 缝合层自测：券价值核算 + 接上真实金额的简报。

为什么单独测这一层：券价值是**最容易看起来对、实际错**的地方。
券面写着 ¥15.9，机制上一算只有 ¥6.1；券包里有 6 张，能核价的只有 3 张。
如果这里不设防，简报就会把"券面价"说成"省下的钱"，
或者把 3 张券的金额说成"这 6 张券值这么多"。

两层：
  1. **边界场景** —— 构造数据，覆盖部分可估、券面价虚高、情绪优先级
  2. **端到端** —— 真实 fixture 跑通 M1→M2→简报，每笔金额都能追到接口返回

用法：
    python tools/test_valuation.py
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
import pricer as PR  # noqa: E402
import valuation as V  # noqa: E402
from fixtures_path import fixture_dir  # noqa: E402
from models import Coupon, StoreCoupon  # noqa: E402
from replay import FixtureCaller  # noqa: E402

RAW = fixture_dir()
NOW = datetime(2026, 10, 16, 11, 0)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<36} {detail}")


def cp(title: str, validity: str = "", price: str = "", tags: str = "",
       status: str = "") -> Coupon:
    return Coupon(title=title, validity=validity, price_text=price,
                  tags=tags, status=status)


def deal(title: str, saving_fen: int, original_fen: int = 0,
         pay_fen: int = 0) -> PR.CouponDeal:
    """构造一条计价结果，便于测边界。"""
    return PR.CouponDeal(
        coupon=StoreCoupon(title=title, coupon_id="ID", coupon_code="CODE",
                           product_code="P1"),
        quote=PR.PriceQuote(original_fen=original_fen,
                            discount_fen=saving_fen, price_fen=pay_fen),
    )


# ------------------------------------------------------------------ 边界

print("=" * 72)
print("Part 1 · 边界场景（构造数据）")
print("=" * 72)

fries = cp("中薯条买一送一", "2026-10-12 10:30-2026-10-16 23:59",
           "¥13.5 (用券价格)", "今日到期")
snack = cp("王牌小食拼", "2026-10-12 10:30-2026-10-16 23:59",
           "¥15.9 (用券价格)", "今日到期")
shake = cp("人气麦旋风买一送一", "2026-10-17 00:00-2026-10-18 23:59",
           "¥15 (用券价格)", "到店专用")

book = V.value_book(
    [deal("中薯条买一送一", 1350, 2700, 1350), deal("王牌小食拼", 610, 2200, 1590)],
    [fries, snack, shake],
)

check("券包券匹配上门店券即可核价", book.can_price("中薯条买一送一"),
      f"saving={book.get('中薯条买一送一').saving_text}")
check("券名归一化后再匹配（空白/标点不影响）",
      book.can_price(" 中薯条 买一送一 "), "带空格也能命中")
check("门店券里没有的券 → 不可核价", not book.can_price("人气麦旋风买一送一"),
      "人气麦旋风 → unpriced")

# —— 关键：部分可估时绝不能报总账
s = book.summarize(["中薯条买一送一", "人气麦旋风买一送一"])
check("部分可估时 complete=False", not s.complete, f"valued={len(s.values)} unpriced={len(s.unpriced)}")
check("合计只算能核价的那部分", s.total_fen == 1350, f"total={s.total_text}")
check("未核价的券不进合计", "15" not in s.total_text, s.total_text)

s_all = book.summarize(["中薯条买一送一", "王牌小食拼"])
check("全部可估时 complete=True", s_all.complete, f"total={s_all.total_text}")
check("今日两张合计 ¥19.6", s_all.total_fen == 1960, s_all.total_text)

# —— 券面价虚高必须能识别出来
gap = book.get("王牌小食拼").face_gap_yuan
check("券面价与实省差额可算", gap == 9.8, f"券面 ¥15.9 - 实省 ¥6.1 = ¥{gap:g}")
check("虚高 ≥¥3 会被点名", len(s_all.mismatches()) == 1,
      "、".join(v.title for v in s_all.mismatches()))
small = V.value_book([deal("冰美式", 910, 1900, 990)],
                     [cp("冰美式", "", "¥9.9 (用券价格)")])
check("虚高 <¥3 不啰嗦", small.summarize(["冰美式"]).mismatches() == [],
      f"差额 ¥{small.get('冰美式').face_gap_yuan:g}")

# —— 情绪优先级：紧迫 > 金额
d = C.digest(mine=[fries, snack], now=NOW)
check("今日到期 -> 【着急】（即使能省 ¥19.6）",
      B._mood(d, book) == B.MOOD_URGENT, B._mood(d, book))

far = cp("远期好券", "2026-10-20 00:00-2026-10-25 23:59", "¥12")
d_far = C.digest(mine=[far], now=NOW)
book_far = V.value_book([deal("远期好券", 1200)], [far])
check("无临期但可省 ≥¥10 -> 【得意】",
      B._mood(d_far, book_far) == B.MOOD_SMUG, B._mood(d_far, book_far))

book_small = V.value_book([deal("远期好券", 300)], [far])
check("可省 <¥10 -> 【平常】",
      B._mood(d_far, book_small) == B.MOOD_NORMAL, B._mood(d_far, book_small))

# —— 简报口径：核不到的券不许出现在金额旁边
partial = V.value_book([deal("中薯条买一送一", 1350, 2700, 1350)],
                       [fries, snack, shake])
d_mix = C.digest(mine=[fries, snack], now=NOW)
text_mix = B.build_briefing(d_mix, valuation=partial, now=NOW)
check("部分可估时说明还有几张没核到", "我还没核到价" in text_mix,
      [l for l in text_mix.splitlines() if "没核到" in l][:1])
check("部分可估时不报总额", "加起来能省" not in text_mix, "")
check("核不到的「王牌小食拼」旁没有金额",
      not re.search(r"王牌小食拼（省 ", text_mix), "")

# —— 没传 valuation 时退回券面口径，且不出现"M2 式"断言
text_plain = B.build_briefing(d_mix, now=NOW)
check("无实测值时退回券面口径", "券面标注分别是" in text_plain, "")
check("无实测值时不谎称已核实", "加起来能省" not in text_plain, "")


# ------------------------------------------------------------------ 端到端

print()
print("=" * 72)
print("Part 2 · 端到端（真实 fixture：券包 → 门店券 → 真实计价 → 简报）")
print("=" * 72)


def read(name: str):
    path = RAW / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


real_now = datetime(2026, 10, 16, 11, 0)

mine = P.to_coupons(P.parse("q", read("query-my-coupons")), C.POOL_MINE)
store_coupons = P.to_store_coupons(P.parse("q", read("query-store-coupons")))
stores = P.to_stores(P.parse("q", read("query-nearby-stores__probe1")))
acct = P.to_account(P.parse("q", read("query-my-account")))

caller = FixtureCaller(RAW)
deals = PR.rank_deals(caller, store_code=stores[0].code, coupons=store_coupons)
real_book = V.value_book(deals, mine)
d = C.digest(mine=mine, store=P.to_coupons(P.parse("q", read("query-store-coupons")), C.POOL_STORE), now=real_now)
text = B.build_briefing(d, acct, valuation=real_book, now=real_now)

check("离线回放命中计价调用", caller.hit_rate == "5/5" or not caller.missed,
      f"命中 {caller.hit_rate}")
check("券包 6 张（门店券已正确合并去重）", d.total == 6, f"total={d.total}")
check("今日到期 2 张", len(d.today) == 2,
      "、".join(a.title for a in d.today))
check("今日到期两张都核到了价",
      real_book.summarize([a.title for a in d.today]).complete,
      real_book.summarize([a.title for a in d.today]).total_text)
check("今日合计 = ¥19.6（真实计价）",
      real_book.summarize([a.title for a in d.today]).total_fen == 1960,
      real_book.summarize([a.title for a in d.today]).total_text)

# —— 全站最亮的那条：券面虚高被说破
check("简报说破「王牌小食拼」券面虚高",
      "实际只能省 ¥6.1" in text and "券面写着 ¥15.9" in text,
      [l for l in text.splitlines() if "王牌小食拼" in l][:1])
check("简报给出今日合计", "这 2 张加起来能省 ¥19.6" in text, "")
check("简报保留【着急】（临期优先于金额）", text.startswith(B.MOOD_URGENT), text.splitlines()[0])

# —— 铁律 1 的机器验证：简报里的每个金额都必须出自接口返回。
#    注意口径：引擎**只许对接口金额做加法**，不许凭空造数。
#    所以允许集合 = 单值 ∪ 若干单值的精确和（"这 2 张加起来能省 ¥19.6"）。
def _fmt(v: float) -> str:
    """与 pricer.money() 同一格式（保留一位小数），保证两边能对上。"""
    return f"{round(v, 1):.1f}"


single: set[str] = set()
for c in mine:
    m = re.search(r"(\d+(?:\.\d+)?)", c.price_text or "")
    if m:
        single.add(m.group(1))
savings: list[float] = []
for dl in deals:
    for fen in (dl.quote.discount_fen, dl.quote.original_fen, dl.quote.price_fen):
        single.add(_fmt(fen / 100))
    savings.append(dl.quote.discount_fen / 100)

from itertools import combinations  # noqa: E402

allowed = set(single)
for r in range(2, len(savings) + 1):
    for combo in combinations(savings, r):
        allowed.add(_fmt(sum(combo)))

found = set(re.findall(r"¥(\d+(?:\.\d+)?)", text))
check("简报金额全部可溯源到接口返回（含精确求和）", found <= allowed,
      f"出现={sorted(found)} 允许={sorted(allowed)}")

# —— 反向验证：凭空编一个金额必须被这条断言抓住
fake_found = set(re.findall(r"¥(\d+(?:\.\d+)?)", text + "\n顺手给你省了 ¥999.9。\n"))
check("凭空编造的金额会被这条断言抓住", not fake_found <= allowed,
      f"检出 {sorted(fake_found - allowed)}")

check("简报不含估算措辞",
      not any(w in text for w in ("大约", "估算", "约为", "大概是")), "")
check("简报不出现「本助手」等自称",
      "本助手" not in text and "本AI" not in text, "")


# ------------------------------------------------------------------ 汇总

print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for name in FAIL:
        print(f"  失败：{name}")
print("=" * 72)

print()
print("简报实拍（接上 M2 真实计价）：")
print("-" * 72)
print(text)

raise SystemExit(1 if FAIL else 0)
