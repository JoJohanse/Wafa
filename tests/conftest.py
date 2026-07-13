"""
Wafa 测试通用 fixtures。

核心: 每个测试用独立的临时 WAFA_HOME, 绝不触碰用户真实 ~/.wafa。
所有测试通过 monkeypatch WAFA_HOME 环境变量隔离。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 把 scripts/ 加入 sys.path, 使测试可直接 import 各模块
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# 一个通过密码强度校验的强密码, 测试中复用
STRONG_PW = "TestP@ssw0rd!"


@pytest.fixture
def tmp_wafa_home(tmp_path, monkeypatch):
    """
    为每个测试创建独立的 WAFA_HOME(临时目录), 并初始化。
    测试结束后 tmp_path 由 pytest 自动清理。
    """
    home = tmp_path / "wafa_home"
    home.mkdir()
    monkeypatch.setenv("WAFA_HOME", str(home))

    # 延迟 import, 确保 monkeypatch 先生效
    import store
    store.init_home()

    return home


@pytest.fixture
def fresh_state(tmp_wafa_home):
    """提供已初始化、状态为空的隔离环境。"""
    import store
    store.save_state({})
    return tmp_wafa_home


@pytest.fixture
def strict_policy(tmp_wafa_home):
    """写入一份带严格限额的策略, 供策略测试使用。"""
    import yaml
    import store

    policy = {
        "limits": {
            "max_per_tx_native": 0.05,
            "daily_limit_native": 0.5,
            "max_per_tx_token": 10,
            "daily_limit_token": 100,
        },
        "rate_limit": {"max_tx_per_minute": 5, "max_tx_per_hour": 50},
        "whitelist": {
            "enabled": True,
            "addresses": ["0x" + "11" * 20],
        },
        "safety": {"require_reason": True, "allowed_purposes": []},
        "kill_switch": False,
    }
    with open(store.policy_path(), "w", encoding="utf-8") as f:
        yaml.dump(policy, f)
    store.save_state({})
    return policy


# 测试用地址: 白名单内的合法地址
WHITELIST_ADDR = "0x" + "11" * 20
# 测试用地址: 白名单外的地址
OTHER_ADDR = "0x" + "22" * 20
