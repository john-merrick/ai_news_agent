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

# --- 1Password Service Account (preferred for unattended use) -----------
# When set, `op` skips the desktop-app integration and talks to 1Password's
# API directly. Token file must be readable only by the user (chmod 600).
if [ -r "${SERVICE_ACCOUNT_TOKEN_FILE}" ]; then
    OP_SERVICE_ACCOUNT_TOKEN="$(< "${SERVICE_ACCOUNT_TOKEN_FILE}")"
    export OP_SERVICE_ACCOUNT_TOKEN
fi

if ! command -v op >/dev/null 2>&1; then
    echo "[${STAMP}] FATAL: 1Password CLI (op) not found on PATH" >> "${ERROR_LOG}"
    exit 1
fi

# --- op_read_timeout: bounded `op read`, returns stdout on success ------
# macOS has no `timeout(1)` by default. Background + watchdog + wait.
op_read_timeout() {
    local ref="$1"
    local out_file
    out_file=$(mktemp)
    op read "$ref" >"${out_file}" 2>>"${ERROR_LOG}" &
    local op_pid=$!
    ( sleep "${OP_TIMEOUT_SECS}" && kill -9 "${op_pid}" 2>/dev/null ) &
    local watchdog_pid=$!
    wait "${op_pid}" 2>/dev/null
    local rc=$?
    kill -9 "${watchdog_pid}" 2>/dev/null
    wait "${watchdog_pid}" 2>/dev/null
    if [ "${rc}" -ne 0 ]; then
        rm -f "${out_file}"
        return 1
    fi
    cat "${out_file}"
    rm -f "${out_file}"
    return 0
}

TELEGRAM_BOT_TOKEN=$(op_read_timeout "op://Dev-Secrets/telegram-news-bot/credential")
TELEGRAM_CHAT_ID=$(op_read_timeout "op://Dev-Secrets/telegram-news-bot/username")
TAVILY_API_KEY=$(op_read_timeout "op://Dev-Secrets/Tavily API Key/credential")
if [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ] || [ -z "${TAVILY_API_KEY}" ]; then
    echo "[${STAMP}] FATAL: failed to resolve secrets via 1Password (timeout or auth?). Service account token present: $([ -r "${SERVICE_ACCOUNT_TOKEN_FILE}" ] && echo yes || echo no)" >> "${ERROR_LOG}"
    exit 1
fi
export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID TAVILY_API_KEY

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
