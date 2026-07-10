# Wafa — AI Agent Crypto Wallet

> 🌐 Language / 语言: **English** (this file) · [中文](README_zh.md)

> Wallet for Agent: an EVM crypto wallet an AI agent can own and use autonomously, distributed as an Agent Skill.

Wafa gives your AI agent its own crypto wallet — create accounts, check balances, and send ETH or ERC-20 (e.g. USDC) transfers. A built-in policy engine (per-transaction / daily-cumulative / rate-limit / whitelist guardrails) checks every transfer before signing; out-of-policy transfers are rejected automatically.

## Features

- 🔑 **Create / import wallets** — standard encrypted keystore (V3, scrypt/AES), password-protected
- 💰 **Balance queries** — native coin (ETH) and ERC-20 tokens (USDC, etc.)
- 📤 **Safe transfers** — local signing + broadcast, private key never touches the RPC; EIP-1559 fee estimation
- 🛡️ **Policy engine** — per-tx limit, daily cumulative, rate limit, recipient whitelist, purpose constraint, kill switch
- 📝 **Full audit trail** — every action (including rejections) is logged
- 🔗 **Multi-chain EVM** — Ethereum / Base / Polygon and testnets, configured out of the box
- 🧩 **Agent Skill format** — downloadable, install-and-use, responds to natural-language commands

## Quick Start

### 1. Install

```bash
# Clone or download this repo
git clone <repo-url> wafa
cd wafa

# Install dependencies
pip install -r scripts/requirements.txt
```

### 2. Install as an Agent Skill

Copy the whole repo into a skill discovery directory:

```bash
# User-level (available everywhere)
cp -r . ~/.agents/skills/wafa/

# Or project-level (this project only)
cp -r . .agents/skills/wafa/
```

See [Using as a Skill](#using-as-a-skill) below.

### 3. Initialize

```bash
python scripts/wafa.py init
```

Creates the `~/.wafa/` directory and config files (config.yaml, policy.yaml).

### 4. Create a wallet

```bash
python scripts/wafa.py create --label main
# Prompts for a password (with confirmation)
```

### 5. Check balance

```bash
python scripts/wafa.py balance              # native coin (ETH)
python scripts/wafa.py balance --token usdc # USDC
```

Fund the generated address first, then the balance will show up.

### 6. Send a transfer

```bash
python scripts/wafa.py send 0xRecipient 0.01 --reason "test transfer"
python scripts/wafa.py send 0xRecipient 5 --token usdc --reason "data purchase"
```

Flow: policy check → enter password to unlock → sign & broadcast → return tx hash.

## Using as a Skill

Once installed into a skill directory, an AI agent discovers the `wafa` skill automatically. You can drive it in natural language:

- "Create a wallet and check its balance"
- "How much USDC do I have?"
- "Send 5 USDC to 0xABC to buy data"

The agent invokes the corresponding CLI command. See `SKILL.en.md`.

> **Security note:** for mainnet real funds, rehearse on a testnet (Base Sepolia) first.

## Command Reference

| Command | What it does |
|---------|--------------|
| `init` | Initialize directory and config |
| `create --label <name>` | Create a new wallet |
| `import <key> --label <name>` | Import an existing private key |
| `list` | List local wallets |
| `balance [--token usdc]` | Check balance |
| `send <to> <amount> [--token usdc] --reason <purpose>` | Transfer |
| `policy show` | Show current policy and today's spend |
| `history --limit 20` | Show audit records |

Optional flags: `--chain base-sepolia` (switch chain), `--address 0x...` (pick wallet).

## Configuration

Two config files, copied from `config/` templates to `~/.wafa/` by `init`:

- **`config.yaml`** — chains and RPCs, token addresses, transaction defaults
- **`policy.yaml`** — payment policy (limits, whitelist, rate limits, etc.)

Edits take effect immediately — no restart needed.

## Project Structure

```
wafa/
├── SKILL.md                      # Skill instructions (Chinese) — read by the agent
├── SKILL.en.md                   # Skill instructions (English)
├── README.md                     # This file's Chinese counterpart
├── README.en.md                  # This file
├── scripts/
│   ├── wafa.py                   # CLI entry point
│   ├── wallet.py                 # Wallet core (keystore encrypt/decrypt)
│   ├── chain.py                  # Chain interaction (balance/transfer)
│   ├── policy.py                 # Policy engine
│   ├── store.py                  # Storage layer (paths/state/audit)
│   └── requirements.txt          # Python dependencies
├── config/
│   ├── config.example.yaml       # Config template
│   └── policy.example.yaml       # Policy template
└── references/
    └── security.md               # Advanced security (read on demand)
```

## Security

- Private keys are stored in an encrypted V3 keystore; the password is entered interactively, never printed/logged/passed on the command line
- Every transfer is policy-checked before signing; out-of-policy transfers are rejected without unlocking
- All actions are written to the audit log
- For real funds, use a trusted private RPC

Advanced security (custodial signers, account abstraction, multisig) is in `references/security.md`.

## Tech Stack

- [eth-account](https://eth-account.readthedocs.io/) — keys & signing
- [web3.py](https://web3py.readthedocs.io/) — chain interaction
- [PyYAML](https://pyyaml.org/) — config parsing

## Roadmap (future extensions)

- [ ] Non-EVM chains (Bitcoin / Solana)
- [ ] Custodial signers (Turnkey / Privy / Coinbase AgentKit)
- [ ] Account abstraction (ERC-4337 session keys)
- [ ] HTTP 402 / x402 payment negotiation
- [ ] Multi-account portfolio management, NFT queries
