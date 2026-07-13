"""
wafa.py CLI 测试 —— 参数解析、子命令分发、import 不接受位置参数、help 输出。

注: 涉及密码输入的命令(create/import/send 的真实执行)依赖 getpass,
   在非 TTY 下会阻塞, 故本文件只测 argparse 行为与不触发 getpass 的路径。
   密码相关的功能已在 test_wallet.py 通过直接调用验证。
"""
from __future__ import annotations

import pytest

# wafa.py 不是包, 直接作为模块导入
import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# 加载 wafa.py 模块
_spec = importlib.util.spec_from_file_location("wafa_cli", _SCRIPTS / "wafa.py")
wafa_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wafa_cli)


class TestArgparse:
    def test_no_command_prints_help(self, capsys):
        with pytest.raises(SystemExit):
            wafa_cli.build_parser().parse_args([])

    def test_init_command_parsed(self):
        args = wafa_cli.build_parser().parse_args(["init"])
        assert args.command == "init"

    def test_create_with_label(self):
        args = wafa_cli.build_parser().parse_args(["create", "--label", "foo"])
        assert args.command == "create"
        assert args.label == "foo"

    def test_import_no_positional_key(self, capsys):
        """import 不应接受位置参数私钥(安全加固后的关键行为)"""
        with pytest.raises(SystemExit):
            wafa_cli.build_parser().parse_args(["import", "0xdeadbeef"])

    def test_import_with_label_only(self):
        """import 仅接受 --label, 私钥从 stdin 读"""
        args = wafa_cli.build_parser().parse_args(["import", "--label", "x"])
        assert args.command == "import"
        assert args.label == "x"
        # 不应有 private_key 属性
        assert not hasattr(args, "private_key")

    def test_list_command(self):
        args = wafa_cli.build_parser().parse_args(["list"])
        assert args.command == "list"

    def test_balance_default(self):
        args = wafa_cli.build_parser().parse_args(["balance"])
        assert args.command == "balance"

    def test_balance_with_token(self):
        args = wafa_cli.build_parser().parse_args(["balance", "--token", "usdc"])
        assert args.token == "usdc"

    def test_send_required_args(self):
        args = wafa_cli.build_parser().parse_args([
            "send", "0x" + "11" * 20, "0.5", "--reason", "test"
        ])
        assert args.command == "send"
        assert args.to == "0x" + "11" * 20
        assert args.amount == "0.5"
        assert args.reason == "test"

    def test_send_missing_to_raises(self, capsys):
        with pytest.raises(SystemExit):
            wafa_cli.build_parser().parse_args(["send"])

    def test_policy_command(self):
        args = wafa_cli.build_parser().parse_args(["policy", "show"])
        assert args.command == "policy"

    def test_history_with_limit(self):
        args = wafa_cli.build_parser().parse_args(["history", "--limit", "5"])
        assert args.limit == 5

    def test_all_subcommands_exist(self):
        """8 个子命令全部可解析"""
        # 每个命令所需的最小参数
        cmd_args = {
            "init": ["init"],
            "create": ["create", "--label", "x"],
            "import": ["import", "--label", "x"],
            "list": ["list"],
            "balance": ["balance"],
            "send": ["send", "0x" + "11" * 20, "1", "--reason", "r"],
            "policy": ["policy", "show"],
            "history": ["history"],
        }
        for cmd, argv in cmd_args.items():
            args = wafa_cli.build_parser().parse_args(argv)
            assert args.command == cmd


class TestDispatch:
    def test_dispatch_table_complete(self):
        """_DISPATCH 应覆盖全部 8 个命令"""
        expected = {"init", "create", "import", "list", "balance", "send", "policy", "history"}
        assert set(wafa_cli._DISPATCH.keys()) == expected


class TestInitViaCLI:
    def test_cmd_init_returns_zero(self, tmp_wafa_home):
        """cmd_init 在已初始化环境再次运行应成功"""
        import argparse
        args = argparse.Namespace(command="init")
        rc = wafa_cli.cmd_init(args)
        assert rc == 0


class TestListViaCLI:
    def test_cmd_list_empty(self, tmp_wafa_home, capsys):
        import argparse
        args = argparse.Namespace(command="list")
        rc = wafa_cli.cmd_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "无" in out  # 空列表提示

    def test_cmd_list_with_wallet(self, tmp_wafa_home, capsys):
        import wallet
        info = wallet.create_wallet("TestP@ssw0rd!", "cli-test")
        import argparse
        args = argparse.Namespace(command="list")
        rc = wafa_cli.cmd_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # 地址大小写不敏感比较(keystore 文件名存小写, Account.address 是 checksum 格式)
        assert info.address.lower() in out.lower()


class TestPolicyViaCLI:
    def test_cmd_policy_show(self, tmp_wafa_home, capsys):
        import argparse
        args = argparse.Namespace(command="policy", show="show")
        rc = wafa_cli.cmd_policy(args)
        assert rc == 0
        out = capsys.readouterr().out
        # 应展示策略相关字段
        assert "限额" in out or "速率" in out


class TestHistoryViaCLI:
    def test_cmd_history_empty(self, tmp_wafa_home, capsys):
        import argparse
        args = argparse.Namespace(command="history", limit=10)
        rc = wafa_cli.cmd_history(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "无记录" in out

    def test_cmd_history_shows_entries(self, tmp_wafa_home, capsys):
        import store
        store.append_audit("test_action", foo="bar")
        import argparse
        args = argparse.Namespace(command="history", limit=10)
        rc = wafa_cli.cmd_history(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "test_action" in out
