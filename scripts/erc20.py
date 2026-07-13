"""
Wafa ERC-20 模块 —— 代币标识、ABI、引用解析。

将 --token 参数(别名或合约地址)解析为 Token 值对象, 供 chain.send() 和
余额查询使用。消灭 store.resolve_token 对陌生地址硬编码 decimals:6 的脚枪:
陌生地址现在查链上 decimals(), 查不到则抛异常。

解析优先级:
  1. config 别名命中(usdc)     → Token(address, decimals) 从 config 读, 不查链
  2. config 地址命中(0x...)    → 同上
  3. 陌生 0x... 地址            → 查链上 decimals() → Token(address, decimals)
                                 查链失败 → ValueError("不是有效的 ERC-20 合约")
  4. 不像地址                   → None
"""

from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3
from web3.exceptions import ContractLogicError

# ERC-20 最小 ABI: balanceOf / decimals / symbol / transfer
ERC20_ABI = [
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


@dataclass
class Token:
    """ERC-20 代币的链上标识, 供 chain.send() 使用。

    address:  合约地址(checksum 或小写均可, send 内部转换)
    decimals: 精度(用于 human amount → 最小单位换算)
    """
    address: str
    decimals: int


def resolve(w3: Web3, config: dict, chain: str, token_ref: str) -> Token | None:
    """将 --token 参数解析为 Token 值对象。

    token_ref 可以是别名(如 'usdc')或合约地址(0x...)。
    返回 None 表示 config 查不到且不像地址; 陌生地址查链失败抛 ValueError。
    """
    if not token_ref:
        return None
    token_ref = token_ref.lower()
    tokens = config.get("tokens", {}).get(chain, {})

    # 1. 按别名命中
    if token_ref in tokens:
        info = tokens[token_ref]
        return Token(address=info["address"], decimals=info.get("decimals", 6))

    # 2. 按地址命中(大小写不敏感)
    for info in tokens.values():
        if isinstance(info, dict) and info.get("address", "").lower() == token_ref:
            return Token(address=info["address"], decimals=info.get("decimals", 6))

    # 3. 陌生地址 —— 查链上 decimals(), 不再硬编码 6
    if token_ref.startswith("0x") and len(token_ref) == 42:
        return _resolve_from_chain(w3, token_ref)

    # 4. 不像地址
    return None


def _resolve_from_chain(w3: Web3, token_address: str) -> Token:
    """对陌生合约地址查链上 decimals(), 构造 Token。

    如果地址不是有效的 ERC-20 合约(无 decimals 函数), 抛 ValueError。
    """
    checksum = Web3.to_checksum_address(token_address)
    contract = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    try:
        decimals = contract.functions.decimals().call()
    except (ContractLogicError, Exception) as e:
        raise ValueError(
            f"地址不是有效的 ERC-20 合约(无法读取 decimals): {token_address} — {e}"
        )
    return Token(address=token_address, decimals=decimals)
