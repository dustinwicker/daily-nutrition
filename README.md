# Daily nutrition (Costco / label-based)

Read a **Daily Food** log from Google Sheets, match rows to **nutrition label** data you transcribed into `label_nutrition.json`, and print totals plus macro percentages.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Export your sheet (same service account as other `google_cloud` scripts):

```bash
export GOOGLE_SERVICE_ACCOUNT='/path/to/service-account.json'
export GOOGLE_SHEET_FOOD_ID='your-spreadsheet-id'
export GOOGLE_SHEET_FOOD_RANGE='Log!A1:Z5000'   # optional; quote for zsh

cd ../google_cloud
python3 read_food_sheet.py > ../daily-nutrition/food_export.csv
```

Compute from labels:

```bash
cd ../daily-nutrition
python3 compute_from_labels.py food_export.csv
```

Optional: copy label PNGs into `product-labels/by-food/` named from each **Food** cell:

```bash
python3 sync_label_files_from_log.py food_export.csv
```

`read_food_sheet.py` in this folder delegates to `../google_cloud/read_food_sheet.py`.

## Files

| File | Purpose |
|------|---------|
| `label_nutrition.json` | Per-serving macros + `match` keywords → label photo path |
| `compute_from_labels.py` | CSV → scaled totals + macro % of kcal |
| `label_data.py` | Shared matcher |
| `sync_label_files_from_log.py` | Sync copies of labels keyed by sheet food name |
| `enrich_log.py` | Optional Open Food Facts + USDA enrichment |
| `product-labels/` | Your label photos (canonical names) |

Do not commit real `food_export.csv` if you prefer privacy; it is gitignored by default (regenerate after pull).
