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


class TestAuditHashChain:
    """审计日志哈希链: 防就地篡改/删除/插入。"""

    def test_chain_intact_after_normal_appends(self, tmp_wafa_home):
        """连续正常写入, 链应完整"""
        for i in range(5):
            store.append_audit("act", i=i)
        ok, bad_idx, reason = store.verify_audit_chain()
        assert ok, f"应完整, 但报: {reason}"
        assert bad_idx is None

    def test_first_record_prev_hash_is_genesis(self, tmp_wafa_home):
        """首条记录的 prev_hash 必须是 'genesis' 占位"""
        store.append_audit("first")
        records = store.read_audit(10)
        assert records[0]["prev_hash"] == "genesis"

    def test_records_linked_by_hash(self, tmp_wafa_home):
        """第 N 条的 prev_hash == 第 N-1 条的 hash"""
        store.append_audit("a")
        store.append_audit("b")
        store.append_audit("c")
        records = store.read_audit(10)
        assert records[1]["prev_hash"] == records[0]["hash"]
        assert records[2]["prev_hash"] == records[1]["hash"]

    def test_detect_tampered_content(self, tmp_wafa_home):
        """改某行内容(不改 hash) → 该行 hash 与内容不符, 报篡改"""
        store.append_audit("a", amount=1)
        store.append_audit("b", amount=2)
        # 篡改第一行: 把 amount 改掉, 但保留旧 hash
        p = store.audit_path()
        lines = p.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["amount"] = 999  # 篡改
        lines[0] = json.dumps(rec, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, bad_idx, reason = store.verify_audit_chain()
        assert not ok
        assert bad_idx == 0
        assert "篡改" in reason

    def test_detect_deleted_middle_record(self, tmp_wafa_home):
        """删中间一行 → 第 N 行 prev_hash 断链"""
        for i in range(4):
            store.append_audit("act", i=i)
        p = store.audit_path()
        lines = p.read_text(encoding="utf-8").splitlines()
        # 删第 1 行(index 1), 留下 0,2,3
        del lines[1]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, bad_idx, reason = store.verify_audit_chain()
        assert not ok
        assert bad_idx is not None
        assert "断链" in reason

    def test_detect_inserted_record(self, tmp_wafa_home):
        """插入一行伪造记录 → 断链"""
        store.append_audit("real")
        p = store.audit_path()
        # 在最前插入一条伪造记录(prev_hash 对不上 genesis 之外的任何东西)
        fake = json.dumps({"ts": "fake", "action": "evil", "prev_hash": "genesis", "hash": "deadbeef"}, ensure_ascii=False)
        original = p.read_text(encoding="utf-8")
        p.write_text(fake + "\n" + original, encoding="utf-8")
        ok, bad_idx, reason = store.verify_audit_chain()
        assert not ok
        # 伪造行 hash 与内容不符(或后续 real 行 prev_hash 断链)
        assert bad_idx is not None

    def test_empty_log_is_intact(self, tmp_wafa_home):
        """空日志视为完整"""
        ok, bad_idx, _ = store.verify_audit_chain()
        assert ok

    def test_hash_deterministic(self, tmp_wafa_home):
        """相同内容应产生相同 hash(跨进程稳定)"""
        store.append_audit("same", x=1)
        records = store.read_audit(1)
        h1 = records[0]["hash"]
        # 重算
        recomputed = store._entry_hash({k: v for k, v in records[0].items()})
        assert h1 == recomputed


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
