#!/usr/bin/env python3
"""
Compute daily nutrition from food_export.csv using label_nutrition.json (your photos).

Supports multi-day logs (each day starts with a header row containing Date:).
Grams column = food weight unless unit is tbsp, eggs, scoop.
Column D = "Cooked" or blank (blank = Uncooked, the default).

Usage:
  python compute_from_labels.py ../google_cloud/food_export.csv
  python compute_from_labels.py ../google_cloud/food_export.csv --json
  python compute_from_labels.py ../google_cloud/food_export.csv --date 3/24/2026
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
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


@dataclass
class FoodRow:
    food: str
    amount: float
    unit: str | None
    cooked: bool
    date: str


@dataclass
class DayLog:
    date: str
    rows: list[FoodRow] = field(default_factory=list)


def _is_header(row: list[str]) -> bool:
    return (
        len(row) >= 1
        and row[0].strip().lower() == "food"
        and any("date" in c.strip().rstrip(":").lower() for c in row)
    )


def _extract_date(row: list[str]) -> str | None:
    for i, cell in enumerate(row):
        if cell.strip().rstrip(":").lower() == "date":
            if i + 1 < len(row) and row[i + 1].strip():
                return row[i + 1].strip()
    return None


def parse_multiday(path: Path) -> list[DayLog]:
    text = path.read_text(encoding="utf-8-sig")
    all_rows = list(csv.reader(text.splitlines()))

    days: list[DayLog] = []
    current: DayLog | None = None

    for row in all_rows:
        if not row or not row[0].strip():
            continue

        if _is_header(row):
            date = _extract_date(row) or "unknown"
            current = DayLog(date=date)
            days.append(current)
            continue

        if current is None:
            continue

        food = row[0].strip()
        amt_raw = row[1].strip() if len(row) > 1 else "0"
        unit = row[2].strip() if len(row) > 2 and row[2].strip() else None
        cooked_raw = row[3].strip().lower() if len(row) > 3 else ""
        cooked = cooked_raw in ("cooked", "c", "yes", "true")

        try:
            amount = float(amt_raw.replace(",", ""))
        except ValueError:
            amount = 0.0

        current.rows.append(FoodRow(food=food, amount=amount, unit=unit, cooked=cooked, date=current.date))

    return days


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


def process_day(day: DayLog, foods_spec: list) -> tuple[list[dict], dict, list[str]]:
    lines = []
    totals = {k: 0.0 for k in KEYS}
    missing = []

    for fr in day.rows:
        spec = match_food(fr.food, foods_spec)
        if not spec:
            missing.append(fr.food)
            lines.append({
                "food": fr.food,
                "amount": fr.amount,
                "unit": fr.unit or "",
                "cooked": fr.cooked,
                "error": "no label match in label_nutrition.json",
            })
            continue
        factor, note = to_scale_amount(fr.food, fr.amount, fr.unit, spec)
        nut = scale_nutrition(spec, factor)
        for k in KEYS:
            totals[k] += nut[k]
        lines.append({
            "food": fr.food,
            "amount": fr.amount,
            "unit": fr.unit or "",
            "cooked": fr.cooked,
            "scale_note": note,
            "factor": round(factor, 4),
            "photo": spec.get("photo"),
            **nut,
        })

    for k in totals:
        totals[k] = round(totals[k], 2)

    return lines, totals, missing


def print_day(date: str, lines: list[dict], totals: dict, macros: dict, missing: list[str]) -> None:
    if missing:
        print("Unmatched rows (add to label_nutrition.json):", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print(file=sys.stderr)

    print(f"{'═' * 110}")
    print(f"  {date}")
    print(f"{'═' * 110}")
    cook_col = any(r.get("cooked") for r in lines)
    hdr = f"{'Food':<48} {'C/U':>3} {'Factor':>8}  {'kcal':>7} {'prot':>6} {'fat':>6} {'carb':>6} {'fib':>5} {'Na+':>7}"
    print(hdr)
    print("-" * 110)
    for row in lines:
        cu = "C" if row.get("cooked") else "U"
        if "error" in row:
            print(f"{row['food'][:48]:<48} {cu:>3} {'—':>8}  {'—':>7} {'—':>6} {'—':>6} {'—':>6} {'—':>5} {'—':>7}")
            continue
        print(
            f"{row['food'][:48]:<48} {cu:>3} {row['factor']:8.3f}  {row['kcal']:7.0f} "
            f"{row['protein_g']:6.1f} {row['fat_g']:6.1f} {row['carbs_g']:6.1f} "
            f"{row['fiber_g']:5.1f} {row['sodium_mg']:7.0f}"
        )
    print("-" * 110)
    t = totals
    print(
        f"{'TOTAL':<48} {'':>3} {'':>8}  {t['kcal']:7.0f} {t['protein_g']:6.1f} "
        f"{t['fat_g']:6.1f} {t['carbs_g']:6.1f} {t['fiber_g']:5.1f} {t['sodium_mg']:7.0f}"
    )
    print()
    print(
        "Macro % of total kcal (4 kcal/g protein & carbs, 9 kcal/g fat):  "
        f"protein {macros['pct_of_total_kcal_protein']:.1f}%  ·  "
        f"fat {macros['pct_of_total_kcal_fat']:.1f}%  ·  "
        f"carbs {macros['pct_of_total_kcal_carbs']:.1f}%"
        "  — can add to >100% when label kcal ≠ 4P+9F+4C."
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
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute daily nutrition from label data.")
    ap.add_argument("csv_path", type=Path, help="Path to food_export.csv")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text tables")
    ap.add_argument("--date", type=str, default=None, help="Show only this date (e.g. 3/24/2026)")
    args = ap.parse_args()

    foods_spec = load_foods()
    days = parse_multiday(args.csv_path)
    if not days:
        sys.exit("No day headers found in CSV.")

    if args.date:
        days = [d for d in days if d.date == args.date]
        if not days:
            sys.exit(f"No data found for date {args.date!r}.")

    all_days_out = []
    for day in days:
        lines, totals, missing = process_day(day, foods_spec)
        macros = macro_stats(totals)
        all_days_out.append({
            "date": day.date,
            "lines": lines,
            "totals": totals,
            "macros": macros,
            "unmatched": missing,
        })

    if args.json:
        print(json.dumps(all_days_out if len(all_days_out) > 1 else all_days_out[0], indent=2))
        return

    for day_out in all_days_out:
        print_day(
            day_out["date"],
            day_out["lines"],
            day_out["totals"],
            day_out["macros"],
            day_out["unmatched"],
        )


if __name__ == "__main__":
    main()
