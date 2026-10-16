"""战报卡片自测。

卡片比简报更危险：**它是要被截图传播的**。一张好看的卡片上写错一个
数字，会被几十上百人当成事实转发，而简报只错在一个人眼前。
所以这里的断言比简报那一层更严——尤其是金额与口径。

四类防线：
  1. **口径** —— 部分可估时不许说"今天能省"；没有估值时一个金额都不许出现
  2. **溯源** —— 卡片里出现的每个金额都必须来自接口返回，或它们的精确和
  3. **合规** —— 不得出现麦当劳的商标字样或标志性描述
  4. **回归** —— 画布高度必须是 min-height（固定高度会裁掉最后一行，实测踩过）

用法：
    python tools/test_card.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcd-penny-pal" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import card as CARD  # noqa: E402
import coupons as C  # noqa: E402
import parser as P  # noqa: E402
import pricer as PR  # noqa: E402
import valuation as V  # noqa: E402
from fixtures_path import fixture_dir  # noqa: E402
from models import Coupon, StoreCoupon  # noqa: E402

RAW = fixture_dir()
NOW = datetime(2026, 10, 16, 11, 0)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<38} {detail}")


def cp(title: str, validity: str = "", price: str = "", tags: str = "",
       status: str = "") -> Coupon:
    return Coupon(title=title, validity=validity, price_text=price,
                  tags=tags, status=status)


def deal(title: str, saving_fen: int, original_fen: int = 0,
         pay_fen: int = 0) -> PR.CouponDeal:
    return PR.CouponDeal(
        coupon=StoreCoupon(title=title, coupon_id="ID", coupon_code="CODE",
                           product_code="P1"),
        quote=PR.PriceQuote(original_fen=original_fen,
                            discount_fen=saving_fen, price_fen=pay_fen),
    )


def build(mine, deals, **kw) -> str:
    d = C.digest(mine=mine, claimable=[], store=[])
    book = V.value_book(deals, mine) if deals is not None else None
    return CARD.build_card(d, valuation=book, now=NOW, **kw)


def amounts(html_text: str) -> set[str]:
    """卡片里出现的所有 ¥ 金额（去掉 ¥ 与末尾零后的数字串）。"""
    return {m.rstrip("0").rstrip(".") for m in re.findall(r"¥(\d+(?:\.\d+)?)", html_text)}


# ------------------------------------------------------------------ Part 1

print("=" * 72)
print("Part 1 · 口径（构造数据）")
print("=" * 72)

fries = cp("中薯条买一送一", "2026-10-12 10:30-2026-10-16 23:59",
           "¥13.5 (用券价格)", "今日到期")
snack = cp("王牌小食拼", "2026-10-12 10:30-2026-10-16 23:59",
           "¥15.9 (用券价格)", "今日到期")
shake = cp("人气麦旋风买一送一", "2026-10-15 00:00-2026-10-18 23:59",
           "¥15 (用券价格)", "到店专用")
mine3 = [fries, snack, shake]
deals3 = [deal("中薯条买一送一", 1350, 2700, 1350),
          deal("王牌小食拼", 610, 2200, 1590),
          deal("人气麦旋风买一送一", 1500, 3000, 1500)]

# —— 全部可估：才敢说"今天能省"
html = build(mine3, deals3)
text = CARD.card_plain_text(html)
check("全部可估时说「今天能省」", "今天能省" in text, "大数字口径")
check("全部可估时不给未核价字样", "未核到价" not in text and "还没核到价" not in text)

# —— 部分可估：只说核过的那部分
html_part = build(mine3, [deal("中薯条买一送一", 1350, 2700, 1350)])
text_part = CARD.card_plain_text(html_part)
check("部分可估时不说「今天能省」", "今天能省" not in text_part,
      "把 1 张的金额说成 3 张的，就是撒谎")
check("部分可估时说明已核价张数", "已核价的 1 张" in text_part)
check("部分可估时点名未核价的券", "未核到价" in text_part)
check("部分可估金额只算核过的", amounts(html_part) >= {"13.5"} and "19.6" not in amounts(html_part))

# —— 完全没有估值：一个金额都不许出现
html_plain = build(mine3, [])
text_plain = CARD.card_plain_text(html_plain)
check("无估值卡片不含任何金额", not amounts(html_plain),
      f"检出 {sorted(amounts(html_plain)) or '无'}")
check("无估值仍有临期提醒", "就作废" in text_plain or "到期" in text_plain)

# —— 券面虚高提醒
check("券面虚高时主动说破", "券面写着" in text and "用券后的价" in text)
check("说破的是真实节省额（非券面价）", "¥6.1" in text, "券面 ¥15.9 / 实省 ¥6.1")

# —— 情绪与空态
check("有今日到期券 → 着急", "着急" in text)
empty_html = CARD.build_card(C.digest(mine=[], claimable=[], store=[]),
                             valuation=None, now=NOW)
empty_text = CARD.card_plain_text(empty_html)
check("空券包不崩且神态为「空手」", "空手" in empty_text, empty_text[:40])
expired = cp("过期券", "2026-10-01 00:00-2026-10-05 23:59", "", "已过期")
exp_html = build([expired], [])
check("只剩过期券 → 空手", "空手" in CARD.card_plain_text(exp_html))

# —— 转义
nasty = cp("A&B <script>alert(1)</script>", "2026-10-12 10:30-2026-10-16 23:59", "¥9")
nasty_html = build([nasty], [])
check("券名被 HTML 转义", "<script>" not in nasty_html and "&lt;script&gt;" in nasty_html)

# —— 降级：形象图缺失
check("形象图缺失时降级为占位", "avatar empty" in build(mine3, deals3))
real_mascot = ROOT / "assets" / "mascot-card.png"
has_img = "data:image/png;base64" in build(mine3, deals3, mascot_path=real_mascot)
check("形象图存在时以 base64 内联", has_img, "自包含，换台机器也能开")

# —— 传播钩子
check("卡片带仓库地址", "github.com" in CARD.card_plain_text(
    build(mine3, deals3, project_url="github.com/kinderry/mcd-penny-pal")),
    "截图发出去能被搜到")

# ------------------------------------------------------------------ Part 2

print()
print("=" * 72)
print("Part 2 · 回归（防的是已经踩过的坑）")
print("=" * 72)

# ⚠️ 实测 bug：画布写死 height:1440，券多时把最后一行挤出画面。
#    必须是 min-height（自适应），让内容把卡片撑开。
css = html.split("</style>")[0]
check("画布用 min-height 而非固定 height",
      not re.search(r"(?<!min-)height:\s*1440px", css) and "min-height: 1440px" in css,
      "固定高度会裁掉 footer 最后一行")

# ⚠️ 大数字必须是"最紧急那一组"的合计，不能把 today + soon 混算
mix_mine = [fries, snack, shake]
mix_html = build(mix_mine, deals3)
mix_text = CARD.card_plain_text(mix_html)
check("大数字只算今天到期那组", "19.6" in amounts(mix_html),
      "¥13.5 + ¥6.1，不含 T-3 的麦旋风")
check("T-3 券只用一行带过", "在倒计时" in mix_text)

# ⚠️ 金额格式：¥19.6 而不是 ¥19.60
check("金额不带多余尾零", "¥19.60" not in html and "¥19.6" in html)

# ------------------------------------------------------------------ Part 3

print()
print("=" * 72)
print("Part 3 · 端到端（真实 fixture）")
print("=" * 72)

from run_briefing import collect_offline  # noqa: E402

data = collect_offline()
d = C.digest(mine=data["mine"], claimable=data["claimable"], store=data["store"])
book = V.value_book(data["deals"], data["mine"])
real_html = CARD.build_card(d, valuation=book, account=data["account"], now=NOW,
                            store_name=data["store_name"],
                            mascot_path=ROOT / "assets" / "mascot-card.png",
                            project_url="github.com/kinderry/mcd-penny-pal")
real_text = CARD.card_plain_text(real_html)
print(f"  券包 {len(data['mine'])} 张 / 可核价 {len(data['deals'])} 张")
print()

check("真实数据卡片生成成功", len(real_html) > 5000, f"{len(real_html) // 1024} KB")

# —— 追溯：每个金额要么来自接口，要么是它们之和
#
# 卡片比简报多一项 **券面标注合计**（那是卡片特有的对照行，简报里没有），
# 所以允许集合里要同时放入「真实优惠额」和「券面价」各自的子集和。
face_values: list[float] = []
for c in data["mine"]:
    m = re.search(r"(\d+(?:\.\d+)?)", c.price_text or "")
    if m:
        face_values.append(float(m.group(1)))

savings: list[float] = []
single: set[str] = set()
for dl in data["deals"]:
    for fen in (dl.quote.discount_fen, dl.quote.original_fen, dl.quote.price_fen):
        single.add(f"{fen / 100:.2f}".rstrip("0").rstrip("."))
    savings.append(dl.quote.discount_fen / 100)

allowed = set(single)
for group in (savings, face_values):
    for r in range(1, len(group) + 1):
        for combo in combinations(group, r):
            allowed.add(f"{round(sum(combo), 2):.2f}".rstrip("0").rstrip("."))

found = amounts(real_html)
check("卡片金额全部可溯源", found <= allowed,
      f"出现={sorted(found)} 允许={sorted(allowed)}")

fake = real_html.replace("</body>", "<div>顺手给你省了 ¥888.8</div></body>")
check("凭空编造的金额会被抓出来", not amounts(fake) <= allowed,
      f"检出 {sorted(amounts(fake) - allowed)}")

# —— 与简报口径一致
import briefing as B  # noqa: E402

brief_text = B.build_briefing(d, data["account"], valuation=book, now=NOW)
brief_amounts = {m.rstrip("0").rstrip(".") for m in re.findall(r"¥(\d+(?:\.\d+)?)", brief_text)}

# 卡片聚焦「今天到期」那一组，简报覆盖全部临期，所以卡片是简报的子集；
# 唯一的例外是券面标注合计——那是卡片特有的对照行。
face_combos: set[str] = set()
for r in range(1, len(face_values) + 1):
    for combo in combinations(face_values, r):
        face_combos.add(f"{round(sum(combo), 2):.2f}".rstrip("0").rstrip("."))
extra = found - brief_amounts
check("卡片金额不超出简报（券面合计除外）", extra <= face_combos,
      f"超出={sorted(extra)} 券面组合={sorted(extra & face_combos)}")

focus_savings = {
    f"{v.saving_fen / 100:.2f}".rstrip("0").rstrip(".")
    for v in book.summarize([a.title for a in d.today]).values
}
check("卡片里的实省额都与简报一致",
      focus_savings <= brief_amounts and focus_savings <= found,
      f"今天到期 {sorted(focus_savings)}")

# ------------------------------------------------------------------ Part 4

print()
print("=" * 72)
print("Part 4 · 合规（卡片要传播，风险高于简报）")
print("=" * 72)

# ⚠️ 合规检查必须在**纯文本**上做。整份 HTML 里嵌着 base64 图片数据，
#    随机字符里能凑出 "kfc" 之类的巧合，会造成假警报（实测撞过）。
body_text = CARD.card_plain_text(real_html)
low = body_text.lower()

# 麦当劳商标与标志性描述。金拱门图形、字母 M 标志、官方中文名等一律不得出现。
BANNED = [
    "金拱门", "麦当劳叔叔", "Ronald", "McDonald", "McDonald's",
    "mcdonalds", "拱门", "Golden Arches", "我就喜欢", "i'm lovin",
    "logo", "trademark", "®", "™",
]
hits = [b for b in BANNED if b.lower() in low]
check("卡片不含任何商标字样", not hits, f"检出 {hits or '无'}")

# 字母 M 标志：正文里单个大写 M 只允许出现在仓库 slug（mcd-penny-pal）里
stray_m = [w for w in re.findall(r"\bM\b", body_text)]
check("正文不含孤立的字母 M 标志", not stray_m, f"检出 {stray_m or '无'}")

# 不与其他品牌做对比（活动规则明令禁止）
OTHER_BRANDS = ["肯德基", "KFC", "汉堡王", "Burger King", "星巴克", "德克士",
                "华莱士", "必胜客", "赛百味"]
other = [b for b in OTHER_BRANDS if b.lower() in low]
check("不与任何其他品牌对比", not other, f"检出 {other or '无'}")

# 不得出现"薅""白嫖"等调性
BAD_TONE = ["薅", "白嫖", "薅垮", "薅羊毛", "免费吃垮", "薅秃"]
tone = [b for b in BAD_TONE if b in body_text]
check("调性不含「薅/白嫖」等表述", not tone, f"检出 {tone or '无'}")

# ------------------------------------------------------------------ 汇总

print()
print("=" * 72)
total = len(PASS) + len(FAIL)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项，共 {total} 项")
if FAIL:
    for name in FAIL:
        print(f"  未通过：{name}")
    raise SystemExit(1)
print("全部通过")
