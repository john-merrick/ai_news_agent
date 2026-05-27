#!/bin/bash
# Weekly canned-dataset eval — regression check against the hand-crafted
# DATASET_ITEMS in eval/run_eval.py. Daily inline eval (in main.py) tracks
# per-run quality; this one catches summariser regressions over time.

set -o pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_DIR}/venv/bin/python"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_LOG="${LOG_DIR}/weekly-eval.log"
ERROR_LOG="${LOG_DIR}/weekly-eval.error.log"
SECRETS_FILE="${PROJECT_DIR}/.env.secrets"
STAMP=$(date +"%Y-%m-%d %H:%M:%S")

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}" || exit 1

if [ ! -r "${SECRETS_FILE}" ]; then
    echo "[${STAMP}] FATAL: ${SECRETS_FILE} missing." >> "${ERROR_LOG}"
    exit 1
fi
set -a
# shellcheck disable=SC1090
. "${SECRETS_FILE}"
set +a

echo "[${STAMP}] --- weekly eval start ---" >> "${RUN_LOG}"
"${PYTHON}" -m eval.run_eval >> "${RUN_LOG}" 2>> "${ERROR_LOG}"
EXIT=$?
echo "[$(date +"%Y-%m-%d %H:%M:%S")] --- weekly eval end (exit=${EXIT}) ---" >> "${RUN_LOG}"
exit ${EXIT}
