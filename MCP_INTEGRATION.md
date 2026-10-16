# MCP 集成说明

> 本项目怎么跟麦当劳中国官方 MCP 打交道。工具参数明细见 [`docs/MCP_TOOLS.md`](docs/MCP_TOOLS.md)，
> 这里讲的是**集成视角**：调了哪些、按什么顺序调、为什么这么调、路上踩了哪些坑。
>
> 这份文档是写给要接手代码的人看的，所以语气收着写 —— 表格、参数、错误码一律保持精确，
> 不跟着麦小省卖萌。

## 一、对接口径

| 项 | 值 |
|---|---|
| 端点 | `https://mcp.mcd.cn` |
| 传输 | Streamable HTTP |
| 协议版本 | `2025-06-18` |
| 服务端 | `mcd-mcp 1.0.0` |
| 认证 | `Authorization: Bearer <token>`（HTTP 头） |
| 限流 | 600 次/分钟 |
| 工具总数 | 35 |

认证行为的实测表现（这个很重要，决定了接错 Token 时会看到什么）：

| 情况 | 返回 |
|---|---|
| 带正确 Bearer 头 | `200` |
| **不带**认证头 | `403` |
| **Token 错误** | `401` ← 客户端会把它当成"需要 OAuth"，进而走错路 |

## 二、本项目调用了哪些工具

### 只读（离线 demo 与自测只跑这些）

| Tool | 用在 | 业务价值 |
|---|---|---|
| `query-my-coupons` | M1 | **唯一带真实有效期的券来源**，临期预警的数据基座 |
| `available-coupons` | M1 | 麦麦省可领券池 —— 少查这一路，用户就少省钱 |
| `query-store-coupons` | M1 / M2 | **唯一带 `couponId` / `couponCode` 的来源**，没有它就算不出真实优惠额 |
| `query-nearby-stores` | M1 / M2 | 定位门店，拿到 `storeCode`（比价的前置条件） |
| `query-meals` | M2 | 当前门店实时菜单 —— 价格主数据源，绝不写死 |
| `calculate-price` | M2 | **真实计价**，`discount` 字段就是"省了多少" |
| `query-my-account` | M4 | 积分余额（积分是另一种有保质期的钱） |
| `order-list` | M4 | 历史消费还原 |
| `list-nutrition-foods` | M2 | 热量约束（160 条营养表） |
| `mall-points-products` | M4 | 积分能换什么（50 条） |
| `campaign-calendar` | 感知 | 当前活动档期 |
| `query-lottery-info` | 感知 | 积分抽奖资格（以 `drawDecision.resourceEligible` 为准） |
| `now-time-info` | 全链路 | 服务端时间，用于计算券剩余天数（不依赖本机时钟） |

### 写操作（**不进入任何离线 demo 脚本**）

| Tool | 用在 | 安全边界 |
|---|---|---|
| `auto-bind-coupons` | M1 领券 | 执行前必须向用户复述"将领取 N 张券" |
| `create-order` | M3 下单 | **不可逆**。门店、餐品、金额必须复述确认后才执行 |
| `cancel-order` | M3 撤单 | 需要 `orderId` + `cancelReasonCode` |

> `tools/run_briefing.py`、`run_price.py`、`run_card.py`、`test_all.py`
> **只调只读工具**，不领券、不下单。这一点在脚本 docstring 里都写明了。

## 三、两条主链路的调用时序

### 链路 A：M1 券管家 → 简报

```
                        ┌──────────────────────────┐
                        │  query-my-coupons        │ ① 我的券包（有有效期）
                        │  available-coupons       │ ② 麦麦省可领池
                        │  query-nearby-stores     │ ③ 定位门店
                        └────────────┬─────────────┘
                                     │ storeCode
                                     ▼
                        ┌──────────────────────────┐
                        │  query-store-coupons     │ ④ 门店专属券
                        └────────────┬─────────────┘  （couponId/couponCode 在这）
                                     │
        ┌────────────────────────────┴──────────────────────┐
        │  coupons.digest()  四路合并去重 + 临期分级          │
        │  去重键 = 券名 + 到期时刻（不含到期时刻会吃掉同名券）│
        └────────────────────────────┬──────────────────────┘
                                     ▼
                        ┌──────────────────────────┐
                        │  briefing.build_briefing │ → 今日省钱简报
                        └──────────────────────────┘
```

### 链路 B：M2 真实比价（省钱功能的唯一通路，缺一步就拿不到真实金额）

```
query-meals ──────────→ 菜单（data.categories 分类索引 ⨝ data.meals 映射表）
                          │  ⚠️ 菜单不是平的，必须 join
                          ▼
query-store-coupons ──→ couponId / couponCode / 券专属商品编码
                          │  ⚠️ 钥匙只在这里，券包给不出
                          ▼
calculate-price ──────→ originalPrice / discount / price（单位：分）
                          │  ⚠️ 券商品不带钥匙时价格全是 0 —— 不是免费，是没认出券
                          ▼
        valuation.value_book()  ──→ 按券名把计价结果映射回券包
                          ▼
                  简报 / 卡片里的每一个金额
```

## 四、为什么需要"离线回放"

`tools/replay.py` 把 `(tool, args)` **显式映射**到 `fixtures/` 里已落盘的真实返回：

- 评委和路人**不需要任何 Token** 就能跑完整条链路
- 走的是**同一份引擎代码**（`src/*.py`），只是 caller 换了个实现
- 映射命中不了就抛 `LookupError`，**绝不返回编造的数据**

所以 `python tools/run_briefing.py` 的输出，和 `--live` 联网跑出来的结果逐字一致。

`fixtures/demo/` 是可公开的脱敏版（只换了账号 ID、收货地址、手机号），
`fixtures/raw/` 是本地原始版（已 gitignore）。切换由 `tools/fixtures_path.py` 统一处理。

## 五、接入 WorkBuddy 自定义连接器

配置写入 `~/.workbuddy/mcp.json`（Windows：`C:\Users\<你>\.workbuddy\mcp.json`），
样例见仓库根目录 [`mcp-config.example.json`](mcp-config.example.json)：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamable-http",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

### ⚠️ 一个会让人排查一整天的坑

麦当劳官方活动文档给出的配置样例是：

```json
"type": "streamablehttp"
```

但**本项目实测**：这样写会让连接器的请求头被丢弃，最终连接器状态变成 `unauthorized`
并被**自动移除**。把 `type` 改成带连字符的 **`streamable-http`** 之后恢复正常。

写错时的连锁反应：

```
type 写成 streamablehttp
  → 客户端不识别，headers 被丢弃
  → 请求到达服务端时没有 Authorization 头
  → 服务端返回 401（而不是 403）
  → 客户端把 401 理解成"需要 OAuth 授权"
  → 去走 OAuth 流程 → 404
  → 连接器状态变成 unauthorized，随后被自动移除
```

现象是「**连接器配好了，过一会儿自己消失了**」。而按常规理解，
`401` 确实该走 OAuth 授权 —— 所以很难往"类型名少了个连字符"上想。

如果按官方写法能正常用，就照官方的写；一旦遇到"连接器自己消失"，先试连字符写法。

配套的两个注意点：

1. 改完 `mcp.json` **必须完全重启 WorkBuddy** 才会重新注册（仅保存文件不触发）
2. 排查日志：`~/.workbuddy/logs/<日期>/` 下搜 `MCP-Reconcile` / `MCP-ControllerState`

## 六、已知错误码

| code | 含义 | 出处 |
|---|---|---|
| `600050` | 未收藏门店（`searchType=1` 查收藏店时） | `query-nearby-stores` |
| `400` | 参数缺失/不合法（如到店自取传了 `beCode`） | 通用 |
| 成功 | `success: true, code: 200` | 结构化返回 |

完整错误码与参数坑见 [`docs/MCP_TOOLS.md`](docs/MCP_TOOLS.md)。
