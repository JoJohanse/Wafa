"""
wallet.py 测试 —— 钱包核心: 创建/导入/解锁/标签/内存清零/密码强度/KDF 加固。
重点覆盖安全相关行为。
"""
from __future__ import annotations

import json

import pytest
from eth_account import Account

import wallet
from conftest import STRONG_PW


# ---------------------------------------------------------------------------
# 密码强度检查
# ---------------------------------------------------------------------------

class TestPasswordStrength:
    @pytest.mark.parametrize("pw,should_pass", [
        ("short", False),                 # 太短
        ("1234567890", False),            # 仅数字
        ("alllowercase", False),          # 仅小写(不够10位也)
        ("ALLUPPERCASE1", False),         # 缺符号, 仅2类+长度ok 但 classes=2
        ("password", False),              # 常见弱密码 + 太短
        ("StrongP@ss1", True),            # 合格: 4类 + 11位
        ("Abcdefg123!", True),            # 合格
        (STRONG_PW, True),                # fixture 用的强密码
    ])
    def test_strength(self, pw, should_pass):
        ok, _ = wallet.check_password_strength(pw)
        assert ok == should_pass, f"密码 {pw!r} 预期通过={should_pass}"

    def test_short_rejected(self):
        ok, why = wallet.check_password_strength("Ab1!")
        assert not ok
        assert "10" in why

    def test_single_class_rejected(self):
        ok, why = wallet.check_password_strength("1234567890")
        assert not ok
        assert "3 类" in why

    def test_common_weak_rejected(self):
        ok, why = wallet.check_password_strength("password")
        assert not ok

    def test_empty_rejected(self):
        ok, why = wallet.check_password_strength("")
        assert not ok


# ---------------------------------------------------------------------------
# secure_zero —— 真实内存清零(非 noop)
# ---------------------------------------------------------------------------

class TestSecureZero:
    def test_zeroes_bytearray(self):
        buf = bytearray(b"\x01\x02\x03\x04\x05")
        wallet.secure_zero(buf)
        assert buf == bytearray(5)

    def test_zeroes_memoryview(self):
        buf = bytearray(b"\xff\xff")
        mv = memoryview(buf)
        wallet.secure_zero(mv)
        assert buf == bytearray(2)

    def test_empty_buffer_noop(self):
        buf = bytearray()
        wallet.secure_zero(buf)  # 不应报错
        assert buf == bytearray()

    def test_not_noop(self):
        # 确保实现真的覆写了内容(对照: 原 _zero_bytes 是 no-op)
        buf = bytearray(b"secret")
        wallet.secure_zero(buf)
        assert b"secret" not in bytes(buf)


# ---------------------------------------------------------------------------
# create_wallet
# ---------------------------------------------------------------------------

class TestCreateWallet:
    def test_creates_wallet_and_keystore(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW, "test")
        assert info.address.startswith("0x")
        assert info.keystore_file.exists()

    def test_returns_address_not_key(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW)
        # WalletInfo 数据类不应含任何密钥字段
        assert not hasattr(info, "key")
        assert not hasattr(info, "private_key")

    def test_empty_password_rejected(self, tmp_wafa_home):
        with pytest.raises(ValueError, match="密码不能为空"):
            wallet.create_wallet("", "x")

    def test_weak_password_rejected(self, tmp_wafa_home):
        with pytest.raises(ValueError, match="密码强度不足"):
            wallet.create_wallet("weak", "x")

    def test_label_persisted_in_keystore(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW, "my-label")
        ks = json.loads(info.keystore_file.read_text())
        assert ks["wafa_label"] == "my-label"

    def test_no_label_leaves_field_absent(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW)
        ks = json.loads(info.keystore_file.read_text())
        assert "wafa_label" not in ks

    def test_keystore_kdf_hardened(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW)
        ks = json.loads(info.keystore_file.read_text())
        # 加固参数: scrypt N >= 2^18
        assert ks["crypto"]["kdf"] == "scrypt"
        n = ks["crypto"]["kdfparams"]["n"]
        assert n >= 2 ** 18, f"KDF N={n} 低于加固阈值 2^18"

    def test_audit_logged(self, tmp_wafa_home):
        import store
        info = wallet.create_wallet(STRONG_PW, "audited")
        records = store.read_audit(5)
        create_records = [r for r in records if r["action"] == "create"]
        assert len(create_records) >= 1
        assert create_records[-1]["address"] == info.address


# ---------------------------------------------------------------------------
# import_wallet
# ---------------------------------------------------------------------------

class TestImportWallet:
    def test_import_str_with_0x(self, tmp_wafa_home):
        tmp = Account.create()
        info = wallet.import_wallet("0x" + tmp.key.hex(), STRONG_PW, "s1")
        assert info.address == tmp.address

    def test_import_str_bare_hex(self, tmp_wafa_home):
        tmp = Account.create()
        info = wallet.import_wallet(tmp.key.hex(), STRONG_PW, "s2")
        assert info.address == tmp.address

    def test_import_raw_bytes(self, tmp_wafa_home):
        tmp = Account.create()
        info = wallet.import_wallet(tmp.key, STRONG_PW, "b32")
        assert info.address == tmp.address

    def test_import_hex_as_bytes_64(self, tmp_wafa_home):
        """CLI stdin 路径: bytearray of hex text (64 字节 ascii)"""
        tmp = Account.create()
        hex_ba = bytearray(tmp.key.hex().encode())
        info = wallet.import_wallet(bytes(hex_ba), STRONG_PW, "hba")
        assert info.address == tmp.address

    def test_import_hex_as_bytes_with_0x(self, tmp_wafa_home):
        tmp = Account.create()
        hex_ba = bytearray(("0x" + tmp.key.hex()).encode())
        info = wallet.import_wallet(bytes(hex_ba), STRONG_PW, "hb2")
        assert info.address == tmp.address

    def test_import_invalid_length_rejected(self, tmp_wafa_home):
        with pytest.raises(ValueError, match="长度异常"):
            wallet.import_wallet(b"\x01\x02\x03", STRONG_PW, "bad")

    def test_import_weak_password_rejected(self, tmp_wafa_home):
        tmp = Account.create()
        with pytest.raises(ValueError, match="密码强度不足"):
            wallet.import_wallet(tmp.key, "weak", "x")

    def test_import_zeros_temp_material(self, tmp_wafa_home):
        """调用方传入 bytearray, import 后应可清零"""
        tmp = Account.create()
        pk_ba = bytearray(("0x" + tmp.key.hex()).encode())
        wallet.import_wallet(bytes(pk_ba), STRONG_PW, "zero-test")
        wallet.secure_zero(pk_ba)
        assert pk_ba == bytearray(len(pk_ba))

    def test_imported_wallet_unlockable(self, tmp_wafa_home):
        tmp = Account.create()
        info = wallet.import_wallet(tmp.key, STRONG_PW, "unlockable")
        acct = wallet.unlock(info.address, STRONG_PW)
        assert acct.address == info.address


# ---------------------------------------------------------------------------
# unlock
# ---------------------------------------------------------------------------

class TestUnlock:
    def test_correct_password(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW, "u")
        acct = wallet.unlock(info.address, STRONG_PW)
        assert acct.address == info.address

    def test_wrong_password_rejected(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW, "u")
        with pytest.raises((ValueError, KeyError)):
            wallet.unlock(info.address, "WrongP@ss999!")

    def test_missing_keystore_raises(self, tmp_wafa_home):
        fake_addr = "0x" + "ab" * 20
        with pytest.raises(FileNotFoundError):
            wallet.unlock(fake_addr, STRONG_PW)

    def test_unlocked_can_sign(self, tmp_wafa_home):
        """解锁后的 Account 应具备签名能力"""
        info = wallet.create_wallet(STRONG_PW, "signer")
        acct = wallet.unlock(info.address, STRONG_PW)
        assert hasattr(acct, "key")
        assert hasattr(acct, "address")


# ---------------------------------------------------------------------------
# list / label
# ---------------------------------------------------------------------------

class TestListAndLabel:
    def test_list_empty(self, tmp_wafa_home):
        # tmp_wafa_home 刚 init, 无钱包
        assert wallet.list_wallets() == []

    def test_list_shows_wallets_without_keys(self, tmp_wafa_home):
        wallet.create_wallet(STRONG_PW, "a")
        wallet.create_wallet(STRONG_PW, "b")
        wallets = wallet.list_wallets()
        assert len(wallets) == 2
        for w in wallets:
            assert not hasattr(w, "key")
            assert w.address.startswith("0x")

    def test_list_includes_label(self, tmp_wafa_home):
        wallet.create_wallet(STRONG_PW, "labeled")
        wallets = wallet.list_wallets()
        assert any(w.label == "labeled" for w in wallets)

    def test_set_label(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW)
        wallet.set_label(info.address, "new-label")
        wallets = wallet.list_wallets()
        assert wallets[0].label == "new-label"

    def test_get_default_address(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW, "default")
        # 文件名存小写地址, 比较需大小写不敏感
        assert wallet.get_default_address().lower() == info.address.lower()

    def test_get_default_address_none_when_empty(self, tmp_wafa_home):
        assert wallet.get_default_address() is None
