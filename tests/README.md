# Wafa 测试

本目录包含 Wafa 数字钱包 Skill 的单元测试,使用 pytest 框架。

## 运行测试

```bash
# 安装测试依赖(pytest)
pip install pytest

# 在项目根目录运行全部测试
python -m pytest

# 运行单个测试文件
python -m pytest tests/test_wallet.py

# 运行单个测试类/方法
python -m pytest tests/test_wallet.py::TestCreateWallet
python -m pytest tests/test_wallet.py::TestCreateWallet::test_creates_wallet_and_keystore

# 详细输出
python -m pytest -v

# 只看失败摘要
python -m pytest -q
```

## 测试覆盖

| 文件 | 覆盖模块 | 测试重点 |
|------|---------|---------|
| `test_store.py` | `scripts/store.py` | 路径管理、配置读写、状态追踪、审计日志、初始化 |
| `test_wallet.py` | `scripts/wallet.py` | 钱包创建/导入/解锁、密码强度、内存清零、KDF 加固、标签 |
| `test_policy.py` | `scripts/policy.py` | 单笔/日累计限额、速率限制、白名单、用途约束、kill_switch |
| `test_chain.py` | `scripts/chain.py` | 地址校验、EIP-1559 交易构造与签名、ERC-20 calldata 构造(离线) |
| `test_cli.py` | `scripts/wafa.py` | argparse 解析、子命令分发、import 不接受位置参数 |

## 测试隔离

每个测试用独立的临时 `WAFA_HOME` 目录(通过 `conftest.py` 的 `tmp_wafa_home` fixture),
**绝不触碰用户真实的 `~/.wafa`**。测试结束后 pytest 自动清理临时目录。

## 不在测试范围

以下因依赖外部网络或 TTY,不在默认测试套件内:

- **真实 RPC 连接与广播**(`connect`/`get_native_balance`/`send_*`):沙箱环境无法访问区块链节点。
  交易构造与签名逻辑已离线验证;端到端测试应在能访问 RPC 的环境运行。
- **getpass 交互输入**(`create`/`import`/`send` 命令的真实 CLI 执行):非 TTY 下 getpass 会阻塞。
  密码相关功能已在 `test_wallet.py` 通过直接调用模块函数覆盖。

## 通用 fixtures

定义在 `conftest.py`:

- `tmp_wafa_home` — 隔离的临时数据目录(自动 init)
- `fresh_state` — 清空状态的隔离环境
- `strict_policy` — 写入严格限额策略供策略测试用
- `STRONG_PW` — 通过密码强度校验的强密码
- `WHITELIST_ADDR` / `OTHER_ADDR` — 测试用地址
