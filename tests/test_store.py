"""
store.py 测试 —— 路径管理、配置读写、状态追踪、审计日志、初始化。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import store


# ---------------------------------------------------------------------------
# 路径管理
# ---------------------------------------------------------------------------

class TestPaths:
    def test_wafa_home_uses_env(self, tmp_wafa_home):
        # wafa_home() 应指向被 monkeypatch 的临时目录
        assert store.wafa_home() == tmp_wafa_home

    def test_wafa_home_creates_dir(self, tmp_path, monkeypatch):
        # 指向尚不存在的子目录, wafa_home 应自动创建
        target = tmp_path / "deep" / "wafa"
        monkeypatch.setenv("WAFA_HOME", str(target))
        assert store.wafa_home() == target
        assert target.exists()

    def test_keystores_dir_exists(self, tmp_wafa_home):
        d = store.keystores_dir()
        assert d.exists()
        assert d.is_dir()

    def test_config_and_policy_paths_under_home(self, tmp_wafa_home):
        assert store.config_path().parent == tmp_wafa_home
        assert store.policy_path().parent == tmp_wafa_home

    def test_template_path_valid(self):
        for kind in ("config", "policy"):
            p = store.template_path(kind)
            assert p.exists(), f"模板 {kind} 不存在"

    def test_template_path_invalid_kind(self):
        with pytest.raises(KeyError):
            store.template_path("nonexistent")


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------

class TestConfig:
    def test_load_config_default(self, tmp_wafa_home):
        # init 已复制 config.yaml; 加载应含默认链
        cfg = store.load_config()
        assert "chains" in cfg
        assert "base" in cfg["chains"]

    def test_load_config_missing_file_returns_builtin(self, tmp_path, monkeypatch):
        # 无 config.yaml 时返回内置默认(不崩溃)
        monkeypatch.setenv("WAFA_HOME", str(tmp_path / "empty"))
        cfg = store.load_config()
        assert "chains" in cfg
        assert "default_chain" in cfg

    def test_load_policy_exists(self, tmp_wafa_home):
        pol = store.load_policy()
        # init 复制的策略含 limits/safety 等键
        assert "limits" in pol
        assert "safety" in pol

    def test_load_policy_missing_returns_relaxed(self, tmp_path, monkeypatch):
        # 无 policy.yaml 时返回宽松兜底(不阻断)
        monkeypatch.setenv("WAFA_HOME", str(tmp_path / "empty"))
        pol = store.load_policy()
        assert pol["kill_switch"] is False
        assert pol["whitelist"]["enabled"] is False

    def test_get_chain_config_known(self, tmp_wafa_home):
        cfg = store.load_config()
        cc = store.get_chain_config(cfg, "base")
        assert cc["chain_id"] == 8453
        assert "rpc_url" in cc

    def test_get_chain_config_unknown_raises(self, tmp_wafa_home):
        cfg = store.load_config()
        with pytest.raises(ValueError, match="未在 config"):
            store.get_chain_config(cfg, "nonexistent")

    def test_get_chain_config_default(self, tmp_wafa_home):
        cfg = store.load_config()
        # chain=None 时用 default_chain
        cc = store.get_chain_config(cfg, None)
        default = cfg.get("default_chain", "base")
        assert "chain_id" in cc
        assert cc["chain_id"] == cfg["chains"][default]["chain_id"]

    def test_resolve_token_by_alias(self, tmp_wafa_home):
        cfg = store.load_config()
        info = store.resolve_token(cfg, "base", "usdc")
        assert info is not None
        assert info["address"].startswith("0x")

    def test_resolve_token_by_address(self, tmp_wafa_home):
        cfg = store.load_config()
        # 用已配置的 USDC 地址反查
        usdc = cfg["tokens"]["base"]["usdc"]["address"]
        info = store.resolve_token(cfg, "base", usdc)
        assert info is not None

    def test_resolve_token_unknown(self, tmp_wafa_home):
        cfg = store.load_config()
        assert store.resolve_token(cfg, "base", "nonexistent") is None

    def test_resolve_token_raw_address_assumed(self, tmp_wafa_home):
        cfg = store.load_config()
        # 陌生但合法的地址: 假定 6 位精度
        info = store.resolve_token(cfg, "base", "0x" + "ab" * 20)
        assert info is not None
        assert info["decimals"] == 6


# ---------------------------------------------------------------------------
# 状态追踪
# ---------------------------------------------------------------------------

class TestState:
    def test_load_state_empty(self, tmp_wafa_home):
        store.save_state({})
        assert store.load_state() == {}

    def test_record_spend_accumulates(self, fresh_state):
        store.record_spend(0.1, "native")
        store.record_spend(0.2, "native")
        assert store.get_daily_spent("native") == pytest.approx(0.3)

    def test_record_spend_separate_kinds(self, fresh_state):
        store.record_spend(1.0, "native")
        store.record_spend(5.0, "token")
        assert store.get_daily_spent("native") == pytest.approx(1.0)
        assert store.get_daily_spent("token") == pytest.approx(5.0)

    def test_record_spend_rounding(self, fresh_state):
        # 浮点累计应保留 8 位小数精度
        store.record_spend(0.33333333, "native")
        assert store.get_daily_spent("native") == pytest.approx(0.33333333, abs=1e-8)

    def test_record_tx_timestamp_appended(self, fresh_state):
        store.record_tx_timestamp()
        store.record_tx_timestamp()
        state = store.load_state()
        assert len(state["tx_timestamps"]) == 2

    def test_count_tx_in_window(self, fresh_state):
        for _ in range(3):
            store.record_tx_timestamp()
        assert store.count_tx_in_window(60) == 3

    def test_count_tx_in_window_excludes_old(self, fresh_state):
        # 手动注入一个 2 小时前的旧时间戳
        import time
        state = {"tx_timestamps": [time.time() - 7200, time.time(), time.time()]}
        store.save_state(state)
        # 最近 60 秒内只有 2 笔
        assert store.count_tx_in_window(60) == 2


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------

class TestAudit:
    def test_append_and_read(self, tmp_wafa_home):
        store.append_audit("test_action", key="value", num=42)
        records = store.read_audit(10)
        assert len(records) == 1
        assert records[0]["action"] == "test_action"
        assert records[0]["key"] == "value"
        assert records[0]["num"] == 42
        assert "ts" in records[0]

    def test_read_audit_empty(self, tmp_wafa_home):
        assert store.read_audit(10) == []

    def test_read_audit_limit(self, tmp_wafa_home):
        for i in range(5):
            store.append_audit("act", i=i)
        records = store.read_audit(2)
        assert len(records) == 2
        # 取最近 2 条(即 i=3,4)
        assert records[-1]["i"] == 4

    def test_read_audit_skips_malformed(self, tmp_wafa_home):
        # 写入一条正常 + 一条损坏的行
        store.append_audit("good")
        with open(store.audit_path(), "a", encoding="utf-8") as f:
            f.write("not a json line\n")
        records = store.read_audit(10)
        assert len(records) == 1
        assert records[0]["action"] == "good"


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

class TestInit:
    def test_init_creates_config_and_policy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WAFA_HOME", str(tmp_path / "fresh"))
        results = store.init_home()
        assert store.config_path().exists()
        assert store.policy_path().exists()
        assert any("已生成" in r for r in results)

    def test_init_idempotent(self, tmp_wafa_home):
        # 第二次 init 应跳过已存在文件
        results = store.init_home()
        assert any("已存在" in r for r in results)
