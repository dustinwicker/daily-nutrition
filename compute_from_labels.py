#!/usr/bin/env python3
"""
Compute daily nutrition from food_export.csv using label_nutrition.json (your photos).

Grams column = food weight unless unit is tbsp, eggs, scoop (1 scoop = 35 g Kirkland whey).

Usage:
  python compute_from_labels.py ../google_cloud/food_export.csv
  python compute_from_labels.py ../google_cloud/food_export.csv --json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from label_data import load_foods, match_food

KEYS = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sodium_mg")


def macro_stats(totals: dict) -> dict:
    """Atwater general factors: 4 / 9 / 4 kcal per g for protein, fat, carbohydrate."""
    p, f, c = totals["protein_g"], totals["fat_g"], totals["carbs_g"]
    k_p, k_f, k_c = p * 4, f * 9, c * 4
    k_macro = k_p + k_f + k_c
    total_kcal = totals["kcal"]
    out = {
        "kcal_from_protein": round(k_p, 1),
        "kcal_from_fat": round(k_f, 1),
        "kcal_from_carbs": round(k_c, 1),
        "kcal_from_macros_sum": round(k_macro, 1),
    }
    if k_macro > 0:
        out["split_protein_pct"] = round(100 * k_p / k_macro, 1)
        out["split_fat_pct"] = round(100 * k_f / k_macro, 1)
        out["split_carb_pct"] = round(100 * k_c / k_macro, 1)
    else:
        out["split_protein_pct"] = out["split_fat_pct"] = out["split_carb_pct"] = 0.0
    if total_kcal > 0:
        out["pct_of_total_kcal_protein"] = round(100 * k_p / total_kcal, 1)
        out["pct_of_total_kcal_fat"] = round(100 * k_f / total_kcal, 1)
        out["pct_of_total_kcal_carbs"] = round(100 * k_c / total_kcal, 1)
    else:
        out["pct_of_total_kcal_protein"] = out["pct_of_total_kcal_fat"] = out["pct_of_total_kcal_carbs"] = 0.0
    return out


def parse_rows(path: Path) -> tuple[str | None, list[tuple[str, float, str | None]]]:
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return None, []
    header = rows[0]
    log_date = None
    if len(header) >= 5 and header[3].strip().rstrip(":").lower() == "date":
        log_date = header[4].strip()
    out = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        food = row[0].strip()
        amt_raw = row[1].strip() if len(row) > 1 else "0"
        unit = row[2].strip() if len(row) > 2 and row[2].strip() else None
        try:
            amount = float(amt_raw.replace(",", ""))
        except ValueError:
            amount = 0.0
        out.append((food, amount, unit))
    return log_date, out


def to_scale_amount(
    food: str, amount: float, unit: str | None, spec: dict
) -> tuple[float, str]:
    """Return (scale_factor, description) where nutrition = per_serving * factor."""
    u = (unit or "").strip().lower()
    kind = spec["kind"]

    if kind == "per_tbsp":
        if u != "tbsp":
            return amount, f"assumed {amount:g} tbsp (log unit was {unit!r})"
        return amount, f"{amount:g} tbsp (label)"

    if kind == "per_egg":
        if u in ("egg", "eggs"):
            return amount, f"{amount:g} large eggs (label)"
        return amount, f"assumed {amount:g} eggs (log unit {unit!r})"

    if kind == "per_ml":
        serving = float(spec["serving_ml"])
        if not u or u in ("g", "gram", "grams"):
            ml = amount
        elif u == "ml":
            ml = amount
        else:
            ml = amount
        return ml / serving, f"{ml:g} mL (≈g) ÷ {serving:g} mL/serving"

    if kind == "per_grams":
        serving_g = float(spec["serving_g"])
        if u in ("scoop", "scoops"):
            grams = amount * serving_g
            return grams / serving_g, f"{amount:g} scoop × {serving_g:g} g"
        grams = amount
        if u and u not in ("g", "gram", "grams"):
            return grams / serving_g, f"{grams:g} (treated as g; unit was {unit!r}) ÷ {serving_g:g} g/serving"
        return grams / serving_g, f"{grams:g} g ÷ {serving_g:g} g/serving"

    return 0.0, "unknown kind"


def scale_nutrition(spec: dict, factor: float) -> dict[str, float]:
    kind = spec["kind"]
    if kind == "per_tbsp":
        base = spec["per_tbsp"]
    elif kind == "per_egg":
        base = spec["per_egg"]
    else:
        base = spec["per_serving"]
    return {k: round(float(base.get(k, 0) or 0) * factor, 2) for k in KEYS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    foods_spec = load_foods()
    log_date, rows = parse_rows(args.csv_path)
    if not rows:
        sys.exit("No rows in CSV.")

    lines = []
    totals = {k: 0.0 for k in KEYS}
    missing = []

    for food, amount, unit in rows:
        spec = match_food(food, foods_spec)
        if not spec:
            missing.append(food)
            lines.append(
                {
                    "food": food,
                    "amount": amount,
                    "unit": unit or "",
                    "error": "no label match in label_nutrition.json",
                }
            )
            continue
        factor, note = to_scale_amount(food, amount, unit, spec)
        nut = scale_nutrition(spec, factor)
        for k in KEYS:
            totals[k] += nut[k]
        lines.append(
            {
                "food": food,
                "amount": amount,
                "unit": unit or "",
                "scale_note": note,
                "factor": round(factor, 4),
                "photo": spec.get("photo"),
                **nut,
            }
        )

    for k in totals:
        totals[k] = round(totals[k], 2)

    macros = macro_stats(totals)
    out = {
        "date": log_date,
        "lines": lines,
        "totals": totals,
        "macros": macros,
        "unmatched": missing,
    }

    if missing:
        print("Unmatched rows (add to label_nutrition.json):", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print(file=sys.stderr)

    if args.json:
        print(json.dumps(out, indent=2))
        return

    if log_date:
        print(f"Log date: {log_date}\n")
    print(f"{'Food':<52} {'Factor':>8}  {'kcal':>7} {'prot':>6} {'fat':>6} {'carb':>6} {'fib':>5} {'Na+':>7}")
    print("-" * 110)
    for row in lines:
        if "error" in row:
            print(f"{row['food'][:52]:<52} {'—':>8}  {'—':>7} {'—':>6} {'—':>6} {'—':>6} {'—':>5} {'—':>7}")
            continue
        print(
            f"{row['food'][:52]:<52} {row['factor']:8.3f}  {row['kcal']:7.0f} "
            f"{row['protein_g']:6.1f} {row['fat_g']:6.1f} {row['carbs_g']:6.1f} "
            f"{row['fiber_g']:5.1f} {row['sodium_mg']:7.0f}"
        )
    print("-" * 110)
    t = totals
    print(
        f"{'TOTAL':<52} {'':>8}  {t['kcal']:7.0f} {t['protein_g']:6.1f} "
        f"{t['fat_g']:6.1f} {t['carbs_g']:6.1f} {t['fiber_g']:5.1f} {t['sodium_mg']:7.0f}"
    )
    print()
    print(
        "Macro % of total kcal (4 kcal/g protein & carbs, 9 kcal/g fat):  "
        f"protein {macros['pct_of_total_kcal_protein']:.1f}%  ·  "
        f"fat {macros['pct_of_total_kcal_fat']:.1f}%  ·  "
        f"carbs {macros['pct_of_total_kcal_carbs']:.1f}%"
        "  — the three can add to more than 100% when label kcal ≠ 4P+9F+4C."
    )
    print(
        f"Macro split (only P/F/C kcal; always totals 100%):  "
        f"protein {macros['split_protein_pct']:.1f}%  ·  "
        f"fat {macros['split_fat_pct']:.1f}%  ·  "
        f"carbs {macros['split_carb_pct']:.1f}%"
    )
    print(
        f"(Implied kcal from macros: {macros['kcal_from_macros_sum']:.0f}; summed food kcal: {t['kcal']:.0f}.)"
    )


if __name__ == "__main__":
    main()
