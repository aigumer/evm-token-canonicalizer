#!/usr/bin/env bash
# Go-live steps for listing evm-canon as an A2MCP service on OKX.AI.
# Scriptable steps run; interactive steps (wallet OTP, listing publication)
# print instructions — keys stay in the TEE, so no secrets pass through here.
set -euo pipefail

echo "== 1/6 Install Onchain OS skills =="
npx skills add okx/onchainos-skills

cat <<'EOF'
== 2/6 Create the Agentic Wallet (interactive) ==
In your agent (Claude Code / Cursor / OpenClaw / Codex), say:
    "Log in to Agentic Wallet with email"
then enter your email + OTP. Private keys are generated inside the TEE and
are never exposed to the model.
EOF
read -rp "Press enter once the wallet exists... "

echo "== 3/6 Install the okx-ai-guide skill =="
npx skills add https://github.com/okx/onchainos-skills --skill okx-ai-guide

cat <<'EOF'
== 4/6 Register on-chain identity (interactive, via okx-ai-guide) ==
Ask your agent to register an ERC-8004 identity on X Layer with role `asp`,
then set the avatar + service metadata (name: evm-token-canonicalizer).
EOF
read -rp "Press enter once the ASP identity is registered... "

cat <<'EOF'
== 5/6 Wire the OKX Payment SDK (required for A2MCP) ==
Fill in the TODO(go-live) block in evm_canon/payment.py with live credentials:
    OKX_PAYMENT_API_KEY, OKX_AGENTIC_WALLET_ID
then set EVM_CANON_BILLING=live and verify a charge settles on a test call.
EOF
read -rp "Press enter once billing settles... "

cat <<'EOF'
== 6/6 Publish the listing ==
Listing promise (verbatim): "schema-validated, deterministic core, honest nulls."
Price: 2000 micro-units (0.002) per record (Tier 1). Fund the wallet for gas if needed
(X Layer is gas-free).
EOF
echo "Done."
