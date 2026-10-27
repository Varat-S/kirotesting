#!/usr/bin/env bash
set -euo pipefail
M3_PYTHON="${M3_PYTHON:-python}"
PYTHONDONTWRITEBYTECODE=1 "$M3_PYTHON" -m pytest -q -p no:cacheprovider
