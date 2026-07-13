"""
Wafa 存储层 —— 路径管理、配置读写、状态追踪、审计日志。

所有模块通过本文件获取存储位置与读写状态, 保证跨平台(Windows/Unix)一致。
本模块不涉及任何密钥材料。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# 路径管理
# ---------------------------------------------------------------------------

# 允许通过环境变量覆盖数据目录(便于测试与多实例)
_WAFA_DIR_ENV = "WAFA_HOME"
_DEFAULT_DIR_NAME = ".wafa"


def _chmod_700(p: Path) -> None:
    """目录权限设为仅所有者可读写执行(700)。Windows 上语义有限, 但无害。"""
    import stat as _stat
    try:
        p.chmod(_stat.S_IRWXU)
    except OSError:
        pass


def wafa_home() -> Path:
    """返回 Wafa 数据根目录(~/.wafa), 不存在则创建。

    目录权限设为 700(仅所有者可进入), 因为内含加密 keystore、
    配置、状态与审计日志, 均为敏感财务数据。
    """
    env = os.environ.get(_WAFA_DIR_ENV)
    home = Path(env) if env else Path.home() / _DEFAULT_DIR_NAME
    home.mkdir(parents=True, exist_ok=True)
    _chmod_700(home)
    return home


def keystores_dir() -> Path:
    d = wafa_home() / "keystores"
    d.mkdir(parents=True, exist_ok=True)
    _chmod_700(d)
    return d


def config_path() -> Path:
    return wafa_home() / "config.yaml"


def policy_path() -> Path:
    return wafa_home() / "policy.yaml"


def state_path() -> Path:
    return wafa_home() / "state.json"


def audit_path() -> Path:
    return wafa_home() / "audit.log"


# 打包时随仓库附带的模板(本文件相对位置: scripts/store.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = {
    "config": _REPO_ROOT / "config" / "config.example.yaml",
    "policy": _REPO_ROOT / "config" / "policy.example.yaml",
}


def template_path(kind: str) -> Path:
    """返回仓库内附带的配置模板路径。"""
    if kind not in _TEMPLATES:
        raise KeyError(f"未知模板类型: {kind}")
    return _TEMPLATES[kind]


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "default_chain": "base",
    "chains": {
        "base": {
            "chain_id": 8453,
            "rpc_url": "https://mainnet.base.org",
            "explorer": "https://basescan.org",
            "native_symbol": "ETH",
            "native_decimals": 18,
        }
    },
    "tokens": {},
    "tx_defaults": {"gas_multiplier": 1.1, "receipt_timeout": 120},
}


def load_config() -> dict:
    """加载 config.yaml; 若不存在返回内置默认值, 保证 CLI 不会因缺配置崩溃。"""
    p = config_path()
    if not p.exists():
        return _DEFAULT_CONFIG.copy()
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # 合并默认值, 保证缺失字段有兜底
    merged = _DEFAULT_CONFIG.copy()
    merged.update(data)
    if "chains" not in data or not data.get("chains"):
        merged["chains"] = _DEFAULT_CONFIG["chains"]
    if "tx_defaults" not in data:
        merged["tx_defaults"] = _DEFAULT_CONFIG["tx_defaults"]
    return merged


def get_chain_config(config: dict, chain: str | None = None) -> dict:
    """返回指定链的配置; chain 为 None 时用 default_chain。"""
    chain = chain or config.get("default_chain", "base")
    chains = config.get("chains", {})
    if chain not in chains:
        raise ValueError(
            f"链 '{chain}' 未在 config.yaml 的 chains 中定义。"
            f" 可用: {list(chains.keys())}"
        )
    return chains[chain]


# ---------------------------------------------------------------------------
# 通用 KV 状态(state.json) —— 原子读写
#
# store 只提供 state.json 的文件 I/O, 不感知其内部结构。
# 计数语义(日累计、速率窗口)归 policy 模块所有, 通过本接口持久化。
# ---------------------------------------------------------------------------

def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)  # 原子替换


# ---------------------------------------------------------------------------
# 审计日志(audit.log) —— JSON Lines, 每行一笔
# ---------------------------------------------------------------------------

def append_audit(action: str, **detail) -> None:
    """追加一条审计记录。严禁在 detail 中放入私钥/密码。

    日志文件权限设为 600(仅所有者可读写), 因为日志含地址、金额、用途等
    敏感财务行为, 不应被同机其他用户读取。
    """
    p = audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "action": action,
        **detail,
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # 每次写入后收紧权限(首次创建时尤其重要)
    _chmod_600(p)


def _chmod_600(p) -> None:
    """文件权限设为仅所有者可读写。Windows 上语义有限, 但无害。"""
    import stat as _stat
    try:
        p.chmod(_stat.S_IRUSR | _stat.S_IWUSR)
    except OSError:
        pass


def read_audit(limit: int = 20) -> list[dict]:
    """读取最近 N 条审计记录。"""
    p = audit_path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    records = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

def init_home() -> list[str]:
    """
    初始化 ~/.wafa 目录结构, 从仓库模板复制配置。
    返回创建/跳过的文件描述列表(供 CLI 输出)。
    """
    results = []
    keystores_dir()  # 确保 keystores 目录存在

    for kind, dest in (("config", config_path()), ("policy", policy_path())):
        if dest.exists():
            results.append(f"  已存在, 跳过: {dest}")
        else:
            src = template_path(kind)
            if src.exists():
                with open(src, "r", encoding="utf-8") as f:
                    content = f.read()
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)
                results.append(f"  已生成: {dest}")
            else:
                results.append(f"  [警告] 模板缺失, 跳过: {dest}")

    # state.json 与 audit.log 在首次写入时自动创建, 此处不预建
    return results
