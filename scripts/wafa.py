#!/usr/bin/env python3
"""
Wafa —— AI Agent 数字钱包 CLI

用法:
    python wafa.py init
    python wafa.py create --label my-wallet
    echo '0xABC...' | python wafa.py import --label imported   # 私钥走 stdin
    python wafa.py list
    python wafa.py balance                       # 查默认钱包
    python wafa.py balance --address 0x...
    python wafa.py balance --token usdc          # 查 ERC-20
    python wafa.py send 0xRecipient 0.01 --reason "api access"
    python wafa.py send 0xRecipient 5 --token usdc --reason "data purchase"
    python wafa.py policy show
    python wafa.py history --limit 20

密码与私钥均通过 stdin(getpass) 读取, 绝不作为命令行参数
(防 shell 历史、ps 进程列表、系统日志泄露)。
"""

from __future__ import annotations

import argparse
import getpass
import sys
import traceback
from pathlib import Path

# 确保能 import 同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chain as chain_mod
import policy as policy_mod
import store
import wallet as wallet_mod


# ---------------------------------------------------------------------------
# 输出工具
# ---------------------------------------------------------------------------

def ok(msg: str) -> None:
    print(msg)


def err(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)


def section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def ask_password(prompt: str = "keystore 密码: ", confirm: bool = False) -> str:
    """安全读取密码; confirm=True 时要求二次确认。"""
    pw = getpass.getpass(prompt)
    if not pw:
        raise ValueError("密码不能为空")
    if confirm:
        pw2 = getpass.getpass("再次输入密码: ")
        if pw != pw2:
            raise ValueError("两次密码不一致")
    return pw


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    section("初始化 Wafa")
    print(f"数据目录: {store.wafa_home()}")
    results = store.init_home()
    for r in results:
        print(r)
    ok("\n✅ 初始化完成。")
    print("下一步:")
    print("  1. 编辑 ~/.wafa/config.yaml 配置你的链与 RPC(可选, 已有默认值)")
    print("  2. 编辑 ~/.wafa/policy.yaml 配置支付策略(可选)")
    print("  3. 运行 python wafa.py create 创建第一个钱包")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    section("创建新钱包")
    pw = ask_password("为钱包设置密码: ", confirm=True)
    try:
        info = wallet_mod.create_wallet(password=pw, label=args.label)
    except ValueError as e:
        err(f"创建失败: {e}")
        return 1
    ok(f"\n✅ 钱包已创建")
    print(f"  地址: {info.address}")
    if args.label:
        print(f"  标签: {args.label}")
    print(f"  链:   {args.chain} (余额查询时使用)")
    print(f"\n  ⚠️  请妥善保管密码。丢失密码 = 丢失资金。")
    print(f"  keystore 位于: {info.keystore_file}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    section("导入私钥")
    # 安全: 私钥从 stdin 读取(可用管道或交互输入), 绝不作为命令行参数。
    # 命令行参数会进入 shell 历史、进程列表(ps)、系统日志, 风险极高。
    print("  请通过 stdin 提供私钥(粘贴后回车), 或用管道:")
    print("    echo '0xABC...' | python wafa.py import --label foo")
    print("  (私钥不会回显; 用管道时请确认管道另一端可信)")
    print()
    try:
        privkey_input = getpass.getpass("粘贴私钥(0x... 或裸hex, 输入不回显): ")
    except (KeyboardInterrupt, EOFError):
        err("已取消")
        return 1
    if not privkey_input:
        err("未收到私钥")
        return 1
    privkey_buf = bytearray(privkey_input.encode("utf-8"))
    # 立即清掉原始字符串引用(str 不可变, 只能解除引用)
    del privkey_input
    pw = ask_password("为此钱包设置新密码: ", confirm=True)
    try:
        info = wallet_mod.import_wallet(private_key=bytes(privkey_buf), password=pw, label=args.label)
    except ValueError as e:
        err(f"导入失败: {e}")
        return 1
    finally:
        # 无论成败, 清零本地缓冲
        wallet_mod.secure_zero(privkey_buf)
    ok(f"\n✅ 钱包已导入")
    print(f"  地址: {info.address}")
    if args.label:
        print(f"  标签: {args.label}")
    print(f"  ⚠️  私钥已加密存储; 原始私钥请从剪贴板/历史中清除。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    section("本地钱包列表")
    wallets = wallet_mod.list_wallets()
    if not wallets:
        print("  (无) 运行 `python wafa.py create` 创建第一个钱包。")
        return 0
    for i, w in enumerate(wallets, 1):
        label = f"  [{w.label}]" if w.label else ""
        print(f"  {i}. {w.address}{label}")
    print(f"\n  共 {len(wallets)} 个钱包")
    return 0


def _resolve_address(args_address: str | None) -> str:
    """解析目标地址: 显式 > 默认钱包。"""
    if args_address:
        return args_address
    default = wallet_mod.get_default_address()
    if not default:
        raise ValueError("无本地钱包, 请先运行 `python wafa.py create` 或指定 --address")
    return default


def cmd_balance(args: argparse.Namespace) -> int:
    section("查询余额")
    try:
        address = _resolve_address(args.address)
    except ValueError as e:
        err(str(e))
        return 1

    config = store.load_config()
    chain = args.chain or config.get("default_chain", "base")
    try:
        w3, cc = chain_mod.connect(chain)
    except (ConnectionError, ValueError) as e:
        err(str(e))
        return 1

    print(f"  地址: {address}")
    print(f"  链:   {chain}")

    if args.token:
        token_info = store.resolve_token(config, chain, args.token)
        if not token_info:
            err(f"未知代币: {args.token} (请在 config.yaml 的 tokens.{chain} 中配置)")
            return 1
        try:
            bal = chain_mod.get_token_balance(w3, address, token_info["address"], cc)
        except ValueError as e:
            err(str(e))
            return 1
        print(f"\n  💰 {bal.symbol}: {bal.amount}")
        print(f"     合约: {bal.token_address}")
    else:
        bal = chain_mod.get_native_balance(w3, address, cc)
        print(f"\n  💰 {bal.symbol}: {bal.amount}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    section("发起转账")

    # 1. 基本校验
    if not chain_mod.is_valid_address(args.to):
        err(f"非法收款地址: {args.to}")
        return 1
    try:
        amount = float(args.amount)
        if amount <= 0:
            raise ValueError("金额必须 > 0")
    except ValueError as e:
        err(f"非法金额: {args.amount} ({e})")
        return 1

    # 2. 解析发送方地址
    try:
        from_address = _resolve_address(args.address)
    except ValueError as e:
        err(str(e))
        return 1

    # 3. 判定是原生币还是代币, 确定 kind
    config = store.load_config()
    chain = args.chain or config.get("default_chain", "base")
    token_info = None
    if args.token:
        token_info = store.resolve_token(config, chain, args.token)
        if not token_info:
            err(f"未知代币: {args.token}")
            return 1
        kind = "token"
    else:
        kind = "native"

    # 4. 策略检查(签名前拦截)
    decision = policy_mod.check(
        amount=amount,
        to_address=args.to,
        reason=args.reason,
        kind=kind,
    )
    if not decision:
        store.append_audit(
            "send_declined",
            from_addr=from_address,
            to=args.to,
            amount=amount,
            kind=kind,
            reason=decision.reason,
        )
        err(f"策略拒绝: {decision.reason}")
        return 1

    # 5. 解锁钱包(此时才需要密码)
    print(f"\n  收款方: {args.to}")
    print(f"  金额:   {amount} {kind}" + (f" ({args.token})" if args.token else ""))
    if args.reason:
        print(f"  用途:   {args.reason}")
    try:
        pw = ask_password(f"\n解锁钱包 {from_address[:10]}... 的密码: ")
    except (KeyboardInterrupt, EOFError):
        err("已取消")
        return 1
    try:
        acct = wallet_mod.unlock(from_address, pw)
    except Exception as e:
        err(f"解锁失败(密码错误?): {e}")
        store.append_audit("unlock_failed", address=from_address)
        return 1

    # 6. 连接链
    try:
        w3, cc = chain_mod.connect(chain)
    except (ConnectionError, ValueError) as e:
        err(str(e))
        return 1

    gas_mult = config.get("tx_defaults", {}).get("gas_multiplier", 1.1)

    # 7. 签名 + 广播
    try:
        if kind == "native":
            result = chain_mod.send_native(
                w3, cc, acct, args.to, amount, gas_multiplier=gas_mult
            )
        else:
            result = chain_mod.send_token(
                w3, cc, acct,
                token_info["address"],
                token_info.get("decimals", 6),
                args.to, amount, gas_multiplier=gas_mult,
            )
    except Exception as e:
        err(f"交易失败: {e}")
        store.append_audit(
            "send_failed",
            from_addr=from_address, to=args.to, amount=amount,
            kind=kind, error=str(e),
        )
        return 1

    # 8. 记账 + 审计
    policy_mod.record_outcome(amount, kind=kind)
    store.append_audit(
        "send_ok",
        from_addr=from_address, to=args.to, amount=amount,
        symbol=result.symbol, kind=kind, tx_hash=result.tx_hash, reason=args.reason,
    )

    # 9. 输出
    ok(f"\n✅ 交易已提交")
    print(f"  tx hash: {result.tx_hash}")
    print(f"  金额:    {result.amount_human} {result.symbol}")
    print(f"  收款方:  {result.to}")
    if result.explorer_url:
        print(f"  浏览器:  {result.explorer_url}")
    print(f"\n  提示: 交易上链可能需要数秒(L2)至数分钟(L1)。")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    section("当前策略")
    policy = policy_mod.load_policy()

    if policy.get("kill_switch"):
        print("  🚨 kill_switch: 已开启(所有转账被拒绝)")

    limits = policy.get("limits", {})
    print("\n  限额:")
    for k, v in limits.items():
        print(f"    {k}: {v}")
    if not limits:
        print("    (无单笔/日累计限制)")

    rate = policy.get("rate_limit", {})
    print("\n  速率:")
    for k, v in rate.items():
        print(f"    {k}: {v}")
    if not rate:
        print("    (无速率限制)")

    wl = policy.get("whitelist", {})
    print(f"\n  收款白名单: {'已开启' if wl.get('enabled') else '已关闭'}")
    if wl.get("enabled") and wl.get("addresses"):
        for a in wl["addresses"]:
            print(f"    - {a}")

    safety = policy.get("safety", {})
    print(f"\n  要求理由: {safety.get('require_reason', False)}")
    ap = safety.get("allowed_purposes") or []
    print(f"  允许用途: {ap if ap else '(不限制)'}")

    # 今日累计
    print("\n  今日累计花费:")
    print(f"    原生币: {policy_mod.get_daily_spent('native')}")
    print(f"    代币:   {policy_mod.get_daily_spent('token')}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    section(f"最近 {args.limit} 条审计记录")
    records = store.read_audit(limit=args.limit)
    if not records:
        print("  (无记录)")
        return 0
    for r in records:
        ts = r.get("ts", "?")
        action = r.get("action", "?")
        parts = []
        for k in ("amount", "symbol", "to", "tx_hash", "reason"):
            if k in r:
                parts.append(f"{k}={r[k]}")
        detail = ", ".join(parts) if parts else ""
        print(f"  [{ts}] {action}  {detail}")
    return 0


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wafa",
        description="Wafa —— AI Agent 数字钱包。通过子命令管理钱包、查询余额、安全转账。",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # init
    sp = sub.add_parser("init", help="初始化 ~/.wafa 目录与配置")

    # create
    sp = sub.add_parser("create", help="创建新钱包")
    sp.add_argument("--label", help="钱包标签")
    sp.add_argument("--chain", help="目标链(仅作记录, 默认用 config 的 default_chain)")

    # import (私钥从 stdin 读取, 不作为命令行参数, 防 shell 历史/ps 泄露)
    sp = sub.add_parser("import", help="导入已有私钥(私钥从 stdin 读取)")
    sp.add_argument("--label", help="钱包标签")

    # list
    sub.add_parser("list", help="列出本地钱包")

    # balance
    sp = sub.add_parser("balance", help="查询余额")
    sp.add_argument("--address", help="钱包地址(默认用第一个本地钱包)")
    sp.add_argument("--token", help="ERC-20 代币别名或合约地址(如 usdc)")
    sp.add_argument("--chain", help="链(默认用 config 的 default_chain)")

    # send
    sp = sub.add_parser("send", help="发起转账")
    sp.add_argument("to", help="收款地址")
    sp.add_argument("amount", help="金额(人类可读单位)")
    sp.add_argument("--token", help="ERC-20 代币别名或合约地址; 省略则发送原生币")
    sp.add_argument("--reason", help="转账理由(策略要求时必填)")
    sp.add_argument("--address", help="发送方地址(默认用第一个本地钱包)")
    sp.add_argument("--chain", help="链(默认用 config 的 default_chain)")

    # policy
    sp = sub.add_parser("policy", help="查看策略")
    sp.add_argument("show", help="显示当前策略", nargs="?", default="show")

    # history
    sp = sub.add_parser("history", help="查看审计记录")
    sp.add_argument("--limit", type=int, default=20, help="显示条数")

    return p


_DISPATCH = {
    "init": cmd_init,
    "create": cmd_create,
    "import": cmd_import,
    "list": cmd_list,
    "balance": cmd_balance,
    "send": cmd_send,
    "policy": cmd_policy,
    "history": cmd_history,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH.get(args.command)
    if not handler:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
    except Exception as e:
        err(f"未预期错误: {e}")
        if "--debug" in (argv or sys.argv):
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
