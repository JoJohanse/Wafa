---
name: wafa
description: Manage an EVM crypto wallet (Ethereum/Base/Polygon and other EVM chains). Use it to create wallets, import private keys, check balances, and send ETH or ERC-20 transfers (e.g. USDC). Use this skill whenever the user mentions wallets, crypto, cryptocurrency, Ethereum, EVM, transfer, send ETH, check balance, USDC, token transfer, digital assets, or wants the AI agent to own and autonomously use a blockchain wallet. A built-in policy engine enforces per-transaction, daily-cumulative, rate-limit, and whitelist guardrails — every transfer is checked against policy before signing, and out-of-policy transfers are rejected automatically.
---

# Wafa — AI Agent Crypto Wallet

> 🌐 Language / 语言: **English** (this file) · [中文](SKILL_zh.md)
>
> **Language switch rule**: if the current conversation is primarily in Chinese, read `SKILL.md` instead before executing; if primarily English, use this file. Both documents are content-identical, differing only in language.

Wafa (Wallet for Agent) gives an AI agent its own EVM crypto wallet to manage autonomously.
Once installed, the agent can respond to natural-language requests like "create a wallet", "check my balance", or "send 1 USDC to 0x…".

## ⚠️ Security Red Lines (must obey)

These rules protect user funds and must never be violated:

1. **Never print, log, or pass as a command-line argument a private key or mnemonic.**
   The wallet stores keys in an encrypted keystore; the password is entered interactively via getpass, never on the command line.
2. **Every transfer must pass the policy check before signing.** If it exceeds a limit, targets a non-whitelisted address, or lacks a required reason, reject it and do not unlock the wallet.
3. **Confirm with the user before moving real funds.** For mainnet value, prefer a testnet first or read the transaction details back to the user before executing.
4. **Do not edit policy.yaml to bypass limits.** If the user asks to loosen limits, explain the risk and let the user edit the config file themselves.
5. All actions are audit-logged to `~/.wafa/audit.log`.

Advanced security (custodial signers, multisig, account abstraction) is in `references/security.md` — read on demand.

## Prerequisites

- Python 3.10+
- Dependencies installed: `pip install -r scripts/requirements.txt`
- First run requires `python scripts/wafa.py init` to set up the directory and config

Run all commands from the skill root: `python scripts/wafa.py <subcommand>`.

## Command Reference

| Intent | Command |
|--------|---------|
| Initialize | `python scripts/wafa.py init` |
| Create wallet | `python scripts/wafa.py create --label <name>` |
| Import private key | `python scripts/wafa.py import <0x-key> --label <name>` |
| List wallets | `python scripts/wafa.py list` |
| Native balance | `python scripts/wafa.py balance` |
| Token balance | `python scripts/wafa.py balance --token usdc` |
| Send native | `python scripts/wafa.py send <to> <amount> --reason <purpose>` |
| Send token | `python scripts/wafa.py send <to> <amount> --token usdc --reason <purpose>` |
| Show policy | `python scripts/wafa.py policy show` |
| Audit log | `python scripts/wafa.py history --limit 20` |

Optional flags: `--chain base-sepolia` (switch chain), `--address 0x...` (pick wallet).
Full flags: `python scripts/wafa.py <command> -h`.

## Typical Workflows

### Scenario 1: First-time setup

> User: "Create a wallet and check its balance"

```
1. python scripts/wafa.py init                 # set up (if not done)
2. python scripts/wafa.py create --label main  # prompts for password (with confirmation)
3. python scripts/wafa.py balance              # balance will be 0
```

Give the generated address to the user and tell them to fund it (exchange for mainnet, faucet for testnet).

### Scenario 2: Check USDC balance

> User: "How much USDC do I have?"

```
python scripts/wafa.py balance --token usdc
```

Prints a human-readable amount. If it says "unknown token", the USDC contract must be added under `tokens.<chain>` in `~/.wafa/config.yaml`.

### Scenario 3: Send a transfer

> User: "Send 5 USDC to 0xRecipient to buy data"

```
python scripts/wafa.py send 0xRecipient 5 --token usdc --reason "data purchase"
```

Flow: policy check → prompt for password to unlock → sign & broadcast → print tx hash and explorer link.
If the policy rejects it (over limit / not whitelisted), explain the reason to the user — do not try to work around it.

## Policy Engine

Policy lives in `~/.wafa/policy.yaml` (copied from the template by `init`). Every transfer is checked before signing:

- **Per-transaction limit**: `max_per_tx_native` / `max_per_tx_token`
- **Daily-cumulative limit**: `daily_limit_native` / `daily_limit_token` (auto-resets daily)
- **Rate limit**: `max_tx_per_minute` / `max_tx_per_hour` (sliding window)
- **Recipient whitelist**: `whitelist` (off by default)
- **Purpose constraint**: `require_reason` / `allowed_purposes`
- **Kill switch**: `kill_switch: true` rejects all transfers immediately

`python scripts/wafa.py policy show` prints the current policy and today's spend.
The user can edit `~/.wafa/policy.yaml` at any time — no code changes needed.

## Configuration

`~/.wafa/config.yaml` (copied from the template by `init`) defines:

- **chains**: supported chains with RPC endpoints, chainId, explorer (defaults include ethereum/base/polygon and testnets)
- **tokens**: common ERC-20 addresses and decimals so `--token usdc` just works
- **tx_defaults**: gas-estimation multiplier, timeout

Users can add/remove chains or swap in a private RPC (Alchemy/Infura, recommended for production).

## Troubleshooting

- **Cannot connect to RPC**: check `rpc_url` in config.yaml; public endpoints may rate-limit — use a private RPC for production.
- **Chain ID mismatch**: the RPC doesn't match the configured `chain_id`; fix one or the other.
- **Wrong password**: keystore password is wrong; if lost, funds cannot be recovered.
- **Insufficient balance**: check the balance first, leaving room for gas.
- **Policy rejected**: run `policy show` to see limits; the user decides whether to adjust policy.yaml.
- **Transaction not confirmed**: L2 usually seconds, L1 may take minutes — verify in the explorer.

## What it does NOT do

- Does not store or print private keys or mnemonics in plaintext
- Does not auto-loosen policy to bypass limits
- Does not handle non-EVM chains (Bitcoin/Solana — planned for later)
- Does not do account abstraction / custodial signing (see references/security.md for the upgrade path)
