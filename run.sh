#!/usr/bin/env bash
# Refresh the Google Sheet export and compute nutrition for all days (or one date).
#
# Usage:
#   ./run.sh              # all days
#   ./run.sh 3/24/2026    # single day

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
EXPORT="$DIR/../google_cloud/food_export.csv"

echo "Refreshing food log from Google Sheets…"
python3 "$DIR/../google_cloud/read_food_sheet.py" > "$EXPORT"
echo "Wrote $(wc -l < "$EXPORT" | tr -d ' ') lines to food_export.csv"
echo

if [ -n "${1:-}" ]; then
  python3 "$DIR/compute_from_labels.py" "$EXPORT" --date "$1"
else
  python3 "$DIR/compute_from_labels.py" "$EXPORT"
fi
