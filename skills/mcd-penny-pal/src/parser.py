"""麦小省 · 响应解析层

麦当劳 MCP 的返回体**不是**纯 JSON。2026-10-16 实测到四种形态，
必须同时兼容，否则引擎会在某些接口上静默拿到空数据：

A. 结构化 JSON 型
   外层是给 LLM 看的字段说明文本，真实数据在 `## Original Response` 之后，
   形如 {"success":bool,"code":int,"message":str,"traceId":str,"data":...}
   例：query-my-account / now-time-info / query-nearby-stores / mall-*

B. 纯 Markdown 型
   完全不返回 JSON，只有中文列表。
   例：available-coupons / query-my-coupons / campaign-calendar

C. 紧凑表格型
   data 字段本身是**字符串**，内嵌一张自描述表：
       [160]{productName,energyKj,...}:
         猪柳麦满分,null,1288,...
   例：list-nutrition-foods

D. 混合型
   成功给 Markdown、失败给内联 JSON，且 JSON 夹在说明文字与尾注之间。
   例：query-nearby-stores（缺参数时）

结论：**解析器必须在任意位置探测 JSON**，按固定标记切会在 D 上翻车。

零第三方依赖。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from models import (
    Campaign,
    Coupon,
    LotteryInfo,
    LotteryPrize,
    MallProduct,
    Nutrition,
    PointsAccount,
    Store,
    StoreCoupon,
)

# ===================================================================== JSON 探测

_DECODER = json.JSONDecoder()


def extract_json(text: str) -> Any | None:
    """在文本任意位置探测最像「业务响应」的 JSON。

    做法：从每个 `{` / `[` 起尝试 raw_decode，取评分最高者。
    评分偏好带 data / code / success 的对象，避免把说明文字里的
    零散花括号误认成数据。
    """
    if not isinstance(text, str):
        return text

    best: Any = None
    best_key = (-1, -1)

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch not in "{[":
            i += 1
            continue
        try:
            obj, end = _DECODER.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        key = (_json_score(obj), end - i)
        if key > best_key:
            best, best_key = obj, key
        i = max(end, i + 1)

    return best


def _json_score(obj: Any) -> int:
    """给一个候选 JSON 打分，用于挑出真正的业务响应。"""
    if isinstance(obj, list):
        return 10
    if not isinstance(obj, dict):
        return -1
    score = 0
    if "data" in obj:
        score += 100
    if "code" in obj:
        score += 40
    if "success" in obj:
        score += 30
    if "message" in obj:
        score += 10
    return score


# ===================================================================== 统一封装


@dataclass
class Payload:
    """一次工具调用的归一化结果。

    调用方只需要看 `kind` / `ok` / `data` / `items`，不必关心原始形态。
    """

    tool: str = ""
    raw: str = ""
    kind: str = "empty"  # json | markdown | table | mixed | empty
    json_body: Any = None
    data: Any = None
    items: list[dict[str, str]] = field(default_factory=list)
    total: int | None = None  # Markdown 型头部声明的总数，如「共 6 张可用优惠券」

    # ------------------------------------------------------------ 状态
    @property
    def code(self) -> int | None:
        if isinstance(self.json_body, dict):
            c = self.json_body.get("code")
            return c if isinstance(c, int) else None
        return None

    @property
    def ok(self) -> bool:
        if isinstance(self.json_body, dict):
            return bool(self.json_body.get("success"))
        # Markdown 型没有成功标志，有内容即视为成功
        return bool(self.raw.strip())

    @property
    def message(self) -> str:
        if isinstance(self.json_body, dict):
            return str(self.json_body.get("message") or "")
        return ""

    @property
    def is_empty(self) -> bool:
        """是否是「成功的空结果」，用于区分「没数据」和「出错了」。"""
        if not self.ok:
            return False
        if not self.data and not self.items:
            return True
        return False

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Payload {self.tool} kind={self.kind} ok={self.ok} code={self.code}>"


# 探测用正则。注意 re.M —— 这些正则要扫整段文本，不是单行。
_BULLET_RE = re.compile(r"^\s{0,4}[-*]\s+", re.M)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$", re.M)
_FIELD_RE = re.compile(r"^(?:\*\*)?\s*([^：:*\n]{1,24}?)\s*(?:\*\*)?\s*[：:]\s*(.*)$")
_IMG_TAG_RE = re.compile(r"<img[^>]*>", re.I)          # 整段标签，用于清除
_IMG_SRC_RE = re.compile(r'<img[^>]*?src="([^"]+)"', re.I)  # 只取 src，用于收集
_TABLE_HEAD_RE = re.compile(r"^\s*\[(\d+)\]\{([^}]*)\}")
_ANY_HEAD_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_TOTAL_RE = re.compile(r"共\s*(\d+)\s*[张条个项]")

# 结构化型返回里，真实数据在这个标记之后；标记之前是给 LLM 看的字段说明
_ORIGIN_MARKER = "## Original Response"


def _payload_region(text: str) -> str:
    """剥掉「字段说明」前言，返回真正承载业务的文本区。

    不剥的话，每一条结构化返回都带着 `# API Response Information`
    和一堆 `- **data**: ...` 说明，会被误判成 Markdown 型。
    """
    idx = text.find(_ORIGIN_MARKER)
    if idx >= 0:
        return text[idx + len(_ORIGIN_MARKER):]
    return text


def parse(tool: str, raw: Any) -> Payload:
    """把任意形态的返回统一成 Payload。"""
    if raw is None:
        return Payload(tool=tool, kind="empty")

    if isinstance(raw, (dict, list)):
        body = raw
        text = json.dumps(raw, ensure_ascii=False)
    else:
        text = str(raw)
        # JSON 在全文任意位置探测（混合型的 JSON 会夹在说明文字中间）
        body = extract_json(text)

    payload = Payload(tool=tool, raw=text, json_body=body)
    payload.data = body.get("data") if isinstance(body, dict) else None

    total = _TOTAL_RE.search(text)
    payload.total = int(total.group(1)) if total else None

    region = _payload_region(text)
    has_md = bool(_BULLET_RE.search(region) or _HEADING_RE.search(region))
    has_table = isinstance(payload.data, str) and bool(_TABLE_HEAD_RE.match(payload.data))

    if has_table:
        payload.kind = "table"
    elif body is not None and has_md:
        payload.kind = "mixed"
    elif body is not None:
        payload.kind = "json"
    elif has_md:
        payload.kind = "markdown"

    # 只有「真的是给人看的列表」才解析条目。
    # mixed + 失败（如 query-nearby-stores 缺参数）时，正文里那些
    # `- **门店名称**: data[].storeName` 是字段说明，不是数据，必须排除。
    if payload.kind == "markdown" or (payload.kind == "mixed" and payload.ok):
        payload.items = _parse_markdown_items(region)

    return payload


# ===================================================================== Markdown 列表解析


@dataclass
class MdItem:
    """Markdown 列表里的一个条目，字段名保留原始中文。"""

    section: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)

    def get(self, *names: str) -> str:
        """按候选字段名依次取值，兼容不同接口的命名差异。"""
        for n in names:
            if n in self.fields and self.fields[n]:
                return self.fields[n]
        return ""


def _parse_markdown_items(text: str) -> list[dict[str, str]]:
    """把 Markdown 型返回拆成条目列表。

    实测三种排版，靠**标题层级**区分，不用猜：

    1. 二级标题当条目标题（券包）
           ## 中薯条买一送一
           - **优惠**: ¥13.5 (用券价格)
           - **有效期**: 2026-10-12 10:30-2026-10-16 23:59

    2. bullet 当条目（麦麦省券池）
           - 优惠券标题：中薯条买一送一 \\
             状态：可领取 \\

    3. bullet 当条目、字段带粗体（活动日历）
           -   **活动标题**：xxx
               **活动内容介绍**：多行正文

    规则：`##` 开新条目；`#` / `###` 及以上一律视作分组标题（section）。
    这样 campaign-calendar 的 `#### 2026年10月12日 往期回顾` 自然成为分组，
    而券包的 `## 券名` 自然成为条目标题。
    """
    items: list[MdItem] = []
    section = ""
    cur: MdItem | None = None
    lines: list[str] = []
    from_heading = False

    def flush() -> None:
        nonlocal cur, lines, from_heading
        if cur is not None:
            # _parse_item_lines 返回的是全新字典，必须先把
            # 从二级标题拿到的 _title 存下来再覆盖，否则条目标题会丢
            heading_title = cur.fields.get("_title")
            cur.fields, cur.images = _parse_item_lines(lines)
            if heading_title:
                cur.fields["_title"] = heading_title
            items.append(cur)
        cur, lines, from_heading = None, [], False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        head = _ANY_HEAD_RE.match(line)
        if head:
            flush()
            level, title = len(head.group(1)), head.group(2).strip()
            if level == 2:
                # 二级标题 = 一个条目
                cur = MdItem(section=section)
                cur.fields["_title"] = title
                lines = []
                from_heading = True
            else:
                section = title
            continue

        if _BULLET_RE.match(line):
            content = _BULLET_RE.sub("", line, count=1).strip()
            if cur is not None and from_heading:
                lines.append(content)  # 券包：bullet 是当前条目的字段行
            else:
                flush()
                cur = MdItem(section=section)
                lines = [content]
            continue

        if cur is not None and line.strip():
            lines.append(line.strip())

    flush()

    out: list[dict[str, str]] = []
    for it in items:
        row = dict(it.fields)
        row["_section"] = it.section
        if it.images:
            row["_image"] = it.images[0]
        out.append(row)
    return out


def _parse_item_lines(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """从若干行里抽出「键：值」，并收集图片链接。"""
    fields: dict[str, str] = {}
    images: list[str] = []
    last_key: str | None = None

    for ln in lines:
        images.extend(_IMG_SRC_RE.findall(ln))
        # 必须清掉**整个** <img ...> 标签。只清 src 的话，
        # 剩下的 alt="..." height="auto" 会被当成字段值拼进去。
        line = _IMG_TAG_RE.sub("", ln).strip()
        if not line:
            continue

        m = _FIELD_RE.match(line)
        if m:
            key = m.group(1).strip().strip("*").strip()
            val = _clean(m.group(2))
            if not key:
                continue
            # 同名字段重复出现时保留第一个（券池里偶有重复标题）
            if key not in fields:
                fields[key] = val
            last_key = key
        elif last_key:
            extra = _clean(line)
            if extra:
                fields[last_key] = f"{fields[last_key]} {extra}".strip()

    return fields, images


def _clean(value: str) -> str:
    """去掉 Markdown 换行标记 `\\` 与多余空白。"""
    return value.replace("\\", " ").strip()


# ===================================================================== 紧凑表格解析


def parse_compact_table(data: Any) -> list[dict[str, Any]]:
    """解析 list-nutrition-foods 那种自描述紧凑表格。

    格式：``[160]{col1,col2,...}:`` 后跟若干行逗号分隔的记录。
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, str):
        return []

    lines = [ln for ln in data.splitlines() if ln.strip()]
    if not lines:
        return []

    head = _TABLE_HEAD_RE.match(lines[0].strip())
    if not head:
        return []

    columns = [c.strip() for c in head.group(2).split(",") if c.strip()]
    rows: list[dict[str, Any]] = []

    for ln in lines[1:]:
        cells = [c.strip() for c in ln.strip().split(",")]
        if len(cells) < len(columns):
            cells += [""] * (len(columns) - len(cells))
        row: dict[str, Any] = {}
        for col, cell in zip(columns, cells):
            row[col] = None if cell.lower() in ("null", "") else _to_number(cell)
        rows.append(row)

    return rows


def _to_number(value: str) -> Any:
    try:
        f = float(value)
    except ValueError:
        return value
    return int(f) if f.is_integer() else f


# ===================================================================== 领域转换


#: mixed 型返回的**前言里会出现文档标题**，它们不是券。
#: 实测 `query-store-coupons` 不带 `## Original Response` 标记，
#: 前言中的 `## Response Structure` / `## 字段说明` / `## 输出格式` /
#: `## {data[].title}(共{券码}张)` 会被按二级标题误解析成券名，
#: 结果是**真券一条没解析出来、垃圾条目进了券池**。
_DOC_NOISE_TITLES = frozenset({
    "response structure", "response", "字段说明", "字段", "输出格式",
    "返回结构", "返回格式", "说明", "示例", "参数说明", "注意事项", "请求参数",
})


def _looks_like_doc_title(title: str) -> bool:
    """判断一个 markdown 标题是不是"文档说明"而非"券名"。"""
    t = (title or "").strip()
    if not t:
        return True
    if t.lower() in _DOC_NOISE_TITLES:
        return True
    # 模板占位符，如 `{data[].title}(共{券码}张)`
    return "{" in t or "}" in t


def _coupons_from_data(data: Any, pool: str) -> list[Coupon]:
    """从结构化 `data` 列表里抽券。抽不到返回空列表（调用方回退）。"""
    out: list[Coupon] = []
    if not isinstance(data, list):
        return out
    for row in data:
        if not isinstance(row, dict):
            continue
        title = _pick(row, "couponName", "title", "name", "spuName")
        if not title:
            continue
        out.append(
            Coupon(
                title=str(title),
                status=str(_pick(row, "status", "statusText") or ""),
                pool=pool,
                image=str(_pick(row, "couponImage", "image", "spuImage") or ""),
                # ⚠️ 门店券的到期时间叫 `tradeDateTime`（形如
                # 「2026-10-12 10:30:00-2026-10-16 23:59:59」），
                # 不是 expireTime。漏了它，门店券转出来就没有有效期，
                # 与券包里的同一张券**去重匹配不上**，会被算成两张。
                validity=str(_pick(row, "tradeDateTime", "validity") or ""),
                expire_at_hint=str(_pick(row, "expireTime", "endTime", "validEndTime") or ""),
                coupon_id=str(_pick(row, "couponId") or ""),
                coupon_code=str(_pick(row, "couponCode") or ""),
            )
        )
    return out


def to_coupons(payload: Payload, pool: str) -> list[Coupon]:
    """把任意券接口的返回转成 Coupon 列表。

    同时吃下三种排版：
      - 领券池：`优惠券标题` + `状态`
      - 券包：  条目名来自 `## 标题`，另有 `优惠` / `有效期` / `标签`
      - 结构化：couponName / status / expireTime

    ⚠️ **结构化数据优先于 markdown 条目。**
    `query-store-coupons` 会返回"字段说明前言 + 内联 JSON"的混合体，
    前言里的文档标题会被 markdown 解析器当成券名。所以只要从
    `data` 列表里抽到了券，就直接用，**不再看 markdown 条目**。

    `_title`（来自二级标题）优先于 `优惠券标题`，两者不会同时出现。
    """
    structured = _coupons_from_data(payload.data, pool)
    if structured:
        return structured

    out: list[Coupon] = []
    if payload.items:
        for row in payload.items:
            raw_title = _pick(row, "_title", "优惠券标题", "券名称", "优惠券名称",
                              "title", "couponName")
            if raw_title is None:
                continue
            title = str(raw_title)
            # 二级标题形态的条目要多一道关：前言里的文档标题不是券
            if "_title" in row and _looks_like_doc_title(title):
                continue
            out.append(
                Coupon(
                    title=title,
                    status=str(_pick(row, "状态", "status") or ("已领取" if row.get("_title") else "可领取")),
                    pool=pool,
                    image=str(row.get("_image", "")),
                    price_text=str(_pick(row, "优惠", "券面价", "price") or ""),
                    validity=str(_pick(row, "有效期", "到期时间", "过期时间", "expireTime") or ""),
                    tags=str(_pick(row, "标签", "tags") or ""),
                    received_at=str(_pick(row, "领取时间") or ""),
                )
            )
    return out


def to_store_coupons(payload: Payload) -> list[StoreCoupon]:
    """把 `query-store-coupons` 的结构化返回转成 StoreCoupon。

    这是**唯一**能拿到 `couponId` / `couponCode` 的地方 ——
    M2 计价和 M3 下单都靠它。券包（query-my-coupons）给不出这两个字段。

    返回的 `data` 是 list，每条带 `products[0].productCode`（券专属商品编码，
    例如「中薯条买一送一」是 9900012387，而普通中薯条是 4810）。
    """
    data = payload.data
    if not isinstance(data, list):
        return []

    out: list[StoreCoupon] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        prods = row.get("products") or []
        first = prods[0] if prods and isinstance(prods[0], dict) else {}
        out.append(
            StoreCoupon(
                title=str(row.get("title") or ""),
                coupon_id=str(row.get("couponId") or ""),
                coupon_code=str(row.get("couponCode") or ""),
                trade_datetime=str(row.get("tradeDateTime") or ""),
                promotion_id=str(row.get("promotionId") or ""),
                product_code=str(first.get("productCode") or ""),
                product_name=str(first.get("productName") or ""),
                raw=row,
            )
        )
    return out


def to_campaigns(payload: Payload) -> list[Campaign]:
    out: list[Campaign] = []
    for row in payload.items:
        title = _pick(row, "活动标题", "标题", "title")
        if not title:
            continue
        out.append(
            Campaign(
                title=title,
                period=row.get("_section", ""),
                content=_pick(row, "活动内容介绍", "活动内容", "内容"),
                image=row.get("_image", ""),
            )
        )
    return out


def to_stores(payload: Payload) -> list[Store]:
    data = payload.data
    if not isinstance(data, list):
        return []
    out: list[Store] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        out.append(
            Store(
                code=str(_pick(row, "storeCode") or ""),
                name=str(_pick(row, "storeName") or ""),
                address=str(_pick(row, "address") or ""),
                distance=str(_pick(row, "distance") or ""),
                business_status=str(_pick(row, "businessStatus") or ""),
            )
        )
    return out


def to_account(payload: Payload) -> PointsAccount | None:
    d = payload.data
    if not isinstance(d, dict):
        return None
    return PointsAccount(
        available=_num(d.get("availablePoint")),
        accumulative=_num(d.get("accumulativePoint")),
        expired=_num(d.get("expiredPoint")),
        frozen=_num(d.get("frozenPoint")),
        used=_num(d.get("usedPoint")),
        next_month_expire=_num(d.get("nextMouthExpirePoint")),  # 拼写以接口为准
        current_month_expire=_num(d.get("currentMouthExpirePoint")),
        currency=str(d.get("currency") or "麦享会积分"),
    )


def to_mall_products(payload: Payload) -> list[MallProduct]:
    raw = payload.data
    rows = raw if isinstance(raw, list) else []
    out: list[MallProduct] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            MallProduct(
                spu_id=int(_num(row.get("spuId"))),
                name=str(row.get("spuName") or ""),
                point=_num(row.get("point")),
                price=_opt_num(row.get("price")),
                category=str(row.get("catName") or ""),
                image=str(row.get("spuImage") or ""),
                status=str(row.get("status") or ""),
                selling=bool(row.get("selling")),
            )
        )
    return out


def to_lottery(payload: Payload) -> LotteryInfo | None:
    d = payload.data
    if not isinstance(d, dict):
        return None

    decision = d.get("drawDecision")
    decision = decision if isinstance(decision, dict) else {}

    prizes: list[LotteryPrize] = []
    for p in d.get("prizes") or []:
        if isinstance(p, dict):
            prizes.append(
                LotteryPrize(
                    name=str(_pick(p, "prizeName", "name", "spuName") or ""),
                    raw=p,
                )
            )

    return LotteryInfo(
        activity_name=str(d.get("activityName") or ""),
        activity_code=str(d.get("activityCode") or ""),
        status_text=str(d.get("activityStatusText") or ""),
        begin_time=str(d.get("beginTime") or ""),
        end_time=str(d.get("endTime") or ""),
        draw_point=_num(d.get("drawPoint")),
        available_point=_num(d.get("availablePoint")),
        # 资格只认这个字段，不从 availableTimes 推断
        eligible=bool(decision.get("resourceEligible")),
        reason=decision.get("reason"),
        prizes=prizes,
    )


def to_nutrition(payload: Payload) -> list[Nutrition]:
    rows = parse_compact_table(payload.data)
    out: list[Nutrition] = []
    for r in rows:
        out.append(
            Nutrition(
                product_name=str(r.get("productName") or ""),
                description=r.get("nutritionDescription"),
                energy_kj=_num(r.get("energyKj")),
                energy_kcal=_num(r.get("energyKcal")),
                protein=_num(r.get("protein")),
                fat=_num(r.get("fat")),
                carbohydrate=_num(r.get("carbohydrate")),
                sodium=_num(r.get("sodium")),
                calcium=_num(r.get("calcium")),
            )
        )
    return out


# ===================================================================== 小工具


def _pick(row: dict[str, Any], *names: str) -> Any:
    for n in names:
        v = row.get(n)
        if v not in (None, "", [], {}):
            return v
    return None


def _num(value: Any) -> float:
    """接口把金额/积分都以**字符串**返回，这里统一转数字，失败记 0。"""
    if value is None:
        return 0.0
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _opt_num(value: Any) -> float | None:
    """可能不存在的数值：转不出来就是 None，不要用 0 冒充。"""
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def iter_fields(text: str) -> Iterator[tuple[str, str]]:
    """从任意 Markdown 文本里迭出「键：值」对，供调试与二次开发使用。"""
    for ln in text.splitlines():
        m = _FIELD_RE.match(_IMG_TAG_RE.sub("", ln).strip())
        if m:
            yield m.group(1).strip().strip("*"), _clean(m.group(2))
