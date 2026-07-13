# Wafa — Domain Glossary

> Shared vocabulary for the Wafa codebase. Use these terms in code, docs, and
> conversation. When a term is sharpened or a deepened module is named, update
> this file.

## Core concepts

**Wallet** — an EVM account the agent owns. Stored as an encrypted V3 keystore
(scrypt/AES), password-protected. The `wallet` module handles create / import /
unlock. A wallet is identified by its checksum address.

**Keystore** — the encrypted file holding a wallet's private key. Lives under
`~/.wafa/keystores/`. Never contains the raw key; unlocked only in-memory, only
for the duration of a signed operation.

**Chain** — an EVM-compatible network (Ethereum / Base / Polygon + testnets).
Configured per-chain with an RPC endpoint, chain id, explorer URL, native symbol.
The `chain` module connects, queries balances, estimates fees, and broadcasts
signed transactions.

**Transfer** — a send of native coin or ERC-20 token to a recipient address.
Policy-checked before signing; audit-logged after broadcast.

## Policy engine

**Policy** — the agent's spending guardrails, declared in `policy.yaml`.
Checked before every signing. The `policy` module owns both the decision logic
and the runtime counter state (daily cumulative spend, rate-limit window).

**Decision** — the result of `policy.check()`. A pure value (dataclass): whether
the transfer is `allowed`, a human-readable `reason`, optional `detail`. Caller
branches on `decision.allowed`; rejected transfers never unlock the wallet.

**Policy state** — the runtime counters the policy engine owns and persists in
`state.json` via the store's generic KV:

- **daily cumulative spend** — total sent today per kind (native / token).
  Rolls over implicitly by date key; old entries are not pruned (harmless
  accumulation).
- **rate-limit window** — timestamps of recent successful sends, kept for the
  last hour. Only successful sends are counted.

**record_outcome** — the single public write method on the policy module.
Called once after a successful transfer; updates daily cumulative spend and the
rate-limit timestamp together. There is no public `record_spend` or
`record_tx_timestamp` — a caller cannot update one counter without the other.
Failed or rejected transfers do not call `record_outcome` (they don't count
toward rate limits).

**check** — the pure-read decision entry point. Reads policy config and policy
state, returns a `Decision`. Never writes state.

## Storage layer

**Store** — the foundation module: path management, config loading, generic KV
state, and the audit log. Does **not** know what "daily" or "tx_timestamps"
mean — it provides `load_state` / `save_state` (atomic write via `os.replace`)
and policy reads/writes its own counters through that seam.

**Policy state** (above) is the sole consumer of the generic KV today; the
shape of `state.json` is policy's concern, not store's.

**Audit log** — append-only JSON Lines record of every action (sends, rejections,
unlock failures, creates). File permissions 600. The store owns the write
format; the content is supplied by callers.

## Not yet named / future

- **Receipt** — the post-broadcast confirmation of a transfer's on-chain status.
  Not yet a first-class concept (the current send path fires-and-forgets; see
  candidate #1 in the architecture review). When deepened, the `chain` module
  will return a `Receipt` and `record_outcome` will be called with its result.
- **Token resolution** — resolving a `--token` reference (alias or address) to a
  `{address, decimals, symbol}` triple. Currently split across `store` and
  `chain` (candidate #3); a dedicated `token` module is the planned deepening.
