#!/bin/bash
# Cron wrapper for ai_news_agent daily digest.
# - Self-locates so the path survives any project move.
# - Resolves Telegram secrets via 1Password CLI (`op read`) so the .env keeps
#   only `op://` references, never plaintext tokens.
# - On non-zero exit, posts an alert to the same Telegram chat with the
#   tail of the error log so silent failures get noticed.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_DIR}/venv/bin/python"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_LOG="${LOG_DIR}/agent.log"
ERROR_LOG="${LOG_DIR}/agent.error.log"
STAMP=$(date +"%Y-%m-%d %H:%M:%S")

mkdir -p "${LOG_DIR}"

cd "${PROJECT_DIR}" || {
    echo "[${STAMP}] FATAL: cannot cd to ${PROJECT_DIR}" >> "${ERROR_LOG}"
    exit 1
}

# Resolve Telegram secrets from 1Password (cron must be able to reach `op`;
# on macOS this requires the desktop app integration to be enabled and the
# user signed in, OR an OP_SERVICE_ACCOUNT_TOKEN exported in the environment).
if ! command -v op >/dev/null 2>&1; then
    echo "[${STAMP}] FATAL: 1Password CLI (op) not found on PATH" >> "${ERROR_LOG}"
    exit 1
fi

TELEGRAM_BOT_TOKEN=$(op read "op://Dev-Secrets/telegram-news-bot/credential" 2>>"${ERROR_LOG}")
TELEGRAM_CHAT_ID=$(op read "op://Dev-Secrets/telegram-news-bot/username" 2>>"${ERROR_LOG}")
if [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ]; then
    echo "[${STAMP}] FATAL: failed to resolve telegram secrets via 1Password (op auth?)" >> "${ERROR_LOG}"
    exit 1
fi
export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID

echo "[${STAMP}] --- cron run start ---" >> "${RUN_LOG}"
"${PYTHON}" main.py >> "${RUN_LOG}" 2>> "${ERROR_LOG}"
EXIT=$?
END_STAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "[${END_STAMP}] --- cron run end (exit=${EXIT}) ---" >> "${RUN_LOG}"

if [ ${EXIT} -ne 0 ]; then
    ERR_TAIL=$(tail -c 1200 "${ERROR_LOG}" 2>/dev/null | tr -d '\000')
    ALERT="ai_news_agent failed at ${END_STAMP} (exit=${EXIT}). Last errors:

${ERR_TAIL}"
    curl -sS -m 10 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${ALERT}" \
        --data-urlencode "disable_web_page_preview=true" \
        >/dev/null 2>>"${ERROR_LOG}"
fi

exit ${EXIT}
