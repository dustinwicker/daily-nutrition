#!/usr/bin/env python3
"""
Copy label photos into product-labels/by-food/, one file per unique Food cell.

Filenames are a slug of the exact Google Sheet / CSV "Food" text (safe for macOS).

Requires a fresh export from Daily Food (Log tab):
  cd ../google_cloud && python3 read_food_sheet.py > food_export.csv

Usage:
  python sync_label_files_from_log.py ../google_cloud/food_export.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

from label_data import PACKAGE_ROOT, load_foods, match_food


def slugify(food: str) -> str:
    s = food.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s[:120] or "food") + ".png"


def unique_foods_from_csv(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    seen: set[str] = set()
    out: list[str] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        name = row[0].strip()
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy labels to by-food/<slug>.png per Food name.")
    ap.add_argument("csv_path", type=Path)
    args = ap.parse_args()

    foods_csv = unique_foods_from_csv(args.csv_path)
    if not foods_csv:
        sys.exit("No food rows in CSV.")

    out_dir = PACKAGE_ROOT / "product-labels" / "by-food"
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = load_foods()
    ok, bad = 0, 0
    for name in foods_csv:
        spec = match_food(name, specs)
        if not spec or not spec.get("photo"):
            print(f"[skip] No label mapping: {name}", file=sys.stderr)
            bad += 1
            continue
        src = PACKAGE_ROOT / spec["photo"]
        if not src.is_file():
            print(f"[skip] Missing file {src} for: {name}", file=sys.stderr)
            bad += 1
            continue
        dest = out_dir / slugify(name)
        shutil.copy2(src, dest)
        print(f"{name}\n  -> {dest.relative_to(PACKAGE_ROOT)}")
        ok += 1

    print(f"\nCopied {ok} label(s) to {out_dir.relative_to(PACKAGE_ROOT)}; skipped {bad}.", file=sys.stderr)


if __name__ == "__main__":
    main()
