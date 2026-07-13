"""
Wafa 链交互 —— 余额查询、Gas/费用估算、签名广播转账(原生币 + ERC-20)。

使用 web3.py v6+ API(snake_case, raw_transaction)。
所有链参数从 config.yaml 读取, 默认 Base。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, TransactionNotFound

from erc20 import ERC20_ABI, Token
from store import get_chain_config, load_config


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------

def connect(chain: str | None = None) -> tuple[Web3, dict]:
    """连接到指定链的 RPC, 返回 (w3, chain_config)。"""
    config = load_config()
    cc = get_chain_config(config, chain)
    w3 = Web3(Web3.HTTPProvider(cc["rpc_url"]))
    if not w3.is_connected():
        raise ConnectionError(f"无法连接到链 '{chain or config.get('default_chain')}' 的 RPC: {cc['rpc_url']}")
    # 校验 chainId 匹配, 防止配错 RPC
    actual_id = w3.eth.chain_id
    if actual_id != cc["chain_id"]:
        raise ValueError(
            f"链 ID 不匹配: RPC 返回 {actual_id}, 配置为 {cc['chain_id']}。"
            f" 请检查 config.yaml 中 '{chain}' 的 rpc_url。"
        )
    return w3, cc


# ---------------------------------------------------------------------------
# 余额查询
# ---------------------------------------------------------------------------

@dataclass
class Balance:
    address: str
    chain: str
    symbol: str
    amount: float          # 人类可读单位
    raw: int               # 原始整数(wei / 最小单位)
    is_token: bool = False
    token_address: str | None = None


def get_native_balance(w3: Web3, address: str, chain_config: dict) -> Balance:
    """查询原生币(ETH/MATIC)余额。"""
    raw = w3.eth.get_balance(Web3.to_checksum_address(address))
    decimals = chain_config.get("native_decimals", 18)
    amount = raw / (10 ** decimals)
    return Balance(
        address=address,
        chain=chain_config.get("native_symbol", "ETH"),
        symbol=chain_config.get("native_symbol", "ETH"),
        amount=amount,
        raw=raw,
    )


def get_token_balance(w3: Web3, address: str, token_address: str, chain_config: dict) -> Balance:
    """查询 ERC-20 代币余额。"""
    checksum_token = Web3.to_checksum_address(token_address)
    checksum_owner = Web3.to_checksum_address(address)
    contract = w3.eth.contract(address=checksum_token, abi=ERC20_ABI)
    try:
        raw = contract.functions.balanceOf(checksum_owner).call()
        decimals = contract.functions.decimals().call()
        symbol = contract.functions.symbol().call()
    except ContractLogicError as e:
        raise ValueError(f"调用代币合约失败(地址可能不是 ERC-20): {e}")
    amount = raw / (10 ** decimals)
    return Balance(
        address=address,
        chain=chain_config.get("native_symbol", "?"),
        symbol=symbol,
        amount=amount,
        raw=raw,
        is_token=True,
        token_address=token_address,
    )


# ---------------------------------------------------------------------------
# 费用估算
# ---------------------------------------------------------------------------

@dataclass
class FeeEstimate:
    gas_limit: int
    max_priority_fee_per_gas: int   # wei
    max_fee_per_gas: int            # wei
    total_cost_wei: int             # gas_limit * max_fee_per_gas (上限)

    def human_cost_eth(self, decimals: int = 18) -> float:
        return self.total_cost_wei / (10 ** decimals)


def estimate_fee(w3: Web3, from_addr: str, to: str, data: str = "0x", value: int = 0) -> FeeEstimate:
    """估算 Gas 与 EIP-1559 费用。"""
    from_ck = Web3.to_checksum_address(from_addr)
    to_ck = Web3.to_checksum_address(to)
    # 估算 gas limit
    gas_limit = w3.eth.estimate_gas({
        "from": from_ck,
        "to": to_ck,
        "value": value,
        "data": data,
    })
    # EIP-1559 费用
    try:
        priority = w3.eth.max_priority_fee
    except Exception:
        priority = Web3.to_wei(1, "gwei")
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas", Web3.to_wei(1, "gwei"))
    # maxFee = 2 * baseFee + priority(常见两倍缓冲)
    max_fee = 2 * base_fee + priority
    total = gas_limit * max_fee
    return FeeEstimate(
        gas_limit=gas_limit,
        max_priority_fee_per_gas=priority,
        max_fee_per_gas=max_fee,
        total_cost_wei=total,
    )


# ---------------------------------------------------------------------------
# 转账 —— 统一的 send() 接口
# ---------------------------------------------------------------------------

@dataclass
class Receipt:
    """发送后的链上结果。status 是唯一需要分支判断的字段。

    status:  "success" — 已上链且执行成功(receipt.status == 1)
             "failed"  — 已上链但执行回退(receipt.status == 0)
             "pending" — 已广播但在 receipt_timeout 内未确认
    tx_hash:      交易哈希(0x...)
    block_number: 上链区块号; pending 时为 None
    gas_used:     实际消耗 gas; pending 时为 None
    explorer_url: 区块浏览器链接(从 chain_config 派生)
    """
    status: str
    tx_hash: str
    block_number: int | None = None
    gas_used: int | None = None
    explorer_url: str | None = None

    @property
    def ok(self) -> bool:
        """是否确认成功(仅 status == 'success')。"""
        return self.status == "success"


def send(
    w3: Web3,
    chain_config: dict,
    from_account: Any,            # eth_account.Account(含 .address 与 .key)
    to_address: str,
    amount_human: float,
    token: Token | None = None,
    gas_multiplier: float = 1.1,
    receipt_timeout: int = 30,
) -> Receipt:
    """发送原生币或 ERC-20 代币。本地签名后广播, 等待 receipt, 返回链上结果。

    吸收: 地址校验、精度换算、fee 估算、tx 构造、签名、广播、receipt 等待。
    私钥不上 RPC —— 只在本地签名。

    token=None 发送原生币; token=Token(...) 发送 ERC-20。

    错误模型:
      - 广播前失败(地址非法、签名失败、广播被拒)→ 抛异常
      - 广播后链上结果 → 返回 Receipt(status):
          success / failed(链上回退) / pending(超时未确认)
    """
    # 地址校验(seam 上校验, 任何调用方都受保护)
    if not Web3.is_address(to_address):
        raise ValueError(f"非法收款地址: {to_address}")

    from_ck = Web3.to_checksum_address(from_account.address)
    to_ck = Web3.to_checksum_address(to_address)

    # 构造 tx dict —— 原生与 token 路径不同, 但后半段(签名+广播+receipt)统一
    if token is None:
        tx = _build_native_tx(w3, chain_config, from_ck, to_ck, amount_human, gas_multiplier)
    else:
        tx = _build_token_tx(w3, chain_config, from_ck, to_ck, amount_human, token, gas_multiplier)

    return _sign_broadcast_await(w3, tx, from_account, chain_config, receipt_timeout)


def _build_native_tx(
    w3: Web3,
    chain_config: dict,
    from_ck: str,
    to_ck: str,
    amount_human: float,
    gas_multiplier: float,
) -> dict:
    """构造 EIP-1559 原生币交易 dict。"""
    decimals = chain_config.get("native_decimals", 18)
    value = int(round(amount_human * (10 ** decimals)))
    fee = estimate_fee(w3, from_ck, to_ck, value=value)
    gas_limit = int(fee.gas_limit * gas_multiplier)
    nonce = w3.eth.get_transaction_count(from_ck)
    return {
        "type": 2,  # EIP-1559
        "chainId": chain_config["chain_id"],
        "from": from_ck,
        "to": to_ck,
        "value": value,
        "nonce": nonce,
        "gas": gas_limit,
        "maxFeePerGas": fee.max_fee_per_gas,
        "maxPriorityFeePerGas": fee.max_priority_fee_per_gas,
    }


def _build_token_tx(
    w3: Web3,
    chain_config: dict,
    from_ck: str,
    to_ck: str,
    amount_human: float,
    token: Token,
    gas_multiplier: float,
) -> dict:
    """构造 ERC-20 transfer 交易 dict。

    用 contract.functions.transfer(...).build_transaction 获取 calldata + 默认字段,
    再显式补 nonce(不同 provider 对 build_transaction 的 nonce 自动填充行为不一致),
    最后做 gas 上浮。
    """
    value = int(round(amount_human * (10 ** token.decimals)))
    token_ck = Web3.to_checksum_address(token.address)
    contract = w3.eth.contract(address=token_ck, abi=ERC20_ABI)
    tx = contract.functions.transfer(to_ck, value).build_transaction({"from": from_ck})
    # build_transaction 不保证填充 nonce —— 显式补上
    if "nonce" not in tx:
        tx["nonce"] = w3.eth.get_transaction_count(from_ck)
    tx["gas"] = int(tx["gas"] * gas_multiplier)
    return tx


def _sign_broadcast_await(
    w3: Web3,
    tx: dict,
    from_account: Any,
    chain_config: dict,
    receipt_timeout: int,
) -> Receipt:
    """签名 + 广播 + 等待 receipt。send() 的共享后半段。

    广播前失败(签名/广播)→ 抛异常。
    广播后: receipt.status==1 → success; ==0 → failed; 超时 → pending。
    """
    signed = w3.eth.account.sign_transaction(tx, from_account.key)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash = tx_hash_bytes.hex()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    explorer_url = f"{chain_config.get('explorer', '')}/tx/{tx_hash}".strip() or None

    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=receipt_timeout)
    except TimeExhausted:
        return Receipt(
            status="pending",
            tx_hash=tx_hash,
            explorer_url=explorer_url,
        )

    if receipt.status == 1:
        status = "success"
    else:
        status = "failed"

    return Receipt(
        status=status,
        tx_hash=tx_hash,
        block_number=receipt.blockNumber,
        gas_used=receipt.gasUsed,
        explorer_url=explorer_url,
    )
