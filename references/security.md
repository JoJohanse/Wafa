# Wafa 安全注意事项

本文件讨论进阶安全话题。SKILL.md 已覆盖日常使用必须遵守的红线,这里补充原理与扩展方向。
按需阅读,不必每次执行命令都加载。

## 1. 威胁模型与当前防护

### 1.1 私钥泄露

**威胁**: 私钥一旦泄露,资产立即被盗,且不可逆。

**当前防护**:
- 私钥用 V3 keystore 标准加密(scrypt KDF + AES-128-CTR + MAC),密码由用户掌握。
- **keystore 加密参数加固**: scrypt N=2^18(262144),r=8,p=1,远超 eth-account 默认,显著增加离线暴力破解成本。
- **密码强度强制**: 创建/导入时校验密码(≥10 位、≥3 类字符、常见弱密码黑名单),弱密码直接拒绝。
- **密钥材料内存清零**: 私钥一律以 `bytearray` 持有,使用后通过 `secure_zero()` 原地覆写(`bytes` 不可变,无法清零,故全程避免)。`unlock()` 与 `import_wallet()` 均在 `finally` 中清零临时材料。
- **私钥永不作为命令行参数**: `import` 子命令从 stdin(getpass)读取私钥,不暴露在 shell 历史/进程列表/系统日志。
- keystore 文件权限设为 600(仅所有者可读写);`~/.wafa/` 与 `keystores/` 目录权限 700;审计日志 600。
- 私钥仅在 `unlock()` 后短暂存在于内存,用完即弃,不落盘、不打印、不进日志。
- 密码通过 `getpass`(交互式 stdin)输入,不进命令行参数。

**局限**: Python 的 GC 不保证立即回收被解除引用的对象,`secure_zero` 只能清零我们显式持有的 `bytearray`,无法清除解释器内部副本(如 `Account.from_key` 内部短暂的拷贝)。这是本地托管钱包的固有取舍;对更高安全要求,见第 3 节的托管签名器方案(私钥永不进入 agent 进程)。

### 1.2 误操作/失控 Agent

**威胁**: AI agent 误解意图或被注入恶意指令,发起非授权大额转账;或被诱导循环调用 `send` 反复试探 keystore 密码(本地在线暴力破解)。

**当前防护(策略引擎 + 解锁节流)**:
- 单笔限额、日累计限额: 即使 agent 失控,损失有上限。
- 速率限制: 防止高频狂转账。
- 收款白名单(可选): 限制资金流向。
- 用途理由要求: 强制留痕。
- kill_switch: 紧急停止开关。
- **解锁失败节流**: 连续错误密码达到阈值(默认 5 次,可经 `policy.yaml` 的 `unlock_throttle` 调整)后锁定钱包若干秒(默认 300 秒)。锁定期内拒绝解锁且**不跑 scrypt**,既不给试密机会也不消耗 CPU。成功解锁清零计数。状态持久化在 `state.json`,重启不丢失。
- 审计日志: 每笔操作(含被拒绝的、解锁失败/锁定)都留痕,便于事后追查。

**这是"应用层软护栏"**: 在签名前拒绝。更强的"链上硬约束"见第 3 节。

### 1.3 RPC 信任

**威胁**: 恶意或被入侵的 RPC 节点可伪造余额、欺骗交易状态。

**当前防护**:
- 本地签名后广播原始交易(`send_raw_transaction`),私钥不上 RPC。
- 转账连接时校验 chainId 匹配配置,防配错。

**建议**: 生产环境用可信私有 RPC(Alchemy/Infura/QuickNode),不用公共端点处理大额。

## 2. 密码与备份

- **keystore 密码 = 资金的钥匙之一**。密码丢失且无私钥备份 = 资金永久丢失。
- **离线备份 keystore 文件**: 把 `~/.wafa/keystores/*.json` 安全备份(加密 U 盘、密码管理器附件等)。
- **密码强度**: 至少 16 位,含大小写+数字+符号。用密码管理器生成与存储。
- **不要把 keystore 与密码放在同一位置**。

## 3. 进阶安全方向(后续扩展)

当资金规模增长或部署到云端生产 agent 时,本地 keystore 不再足够。可选升级路径:

### 3.1 托管签名器(MPC / TEE)

让私钥永不进入 agent 进程,改为向签名服务发请求:

- **Turnkey**: 私钥在 [AWS Nitro Enclaves (TEE)](https://aws.amazon.com/blogs/web3/building-secure-verifiable-blockchain-key-management-on-aws-nitro-enclaves-at-turnkey/) 内生成与签名,提供策略门控 API。
- **Privy Agentic Wallets**: 面向 agent 的嵌入式钱包,内置策略控制。
- **Coinbase AgentKit**: MPC 安全钱包 + 可编程会话上限。

agent 持有 `wallet_address + api_key + permissions`,而非私钥本身。

### 3.2 账户抽象(ERC-4337)

把"策略"从应用层下沉到合约层,即使配置被篡改也无法绕过:

- 链上单笔/日累计限额(合约强制)。
- 方法白名单(只允许特定合约的特定函数)。
- 时间锁(留出干预窗口)。
- Gas 赞助(Paymaster,让 agent 用 USDC 付 Gas)。
- 会话密钥(临时授权,可吊销)。

### 3.3 多签(Multi-sig)

大额资金用 Gnosis Safe 等多签钱包,Wafa 仅作为签名者之一,需额外确认方才生效。

## 4. 紧急响应

- **怀疑泄露**: 立即 `policy.yaml` 设 `kill_switch: true`,然后把资金转到新钱包。
- **发现异常转账**: 查 `~/.wafa/audit.log`,定位 tx hash,在浏览器核实。
- **agent 行为异常**: 优先 kill_switch,再排查策略配置与日志。

## 5. 合规提示

加密货币转账在不同司法辖区有 KYC/AML/税务申报要求。AI agent 自主支付的合规边界尚在演进。生产部署前咨询法律顾问,尤其涉及稳定币与法币兑换。
