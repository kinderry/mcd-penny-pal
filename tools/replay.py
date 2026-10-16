"""离线回放：把 MCP 调用映射到已落盘的真实返回。

**为什么要有这个**

评委和路人不需要任何 Token 就能跑通整条链路，看到的结果和联网时一致 ——
因为 `fixtures/raw/` 里放的就是 2026-10-16 对 mcp.mcd.cn 的真实返回原样落盘。

映射是**显式**的：离线 demo 本来就是固定剧本，不假装能覆盖任意调用。
命中不了就抛 `LookupError`，绝不返回编造的数据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 单例工具的固定落盘名
_FIXED = {
    "query-meals": "query-meals__pickup",
    "query-store-coupons": "query-store-coupons",
    "query-my-coupons": "query-my-coupons",
    "available-coupons": "available-coupons",
    "query-my-account": "query-my-account",
    "now-time-info": "now-time-info",
    "list-nutrition-foods": "list-nutrition-foods",
    "campaign-calendar": "campaign-calendar",
}


class FixtureCaller:
    """把 ``(tool, args)`` 映射到 fixtures 文件的可调用对象。

    用法与在线调用一致，因此引擎代码完全不用改：

        caller = FixtureCaller(RAW)
        deals = pricer.rank_deals(caller, store_code=..., coupons=...)
    """

    def __init__(self, raw_dir: Path | str) -> None:
        self.raw = Path(raw_dir)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.missed: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((tool, args))
        path = self._locate(tool, args)
        if path is None or not path.exists():
            self.missed.append((tool, args))
            raise LookupError(f"离线 fixture 缺失：{tool}({args})")
        # fixtures 存的是 JSON 编码后的字符串（MCP 返回本身就是 str，
        # 落盘时 json.dumps 了一层）→ 必须 json.loads 还原
        return json.loads(path.read_text(encoding="utf-8"))

    def _locate(self, tool: str, args: dict[str, Any]) -> Path | None:
        if tool == "calculate-price":
            items = args.get("items") or []
            if not items:
                return None
            head = items[0]
            code = head.get("couponCode")
            if code:
                return self.raw / f"calculate-price__coupon__{code}.json"
            sig = "_".join(
                f"{i.get('productCode')}x{i.get('quantity', 1)}" for i in items
            )
            return self.raw / f"calculate-price__plain__{sig}.json"

        name = _FIXED.get(tool)
        return self.raw / f"{name}.json" if name else None

    @property
    def hit_rate(self) -> str:
        total = len(self.calls)
        return f"{total - len(self.missed)}/{total}" if total else "0/0"
