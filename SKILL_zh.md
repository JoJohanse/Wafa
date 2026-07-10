---
name: wafa
description: 管理 EVM 加密货币钱包(Ethereum/Base/Polygon 等)的数字钱包工具。用于创建钱包、导入私钥、查询余额、发起 ETH 或 ERC-20(如 USDC)转账。每当用户提到钱包、加密货币、crypto、以太坊、EVM、转账、发送 ETH、查余额、USDC、代币转账、数字资产,或希望让 AI agent 拥有并自主使用区块链钱包时,都应使用本 skill。本 skill 内置策略引擎(单笔/日累计/速率/白名单限额),所有转账在签名前先过策略检查,超限自动拒绝。
---

# Wafa — AI Agent 数字钱包

> 🌐 语言 / Language: **中文**(本文件) · [English](SKILL.md)
>
> **语言切换规则**:如果当前对话以英文为主,请改读 `SKILL.md` 再执行;以中文为主则用本文件。两份文档内容一致,仅语言不同。

Wafa(Wallet for Agent)为 AI agent 提供一个可自主管理的 EVM 加密货币钱包。
用户安装后,agent 能响应"建钱包""查余额""转 1 USDC 给 0x…"等自然语言指令。

## ⚠️ 安全红线(必须遵守)

这些规则保护用户资金,任何情况下都不得违反:

1. **私钥/助记词永不打印、永不写入日志、永不作为命令行参数。**
   钱包用加密 keystore 存储,密码通过交互式输入(getpass),不进命令行。
2. **转账前必须过策略检查。** 超过限额、非白名单收款、无理由时直接拒绝,且不解锁钱包。
3. **真实资金转账需用户确认。** 涉及主网真钱时,先用测试网或向用户复述交易细节后再执行。
4. **不修改 policy.yaml 来绕过限额。** 若用户要求放宽限额,告知风险后由用户自行编辑配置文件。
5. 所有操作都有审计日志,记录于 `~/.wafa/audit.log`。

进阶安全(托管签名器、多签、账户抽象)见 `references/security.md`,按需读取。

## 运行前提

- Python 3.10+
- 已安装依赖: `pip install -r scripts/requirements.txt`
- 首次使用需 `python scripts/wafa.py init` 初始化目录与配置

所有命令从 skill 根目录运行,即 `python scripts/wafa.py <子命令>`。

## 命令速查

| 意图 | 命令 |
|------|------|
| 初始化 | `python scripts/wafa.py init` |
| 创建钱包 | `python scripts/wafa.py create --label <名字>` |
| 导入私钥 | `python scripts/wafa.py import <0x私钥> --label <名字>` |
| 列出钱包 | `python scripts/wafa.py list` |
| 查原生币余额 | `python scripts/wafa.py balance` |
| 查代币余额 | `python scripts/wafa.py balance --token usdc` |
| 发原生币 | `python scripts/wafa.py send <收款> <金额> --reason <用途>` |
| 发代币 | `python scripts/wafa.py send <收款> <金额> --token usdc --reason <用途>` |
| 查策略 | `python scripts/wafa.py policy show` |
| 查审计 | `python scripts/wafa.py history --limit 20` |

可选参数: `--chain base-sepolia`(切换链)、`--address 0x...`(指定钱包)。
完整参数见 `python scripts/wafa.py <命令> -h`。

## 典型工作流

### 场景 1:用户首次使用

> 用户:"帮我建个钱包并查余额"

```
1. python scripts/wafa.py init                 # 初始化(若未做过)
2. python scripts/wafa.py create --label main  # 会提示设密码(需确认)
3. python scripts/wafa.py balance              # 此时余额为 0
```

把生成的地址告知用户,提示其通过交易所或水龙头(测试网)转入资金。

### 场景 2:查 USDC 余额

> 用户:"我钱包里有多少 USDC?"

```
python scripts/wafa.py balance --token usdc
```

输出人类可读金额。若提示"未知代币",需在 `~/.wafa/config.yaml` 的 `tokens.<链>` 下配置 USDC 合约地址。

### 场景 3:发起转账

> 用户:"给 0xRecipient 转 5 个 USDC,用途是买数据"

```
python scripts/wafa.py send 0xRecipient 5 --token usdc --reason "data purchase"
```

流程: 策略检查 → 提示输入密码解锁 → 签名广播 → 输出 tx hash 与浏览器链接。
若被策略拒绝(超限/非白名单),向用户说明拒绝原因,不要尝试绕过。

## 策略引擎说明

策略配置在 `~/.wafa/policy.yaml`(由 `init` 从模板复制)。每次转账签名前自动检查:

- **单笔限额**: `max_per_tx_native` / `max_per_tx_token`
- **日累计限额**: `daily_limit_native` / `daily_limit_token`(每日自动重置)
- **速率限制**: `max_tx_per_minute` / `max_tx_per_hour`(滑动窗口)
- **收款白名单**: `whitelist`(默认关闭)
- **用途约束**: `require_reason` / `allowed_purposes`
- **紧急停止**: `kill_switch: true` 立即拒绝所有转账

`python scripts/wafa.py policy show` 查看当前策略与今日累计花费。
用户可随时编辑 `~/.wafa/policy.yaml` 调整,无需改代码。

## 配置说明

`~/.wafa/config.yaml`(由 `init` 从模板复制)定义:

- **chains**: 支持的链及 RPC 端点、chainId、浏览器(默认含 ethereum/base/polygon 及测试网)
- **tokens**: 常用 ERC-20 地址与精度,便于 `--token usdc` 直接引用
- **tx_defaults**: Gas 估算上浮系数、超时

用户可自行增删链或更换为私有 RPC(Alchemy/Infura,生产推荐)。

## 故障排查

- **无法连接 RPC**:检查 `config.yaml` 的 `rpc_url`,公共端点可能限流,生产用私有 RPC。
- **链 ID 不匹配**:RPC 与配置的 `chain_id` 不符,改对 RPC 或 chain_id。
- **密码错误**:keystore 密码错;密码丢失则资金无法恢复。
- **余额不足**:转账前先查余额,含 Gas 费预留。
- **策略拒绝**:运行 `policy show` 查看限额,由用户决定是否调整 `policy.yaml`。
- **交易未上链**:L2 通常数秒,L1 可能数分钟,查浏览器确认。

## 不做的事

- 不保存/输出私钥或助记词原文
- 不自动放宽策略绕过限额
- 不处理非 EVM 链(Bitcoin/Solana,后续扩展)
- 不做账户抽象/托管签名(见 references/security.md 的扩展方向)
