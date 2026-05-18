#!/bin/bash
# Cron wrapper for ai_news_agent daily digest.
#
# Defends against the failure modes we've actually seen since 2026-05-09:
#   1. macOS biometric auth blocks `op read` from cron → secrets are
#      pre-resolved into .env.secrets by `bin/refresh-secrets.sh` (run
#      interactively). Cron itself never calls `op`.
#   2. Yesterday's hung wrapper colliding with today's → single-instance
#      lock with stale-PID detection.
#   3. LiteLLM container dies silently → preflight ping, attempt restart,
#      fail fast with alert if still down.
#   4. On non-zero exit, post a Telegram alert with the tail of the error log.

set -o pipefail

# cron runs with a stripped PATH; Homebrew binaries (op, docker, curl) aren't
# found by default. Prepend Apple-Silicon + Intel Homebrew dirs.
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_DIR}/venv/bin/python"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_LOG="${LOG_DIR}/agent.log"
ERROR_LOG="${LOG_DIR}/agent.error.log"
STAMP=$(date +"%Y-%m-%d %H:%M:%S")

LITELLM_URL="http://127.0.0.1:4000/health/liveness"
LITELLM_COMPOSE_DIR="/Users/isaacboorer/mac-codebase/dev-ops/observability"
LITELLM_SERVICE="litellm"
LITELLM_RESTART_WAIT_SECS=60
SECRETS_FILE="${PROJECT_DIR}/.env.secrets"
LOCK_DIR="/tmp/ai_news_agent.lock"

mkdir -p "${LOG_DIR}"

cd "${PROJECT_DIR}" || {
    echo "[${STAMP}] FATAL: cannot cd to ${PROJECT_DIR}" >> "${ERROR_LOG}"
    exit 1
}

# --- Single-instance lock (mkdir is atomic on every POSIX FS) -----------
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    OTHER_PID=$(cat "${LOCK_DIR}/pid" 2>/dev/null || echo "")
    if [ -n "${OTHER_PID}" ] && kill -0 "${OTHER_PID}" 2>/dev/null; then
        echo "[${STAMP}] another cron-run.sh is alive (pid=${OTHER_PID}); skipping this run" >> "${RUN_LOG}"
        exit 0
    fi
    echo "[${STAMP}] removing stale lock (pid=${OTHER_PID:-unknown})" >> "${RUN_LOG}"
    rm -rf "${LOCK_DIR}"
    mkdir "${LOCK_DIR}" || {
        echo "[${STAMP}] FATAL: cannot create lock dir" >> "${ERROR_LOG}"
        exit 1
    }
fi
echo $$ > "${LOCK_DIR}/pid"
trap 'rm -rf "${LOCK_DIR}"' EXIT

# --- Load pre-resolved secrets ------------------------------------------
# .env.secrets is produced by `bin/refresh-secrets.sh` (which runs `op
# inject` interactively). Cron never calls `op` itself, so the biometric
# prompt that was hanging us at 06:00 every morning is out of the picture.
if [ ! -r "${SECRETS_FILE}" ]; then
    echo "[${STAMP}] FATAL: ${SECRETS_FILE} missing. Run bin/refresh-secrets.sh once interactively." >> "${ERROR_LOG}"
    exit 1
fi
set -a
# shellcheck disable=SC1090
. "${SECRETS_FILE}"
set +a
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ] || [ -z "${TAVILY_API_KEY:-}" ]; then
    echo "[${STAMP}] FATAL: ${SECRETS_FILE} missing one of TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TAVILY_API_KEY. Re-run bin/refresh-secrets.sh." >> "${ERROR_LOG}"
    exit 1
fi

# --- LiteLLM preflight + auto-restart -----------------------------------
check_litellm() {
    curl -fsS -m 5 "${LITELLM_URL}" >/dev/null 2>&1
}

if ! check_litellm; then
    echo "[${STAMP}] LiteLLM not responding at ${LITELLM_URL}, attempting restart..." >> "${RUN_LOG}"
    (cd "${LITELLM_COMPOSE_DIR}" && docker compose up -d "${LITELLM_SERVICE}") >> "${RUN_LOG}" 2>> "${ERROR_LOG}"
    waited=0
    while [ "${waited}" -lt "${LITELLM_RESTART_WAIT_SECS}" ]; do
        sleep 2
        waited=$((waited + 2))
        if check_litellm; then
            echo "[${STAMP}] LiteLLM healthy after ${waited}s" >> "${RUN_LOG}"
            break
        fi
    done
    if ! check_litellm; then
        echo "[${STAMP}] FATAL: LiteLLM unreachable after ${LITELLM_RESTART_WAIT_SECS}s restart attempt" >> "${ERROR_LOG}"
        # fall through to the alert path
        EXIT=1
        END_STAMP=$(date +"%Y-%m-%d %H:%M:%S")
        ERR_TAIL=$(tail -c 1200 "${ERROR_LOG}" 2>/dev/null | tr -d '\000')
        ALERT="ai_news_agent: LiteLLM down and could not be restarted at ${END_STAMP}.

${ERR_TAIL}"
        curl -sS -m 10 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${ALERT}" \
            --data-urlencode "disable_web_page_preview=true" \
            >/dev/null 2>>"${ERROR_LOG}"
        exit ${EXIT}
    fi
fi

# --- Run the agent ------------------------------------------------------
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
