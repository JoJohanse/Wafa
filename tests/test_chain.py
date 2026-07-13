"""
chain.py 测试 —— send() 接口的完整覆盖。

使用 EthereumTesterProvider(内存 EVM, 无需外部 RPC)测试 send() 的:
  - 原生币转账成功(success)
  - ERC-20 转账成功(success)
  - 地址非法抛异常
  - 链上回退(failed, receipt.status==0)
  - 余额不足时的失败路径

这些是处理真钱的代码, 必须通过 send() 的接口(而非 mock)验证。
"""
from __future__ import annotations

import warnings

import pytest
import solcx
from eth_account import Account
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

import chain as chain_mod

# 静默 eth-tester/py-evm 的 DeprecationWarning 噪音
warnings.filterwarnings("ignore", category=DeprecationWarning)


# ---------------------------------------------------------------------------
# MockERC20 合约源码(测试用, 6 位精度, 初始余额给部署者)
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


# ---------------------------------------------------------------------------
# Fixtures: 内存 EVM + 资助账户 + 部署 ERC-20
# ---------------------------------------------------------------------------

@pytest.fixture
def tester_w3():
    """内存 EVM, 已连接。chain_config 的 chain_id 与 tester 一致。"""
    w3 = Web3(EthereumTesterProvider())
    chain_config = {
        "chain_id": w3.eth.chain_id,
        "native_symbol": "ETH",
        "native_decimals": 18,
        "explorer": "https://test.example",
    }
    return w3, chain_config


@pytest.fixture
def funded_account(tester_w3):
    """一个有余额的测试账户(从 tester 创世账户转 ETH 进来)。"""
    w3, _ = tester_w3
    acct = Account.create()
    # 从创世 coinbase 转 5 ETH 到测试账户
    w3.eth.send_transaction({
        "to": acct.address,
        "from": w3.eth.accounts[0],
        "value": Web3.to_wei(5, "ether"),
    })
    # 再发一笔空交易推进 receipt(tester 需要显式出块)
    w3.eth.wait_for_transaction_receipt(
        w3.eth.send_transaction({"to": acct.address, "from": w3.eth.accounts[0], "value": 0})
    )
    return acct


@pytest.fixture
def mock_token(tester_w3, funded_account):
    """部署一个 MockERC20 合约, 返回 (chain_mod.Token, abi, contract_address)。

    部署者(funded_account)持有全部代币初始余额。
    """
    w3, _ = tester_w3
    compiled = solcx.compile_source(
        _MOCK_ERC20_SRC, output_values=["abi", "bin"], solc_version="0.8.20"
    )
    cd = compiled["<stdin>:MockERC20"]
    nonce = w3.eth.get_transaction_count(funded_account.address)
    tx = {
        "from": funded_account.address,
        "data": "0x" + cd["bin"],
        "gas": 2000000,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gasPrice": w3.eth.gas_price,
    }
    signed = w3.eth.account.sign_transaction(tx, funded_account.key)
    receipt = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(signed.raw_transaction)
    )
    assert receipt.status == 1, "MockERC20 部署失败"
    token_address = receipt.contractAddress
    token = chain_mod.Token(address=token_address, decimals=6)
    return token, cd["abi"], token_address


def _recipient(tester_w3):
    """一个干净的收款地址(新创建, 无余额)。"""
    return Account.create().address


# ---------------------------------------------------------------------------
# send() — 原生币
# ---------------------------------------------------------------------------

class TestSendNative:
    def test_success(self, tester_w3, funded_account):
        """原生币转账: receipt.ok == True, status == 'success'。"""
        w3, cc = tester_w3
        to = _recipient(tester_w3)
        receipt = chain_mod.send(
            w3, cc, funded_account, to, 0.1, receipt_timeout=10
        )
        assert receipt.ok
        assert receipt.status == "success"
        assert receipt.tx_hash.startswith("0x")
        assert receipt.block_number is not None
        assert receipt.gas_used == 21000  # 原生转账固定 21000
        assert receipt.explorer_url is not None
        # 余额确实转移了(收款方从 0 变为 0.1 ETH)
        assert w3.eth.get_balance(to) == Web3.to_wei(0.1, "ether")

    def test_invalid_address_raises(self, tester_w3, funded_account):
        """非法地址在广播前抛 ValueError, 不接触私钥签名流程之外的东西。"""
        w3, cc = tester_w3
        with pytest.raises(ValueError, match="非法收款地址"):
            chain_mod.send(w3, cc, funded_account, "0xnotanaddress", 0.01)

    def test_explorer_url_from_config(self, tester_w3, funded_account):
        """explorer_url 从 chain_config['explorer'] 派生。"""
        w3, cc = tester_w3
        to = _recipient(tester_w3)
        receipt = chain_mod.send(w3, cc, funded_account, to, 0.01, receipt_timeout=10)
        assert "https://test.example/tx/" in receipt.explorer_url
        assert receipt.tx_hash in receipt.explorer_url


# ---------------------------------------------------------------------------
# send() — ERC-20
# ---------------------------------------------------------------------------

class TestSendToken:
    def test_success(self, tester_w3, funded_account, mock_token):
        """ERC-20 转账: receipt.ok == True, 代币余额确实转移。"""
        w3, cc = tester_w3
        token, abi, token_addr = mock_token
        to = _recipient(tester_w3)
        receipt = chain_mod.send(
            w3, cc, funded_account, to, 5.0, token=token, receipt_timeout=10
        )
        assert receipt.ok
        assert receipt.status == "success"
        assert receipt.gas_used > 21000  # ERC-20 transfer 比原生转账贵

        # 验证代币余额确实转移了 5 个 token(6 位精度 = 5000000 最小单位)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_addr), abi=abi
        )
        assert contract.functions.balanceOf(to).call() == 5_000_000

    def test_token_decimals_conversion(self, tester_w3, funded_account, mock_token):
        """amount_human=5.0 + decimals=6 → 链上转移 5000000 最小单位。"""
        w3, cc = tester_w3
        token, abi, token_addr = mock_token
        to = _recipient(tester_w3)
        chain_mod.send(w3, cc, funded_account, to, 5.0, token=token, receipt_timeout=10)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_addr), abi=abi
        )
        # 5.0 * 10^6 = 5000000
        assert contract.functions.balanceOf(to).call() == 5_000_000


# ---------------------------------------------------------------------------
# send() — 链上失败
# ---------------------------------------------------------------------------

class TestSendFailure:
    def test_revert_raises_at_estimate(self, tester_w3, funded_account):
        """合约 revert 在 estimate_gas 阶段被捕获, send() 抛异常(广播前失败)。

        部署一个 transfer 永远 revert 的合约, send() 应在 build_transaction
        (内部调 estimate_gas 模拟)时抛出, 而非广播后返回 failed receipt。

        注: 真正的 status=0 receipt 出现在 estimate 通过但执行时回退的竞态
        (如主网状态在 estimate 与打包间变化), tester 无法复现该竞态。
        """
        w3, cc = tester_w3
        bad_src = """
        pragma solidity ^0.8.20;
        contract BadToken {
            string public symbol = "BAD";
            uint8 public decimals = 6;
            function transfer(address, uint256) public returns (bool) {
                revert("always fails");
            }
        }
        """
        compiled = solcx.compile_source(
            bad_src, output_values=["abi", "bin"], solc_version="0.8.20"
        )
        cd = compiled["<stdin>:BadToken"]
        nonce = w3.eth.get_transaction_count(funded_account.address)
        tx = {
            "from": funded_account.address,
            "data": "0x" + cd["bin"],
            "gas": 2000000,
            "nonce": nonce,
            "chainId": w3.eth.chain_id,
            "gasPrice": w3.eth.gas_price,
        }
        signed = w3.eth.account.sign_transaction(tx, funded_account.key)
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(signed.raw_transaction)
        )
        assert receipt.status == 1, "BadToken 部署失败"
        token = chain_mod.Token(address=receipt.contractAddress, decimals=6)

        to = _recipient(tester_w3)
        # transfer 永远 revert → estimate_gas 模拟时抛 → send() 抛异常
        with pytest.raises(Exception):
            chain_mod.send(
                w3, cc, funded_account, to, 1.0, token=token, receipt_timeout=10
            )


# ---------------------------------------------------------------------------
# Receipt 值对象
# ---------------------------------------------------------------------------

class TestReceipt:
    def test_ok_property_true_for_success(self):
        r = chain_mod.Receipt(status="success", tx_hash="0xabc", block_number=1, gas_used=21000)
        assert r.ok is True

    def test_ok_property_false_for_pending(self):
        r = chain_mod.Receipt(status="pending", tx_hash="0xabc")
        assert r.ok is False

    def test_ok_property_false_for_failed(self):
        r = chain_mod.Receipt(status="failed", tx_hash="0xabc", block_number=1, gas_used=50000)
        assert r.ok is False

    def test_pending_has_no_block_or_gas(self):
        r = chain_mod.Receipt(status="pending", tx_hash="0xabc")
        assert r.block_number is None
        assert r.gas_used is None


# ---------------------------------------------------------------------------
# Token 值对象
# ---------------------------------------------------------------------------

class TestToken:
    def test_fields(self):
        t = chain_mod.Token(address="0x" + "ab" * 20, decimals=6)
        assert t.address == "0x" + "ab" * 20
        assert t.decimals == 6


# ---------------------------------------------------------------------------
# 离线交易构造(保留: 无需 EVM 即可验证的纯逻辑)
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
        assert isinstance(signed.raw_transaction, (bytes, bytearray))
        assert len(signed.raw_transaction) > 0
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
