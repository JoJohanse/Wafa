# AGENTS.md — Wafa Workspace Instructions

> 🌐 Language / 语言: **English** (this file) · [中文](AGENTS_zh.md)

## Project Overview

Wafa (Wallet for Agent) is an EVM crypto wallet distributed as an Agent Skill,
enabling AI agents to autonomously create accounts, query balances, and send
ETH / ERC-20 transfers. A built-in policy engine intercepts every transfer
before signing (per-tx / daily-cumulative / rate-limit / whitelist /
kill_switch). HTTP 402 payment negotiation is not implemented (see `idea.md`
for the original vision and roadmap).

- Repo: https://github.com/JoJohanse/Wafa
- Language: Python ≥3.10 (dev uses conda env `wafa`, Python 3.14)
- Form: repo root = the Skill package; users clone then copy to `~/.agents/skills/wafa/`

## Directory Layout

```
wafa/
├── scripts/          # all executable code (flat .py modules, NOT a package; import via sys.path)
│   ├── wafa.py        # CLI entry (init/create/import/list/balance/send/policy/history)
│   ├── wallet.py      # wallet core: V3 keystore encrypt/decrypt, unlock, secure_zero
│   ├── chain.py       # chain interaction: connect, balance, EIP-1559 sign+broadcast (local sign, key never sent to RPC)
│   ├── erc20.py       # ERC-20 resolver: alias/address → Token(address, decimals); unknown address queries on-chain decimals()
│   ├── policy.py      # policy engine: pre-sign interception (limits/rate/whitelist/purpose/kill_switch)
│   └── store.py       # storage layer: paths, config, state, audit log (no key material)
├── tests/            # pytest tests, isolated WAFA_HOME (see conftest.py)
├── config/           # config.example.yaml / policy.example.yaml templates (init copies to ~/.wafa/)
├── references/        # security.md advanced security (read on demand, not in SKILL.md body)
├── SKILL.md / SKILL_zh.md      # Skill instructions (English primary / Chinese), read by agents
├── README.md / README_zh.md    # user docs (English primary / Chinese)
├── AGENTS.md / AGENTS_zh.md     # workspace instructions (this file / Chinese)
├── CONTEXT.md        # domain glossary; read before changing sensitive terminology
├── idea.md           # original vision (402 / machine payments), reference only — not implemented
└── pytest.ini
```

## Common Commands

```bash
# Activate dev environment
conda activate wafa

# Install dependencies (runtime / dev+test)
pip install -r scripts/requirements.txt
pip install -r scripts/requirements-dev.txt   # includes pytest

# Run tests (all 126 pass without RPC access in the sandbox)
python -m pytest
python -m pytest tests/test_wallet.py -v      # single file

# Invoke CLI directly (from repo root)
python scripts/wafa.py init
python scripts/wafa.py create --label main    # interactive password via getpass
python scripts/wafa.py balance --token usdc
echo '0xKEY' | python scripts/wafa.py import --label x   # private key via stdin, never on cmdline
```

Tests isolate the data directory via the `WAFA_HOME` env var (see
`tests/conftest.py` fixtures: `tmp_wafa_home` / `fresh_state` /
`strict_policy`). **Never touches the real `~/.wafa`.** The sandbox cannot
reach blockchain RPC, so `test_chain.py` only covers offline logic (address
validation, signing, calldata construction); real broadcast tests need a
networked environment with `-m e2e`.

## Architecture Boundaries & Layering (read before editing)

1. **`scripts/` are flat modules, NOT a Python package.** Modules import each
   other via `import store`; `wafa.py` does `sys.path.insert(0, scripts_dir)`
   on startup. Do not switch to relative imports or add `__init__.py` without
   adjusting every import accordingly.

2. **Layering is strictly one-directional:**
   - `wafa.py` (CLI) → wallet / chain / policy / erc20 → store
   - `store.py` is the lowest layer; **it imports nothing from upper layers**
     and never touches keys.
   - `wallet.py` holds keys; `chain.py` holds RPC connections; the two do not
     depend on each other.
   - `erc20.py` resolves the `--token` argument into a `Token(address,
     decimals)`, querying on-chain `decimals()` for unknown addresses
     (eliminates the old `store.resolve_token` hardcoded-decimals=6 footgun).

3. **The policy engine intercepts *before* signing.** `policy.check()` returns
   a `Decision`; when it rejects, **the wallet is not unlocked, no key is
   touched.** Every transfer path (`wafa.cmd_send`) must call `policy.check()`
   *before* `wallet.unlock()`. This ordering is a security red line — do not
   invert it.

## Security Red Lines (mandatory when touching wallet.py / policy.py / wafa.py)

1. **Private keys are never printed, logged, or passed as command-line
   arguments.** The `import` subcommand reads the key from stdin (`getpass`);
   the positional `private_key` argument was removed, and
   `test_cli.py::test_import_no_positional_key` guards this (reverting to a
   positional arg will turn the test red).

2. **Key material is held in `bytearray` and zeroed via `secure_zero()` after
   use.** `bytes` is immutable and cannot be zeroed, so `unlock` /
   `import_wallet` use `bytearray` throughout and zero it in a `finally`
   block. `secure_zero` is a real implementation (an earlier `_zero_bytes` was
   a no-op placeholder; it has been replaced).

3. **Keystore KDF is hardened**: scrypt N=2^18, r=8, p=1 (well above
   eth-account defaults). `test_wallet.py::test_keystore_kdf_hardened`
   guards N≥2^18.

4. **Password strength is enforced.** `check_password_strength` requires
   ≥10 chars, ≥3 character classes, and rejects a weak-password blocklist;
   `create` / `import` reject weak passwords.

5. **File permissions**: `~/.wafa/` and `keystores/` dirs are 700, keystore
   files are 600, and `audit.log` is re-chmodded to 600 after every append.

## Conventions

- **Keys / private keys / mnemonics**: never write these into print / repr /
  log / json.dump. The audit log (`store.append_audit`) only records addresses,
  amounts, purposes, tx hashes, and rejection reasons — **never put a key or
  password into `**detail`.**
- **Address case**: keystore filenames store the lowercase address while
  `Account.address` returns mixed-case checksum format; compare with `.lower()`
  in tests (see the historical bugfixes in `test_wallet.py` / `test_cli.py`).
- **Config changes are live.** `config.yaml` / `policy.yaml` are copied by
  `init` from `config/` templates to `~/.wafa/`; user edits take effect
  immediately, no restart needed.
- **Bilingual docs**: `SKILL.md` / `README.md` / `AGENTS.md` = English
  (primary); `*_zh.md` = Chinese. When changing one, keep the other in sync;
  each file has a language-switch link at the top.
- **Domain terms**: read `CONTEXT.md` glossary before renaming sensitive
  terminology.

## Known Gotchas

- **Sandbox TLS egress is blocked.** `git push` over HTTPS fails the TLS
  handshake; pushing instead uses the GitHub Git Data API
  (blobs → tree → commit → update ref) via a throwaway script
  (`scripts/_push_api.py`, not committed). `git fetch` is also blocked, so a
  commit pushed via the API will have a different SHA than the local `git
  commit` (same content); run `git reset --hard origin/main` to align once
  `git fetch` works again.
- **`getpass` blocks on non-TTY.** Piping a password into the CLI will hang;
  tests call module functions directly to bypass the CLI's getpass rather than
  `echo | wafa.py`.
- **Public RPC is unreachable.** The sandbox cannot connect to public endpoints
  like `eth.llamarpc.com` / `mainnet.base.org`; end-to-end broadcast tests
  must be added in a networked environment.