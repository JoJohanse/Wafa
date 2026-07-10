# Wafa — AI Agent 数字钱包

> 🌐 语言 / Language: **中文**(本文件) · [English](README.md)

> Wallet for Agent: 一个可供 AI agent 自主使用的 EVM 加密货币钱包,以 Agent Skill 形式分发。

Wafa 让你的 AI agent 拥有自己的加密钱包——创建账户、查询余额、发起 ETH 或 ERC-20(USDC 等)转账。内置策略引擎(单笔/日累计/速率/白名单限额),所有转账在签名前先过策略检查,超限自动拒绝。

## 特性

- 🔑 **创建/导入钱包** — 标准加密 keystore(V3,scrypt/AES)存储,密码保护
- 💰 **余额查询** — 原生币(ETH)与 ERC-20 代币(USDC 等)
- 📤 **安全转账** — 本地签名 + 广播,私钥不上 RPC;EIP-1559 费用估算
- 🛡️ **策略引擎** — 单笔限额、日累计、速率限制、收款白名单、用途约束、紧急停止
- 📝 **全审计** — 每笔操作(含被拒绝的)记录于审计日志
- 🔗 **多 EVM 链** — Ethereum / Base / Polygon 及测试网,配置即用
- 🧩 **Agent Skill 形态** — 可下载、安装即用,响应自然语言指令

## 快速开始

### 1. 安装

```bash
# 克隆或下载本仓库
git clone <repo-url> wafa
cd wafa

# 安装依赖
pip install -r scripts/requirements.txt
```

### 2. 作为 Agent Skill 安装

把整个仓库复制到 skill 发现目录:

```bash
# 用户级(所有项目可用)
cp -r . ~/.agents/skills/wafa/

# 或项目级(仅当前项目)
cp -r . .agents/skills/wafa/
```

详见下方 [作为 Skill 使用](#作为-skill-使用)。

### 3. 初始化

```bash
python scripts/wafa.py init
```

生成 `~/.wafa/` 目录、配置文件(config.yaml、policy.yaml)。

### 4. 创建钱包

```bash
python scripts/wafa.py create --label main
# 会提示设置密码(需二次确认)
```

### 5. 查余额

```bash
python scripts/wafa.py balance              # 原生币(ETH)
python scripts/wafa.py balance --token usdc # USDC
```

向生成的地址转入资金后即可看到余额。

### 6. 转账

```bash
python scripts/wafa.py send 0xRecipient 0.01 --reason "测试转账"
python scripts/wafa.py send 0xRecipient 5 --token usdc --reason "数据购买"
```

转账流程:策略检查 → 输入密码解锁 → 签名广播 → 返回 tx hash。

## 作为 Skill 使用

安装到 skill 目录后,AI agent 会自动发现 `wafa` skill。你可以用自然语言驱动:

- "帮我建个钱包并查余额"
- "我钱包里有多少 USDC?"
- "给 0xABC 转 5 个 USDC,用途是买数据"

Agent 会调用对应的 CLI 命令完成操作。详见 `SKILL.md`。

> **安全提示**:涉及主网真实资金时,先用测试网(Base Sepolia)演练。

## 命令一览

| 命令 | 作用 |
|------|------|
| `init` | 初始化目录与配置 |
| `create --label <名字>` | 创建新钱包 |
| `import <私钥> --label <名字>` | 导入已有私钥 |
| `list` | 列出本地钱包 |
| `balance [--token usdc]` | 查余额 |
| `send <收款> <金额> [--token usdc] --reason <用途>` | 转账 |
| `policy show` | 查看当前策略与今日花费 |
| `history --limit 20` | 查看审计记录 |

可选参数:`--chain base-sepolia`(切链)、`--address 0x...`(指定钱包)。

## 配置

两个配置文件,由 `init` 从 `config/` 模板复制到 `~/.wafa/`:

- **`config.yaml`** — 链与 RPC、代币地址、交易默认值
- **`policy.yaml`** — 支付策略(限额、白名单、速率等)

编辑后即时生效,无需重启。

## 项目结构

```
wafa/
├── SKILL.md                      # Skill 主指令(Agent 读取)
├── README.md                     # 本文件
├── scripts/
│   ├── wafa.py                   # CLI 入口
│   ├── wallet.py                 # 钱包核心(keystore 加解密)
│   ├── chain.py                  # 链交互(余额/转账)
│   ├── policy.py                 # 策略引擎
│   ├── store.py                  # 存储层(路径/状态/审计)
│   └── requirements.txt          # Python 依赖
├── config/
│   ├── config.example.yaml       # 配置模板
│   └── policy.example.yaml       # 策略模板
└── references/
    └── security.md               # 进阶安全(按需阅读)
```

## 安全

- 私钥用 V3 keystore 加密存储,密码通过交互输入,永不打印/进日志/进命令行
- 转账前强制策略检查;超限拒绝且不解锁
- 所有操作写入审计日志
- 真实资金请用可信私有 RPC

进阶安全(托管签名器、账户抽象、多签)见 `references/security.md`。

## 技术栈

- [eth-account](https://eth-account.readthedocs.io/) — 密钥与签名
- [web3.py](https://web3py.readthedocs.io/) — 链交互
- [PyYAML](https://pyyaml.org/) — 配置解析

## 路线图(后续扩展)

- [ ] 非 EVM 链(Bitcoin / Solana)
- [ ] 托管签名器(Turnkey / Privy / Coinbase AgentKit)
- [ ] 账户抽象(ERC-4337 会话密钥)
- [ ] HTTP 402 / x402 支付协商
- [ ] 多账户组合管理、NFT 查询
