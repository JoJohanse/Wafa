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
# 通用 KV 状态 —— store 只提供原子读写, 不感知计数语义
# (日累计/速率窗口的测试见 test_policy.py::TestPolicyState)
# ---------------------------------------------------------------------------

class TestStateKV:
    def test_load_state_empty(self, tmp_wafa_home):
        store.save_state({})
        assert store.load_state() == {}

    def test_save_load_roundtrip(self, tmp_wafa_home):
        store.save_state({"daily": {"x": 1}, "tx_timestamps": [1.0, 2.0]})
        assert store.load_state() == {"daily": {"x": 1}, "tx_timestamps": [1.0, 2.0]}

    def test_load_state_missing_file(self, tmp_path, monkeypatch):
        # 无 state.json 时返回空 dict, 不崩溃
        monkeypatch.setenv("WAFA_HOME", str(tmp_path / "empty"))
        assert store.load_state() == {}


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
