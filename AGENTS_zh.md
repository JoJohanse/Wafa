# AGENTS.md — Wafa 工作区指令

> 🌐 语言 / Language: **中文**(本文件) · [English](AGENTS.md)

## 项目概述

Wafa (Wallet for Agent) 是一个以 Agent Skill 形式分发的 EVM 加密钱包,供 AI
agent 自主创建账户、查询余额、发起 ETH/ERC-20 转账。内置策略引擎在签名前拦截
每笔转账(单笔/日累计/速率/白名单/kill_switch)。不实现 HTTP 402 支付协商(见
`idea.md` 的构想与路线图)。

- 仓库: https://github.com/JoJohanse/Wafa
- 语言: Python ≥3.10 (开发用 conda 环境 `wafa`, Python 3.14)
- 形态: 仓库根 = Skill 包; 用户克隆后复制到 `~/.agents/skills/wafa/`

## 目录结构

```
wafa/
├── scripts/          # 全部可执行代码(非包, 平铺 .py, 通过 sys.path 互导入)
│   ├── wafa.py        # CLI 入口(init/create/import/list/balance/send/policy/history)
│   ├── wallet.py      # 钱包核心: V3 keystore 加解密、unlock、secure_zero
│   ├── chain.py       # 链交互: 连接、余额、EIP-1559 签名广播(本地签名, 私钥不上 RPC)
│   ├── erc20.py       # ERC-20 解析: alias/address → Token(address, decimals), 陌生地址查链上 decimals()
│   ├── policy.py      # 策略引擎: 签名前拦截(限额/速率/白名单/用途/kill_switch)
│   └── store.py       # 存储层: 路径、配置、状态、审计日志(无密钥材料)
├── tests/            # pytest 测试, 隔离 WAFA_HOME(见 conftest.py)
├── config/           # config.example.yaml / policy.example.yaml 模板(init 复制到 ~/.wafa/)
├── references/        # security.md 进阶安全(按需读, 不入 SKILL.md 主体)
├── SKILL.md / SKILL_zh.md      # Skill 指令(英文主 / 中文), Agent 读取
├── README.md / README_zh.md    # 用户文档(英文主 / 中文)
├── AGENTS.md / AGENTS_zh.md    # 工作区指令(英文主 / 中文, 本文件)
├── CONTEXT.md        # 领域词汇表(domain glossary), 改敏感术语前先读
├── idea.md           # 原始构想(402/机器支付), 仅供参考不实现
└── pytest.ini
```

## 常用命令

```bash
# 激活开发环境
conda activate wafa

# 安装依赖(运行时 / 开发+测试)
pip install -r scripts/requirements.txt
pip install -r scripts/requirements-dev.txt   # 含 pytest

# 跑测试(全部 126 个, 沙箱无 RPC 也能完整通过)
python -m pytest
python -m pytest tests/test_wallet.py -v      # 单文件

# 直接调用 CLI(从仓库根)
python scripts/wafa.py init
python scripts/wafa.py create --label main    # 交互式输入密码(getpass)
python scripts/wafa.py balance --token usdc
echo '0xKEY' | python scripts/wafa.py import --label x   # 私钥走 stdin, 不进命令行
```

测试用 `WAFA_HOME` 环境变量隔离数据目录(见 `tests/conftest.py` 的
`tmp_wafa_home`/`fresh_state`/`strict_policy` fixtures),**绝不触碰真实 `~/.wafa`**。
沙箱环境无法访问区块链 RPC, 故 `test_chain.py` 只测离线逻辑(地址校验、签名、
calldata 构造); 真实广播需在能联网的环境补 `-m e2e` 测试。

## 架构边界与分层(改代码前必读)

1. **`scripts/` 是平铺模块, 不是 Python 包**: 模块间用 `import store` 直接引用,
   `wafa.py` 在启动时 `sys.path.insert(0, scripts_dir)`。不要改成相对导入或加
   `__init__.py` 除非同时调整所有 import。

2. **分层依赖是单向的**:
   - `wafa.py`(CLI) → wallet/chain/policy/erc20 → store
   - `store.py` 是最底层, **不 import 任何上层模块**, 不涉及密钥
   - `wallet.py` 持有密钥; `chain.py` 持有 RPC 连接; 两者互不依赖
   - `erc20.py` 解析 --token 参数为 `Token(address, decimals)`, 陌生地址查链上
     `decimals()`(消灭旧 `store.resolve_token` 硬编码 decimals=6 的脚枪)

3. **策略引擎在签名前拦截**: `policy.check()` 返回 `Decision`, 拒绝时**不解锁
   钱包、不接触私钥**。所有转账路径(`wafa.cmd_send`)必须先过 `policy.check()` 再
   调 `wallet.unlock()`。这个顺序是安全红线, 不要倒置。

## 安全红线(改 wallet.py/policy.py/wafa.py 时强制遵守)

1. **私钥永不打印、不进日志、不作为命令行参数**。`import` 子命令从 stdin
   (`getpass`)读取; 位置参数 `private_key` 已移除, `test_cli.py::
   test_import_no_positional_key` 守护这一点(误改回位置参数测试会红)。
2. **密钥材料用 `bytearray` 持有, 用完 `secure_zero()` 原地清零**。`bytes` 不可
   变无法清零, 故 `unlock`/`import_wallet` 全程用 `bytearray`, 在 `finally` 中
   清零。`secure_zero` 是真实实现(早先的 `_zero_bytes` 是 noop 占位, 已替换)。
3. **keystore KDF 加固**: scrypt N=2^18, r=8, p=1(远超 eth-account 默认)。
   `test_wallet.py::test_keystore_kdf_hardened` 守护 N≥2^18。
4. **密码强度强制**: `check_password_strength` 要求 ≥10 位、≥3 类字符、弱密码
   黑名单; `create`/`import` 拒绝弱密码。
5. 文件权限: `~/.wafa/` 与 `keystores/` 目录 700, keystore 文件 600, audit.log
   每次 append 后 600。

## 约定

- **密钥 / 私钥 / 助记词**: 绝不写进 print/repr/log/json.dump。审计日志
  (`store.append_audit`)只记地址、金额、用途、tx_hash、拒绝原因, **严禁把密钥
  或密码放进 `**detail`**。
- **地址大小写**: keystore 文件名存小写地址, `Account.address` 返回 checksum
  混合大小写; 测试中比较地址用 `.lower()`(见 `test_wallet.py`/`test_cli.py` 的
  历史 bugfix)。
- **配置改动即时生效**: `config.yaml`/`policy.yaml` 由 `init` 从 `config/` 模板
  复制到 `~/.wafa/`, 用户编辑后无需重启。
- **多语言文档**: `SKILL.md`/`README.md` = 英文(主), `*_zh.md` = 中文; 改一处
  要同步另一处, 顶部有语言切换链接。
- **领域术语**: 改敏感名词前先读 `CONTEXT.md` 的 glossary。

## 已知 gotchas

- **沙箱 TLS 出口被拦**: `git push` 走 HTTPS 会 TLS 握手失败; 推送改用 GitHub
  Git Data API(blobs→tree→commit→update ref)脚本, 用后即删(`scripts/_push_api.py`
  不入库)。`git fetch` 同样不通, 故经 API 推送的 commit SHA 与本地 `git commit`
  的 SHA 会不同(内容相同), 下次能 `git fetch` 时 `git reset --hard origin/main`
  对齐即可。
- **`getpass` 在非 TTY 阻塞**: 用管道喂密码会挂起; 测试通过直接调用模块函数绕过
  CLI 的 getpass, 而非通过 `echo | wafa.py`。
- **公共 RPC 不可用**: 沙箱无法连 `eth.llamarpc.com`/`mainnet.base.org` 等公共
  端点; 端到端广播测试要在联网环境补。