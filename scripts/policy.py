"""
Wafa 策略引擎 —— 在签名前拦截每一笔转账, 并拥有自己的计数状态。

检查项(任一不通过即拒绝, 且不解锁钱包):
  1. kill_switch: 紧急停止
  2. 单笔限额    max_per_tx_native / max_per_tx_token
  3. 日累计限额  daily_limit_native / daily_limit_token
  4. 速率限制    max_tx_per_minute / max_tx_per_hour
  5. 收款白名单  whitelist
  6. 用途/理由   require_reason / allowed_purposes

状态归属:
  本模块拥有"日累计"与"速率窗口"两种运行时计数状态, 通过 store 的
  通用 KV(load_state / save_state) 持久化到 state.json。
  store 只负责文件 I/O, 不感知计数的语义与结构。

  - check()          读状态 + 读策略, 返回 Decision(纯读)
  - record_outcome() 写状态(仅成功发送后调用), 一次更新日累计 + 速率时间戳
  - get_daily_spent / count_tx_in_window  只读查询, 供 CLI 展示

这是 agent 自主支付的"软"护栏: 应用层拒绝。
链上硬约束(如 ERC-4337 会话密钥)不在本期范围, 见 references/security.md。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

import yaml

from store import load_state, policy_path, save_state


# ---------------------------------------------------------------------------
# 策略配置读取
# ---------------------------------------------------------------------------

def load_policy() -> dict:
    """加载 policy.yaml; 若不存在返回宽松默认(不阻断基本使用)。"""
    p = policy_path()
    if not p.exists():
        return _relaxed_policy()
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _relaxed_policy() -> dict:
    """无策略文件时的宽松兜底: 仅保留 require_reason, 其余不限制。"""
    return {
        "limits": {},
        "rate_limit": {},
        "whitelist": {"enabled": False, "addresses": []},
        "safety": {"require_reason": False, "allowed_purposes": []},
        "kill_switch": False,
    }


# ---------------------------------------------------------------------------
# 决策结果
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    """策略检查结果。"""

    allowed: bool
    reason: str = ""
    detail: dict | None = None

    def __bool__(self) -> bool:
        return self.allowed


def check(
    amount: float,
    to_address: str,
    reason: str | None,
    kind: str = "native",
) -> Decision:
    """
    执行全部策略检查(纯读, 不改状态)。

    amount:     人类可读金额(ETH 或 USDC)
    to_address: 收款地址
    reason:     转账理由(来自 --reason)
    kind:       'native' 或 'token', 决定查哪一组限额
    返回 Decision; allowed=False 时 reason 给出拒绝原因。
    """
    policy = load_policy()

    # 0. 紧急停止
    if policy.get("kill_switch", False):
        return Decision(False, "kill_switch 已开启: 所有转账被拒绝")

    limits = policy.get("limits", {})
    rate = policy.get("rate_limit", {})
    whitelist = policy.get("whitelist", {})
    safety = policy.get("safety", {})

    # 1. 单笔限额
    max_per_tx_key = f"max_per_tx_{kind}"
    if max_per_tx_key in limits:
        cap = float(limits[max_per_tx_key])
        if amount > cap:
            return Decision(
                False,
                f"超过单笔限额: {amount} > {cap} ({kind})",
                {"limit_type": "per_tx", "cap": cap, "amount": amount},
            )

    # 2. 日累计限额
    daily_key = f"daily_limit_{kind}"
    if daily_key in limits:
        cap = float(limits[daily_key])
        spent = get_daily_spent(kind=kind)
        if spent + amount > cap:
            return Decision(
                False,
                f"超过日累计限额: 今日已花费 {spent}, 本次 {amount}, 合计 {spent + amount} > 上限 {cap} ({kind})",
                {"limit_type": "daily", "cap": cap, "spent": spent, "amount": amount},
            )

    # 3. 速率限制
    per_minute = rate.get("max_tx_per_minute")
    if per_minute is not None:
        n = count_tx_in_window(60)
        if n >= int(per_minute):
            return Decision(
                False,
                f"触发速率限制: 最近 60 秒内已有 {n} 笔(上限 {per_minute})",
                {"limit_type": "rate_minute", "count": n, "cap": per_minute},
            )
    per_hour = rate.get("max_tx_per_hour")
    if per_hour is not None:
        n = count_tx_in_window(3600)
        if n >= int(per_hour):
            return Decision(
                False,
                f"触发速率限制: 最近 1 小时内已有 {n} 笔(上限 {per_hour})",
                {"limit_type": "rate_hour", "count": n, "cap": per_hour},
            )

    # 4. 收款白名单
    if whitelist.get("enabled", False):
        allowed_addrs = {
            a.lower().strip() for a in whitelist.get("addresses", []) if a
        }
        if to_address.lower().strip() not in allowed_addrs:
            return Decision(
                False,
                f"收款方不在白名单: {to_address}",
                {"limit_type": "whitelist", "to": to_address},
            )

    # 5. 用途/理由
    if safety.get("require_reason", False):
        if not reason or not reason.strip():
            return Decision(
                False,
                "缺少转账理由: 策略要求 --reason, 请附上转账用途",
                {"limit_type": "reason_required"},
            )
    allowed_purposes = safety.get("allowed_purposes") or []
    if allowed_purposes and reason:
        if reason.strip() not in allowed_purposes:
            return Decision(
                False,
                f"用途不在允许列表: '{reason}'. 允许: {allowed_purposes}",
                {"limit_type": "purpose", "reason": reason, "allowed": allowed_purposes},
            )

    return Decision(True, "通过")


# ---------------------------------------------------------------------------
# 状态写入 —— 仅成功发送后调用
# ---------------------------------------------------------------------------

def record_outcome(amount: float, kind: str = "native") -> None:
    """记录一次成功发送: 同时更新日累计与速率时间戳。

    这是策略状态的唯一公开写入口。调用方仅在发送成功后调用一次;
    policy 内部保证日累计与速率窗口一起更新, 避免调用方漏写其一。
    失败/被拒绝的发送不应调用本方法(速率窗口只数成功发送)。
    """
    _record_spend(amount, kind=kind)
    _record_tx_timestamp()


# ---------------------------------------------------------------------------
# 只读查询 —— 供 CLI 展示
# ---------------------------------------------------------------------------

def get_daily_spent(kind: str = "native") -> float:
    """读取当日累计花费。kind: 'native' | 'token'。"""
    state = load_state()
    today = _today_key()
    return state.get("daily", {}).get(today, {}).get(kind, 0.0)


def count_tx_in_window(seconds: int) -> int:
    """统计最近 N 秒内的转账次数。"""
    state = load_state()
    txs = state.get("tx_timestamps", [])
    cutoff = time.time() - seconds
    return sum(1 for t in txs if t >= cutoff)


# ---------------------------------------------------------------------------
# 内部: 计数状态读写
# ---------------------------------------------------------------------------

def _today_key() -> str:
    """当日日期键, 用于日累计滚动重置。"""
    return date.today().isoformat()


def _record_spend(amount: float, kind: str = "native") -> None:
    """累加当日花费并落盘。"""
    state = load_state()
    today = _today_key()
    daily = state.setdefault("daily", {})
    today_entry = daily.setdefault(today, {"native": 0.0, "token": 0.0})
    today_entry[kind] = round(today_entry.get(kind, 0.0) + amount, 8)
    save_state(state)


def _record_tx_timestamp() -> None:
    """记录一次转账的时间戳(用于速率限制)并落盘。"""
    state = load_state()
    txs = state.setdefault("tx_timestamps", [])
    txs.append(time.time())
    # 只保留最近 1 小时(足够覆盖分钟/小时窗口)
    cutoff = time.time() - 3600
    state["tx_timestamps"] = [t for t in txs if t >= cutoff]
    save_state(state)
