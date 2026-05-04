#!/bin/bash
# Cron wrapper for ai_news_agent daily digest.
# Logs stdout/stderr to logs/agent.log and logs/agent.error.log with timestamps.
set -o pipefail

PROJECT_DIR="/Users/isaacboorer/codebase/langchain-projects/ai_news_agent"
PYTHON="${PROJECT_DIR}/venv/bin/python"
LOG_DIR="${PROJECT_DIR}/logs"
STAMP=$(date +"%Y-%m-%d %H:%M:%S")

mkdir -p "${LOG_DIR}"

cd "${PROJECT_DIR}" || { echo "[${STAMP}] FATAL: cannot cd to ${PROJECT_DIR}" >> "${LOG_DIR}/agent.error.log"; exit 1; }

echo "[${STAMP}] --- cron run start ---" >> "${LOG_DIR}/agent.log"
"${PYTHON}" main.py >> "${LOG_DIR}/agent.log" 2>> "${LOG_DIR}/agent.error.log"
EXIT=$?
echo "[$(date +"%Y-%m-%d %H:%M:%S")] --- cron run end (exit=${EXIT}) ---" >> "${LOG_DIR}/agent.log"
exit ${EXIT}
