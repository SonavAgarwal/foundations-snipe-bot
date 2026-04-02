#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-data/foundations_bot.db}"

if [[ -f "${DB_PATH}" ]]; then
  rm "${DB_PATH}"
  echo "Deleted ${DB_PATH}"
else
  echo "No database file found at ${DB_PATH}"
fi
