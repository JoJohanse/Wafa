"""
chain.py 测试 —— 链交互的离线可验证部分。

注: 本沙箱环境无法访问区块链 RPC, 故:
  - 不测试 connect / get_native_balance / send_* 的真实广播
  - 重点测试: 地址校验、交易构造与签名正确性、ERC-20 calldata 构造
这些是无需网络即可验证的核心逻辑。
连接/广播的端到端测试应在能访问 RPC 的环境用 -m 'e2e' 标记运行。
"""
from __future__ import annotations

import pytest
from web3 import Web3
from eth_account import Account

import chain as chain_mod


# ---------------------------------------------------------------------------
# 地址校验
# ---------------------------------------------------------------------------

class TestIsValidAddress:
    @pytest.mark.parametrize("addr,valid", [
        ("0x" + "ab" * 20, True),                    # 标准合法
        ("0x" + "AB" * 20, True),                    # 大写 hex
        ("0x" + "12" * 20, True),                    # 数字 hex
        ("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", True),  # Vitalik
        ("0x123", False),                            # 太短
        ("0x" + "ab" * 19, False),                   # 少一位
        ("0x" + "ab" * 21, False),                   # 多一位
        ("0xZZ" + "00" * 19, False),                 # 非 hex 字符
        ("notanaddress", False),                     # 无 0x 前缀
        ("", False),                                 # 空
        (None, False),                               # None
        (12345, False),                              # 非字符串
    ])
    def test_validation(self, addr, valid):
        assert chain_mod.is_valid_address(addr) == valid


# ---------------------------------------------------------------------------
# 交易构造与签名(离线)
# ---------------------------------------------------------------------------

class TestNativeTxConstruction:
    def test_native_tx_signed_correctly(self):
        """构造 EIP-1559 原生币交易, 本地签名后校验字段"""
        acct = Account.create()
        to = "0x" + "11" * 20
        value = Web3.to_wei(0.01, "ether")

        tx = {
            "type": 2,
            "chainId": 8453,
            "from": acct.address,
            "to": Web3.to_checksum_address(to),
            "value": value,
            "nonce": 0,
            "gas": 21000,
            "maxFeePerGas": Web3.to_wei(0.1, "gwei"),
            "maxPriorityFeePerGas": Web3.to_wei(0.001, "gwei"),
        }
        signed = Web3().eth.account.sign_transaction(tx, acct.key)

        # 签名产物应含 raw_transaction 与 hash
        assert hasattr(signed, "raw_transaction")
        assert hasattr(signed, "hash")
        # raw_transaction 是 bytes, 非空
        assert isinstance(signed.raw_transaction, (bytes, bytearray))
        assert len(signed.raw_transaction) > 0
        # hash 是 32 字节
        assert len(signed.hash) == 32

    def test_tx_value_conversion(self):
        """0.01 ETH 应正确换算为 10^16 wei"""
        value_wei = int(0.01 * 10 ** 18)
        assert value_wei == 10 ** 16


class TestERC20Calldata:
    def test_transfer_calldata_selector(self):
        """ERC-20 transfer(address,uint256) 的 selector 应为 0xa9059cbb"""
        w3 = Web3()
        token = "0x" + "ab" * 20
        to = "0x" + "11" * 20
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token), abi=chain_mod._ERC20_ABI
        )
        tx = contract.functions.transfer(
            Web3.to_checksum_address(to), 5_000_000
        ).build_transaction({
            "from": Web3.to_checksum_address("0x" + "cd" * 20),
            "nonce": 0, "gas": 60000, "chainId": 8453,
            "maxFeePerGas": 10 ** 9, "maxPriorityFeePerGas": 10 ** 8,
        })
        data = tx["data"]
        # selector 是前 4 字节(8 hex 字符)
        assert data.startswith("0xa9059cbb")
        # 完整 calldata: 0x + 8(selector) + 64(address) + 64(amount) = 138 字符
        assert len(data) == 138
        # to 字段应指向代币合约, 不是收款人
        assert tx["to"].lower() == token.lower()

    def test_transfer_calldata_signable(self):
        """构造的 ERC-20 transfer tx 应可被签名"""
        w3 = Web3()
        acct = Account.create()
        token = "0x" + "ab" * 20
        to = "0x" + "11" * 20
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token), abi=chain_mod._ERC20_ABI
        )
        tx = contract.functions.transfer(
            Web3.to_checksum_address(to), 100
        ).build_transaction({
            "from": acct.address,
            "nonce": 0, "gas": 60000, "chainId": 8453,
            "maxFeePerGas": 10 ** 9, "maxPriorityFeePerGas": 10 ** 8,
        })
        signed = w3.eth.account.sign_transaction(tx, acct.key)
        assert len(signed.raw_transaction) > 0


# ---------------------------------------------------------------------------
# ABI 常量校验
# ---------------------------------------------------------------------------

class TestERC20ABI:
    def test_abi_contains_required_functions(self):
        """_ERC20_ABI 应含 balanceOf / decimals / symbol / transfer"""
        names = {fn["name"] for fn in chain_mod._ERC20_ABI}
        assert {"balanceOf", "decimals", "symbol", "transfer"}.issubset(names)

    def test_abi_function_count(self):
        assert len(chain_mod._ERC20_ABI) == 4
