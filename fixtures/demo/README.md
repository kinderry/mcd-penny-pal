# fixtures/demo —— 可公开的脱敏返回

这里每个文件都是 **2026-10-16 对 `mcp.mcd.cn` 的真实返回**哦 —— 由 `tools/smoke.py`
原样落盘，再经 `tools/make_demo_fixtures.py` 脱敏后生成的。一个字符都没有编。

（这份说明是写给要接手代码的人看的，所以语气收着写。下面的字段表保持精确。）

## 脱敏删了什么

只动**个人信息字段**，一共 6 类、26 处：

| 字段 | 处理方式 |
|---|---|
| `accountId` | 换成全零占位 UUID |
| `addressId` | 换成同长度的占位数字串 |
| `contactName` | 换成「示例」 |
| `phone` | 换成 `1XX****000X` 形态的占位 |
| `fullAddress` | 换成「XX省XX市XX区示例路N号（脱敏示例地址）」 |
| `traceId` | 换成原值的 MD5（去掉调用指纹） |

## 脱敏没动什么

**业务字段一个字符都没改** —— 券名、券码（`couponId` / `couponCode`）、
门店编码（`storeCode`）、商品编码（`productCode`）、价格（`originalPrice` /
`discount` / `price`）、有效期（`tradeDateTime`）、券面文案，全部与真实返回逐字一致。

门店名和门店地址是**公开经营信息**，不是个人信息，保留原样。

所以离线跑出来的每一步都和联网时相同：同一个门店、同一批券、同样的计价结果。

## 验证

```bash
python tools/make_demo_fixtures.py --check   # 自检：扫不到手机号，也扫不到任何原始 PII 值
python tools/test_all.py                     # 109 项自测，全部跑在这批脱敏返回上
```

## 原始返回在哪

本地开发用的未脱敏版本在 `fixtures/raw/`，已被 `.gitignore` 排除，不会进公开仓库。
两个目录之间的切换由 `tools/fixtures_path.py` 统一处理：

```bash
python tools/run_briefing.py          # 默认读 fixtures/demo（脱敏版）
python tools/run_briefing.py --raw    # 读 fixtures/raw（本地真实版）
```
