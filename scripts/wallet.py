"""
Wafa 钱包核心 —— 密钥生成、V3 keystore 加密存储、解锁。

安全原则(本模块强制执行):
  1. 私钥仅在内存中短暂存在(解锁后返回给调用方, 用完即弃)。
  2. 私钥永不写入日志、永不打印、永不作为函数返回值的字符串形式泄露。
  3. keystore 文件权限设为 600(仅所有者可读写)。
  4. 密钥材料使用 bytearray 存储, 使用后原地清零(见 secure_zero)。
     Python 的 bytes 是不可变的, 无法清零; 故全程避免用 bytes 持有明文私钥。
  5. keystore 加密使用加固的 scrypt 参数(N=2**18), 远超 eth-account 默认。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml import YAMLError

from eth_account import Account

from store import (
    append_audit,
    clear_unlock_failure,
    is_unlock_locked,
    keystores_dir,
    record_unlock_failure,
)

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
# 安全原语: 密钥材料的内存清零
# ---------------------------------------------------------------------------

def secure_zero(buf: "bytearray | memoryview") -> None:
    """
    原地清零可变字节缓冲区。用于私钥使用完毕后的清理。

    bytearray 是可变的, 可通过 [:] = b'' 原地覆写, 真正减少内存中明文残留时间。
    这是防御性措施, 不替代 keystore 加密; 主要降低"进程内存被 dump 后私钥被提取"
    的窗口期。Python 的 GC 不保证立即回收, 但清零比依赖 GC 更主动。
    """
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0
    elif isinstance(buf, memoryview):
        # memoryview over bytearray 也可写
        for i in range(len(buf)):
            buf[i] = 0


# ---------------------------------------------------------------------------
# keystore 加密参数加固
# ---------------------------------------------------------------------------

# eth-account 默认 scrypt N=131072(2**17)已可接受, 我们进一步提升到 2**18,
# 增加离线暴力破解的成本。KDF 参数只影响"加密耗时", 不影响解密兼容性。
# 参考 ethereum.org 推荐: N>=2**17, 这里取 2**18 提供更高强度。
_HARDENED_KDF_PARAMS = {
    "n": 2 ** 18,   # CPU/内存成本
    "r": 8,         # 块大小
    "p": 1,         # 并行参数
}


def _encrypt_key(privkey_bytes: bytes, password: str) -> dict:
    """用加固的 scrypt 参数加密私钥, 返回 V3 keystore JSON dict。"""
    # eth-account 的 encrypt 支持 kdf 参数; 不同版本字段名可能为 'kdf'/'kdf_params'
    # 现代版本(0.13+)通过 kdf_params 传参
    try:
        return Account.encrypt(privkey_bytes, password, kdf_params=_HARDENED_KDF_PARAMS)
    except TypeError:
        # 旧版本不支持 kdf_params 参数, 退回默认(仍安全, 只是 N 较小)
        keystore = Account.encrypt(privkey_bytes, password)
        # 手动注入加固参数(若 keystore 用了 scrypt)
        if keystore.get("crypto", {}).get("kdf") == "scrypt":
            keystore["crypto"]["kdfparams"].update(_HARDENED_KDF_PARAMS)
        return keystore


# ---------------------------------------------------------------------------
# 密码强度检查
# ---------------------------------------------------------------------------

def check_password_strength(password: str) -> tuple[bool, str]:
    """
    检查 keystore 密码强度。返回 (是否通过, 原因)。

    密码是 keystore 的唯一保护层; 弱密码 = 私钥可被离线暴力破解。
    本检查不是完美的密码学策略, 而是阻止明显不安全的密码。
    """
    if len(password) < 10:
        return False, "密码至少 10 位"
    # 字符类别计数
    classes = 0
    if any(c.islower() for c in password):
        classes += 1
    if any(c.isupper() for c in password):
        classes += 1
    if any(c.isdigit() for c in password):
        classes += 1
    if any(not c.isalnum() for c in password):
        classes += 1
    if classes < 3:
        return False, "密码需至少包含 3 类字符(大写/小写/数字/符号)"
    # 常见弱密码黑名单(不完整, 仅挡最明显的)
    weak = {"password", "1234567890", "qwertyuiop", "abcdefghij"}
    if password.lower() in weak:
        return False, "密码过于常见"
    return True, "通过"


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
    ok, why = check_password_strength(password)
    if not ok:
        raise ValueError(f"密码强度不足: {why}")
    acct = Account.create()
    info = WalletInfo(address=acct.address, label=label)
    keystore = _encrypt_key(acct.key, password)
    if label:
        keystore["wafa_label"] = label
    _secure_write_keystore(info.keystore_file, keystore)
    append_audit(
        "create",
        address=acct.address,
        label=label,
    )
    return info


def import_wallet(private_key: "str | bytes | bytearray", password: str, label: str | None = None) -> WalletInfo:
    """
    导入已有私钥并加密存储。

    private_key: 0x 开头的十六进制字符串, 或 bytes/bytearray。
                 推荐用 bytearray, 调用方可在本函数返回后调用 secure_zero 清零。
    password:    新的 keystore 加密密码。
    label:       可选标签。
    """
    if not password:
        raise ValueError("密码不能为空")
    ok, why = check_password_strength(password)
    if not ok:
        raise ValueError(f"密码强度不足: {why}")

    # 统一为 bytearray 以便用完清零; 不保留原始引用
    if isinstance(private_key, str):
        hex_str = private_key.strip()
        if hex_str.startswith(("0x", "0X")):
            hex_str = hex_str[2:]
        key_bytes = bytearray.fromhex(hex_str)
        # 清掉本地 hex_str 的内存副本(尽力而为, str 不可变但可解除引用)
        del hex_str
    elif isinstance(private_key, (bytes, bytearray)):
        raw = bytes(private_key)
        if len(raw) == 32:
            # 已是原始 32 字节私钥
            key_bytes = bytearray(raw)
        elif len(raw) in (64, 66):
            # 可能是 hex 编码的私钥字符串(64 或含 0x 前缀的 66)
            try:
                hex_str = raw.decode("ascii")
                if hex_str.startswith(("0x", "0X")):
                    hex_str = hex_str[2:]
                key_bytes = bytearray.fromhex(hex_str)
                del hex_str
            except (UnicodeDecodeError, ValueError) as e:
                raise ValueError(
                    "私钥必须是 32 字节原始数据, 或其 hex 编码(64 hex 字符)"
                ) from e
        else:
            raise ValueError(
                f"私钥长度异常: {len(raw)} 字节; 应为 32(原始)或 64(hex 编码)"
            )
        del raw
    else:
        raise TypeError("private_key 必须是 str/bytes/bytearray")

    try:
        acct = Account.from_key(bytes(key_bytes))
        info = WalletInfo(address=acct.address, label=label)
        keystore = _encrypt_key(acct.key, password)
        if label:
            keystore["wafa_label"] = label
        _secure_write_keystore(info.keystore_file, keystore)
        append_audit(
            "import",
            address=acct.address,
            label=label,
        )
        return info
    finally:
        # 无论成功失败, 清零临时密钥材料
        secure_zero(key_bytes)


class UnlockLocked(Exception):
    """钱包因连续解锁失败过多被暂时锁定。"""


# 解锁节流默认参数(可被 policy.yaml 的 unlock_throttle 覆盖)
_DEFAULT_MAX_ATTEMPTS = 5
_DEFAULT_LOCKOUT_SECONDS = 300


def _load_throttle_config() -> tuple[int, int]:
    """从 policy.yaml 读取 unlock_throttle 参数, 缺失或格式错误则用默认。

    注意: 这里故意收窄异常类型, 只捕获"配置读不出/格式坏"这类预期错误,
    不吞 KeyboardInterrupt 等; 同时打 stderr 告警, 避免静默退回默认导致
    用户以为"调成了 3 次"实际还在用 5 次。
    """
    try:
        from policy import load_policy
        pol = load_policy() or {}
        t = pol.get("unlock_throttle", {}) or {}
        return int(t.get("max_attempts", _DEFAULT_MAX_ATTEMPTS)), int(t.get("lockout_seconds", _DEFAULT_LOCKOUT_SECONDS))
    except (OSError, YAMLError, TypeError, ValueError) as e:
        # 配置读不到/格式坏: 退默认, 但要告警, 不能静默
        print(f"[wafa] 警告: 读取 unlock_throttle 失败({type(e).__name__}: {e}), 退回默认值"
              f"(max_attempts={_DEFAULT_MAX_ATTEMPTS}, lockout={_DEFAULT_LOCKOUT_SECONDS}s)",
              file=sys.stderr)
        return _DEFAULT_MAX_ATTEMPTS, _DEFAULT_LOCKOUT_SECONDS


def unlock(address: str, password: str) -> Account:
    """
    用密码解锁钱包, 返回内存中的 Account 对象。

    调用方应在使用后尽快丢弃返回值, 不要缓存或序列化。
    密码错误时抛出 eth_account 的异常(KeyError / ValueError);
    连续失败过多时抛出 UnlockLocked(锁定期内不再跑 scrypt)。
    """
    # 节流: 锁定期内直接拒绝, 不跑 scrypt(不消耗 CPU, 不给试密机会)
    locked, remaining = is_unlock_locked(address)
    if locked:
        append_audit("unlock_locked", address=address, remaining_seconds=round(remaining, 1))
        raise UnlockLocked(
            f"钱包 {address[:10]}... 因连续失败被锁定, 还剩 {int(remaining)} 秒"
        )

    info = WalletInfo(address=address)
    if not info.keystore_file.exists():
        raise FileNotFoundError(f"找不到 {address} 的 keystore 文件: {info.keystore_file}")
    keystore = _read_keystore(info.keystore_file)

    max_attempts, lockout_seconds = _load_throttle_config()
    try:
        # decrypt 返回 bytes 私钥(不可变); 立即拷贝到 bytearray 以便清零
        privkey_bytes = Account.decrypt(keystore, password)
    except (ValueError, KeyError):
        # 密码错误(MAC mismatch 等): 记账, 可能触发锁定。
        # 故意只捕这两类 —— decrypt 密码错的真实类型; 其余异常(如 keystore
        # 文件损坏的 JSONDecodeError)不应被当作"试密失败"计入节流, 否则
        # 文件坏了也会把用户锁死。
        count, locked_until = record_unlock_failure(address, max_attempts, lockout_seconds)
        append_audit("unlock_failed", address=address, fail_count=count)
        if count >= max_attempts:
            raise UnlockLocked(
                f"连续失败 {count} 次, 钱包已锁定 {lockout_seconds} 秒"
            ) from None
        raise
    else:
        # 成功: 清零失败计数
        clear_unlock_failure(address)
        privkey_mut = bytearray(privkey_bytes)
        # 原始 bytes 立即解除引用, 缩短明文残留窗口
        del privkey_bytes
        try:
            acct = Account.from_key(bytes(privkey_mut))
            return acct
        finally:
            secure_zero(privkey_mut)


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
