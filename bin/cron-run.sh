#!/bin/bash
# Cron wrapper for ai_news_agent daily digest.
# Self-locates (so the path survives any project move) and sends a Telegram
# alert if the run exits non-zero, so silent failures get noticed.
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

echo "[${STAMP}] --- cron run start ---" >> "${RUN_LOG}"
"${PYTHON}" main.py >> "${RUN_LOG}" 2>> "${ERROR_LOG}"
EXIT=$?
END_STAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "[${END_STAMP}] --- cron run end (exit=${EXIT}) ---" >> "${RUN_LOG}"

if [ ${EXIT} -ne 0 ] && [ -f "${PROJECT_DIR}/.env" ]; then
    BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "${PROJECT_DIR}/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' "${PROJECT_DIR}/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    if [ -n "${BOT_TOKEN}" ] && [ -n "${CHAT_ID}" ]; then
        ERR_TAIL=$(tail -c 1200 "${ERROR_LOG}" 2>/dev/null | tr -d '\000')
        ALERT="ai_news_agent failed at ${END_STAMP} (exit=${EXIT}). Last errors:

${ERR_TAIL}"
        curl -sS -m 10 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${CHAT_ID}" \
            --data-urlencode "text=${ALERT}" \
            --data-urlencode "disable_web_page_preview=true" \
            >/dev/null 2>>"${ERROR_LOG}"
    fi
fi

exit ${EXIT}
