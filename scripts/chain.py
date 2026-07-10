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

from store import get_chain_config, load_config

# ERC-20 最小 ABI: 仅用到 balanceOf / decimals / transfer
_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


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
    contract = w3.eth.contract(address=checksum_token, abi=_ERC20_ABI)
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
# 转账
# ---------------------------------------------------------------------------

@dataclass
class SendResult:
    tx_hash: str
    to: str
    amount_human: float
    symbol: str
    is_token: bool
    explorer_url: str | None = None


def _wait_for_receipt(w3: Web3, tx_hash: bytes, timeout: int = 120) -> dict:
    """等待交易上链, 返回 receipt。"""
    return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)


def send_native(
    w3: Web3,
    chain_config: dict,
    from_account: Any,           # eth_account.Account(含 .address 与 .key)
    to_address: str,
    amount_human: float,
    gas_multiplier: float = 1.1,
) -> SendResult:
    """发送原生币(ETH)。本地签名后广播原始交易, 私钥不上 RPC。"""
    decimals = chain_config.get("native_decimals", 18)
    value = int(round(amount_human * (10 ** decimals)))
    from_ck = Web3.to_checksum_address(from_account.address)
    to_ck = Web3.to_checksum_address(to_address)

    fee = estimate_fee(w3, from_ck, to_ck, value=value)
    gas_limit = int(fee.gas_limit * gas_multiplier)

    nonce = w3.eth.get_transaction_count(from_ck)
    tx = {
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
    signed = w3.eth.account.sign_transaction(tx, from_account.key)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash = tx_hash_bytes.hex()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash

    return SendResult(
        tx_hash=tx_hash,
        to=to_address,
        amount_human=amount_human,
        symbol=chain_config.get("native_symbol", "ETH"),
        is_token=False,
        explorer_url=f"{chain_config.get('explorer', '')}/tx/{tx_hash}".strip(),
    )


def send_token(
    w3: Web3,
    chain_config: dict,
    from_account: Any,
    token_address: str,
    token_decimals: int,
    to_address: str,
    amount_human: float,
    gas_multiplier: float = 1.1,
) -> SendResult:
    """发送 ERC-20 代币。构造 transfer() 调用数据后签名广播。"""
    value = int(round(amount_human * (10 ** token_decimals)))
    from_ck = Web3.to_checksum_address(from_account.address)
    to_ck = Web3.to_checksum_address(to_address)
    token_ck = Web3.to_checksum_address(token_address)

    contract = w3.eth.contract(address=token_ck, abi=_ERC20_ABI)
    transfer_data = contract.functions.transfer(to_ck, value).build_transaction(
        {"from": from_ck}
    )
    # build_transaction 已含 chainId/gas/nonce/费用, 这里只做 gas 上浮
    transfer_data["gas"] = int(transfer_data["gas"] * gas_multiplier)

    signed = w3.eth.account.sign_transaction(transfer_data, from_account.key)
    tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash = tx_hash_bytes.hex()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash

    # 读取代币 symbol 作为显示
    try:
        symbol = contract.functions.symbol().call()
    except Exception:
        symbol = "TOKEN"

    return SendResult(
        tx_hash=tx_hash,
        to=to_address,
        amount_human=amount_human,
        symbol=symbol,
        is_token=True,
        explorer_url=f"{chain_config.get('explorer', '')}/tx/{tx_hash}".strip(),
    )


# ---------------------------------------------------------------------------
# 地址校验
# ---------------------------------------------------------------------------

def is_valid_address(addr: str) -> bool:
    """检查是否为合法 EVM 地址(0x + 40 hex)。"""
    if not isinstance(addr, str):
        return False
    addr = addr.strip()
    if not (addr.startswith("0x") or addr.startswith("0X")):
        return False
    hex_part = addr[2:]
    if len(hex_part) != 40:
        return False
    try:
        int(hex_part, 16)
        return True
    except ValueError:
        return False
