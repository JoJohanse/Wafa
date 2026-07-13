"""
policy.py 测试 —— 策略引擎: 单笔/日累计/速率/白名单/用途/kill_switch。
重点: 所有检查应在签名前拦截, 拒绝时不接触密钥。
"""
from __future__ import annotations

import pytest

import policy
import store
from conftest import WHITELIST_ADDR, OTHER_ADDR


def ok_decision():
    """构造一个合法的 baseline check, 测试在此基础上扰动。"""
    return policy.check(
        amount=0.01, to_address=WHITELIST_ADDR, reason="test", kind="native"
    )


class TestPolicyBaseline:
    def test_legitimate_passes(self, strict_policy):
        """白名单内 + 小额 + 有理由 → 通过"""
        d = policy.check(0.01, WHITELIST_ADDR, "reason", "native")
        assert d.allowed
        assert d.reason == "通过"


class TestPerTxLimit:
    def test_over_per_tx_native_rejected(self, strict_policy):
        d = policy.check(50, WHITELIST_ADDR, "reason", "native")
        assert not d.allowed
        assert "单笔限额" in d.reason

    def test_over_per_tx_token_rejected(self, strict_policy):
        d = policy.check(100, WHITELIST_ADDR, "reason", "token")
        assert not d.allowed
        assert "单笔限额" in d.reason

    def test_at_limit_passes(self, strict_policy):
        # 恰好等于上限 0.05 应通过
        d = policy.check(0.05, WHITELIST_ADDR, "reason", "native")
        assert d.allowed


class TestDailyLimit:
    def test_over_daily_rejected(self, strict_policy):
        # 模拟今日已花费 0.48, 再花 0.05 超 0.5 上限
        import datetime
        today = datetime.date.today().isoformat()
        store.save_state({"daily": {today: {"native": 0.48, "token": 0.0}}})
        d = policy.check(0.05, WHITELIST_ADDR, "reason", "native")
        assert not d.allowed
        assert "日累计" in d.reason

    def test_daily_accumulates(self, strict_policy):
        store.save_state({})
        # 花两次 0.2, 第三次 0.2 应触发(0.4+0.2=0.6 > 0.5, 但单笔 0.2<=0.05? 不, 0.2>0.05 单笔先拦)
        # 用更小金额: 单笔上限 0.05, 日累计 0.5
        for _ in range(9):
            store.record_spend(0.05, "native")  # 累计 0.45
        # 再花 0.05: 累计 0.50 = 上限, 通过
        d1 = policy.check(0.05, WHITELIST_ADDR, "r", "native")
        assert d1.allowed
        # 记录后, 再花 0.01: 累计 0.51 > 0.5, 拒
        store.record_spend(0.05, "native")
        d2 = policy.check(0.01, WHITELIST_ADDR, "r", "native")
        assert not d2.allowed


class TestRateLimit:
    def test_minute_rate_exceeded(self, strict_policy):
        store.save_state({})
        for _ in range(5):
            store.record_tx_timestamp()
        # 第 6 笔应被速率限制拦截
        d = policy.check(0.01, WHITELIST_ADDR, "r", "native")
        assert not d.allowed
        assert "速率限制" in d.reason
        assert "60 秒" in d.reason


class TestWhitelist:
    def test_non_whitelisted_rejected(self, strict_policy):
        d = policy.check(0.01, OTHER_ADDR, "reason", "native")
        assert not d.allowed
        assert "白名单" in d.reason

    def test_whitelist_disabled_allows_all(self, tmp_wafa_home):
        """默认宽松策略白名单关闭, 任意地址应通过(其余不限)"""
        import yaml
        pol = {
            "limits": {}, "rate_limit": {},
            "whitelist": {"enabled": False, "addresses": []},
            "safety": {"require_reason": False, "allowed_purposes": []},
            "kill_switch": False,
        }
        with open(store.policy_path(), "w") as f:
            yaml.dump(pol, f)
        store.save_state({})
        d = policy.check(0.01, OTHER_ADDR, None, "native")
        assert d.allowed


class TestReasonAndPurpose:
    def test_missing_reason_rejected(self, strict_policy):
        d = policy.check(0.01, WHITELIST_ADDR, None, "native")
        assert not d.allowed
        assert "理由" in d.reason

    def test_empty_reason_rejected(self, strict_policy):
        d = policy.check(0.01, WHITELIST_ADDR, "   ", "native")
        assert not d.allowed

    def test_allowed_purposes_enforced(self, tmp_wafa_home):
        import yaml
        pol = {
            "limits": {}, "rate_limit": {},
            "whitelist": {"enabled": False, "addresses": []},
            "safety": {"require_reason": True, "allowed_purposes": ["api_access", "data_buy"]},
            "kill_switch": False,
        }
        with open(store.policy_path(), "w") as f:
            yaml.dump(pol, f)
        store.save_state({})
        # 合法用途
        assert policy.check(0.01, OTHER_ADDR, "api_access", "native").allowed
        # 非法用途
        d = policy.check(0.01, OTHER_ADDR, "gambling", "native")
        assert not d.allowed
        assert "用途" in d.reason


class TestKillSwitch:
    def test_kill_switch_blocks_all(self, strict_policy):
        import yaml
        strict_policy["kill_switch"] = True
        with open(store.policy_path(), "w") as f:
            yaml.dump(strict_policy, f)
        # 即便其余条件全合法, kill_switch 一票否决
        d = policy.check(0.01, WHITELIST_ADDR, "reason", "native")
        assert not d.allowed
        assert "kill_switch" in d.reason


class TestDecisionObject:
    def test_decision_bool_true(self, strict_policy):
        d = policy.check(0.01, WHITELIST_ADDR, "r", "native")
        assert bool(d) is True

    def test_decision_bool_false(self, strict_policy):
        d = policy.check(50, WHITELIST_ADDR, "r", "native")  # 超单笔
        assert bool(d) is False
