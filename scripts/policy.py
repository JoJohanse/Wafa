"""
Wafa 策略引擎 —— 在签名前拦截每一笔转账。

检查项(任一不通过即拒绝, 且不解锁钱包):
  1. kill_switch: 紧急停止
  2. 单笔限额    max_per_tx_native / max_per_tx_token
  3. 日累计限额  daily_limit_native / daily_limit_token
  4. 速率限制    max_tx_per_minute / max_tx_per_hour
  5. 收款白名单  whitelist
  6. 用途/理由   require_reason / allowed_purposes

这是 agent 自主支付的"软"护栏: 应用层拒绝。
链上硬约束(如 ERC-4337 会话密钥)不在本期范围, 见 references/security.md。
"""

from __future__ import annotations

from dataclasses import dataclass

from store import (
    count_tx_in_window,
    get_daily_spent,
    load_policy,
)


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
    执行全部策略检查。

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
