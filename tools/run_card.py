"""生成「今日省钱战报」卡片。

    python tools/run_card.py              # 离线：读 fixtures/demo 的脱敏返回
    python tools/run_card.py --live       # 实时：直连 mcp.mcd.cn（需 MCD_MCP_TOKEN）
    python tools/run_card.py --no-png     # 只出 HTML，不调浏览器截图
    python tools/run_card.py --raw        # 离线但读 fixtures/raw 的原始返回

产物（默认写到 output/）：
    output/card-preview.html   自包含 HTML，双击就能看，也能直接用浏览器截图
    output/card-preview.png    1080×1440，3:4 竖版，可直接发社交平台

离线模式刻意保留：评委和路人不需要任何 Token 就能自己跑出同一张卡片，
因为 fixtures 就是真实返回的原样落盘（demo 版只脱敏了个人信息字段，
券码、门店、价格一个字符都没动），走的是和联网完全相同的引擎代码。

**本脚本只调只读工具。** 不领券、不下单。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills" / "mcd-penny-pal" / "src"
sys.path.insert(0, str(SRC))

import card as CARD  # noqa: E402
import coupons as C  # noqa: E402
import valuation as V  # noqa: E402
from fixtures_path import label as fixtures_label  # noqa: E402
from run_briefing import collect_live, collect_offline  # noqa: E402

OUT_DIR = ROOT / "output"
MASCOT = ROOT / "assets" / "mascot-card.png"

# 截图用一个足够高的视口，让卡片内容完全铺开，之后再按实际内容裁切。
# 直接按 1440 截会在券多的时候把最后一行挤出画面。
VIEWPORT_H = 2400

# 项目地址：卡片底部的传播钩子，截图发出去能看到仓库
PROJECT_URL = "github.com/kinderry/mcd-penny-pal"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str | None:
    for path in BROWSERS:
        if Path(path).is_file():
            return path
    return shutil.which("chrome") or shutil.which("msedge")


def screenshot(html_path: Path, png_path: Path, *, width: int, height: int) -> tuple[bool, str]:
    """用无头浏览器把 HTML 截图成 PNG。失败时返回原因，不抛异常。"""
    exe = find_browser()
    if not exe:
        return False, "本机没找到 Chrome / Edge，跳过截图（HTML 仍可直接打开）"

    uri = html_path.resolve().as_uri()
    cmd = [
        exe,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-proxy-server",
        "--force-device-scale-factor=1",
        "--virtual-time-budget=3000",
        f"--window-size={width},{height}",
        f"--screenshot={png_path}",
        uri,
    ]
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy")}
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=90, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"截图失败：{exc}"

    if png_path.is_file() and png_path.stat().st_size > 0:
        return True, f"{png_path.stat().st_size // 1024} KB"
    return False, (proc.stderr or b"").decode("utf-8", "replace")[:200] or "未知错误"


def trim_png(png_path: Path, *, min_height: int = 0) -> tuple[bool, str]:
    """把大视口截图裁到实际内容高度（不低于 3:4）。

    卡片高度是自适应的，券多有可能会长过 1440，所以截图不能按固定高度截。
    PIL 装在隔离 venv 里；没有就原样返回，不阻塞出图。
    """
    try:
        from PIL import Image
    except ImportError:
        return False, "本机没装 PIL，跳过裁剪（图片底部会多出一段背景）"

    im = Image.open(png_path).convert("RGB")
    W, H = im.size
    px = im.load()
    base = px[W // 2, H - 4]  # 视口最底部就是画布外背景

    def ink(y: int) -> int:
        return sum(
            1 for x in range(0, W, 3)
            if sum(abs(a - b) for a, b in zip(px[x, y], base)) > 18
        )

    last = max((y for y in range(H) if ink(y) > 2), default=H - 1)
    target = max(min_height, min(H, last + 1))
    if target >= H:
        return False, f"内容铺满了整块视口（{H}px），请调大 VIEWPORT_H"
    im.crop((0, 0, W, target)).save(png_path)
    ratio = target / W
    return True, f"裁切为 {W}×{target}（比例 1:{ratio:.2f}，3:4 为 1:1.33）"


def _has_pil() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def main() -> int:
    live = "--live" in sys.argv
    want_png = "--no-png" not in sys.argv

    if live and not os.environ.get("MCD_MCP_TOKEN"):
        print("--live 模式需要环境变量 MCD_MCP_TOKEN", file=sys.stderr)
        return 2

    data = collect_live() if live else collect_offline()
    d = C.digest(mine=data["mine"], claimable=data["claimable"], store=data["store"])
    book = V.value_book(data["deals"], data["mine"])

    html_text = CARD.build_card(
        d,
        valuation=book,
        account=data["account"],
        now=datetime.now(),
        store_name=data["store_name"],
        mascot_path=MASCOT,
        project_url=PROJECT_URL,
    )

    OUT_DIR.mkdir(exist_ok=True)
    html_path = OUT_DIR / "card-preview.html"
    html_path.write_text(html_text, encoding="utf-8")

    print("=" * 66)
    print(f"数据来源：{'实时 MCP' if live else fixtures_label()}")
    print("=" * 66)
    print(f"卡片 HTML ：{html_path}  ({len(html_text) // 1024} KB，自包含)")
    print(f"形象内联  ：{'是' if CARD._data_uri(MASCOT) else '否（已降级为占位圆）'}")

    if want_png:
        png_path = OUT_DIR / "card-preview.png"
        # 没有 PIL 就没法按内容裁切，退回按标准 3:4 视口截，避免出图带一截背景
        viewport_h = VIEWPORT_H if _has_pil() else CARD.CARD_H
        ok, info = screenshot(html_path, png_path, width=CARD.CARD_W, height=viewport_h)
        if not ok:
            print(f"卡片 PNG  ：未生成 —— {info}")
        elif viewport_h == CARD.CARD_H:
            print(f"卡片 PNG  ：已生成 —— 1080×{CARD.CARD_H}（未装 PIL，按标准画布截）")
        else:
            trimmed, tinfo = trim_png(png_path, min_height=CARD.CARD_H)
            mark = "已生成" if trimmed else "已生成（未裁切）"
            print(f"卡片 PNG  ：{mark} —— {tinfo}")

    # 卡片正文（纯文本），便于快速核对内容与口径
    print()
    print("-" * 66)
    print("卡片正文（纯文本预览）")
    print("-" * 66)
    print(CARD.card_plain_text(html_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
