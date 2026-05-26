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
#   4. Operational alerts (success stats + errors) go to a dedicated
#      sys-ops Telegram bot, keeping the user-facing digest channel clean.

set -o pipefail

# cron runs with a stripped PATH; Homebrew binaries (op, docker, curl, jq) aren't
# found by default. Prepend Apple-Silicon + Intel Homebrew dirs.
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_DIR}/venv/bin/python"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_LOG="${LOG_DIR}/agent.log"
ERROR_LOG="${LOG_DIR}/agent.error.log"
STATUS_FILE="${LOG_DIR}/last-run.json"
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

# --- Sys-ops alert routing ----------------------------------------------
# Bot tokens look like "<digits>:<base64ish>" — guard against an unresolved
# op:// reference accidentally landing as a literal string and 401ing.
SYSOPS_OK=1
if [ -z "${SYSOPS_TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${SYSOPS_TELEGRAM_CHAT_ID:-}" ] \
   || [[ "${SYSOPS_TELEGRAM_BOT_TOKEN}" == op://* ]] || [[ "${SYSOPS_TELEGRAM_CHAT_ID}" == op://* ]] \
   || [[ ! "${SYSOPS_TELEGRAM_BOT_TOKEN}" == *:* ]]; then
    SYSOPS_OK=0
    echo "[${STAMP}] WARN: SYSOPS_* credentials missing/unresolved; falling back to user bot for alerts" >> "${RUN_LOG}"
fi

# notify_sysops <text>  — best-effort; never fails the run.
notify_sysops() {
    local text="$1"
    local token chat
    if [ "${SYSOPS_OK}" = "1" ]; then
        token="${SYSOPS_TELEGRAM_BOT_TOKEN}"
        chat="${SYSOPS_TELEGRAM_CHAT_ID}"
    else
        token="${TELEGRAM_BOT_TOKEN}"
        chat="${TELEGRAM_CHAT_ID}"
    fi
    # Telegram limit is 4096; truncate generously.
    text="${text:0:3500}"
    curl -sS -m 10 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${chat}" \
        --data-urlencode "text=${text}" \
        --data-urlencode "disable_web_page_preview=true" \
        >/dev/null 2>>"${ERROR_LOG}"
}

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
        END_STAMP=$(date +"%Y-%m-%d %H:%M:%S")
        ERR_TAIL=$(tail -c 1200 "${ERROR_LOG}" 2>/dev/null | tr -d '\000')
        notify_sysops "ai_news_agent: LiteLLM down and could not be restarted at ${END_STAMP}.

${ERR_TAIL}"
        exit 1
    fi
fi

# --- Run the agent ------------------------------------------------------
echo "[${STAMP}] --- cron run start ---" >> "${RUN_LOG}"
"${PYTHON}" main.py >> "${RUN_LOG}" 2>> "${ERROR_LOG}"
EXIT=$?
END_STAMP=$(date +"%Y-%m-%d %H:%M:%S")
echo "[${END_STAMP}] --- cron run end (exit=${EXIT}) ---" >> "${RUN_LOG}"

# --- Alert ---------------------------------------------------------------
if [ ${EXIT} -eq 0 ]; then
    # SUCCESS: read structured status file written by main.py.
    if [ -r "${STATUS_FILE}" ] && command -v jq >/dev/null 2>&1; then
        msg=$(jq -r '
            "ai_news_agent: OK at \(.ended_at) (\(.duration_sec)s)\n" +
            "RSS:\(.counts.rss)  Reddit:\(.counts.reddit)  Tavily:\(.counts.tavily)  Twitter:\(.counts.twitter)  ArXiv:\(.counts.arxiv)\n" +
            "Total:\(.total_collected) → dedup:\(.after_dedupe) → enriched:\(.enriched_succeeded)/\(.enriched_attempted)\n" +
            "Top:\n" +
            ((.top_headlines // []) | map("  • [\(.source)] \(.title)") | join("\n")) +
            (if .eval_score != null then "\nEval: \(.eval_score) — \(.eval_reasoning // "")" else "" end)
        ' "${STATUS_FILE}" 2>/dev/null)
        if [ -n "${msg}" ]; then
            notify_sysops "${msg}"
        else
            notify_sysops "ai_news_agent: OK at ${END_STAMP} (status file unreadable)"
        fi
    else
        notify_sysops "ai_news_agent: OK at ${END_STAMP} (no status file or jq missing)"
    fi
else
    # FAILURE: include error tail.
    ERR_TAIL=$(tail -c 1200 "${ERROR_LOG}" 2>/dev/null | tr -d '\000')
    notify_sysops "ai_news_agent failed at ${END_STAMP} (exit=${EXIT}). Last errors:

${ERR_TAIL}"
fi

exit ${EXIT}
