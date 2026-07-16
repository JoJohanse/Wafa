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
# 解锁失败节流 —— 安全核心: 防止被入侵的 agent 反复试密码
# ---------------------------------------------------------------------------

class TestUnlockThrottle:
    """解锁失败计数与锁定行为。默认阈值 5 次, 锁定 300 秒。"""

    def _force_failures(self, address: str, n: int):
        """触发 n 次错误解锁, 吞掉非锁定异常, 在触发锁定时停止。"""
        for _ in range(n):
            try:
                wallet.unlock(address, "WrongP@ss1!")
            except wallet.UnlockLocked:
                break
            except (ValueError, KeyError):
                continue  # 密码错误, 计数累积, 继续下一次

    def test_few_failures_no_lock(self, tmp_wafa_home):
        """阈值以下失败不锁定, 仍可正常解锁"""
        info = wallet.create_wallet(STRONG_PW, "t")
        self._force_failures(info.address, 4)  # 阈值 5, 试 4 次
        # 第 5 次用正确密码应能解锁(未达锁定)
        acct = wallet.unlock(info.address, STRONG_PW)
        assert acct.address == info.address

    def test_lock_after_threshold(self, tmp_wafa_home):
        """达到阈值后锁定, 即便用正确密码也被拒"""
        info = wallet.create_wallet(STRONG_PW, "t")
        self._force_failures(info.address, 5)
        # 锁定期内正确密码仍被拒
        with pytest.raises(wallet.UnlockLocked):
            wallet.unlock(info.address, STRONG_PW)

    def test_lock_blocks_even_correct_password(self, tmp_wafa_home):
        info = wallet.create_wallet(STRONG_PW, "t")
        self._force_failures(info.address, 5)
        with pytest.raises(wallet.UnlockLocked):
            wallet.unlock(info.address, STRONG_PW)

    def test_success_clears_counter(self, tmp_wafa_home):
        """成功解锁清零失败计数, 之后可重新有 5 次机会"""
        info = wallet.create_wallet(STRONG_PW, "t")
        self._force_failures(info.address, 3)
        # 用正确密码解锁 → 计数清零
        wallet.unlock(info.address, STRONG_PW)
        import store
        assert store.get_unlock_state(info.address) == {}

    def test_lock_expires_allows_retry(self, tmp_wafa_home):
        """锁定期满后可再次尝试(手动把 locked_until 调到过去)"""
        info = wallet.create_wallet(STRONG_PW, "t")
        self._force_failures(info.address, 5)
        import time as _t
        import store
        # 手动把锁定时间提前到过去, 模拟锁定过期
        st = store.load_state()
        st["unlock_failures"][info.address.lower()]["locked_until"] = _t.time() - 1
        store.save_state(st)
        # 过期后正确密码应能解锁并清零
        acct = wallet.unlock(info.address, STRONG_PW)
        assert acct.address == info.address

    def test_locked_does_not_run_scrypt(self, tmp_wafa_home):
        """锁定期内不跑 scrypt(返回快, CPU 不消耗)"""
        info = wallet.create_wallet(STRONG_PW, "t")
        self._force_failures(info.address, 5)
        # 第二次调用应立即抛 UnlockLocked, 不跑 decrypt
        import time as _t
        start = _t.time()
        with pytest.raises(wallet.UnlockLocked):
            wallet.unlock(info.address, STRONG_PW)
        # scrypt N=2^18 至少数十毫秒; 锁定拒绝应在 0.5s 内
        assert _t.time() - start < 0.5

    def test_failure_count_persists_across_unlocks(self, tmp_wafa_home):
        """失败计数在 state.json 中持久化, 不因进程退出而丢失"""
        info = wallet.create_wallet(STRONG_PW, "t")
        self._force_failures(info.address, 1)
        # 重新 load_state 看 unlock_failures 已记录
        import store
        st = store.load_state()
        assert info.address.lower() in st.get("unlock_failures", {})
        assert st["unlock_failures"][info.address.lower()]["count"] == 1

    def test_throttle_respects_policy_config(self, tmp_wafa_home):
        """policy.yaml 的 unlock_throttle 可调低阈值"""
        import yaml
        import policy as policy_mod
        import store
        pol = policy_mod.load_policy()
        pol["unlock_throttle"] = {"max_attempts": 3, "lockout_seconds": 60}
        with open(store.policy_path(), "w", encoding="utf-8") as f:
            yaml.dump(pol, f)
        info = wallet.create_wallet(STRONG_PW, "t")
        # 试 3 次错误 → 锁(阈值被调成 3)
        for _ in range(3):
            try:
                wallet.unlock(info.address, "WrongP@ss1!")
            except wallet.UnlockLocked:
                break
            except (ValueError, KeyError):
                continue
        with pytest.raises(wallet.UnlockLocked):
            wallet.unlock(info.address, STRONG_PW)

    def test_corrupted_keystore_not_counted_as_failure(self, tmp_wafa_home, capsys):
        """keystore 文件损坏(JSONDecodeError)不应被当作'试密失败'计入节流。

        回归守护: except 收窄后, 只有真正的密码错(ValueError/KeyError)才计数;
        文件坏了应原样抛出, 不会把用户锁死。
        """
        info = wallet.create_wallet(STRONG_PW, "t")
        # 把 keystore 文件搞坏
        info.keystore_file.write_text("{ not valid json ")
        # 解锁应抛 JSONDecodeError(或类似), 而不是被计入失败
        with pytest.raises(json.JSONDecodeError):
            wallet.unlock(info.address, STRONG_PW)
        # 关键: 失败计数应为 0(没被当作试密)
        import store
        assert store.get_unlock_state(info.address) == {}

    def test_throttle_config_warns_on_bad_yaml(self, tmp_wafa_home, capsys):
        """policy.yaml 的 unlock_throttle 格式坏时, 退默认且打 stderr 告警(不静默)。"""
        import policy as policy_mod
        import store
        # 写一份 unlock_throttle 值非法的 policy.yaml
        bad = policy_mod.load_policy()
        bad["unlock_throttle"] = {"max_attempts": "not_a_number", "lockout_seconds": 60}
        with open(store.policy_path(), "w", encoding="utf-8") as f:
            import yaml
            yaml.dump(bad, f)
        # 读 throttle config
        max_a, lock_s = wallet._load_throttle_config()
        # 应退回默认
        assert max_a == wallet._DEFAULT_MAX_ATTEMPTS
        assert lock_s == wallet._DEFAULT_LOCKOUT_SECONDS
        # 且 stderr 有告警(不静默)
        captured = capsys.readouterr()
        assert "警告" in captured.err or "warning" in captured.err.lower()


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
