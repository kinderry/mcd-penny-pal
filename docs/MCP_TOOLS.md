# 麦当劳 MCP 工具参考（实测）

> 本文件由 `fixtures/raw/_tools_list.json` 整理生成。参数以 `tools/list` 返回的 `inputSchema` 为准，**非人工猜测**。
> 对接口径：`https://mcp.mcd.cn` · Streamable HTTP · 协议 `2025-06-18` · 实测 35 个工具
> 参数后带 `*` 为必填。
>
> 这里全是接口事实，一句卖萌的话都没有 —— 查参数的时候，可爱是负资产。

## 券 · 四路券池

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `available-coupons` | — | — |
| `auto-bind-coupons` | — | — |
| `query-my-coupons` | — | `page`, `pageSize` |
| `query-store-coupons` | `beType`, `orderType`, `storeCode` | `beCode`, `reservationDate` |
| `query-promotions` | `beType`, `orderType`, `storeCode` | `beCode`, `reservationDate` |

## 点餐 · 门店与菜单

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `query-nearby-stores` | `beType`, `searchType` | `city`, `keyword` |
| `query-meals` | `beType`, `orderType`, `storeCode` | `beCode`, `reservationDate` |
| `query-meal-detail` | `beType`, `code`, `orderType`, `storeCode` | `beCode`, `reservationDate` |
| `calculate-price` | `beType`, `orderType`, `storeCode` | `beCode`, `gmServiceCode`, `items`, `needTableware`, `reservationDate`, `withOrder` |
| `query-meal-assistance` | `storeCode` | `beCode`, `beType`, `orderType`, `reservationDate` |

## 点餐 · 订单

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `create-order` | `beType`, `orderType`, `storeCode` | `addressId`, `beCode`, `gmServiceCode`, `items`, `needTableware`, `remark`, `reservationDate`, `takeWayCode`, `withOrder` |
| `query-order` | `orderId` | — |
| `order-list` | — | — |
| `cancel-order` | `cancelReasonCode`, `orderId` | — |
| `query-survey-coupon` | `orderId` | — |

## 外送专用

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `delivery-query-addresses` | — | — |
| `delivery-create-address` | `address`, `addressDetail`, `city`, `contactName`, `phone` | `gender` |
| `delivery-query-stores` | `addressId`, `beType` | — |

## 积分 · 账户与商城

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `query-my-account` | — | — |
| `mall-points-products` | — | `catRuleIds` |
| `mall-product-detail` | `spuId` | — |
| `mall-create-order` | `skuId`, `spuCategory` | `addressId`, `count` |
| `mall-order-list` | — | `lastId`, `size` |
| `mall-order-detail` | `orderId` | — |

## 积分 · 抽奖

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `query-lottery-info` | — | — |
| `draw-lottery` | — | — |
| `query-my-prizes` | — | `pageNum`, `pageSize` |

## 主题活动 · 派对

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `query-party-city` | `spuId` | — |
| `query-party-store` | `code` | `latitude`, `longitude`, `spuId` |
| `query-party-store-date` | `spuId`, `storeCode` | — |
| `query-party-store-session` | `dateStr`, `spuId`, `storeCode` | — |
| `party-order-create` | `partyType` | `code`, `count`, `dateStr`, `id`, `leftNum`, `partyTimeInfo`, `skuId`, `spuId`, `storeCode`, `timeEnd`, `timeStart` |

## 基础

| Tool | 必填参数 | 可选参数 |
|---|---|---|
| `now-time-info` | — | — |
| `list-nutrition-foods` | — | — |
| `campaign-calendar` | — | `specifiedDate` |

---

## 返回体格式（关键）

实测麦当劳 MCP 返回**两套形态**，解析层必须分别处理。这是本项目最容易踩的坑。

### A. 结构化 JSON 型

外层套一段"给 LLM 看的"字段说明文本，真实数据在 `## Original Response` 标记**之后**：

```
{ success, code, message, datetime, traceId, data }
```

实测涉及：`list-nutrition-foods`、`now-time-info`、`query-my-account`、`query-nearby-stores`、`delivery-query-addresses` 等。

### B. 纯 Markdown 型

不返回 JSON，只有中文可读列表，必须按 Markdown 解析：

实测涉及：`available-coupons`、`query-my-coupons`、`campaign-calendar` 等。

### C. 混合型（最坑）

同一个工具可能**两种都返回**——成功时给 Markdown，失败时给内联 JSON。例如 `query-nearby-stores` 失败时返回：

```
...说明文字...
{"success":false,"code":600050,"message":"收藏餐厅列表为空，请您提供城市+关键词进行搜索",...}
## 展示结果时，每个门店的 storeCode 和 beCode 必须完整展示，
```

即 JSON 被夹在说明文字和尾注之间，前后都有干扰文本。解析器需要"在任意位置探测 JSON 对象"的能力，不能只按固定标记切。

### D. 说明前言 + 内联 JSON（最阴的一种）

`query-store-coupons` **不带** `## Original Response` 标记，返回体长这样（原文节选）：

```
## Response Structure
- **重要**: 同一券ID可对应多个券码，展示时按券ID分组
## 字段说明
| 字段 | 数据源 | 展示规则 | 是否展示
|-----|--------|----------|--|
| 券名 | `data[].title` | 完整显示 | 强制展示 |
...
## 输出格式
## {data[].title}(共{券码}张)
|券ID|券码|有效期|适用商品|商品编码|
|:---:|:---:|:---:|:---:|:---:|
|{data[].couponId}|...|...|
{"success":true,"code":200,...,"data":[{"title":"中薯条买一送一",...}]}
```

**坑**：前言里的 `## Response Structure`、`## 字段说明`、`## 输出格式`、
`## {data[].title}(共{券码}张)` 都是**二级标题**，会被 Markdown 解析器
当成"券名"。实测后果是**真券一条没解析出来、4 条垃圾条目混进券池**
（券包总数从 6 虚报成 10）。

**修法**：`to_coupons()` 里**结构化数据优先**——只要从 `data` 列表里
抽到了券，就直接用，**不再看 markdown 条目**。另加一道保险：
二级标题形态的条目若命中文档标题词表或含 `{}` 模板占位符，直接丢弃。

---

## 错误码

| code | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 缺少参数 |
| 600050 | 收藏餐厅列表为空，需传 `city` + `keyword` |
| 401 | Token 无效 / 已过期 / 未提供 |
| 429 | 限流（超过 600 次/分钟） |

---

## 已知坑（实测踩过）

1. **`query-nearby-stores` 的参数不是 `address`** —— 必传 `beType` + `searchType`。`searchType=1` 查收藏门店（未收藏会报 600050），`searchType=2` 需配合 `city` + `keyword`。
2. **到店自取（`beType=1`）时 `beCode` 是无效参数，传了会报错**；而来得速（`beType=5`）和外送（`orderType=2`）必传 `beCode`。
3. **`orderType` 语义**：`1` = 到店（含到店自取 + 得来速车道取餐），`2` = 外送（含麦乐送 + 企业团餐）。
4. **积分抽奖资格判断**：必须以 `drawDecision.resourceEligible` 为准，**不得**从 `availableTimes` 为 0 或 null 自行推断。混合规则下 `availableTimes=0` 且 `nextConsumption.type=POINTS` 表示仍可用积分抽。
5. **积分商城下单与派对下单是两个不同工具**：`mall-create-order` 仅适用 `shopId=2`（商品券/实物），门店+场次类的用 `party-order-create`（`shopId=5`）。搞错会失败。
6. **同一商品多个兑换必须分别下单并排队**，等前一单响应成功再调下一次 `mall-create-order`。
7. **`calculate-price` 的金额单位是「分」**（`originalPrice=2700` 表示 ¥27），
   而菜单 `query-meals` 的 `currentPrice` 是**元字符串**（`"13.5"`）。混用差 100 倍。
8. **券商品不带 `couponId` + `couponCode` 时价格全为 0** —— 那不是"免费"，是"没认出这张券"。
   想拿原价做对比，要用 `originalSubtotal`，不能拿 0 当真。
9. **门店券的到期字段叫 `tradeDateTime`**（形如 `2026-10-12 10:30:00-2026-10-16 23:59:59`），
   **不是** `expireTime`。漏了它，门店券转出来就没有有效期，
   于是**与券包里同一张券去重匹配不上**——「中薯条买一送一」被算成两张，
   券包总数从 6 虚报成 9。
10. **`query-store-coupons` 的返回会被"字段说明"污染**，详见上文「返回体格式 D」。
   它是单次实测里最隐蔽的一个坑：不报错、不抛异常，只是**安静地返回错数据**。
11. **「领券成功」不等于「进卡包」**：`auto-bind-coupons` 报"总计 7 张、成功 7 张"，
    但券包只到账 6 张（「免费脆薯饼」缺席）。引擎一律以券包为资产口径。
12. **券包（`query-my-coupons`）不给 `couponId` / `couponCode`**，
    这两个字段只在门店券里。想说"这张券值多少"，必须先反查门店券。

---

## 计价链路（M2 实测）

写「省钱」功能必须走这条路，缺一步就拿不到真实金额：

```
query-meals          → 菜单（categories 索引 + meals 映射表，要 join）
query-store-coupons  → couponId / couponCode / 券专属商品编码
calculate-price      → 真实计价，拿到 discount
create-order         → 下单（要 takeWayCode，来自 calculate-price 的 takeWayList）
```

### 菜单为什么不是平的

`query-meals` 的 `data` 由两个字段组成，必须 join：

- `data.categories[]` = `{name, meals: [{code, tags}]}` —— 只有分类索引
- `data.meals` = `{code: {name, image, currentPrice, originalPrice, canWithOrder}}` —— 详情映射表

实测 15 个分类、105 个餐品。

### 券的钥匙在门店券里，不在券包里

| 字段 | 券包 `query-my-coupons` | 门店券 `query-store-coupons` |
|---|---|---|
| 标题 / 有效期 | ✅ | ✅ |
| `couponId` | ❌ | ✅ |
| `couponCode` | ❌ | ✅ |
| 券专属商品编码 | ❌ | ✅ `products[0].productCode` |

`query-store-coupons` 实测返回（到店自取）：

```json
{
  "title": "中薯条买一送一",
  "couponId": "8692944B045928451618BFF22F535D2A",
  "couponCode": "MCD61U9Y010008T76000N",
  "tradeDateTime": "2026-10-12 10:30:00-2026-10-16 23:59:59",
  "products": [{"productCode": "9900012387", "productName": "中薯条买一送一"}],
  "promotionId": "112458028"
}
```

⚠️ `products[0].productCode` 是**券专属商品编码**（9900012387），
不是菜单里的普通中薯条（4810）。计价必须用它。

### calculate-price 的返回

| 字段 | 含义 |
|---|---|
| `originalPrice` | 原价（分）|
| `discount` | **优惠金额（分）** ← 「省了多少」就是这个 |
| `price` | 实付（分）|
| `productList[]` | `{productCode, productName, quantity, originalSubtotal, subtotal}` |
| `takeWayList[]` | `{code, title, subtitle}` —— 下单的 `takeWayCode` 从这里取 |

### 实测数据（2026-10-16 · 门店 1450118 · 到店自取）

| 券名 | 券面标注 | 原价 | **真实优惠** | 实付 |
|---|---|---|---|---|
| 中薯条买一送一 | ¥13.5 | ¥27.0 | **¥13.5** | ¥13.5 |
| 9.9元中杯冰美式 | ¥9.9 | ¥19.0 | **¥9.1** | ¥9.9 |
| 王牌小食拼 | ¥15.9 | ¥22.0 | **¥6.1** | ¥15.9 |

⚠️ **「王牌小食拼」券面写 ¥15.9，真实只省 ¥6.1** ——
券面那个数是用券**后的价格**，不是省下的钱。
排序和播报一律以 `discount` 为准，永远不要拿券面价当省钱额。
