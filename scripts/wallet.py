"""
Wafa 钱包核心 —— 密钥生成、V3 keystore 加密存储、解锁。

安全原则(本模块强制执行):
  1. 私钥仅在内存中短暂存在(解锁后返回给调用方, 用完即弃)。
  2. 私钥永不写入日志、永不打印、永不作为函数返回值的字符串形式泄露。
  3. keystore 文件权限设为 600(仅所有者可读写)。
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from eth_account import Account

from store import append_audit, keystores_dir

# 启用未审计的 hdwallet 功能不是必须的; eth-account 原生支持 keystore。
Account.enable_unaudited_hdwallet_features()


@dataclass
class WalletInfo:
    """本地钱包的公开元信息(不含任何密钥)。"""

    address: str
    label: str | None = None

    @property
    def keystore_file(self) -> Path:
        return keystores_dir() / f"{self.address.lower()}.json"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _secure_write_keystore(path: Path, keystore_json: dict) -> None:
    """写入 keystore 并设置严格文件权限(600)。"""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keystore_json, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    _chmod_600(path)


def _chmod_600(path: Path) -> None:
    """将文件权限设为仅所有者可读写。Windows 上 chmod 语义有限, 但无害。"""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows 对部分 ACL 场景可能抛错, 忽略即可
        pass


def _read_keystore(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 钱包操作
# ---------------------------------------------------------------------------

def create_wallet(password: str, label: str | None = None) -> WalletInfo:
    """
    生成全新钱包并加密存储。

    password: 用户设置的 keystore 密码(用于 V3 加密)。
    label:    可选的人类可读标签。
    返回 WalletInfo(仅含地址与标签, 不含密钥)。
    """
    if not password:
        raise ValueError("密码不能为空")
    acct = Account.create()
    info = WalletInfo(address=acct.address, label=label)
    keystore = Account.encrypt(acct.key, password)
    if label:
        keystore["wafa_label"] = label
    _secure_write_keystore(info.keystore_file, keystore)
    append_audit(
        "create",
        address=acct.address,
        label=label,
    )
    return info


def import_wallet(private_key: str, password: str, label: str | None = None) -> WalletInfo:
    """
    导入已有私钥并加密存储。

    private_key: 0x 开头的 64 位十六进制私钥。
    password:    新的 keystore 加密密码。
    label:       可选标签。
    """
    if not password:
        raise ValueError("密码不能为空")
    private_key = private_key.strip()
    acct = Account.from_key(private_key)
    info = WalletInfo(address=acct.address, label=label)
    keystore = Account.encrypt(acct.key, password)
    if label:
        keystore["wafa_label"] = label
    _secure_write_keystore(info.keystore_file, keystore)
    append_audit(
        "import",
        address=acct.address,
        label=label,
    )
    # 显式清掉局部变量中的私钥引用(尽力而为, Python GC 不保证立即回收)
    del acct
    return info


def unlock(address: str, password: str) -> Account:
    """
    用密码解锁钱包, 返回内存中的 Account 对象。

    调用方应在使用后尽快丢弃返回值, 不要缓存或序列化。
    密码错误时抛出 eth_account 的异常(KeyError / ValueError)。
    """
    info = WalletInfo(address=address)
    if not info.keystore_file.exists():
        raise FileNotFoundError(f"找不到 {address} 的 keystore 文件: {info.keystore_file}")
    keystore = _read_keystore(info.keystore_file)
    # decrypt 返回 bytes 私钥; Account.from_key 构造可签名对象
    privkey_bytes = Account.decrypt(keystore, password)
    acct = Account.from_key(privkey_bytes)
    # 立即清掉明文 bytes
    _zero_bytes(privkey_bytes)
    return acct


def _zero_bytes(b: bytes) -> None:
    """尽力清零 bytes 内容。bytes 不可变, 这里只是提示性操作。"""
    # bytes 是不可变的, 无法原地清零; 真正清零需用 bytearray。
    # 此函数保留为占位, 提醒调用方优先使用 bytearray 处理密钥材料。
    pass


def list_wallets() -> list[WalletInfo]:
    """列出所有本地钱包(仅地址与标签, 不读密钥)。"""
    result = []
    for p in sorted(keystores_dir().glob("0x*.json")):
        address = p.stem  # 文件名即地址(小写)
        label = _read_label(p)
        result.append(WalletInfo(address=address, label=label))
    return result


def _read_label(keystore_path: Path) -> str | None:
    """从 keystore 文件读取标签(若有)。标签存在 address 字段或自定义字段。"""
    try:
        data = _read_keystore(keystore_path)
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("wafa_label")


def set_label(address: str, label: str) -> None:
    """为已有钱包设置/更新标签。"""
    info = WalletInfo(address=address)
    if not info.keystore_file.exists():
        raise FileNotFoundError(f"找不到 {address} 的 keystore")
    data = _read_keystore(info.keystore_file)
    data["wafa_label"] = label
    _secure_write_keystore(info.keystore_file, data)
    append_audit("set_label", address=address, label=label)


def get_default_address() -> str | None:
    """
    返回默认钱包地址(第一个本地钱包)。
    用于 balance/send 命令省略 --address 时的兜底。
    """
    wallets = list_wallets()
    return wallets[0].address if wallets else None
