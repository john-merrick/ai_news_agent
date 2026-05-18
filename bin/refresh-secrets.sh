#!/bin/bash
# Resolve op:// references in .env.secrets.tmpl → .env.secrets (mode 600).
#
# Run this interactively at setup, and again after rotating any secret in
# 1Password. The wrapper (bin/cron-run.sh) sources the resolved file, so
# cron itself never has to call `op read` — that's what was hanging on
# biometric auth at 06:00 every morning.

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${PROJECT_DIR}/.env.secrets.tmpl"
OUTPUT="${PROJECT_DIR}/.env.secrets"

if [ ! -f "${TEMPLATE}" ]; then
    echo "FATAL: template not found at ${TEMPLATE}" >&2
    exit 1
fi

if ! command -v op >/dev/null 2>&1; then
    echo "FATAL: 1Password CLI (op) not found on PATH" >&2
    exit 1
fi

echo "Resolving secrets from 1Password (Touch ID prompt expected)..."
op inject -i "${TEMPLATE}" -o "${OUTPUT}" --force
chmod 600 "${OUTPUT}"
echo "✓ Wrote ${OUTPUT} (mode 600, $(wc -l < "${OUTPUT}" | tr -d ' ') lines)"
echo "  Cron now sources this file. Re-run this script after rotating any secret."
