"""
erc20.py 测试 —— Token 值对象、ERC20_ABI、resolve() 解析。

resolve() 的核心行为:
  - config 别名命中 → Token 从 config 读 decimals, 不查链
  - config 地址命中 → 同上
  - 陌生地址 → 查链上 decimals() → Token(消灭硬编码 6 的脚枪)
  - 陌生地址非合约 → ValueError
  - 不像地址 → None

使用 EthereumTesterProvider 测陌生地址的链上 decimals() 查询。
"""
from __future__ import annotations

import warnings

import pytest
import solcx
from eth_account import Account
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

import erc20

# 静默 eth-tester/py-evm 的 DeprecationWarning 噪音
warnings.filterwarnings("ignore", category=DeprecationWarning)


# ---------------------------------------------------------------------------
# ABI 常量校验
# ---------------------------------------------------------------------------

class TestERC20ABI:
    def test_abi_contains_required_functions(self):
        """ERC20_ABI 应含 balanceOf / decimals / symbol / transfer"""
        names = {fn["name"] for fn in erc20.ERC20_ABI}
        assert {"balanceOf", "decimals", "symbol", "transfer"}.issubset(names)

    def test_abi_function_count(self):
        assert len(erc20.ERC20_ABI) == 4


# ---------------------------------------------------------------------------
# Token 值对象
# ---------------------------------------------------------------------------

class TestToken:
    def test_fields(self):
        t = erc20.Token(address="0x" + "ab" * 20, decimals=6)
        assert t.address == "0x" + "ab" * 20
        assert t.decimals == 6

    def test_zero_decimals(self):
        t = erc20.Token(address="0x" + "ab" * 20, decimals=0)
        assert t.decimals == 0


# ---------------------------------------------------------------------------
# ERC-20 transfer calldata 构造(离线, 无需 EVM)
# ---------------------------------------------------------------------------

class TestERC20Calldata:
    def test_transfer_calldata_selector(self):
        """ERC-20 transfer(address,uint256) 的 selector 应为 0xa9059cbb"""
        w3 = Web3()
        token = "0x" + "ab" * 20
        to = "0x" + "11" * 20
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token), abi=erc20.ERC20_ABI
        )
        tx = contract.functions.transfer(
            Web3.to_checksum_address(to), 5_000_000
        ).build_transaction({
            "from": Web3.to_checksum_address("0x" + "cd" * 20),
            "nonce": 0, "gas": 60000, "chainId": 8453,
            "maxFeePerGas": 10 ** 9, "maxPriorityFeePerGas": 10 ** 8,
        })
        data = tx["data"]
        assert data.startswith("0xa9059cbb")
        assert len(data) == 138
        assert tx["to"].lower() == token.lower()

    def test_transfer_calldata_signable(self):
        """构造的 ERC-20 transfer tx 应可被签名"""
        w3 = Web3()
        acct = Account.create()
        token = "0x" + "ab" * 20
        to = "0x" + "11" * 20
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token), abi=erc20.ERC20_ABI
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
# resolve() —— config 命中路径(不查链)
# ---------------------------------------------------------------------------

_MOCK_ERC20_SRC = """
pragma solidity ^0.8.20;

contract MockERC20 {
    string public symbol = "MOCK";
    uint8 public decimals = 6;
    mapping(address => uint256) public balanceOf;

    constructor() {
        balanceOf[msg.sender] = 1000000 * 10**6;
    }

    function transfer(address to, uint256 amt) public returns (bool) {
        require(balanceOf[msg.sender] >= amt, "insufficient balance");
        balanceOf[msg.sender] -= amt;
        balanceOf[to] += amt;
        return true;
    }
}
"""


class TestResolveFromConfig:
    def test_by_alias(self, tmp_wafa_home):
        """config 别名 'usdc' → Token, decimals 从 config 读"""
        import store
        cfg = store.load_config()
        w3 = Web3(EthereumTesterProvider())
        tok = erc20.resolve(w3, cfg, "base", "usdc")
        assert tok is not None
        assert tok.address.startswith("0x")
        assert tok.decimals == 6  # config 里配的

    def test_by_address(self, tmp_wafa_home):
        """用已配置的 USDC 地址反查 → Token"""
        import store
        cfg = store.load_config()
        usdc_addr = cfg["tokens"]["base"]["usdc"]["address"]
        w3 = Web3(EthereumTesterProvider())
        tok = erc20.resolve(w3, cfg, "base", usdc_addr)
        assert tok is not None
        assert tok.address.lower() == usdc_addr.lower()

    def test_unknown_returns_none(self, tmp_wafa_home):
        """不像地址的未知别名 → None"""
        import store
        cfg = store.load_config()
        w3 = Web3(EthereumTesterProvider())
        assert erc20.resolve(w3, cfg, "base", "nonexistent") is None

    def test_empty_ref_returns_none(self, tmp_wafa_home):
        """空字符串 → None"""
        import store
        cfg = store.load_config()
        w3 = Web3(EthereumTesterProvider())
        assert erc20.resolve(w3, cfg, "base", "") is None


# ---------------------------------------------------------------------------
# resolve() —— 陌生地址路径(查链上 decimals)
# ---------------------------------------------------------------------------

@pytest.fixture
def tester_w3():
    w3 = Web3(EthereumTesterProvider())
    return w3


@pytest.fixture
def deployed_token(tester_w3):
    """部署一个 MockERC20(decimals=6), 返回合约地址。"""
    w3 = tester_w3
    compiled = solcx.compile_source(
        _MOCK_ERC20_SRC, output_values=["abi", "bin"], solc_version="0.8.20"
    )
    cd = compiled["<stdin>:MockERC20"]
    deployer = Account.create()
    w3.eth.send_transaction({
        "to": deployer.address,
        "from": w3.eth.accounts[0],
        "value": Web3.to_wei(5, "ether"),
    })
    w3.eth.wait_for_transaction_receipt(
        w3.eth.send_transaction({"to": deployer.address, "from": w3.eth.accounts[0], "value": 0})
    )
    nonce = w3.eth.get_transaction_count(deployer.address)
    tx = {
        "from": deployer.address,
        "data": "0x" + cd["bin"],
        "gas": 2000000,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gasPrice": w3.eth.gas_price,
    }
    signed = w3.eth.account.sign_transaction(tx, deployer.key)
    receipt = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(signed.raw_transaction)
    )
    assert receipt.status == 1, "MockERC20 部署失败"
    return receipt.contractAddress, cd["abi"]


class TestResolveFromChain:
    def test_unknown_address_queries_chain(self, tester_w3, deployed_token, tmp_wafa_home):
        """陌生地址(不在 config) → 查链上 decimals() → Token(address, 6)。

        这是消灭 decimals:6 脚枪的关键测试: 陌生地址不再硬编码,
        而是查链上真实 decimals。
        """
        import store
        cfg = store.load_config()
        token_addr, abi = deployed_token

        tok = erc20.resolve(tester_w3, cfg, "base", token_addr)
        assert tok is not None
        assert tok.address.lower() == token_addr.lower()
        assert tok.decimals == 6  # MockERC20 声明的 decimals

    def test_non_contract_address_raises(self, tester_w3, tmp_wafa_home):
        """陌生地址是 EOA(无合约代码) → ValueError("不是有效的 ERC-20 合约")。

        不再静默返回 decimals:6 —— 显式失败。
        """
        import store
        cfg = store.load_config()
        eoa = Account.create().address  # 普通账户, 无合约代码

        with pytest.raises(ValueError, match="不是有效的 ERC-20 合约"):
            erc20.resolve(tester_w3, cfg, "base", eoa)

    def test_unknown_address_case_insensitive(self, tester_w3, deployed_token, tmp_wafa_home):
        """陌生地址大小写不敏感(大写也能查)"""
        import store
        cfg = store.load_config()
        token_addr, _ = deployed_token
        # config 里是小写, 传入大写 checksum 版本
        checksum_addr = Web3.to_checksum_address(token_addr)
        tok = erc20.resolve(tester_w3, cfg, "base", checksum_addr)
        assert tok is not None
        assert tok.decimals == 6
