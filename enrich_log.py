#!/usr/bin/env python3
"""
Parse a Log CSV (from read_food_sheet.py), resolve nutrition via Open Food Facts
then USDA FoodData Central, scale by amount (grams default; tbsp / eggs supported).

Gram amounts are treated as whatever you weighed (e.g. dry rice before cooking is dry grams).

Environment:
  USDA_API_KEY — optional; defaults to DEMO_KEY (strict rate limits). Get a key at:
  https://fdc.nal.usda.gov/api-key-signup.html

Usage:
  python enrich_log.py path/to/food_export.csv
  python enrich_log.py path/to/food_export.csv --json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OFF_SEARCH = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_HEADERS = {
    "User-Agent": "DailyNutritionEnricher/1.0 (personal use; dustinlwicker@gmail.com)",
}
USDA_SEARCH = "https://api.nal.usda.gov/fdc/v1/foods/search"

# USDA nutrient IDs (per 100 g on typical search hits)
USDA_IDS = {
    "kcal": 1008,
    "protein": 1003,
    "fat": 1004,
    "carbs": 1005,
    "fiber": 1079,
    "sodium_mg": 1093,
}

EGG_LARGE_G = 50.0
TBSP_OIL_G = 13.6
TBSP_DEFAULT_G = 14.8


@dataclass
class NutritionPer100g:
    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    source: str = ""
    matched_name: str = ""


@dataclass
class LineItem:
    food: str
    amount_raw: str
    unit: str | None
    grams: float
    grams_note: str
    nutrition: NutritionPer100g | None = None
    scaled: dict[str, float] = field(default_factory=dict)


def parse_log(path: Path) -> tuple[str | None, list[LineItem]]:
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return None, []

    header = rows[0]
    log_date = None
    if len(header) >= 5 and header[3].strip().rstrip(":").lower() == "date":
        log_date = header[4].strip()

    items: list[LineItem] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        food = row[0].strip()
        amount_raw = row[1].strip() if len(row) > 1 else ""
        unit = row[2].strip() if len(row) > 2 and row[2].strip() else None
        try:
            amount = float(amount_raw.replace(",", ""))
        except ValueError:
            amount = 0.0
        grams, note = amount_to_grams(food, amount, unit)
        items.append(
            LineItem(
                food=food,
                amount_raw=amount_raw,
                unit=unit,
                grams=grams,
                grams_note=note,
            )
        )
    return log_date, items


def amount_to_grams(food: str, amount: float, unit: str | None) -> tuple[float, str]:
    u = (unit or "").strip().lower()
    if not u:
        return amount, "g (assumed)"
    if u in ("g", "gram", "grams"):
        return amount, "g"
    if u == "oz":
        return amount * 28.3495, "oz→g"
    if u == "tbsp":
        g_per = TBSP_OIL_G if "oil" in food.lower() else TBSP_DEFAULT_G
        return amount * g_per, f"tbsp×{g_per}g"
    if u == "tsp":
        g_per = TBSP_OIL_G / 3 if "oil" in food.lower() else TBSP_DEFAULT_G / 3
        return amount * g_per, f"tsp×{g_per:.2f}g"
    if u in ("egg", "eggs"):
        return amount * EGG_LARGE_G, f"{amount:g}×{EGG_LARGE_G:g}g/egg"
    return amount, f"unknown unit {unit!r} — treated as g"


def off_pick_nutriments(product: dict) -> NutritionPer100g | None:
    n = product.get("nutriments") or {}

    def f(key: str) -> float | None:
        v = n.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    kcal = f("energy-kcal_100g")
    if kcal is None:
        return None
    name = (product.get("product_name") or product.get("generic_name") or "").strip()
    return NutritionPer100g(
        kcal=kcal,
        protein_g=f("proteins_100g"),
        fat_g=f("fat_100g"),
        carbs_g=f("carbohydrates_100g"),
        fiber_g=f("fiber_100g"),
        sodium_mg=(f("sodium_100g") * 1000) if f("sodium_100g") is not None else None,
        source="Open Food Facts",
        matched_name=name[:120],
    )


def off_search(query: str, session: requests.Session) -> NutritionPer100g | None:
    params = {
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 8,
    }
    r = session.get(OFF_SEARCH, params=params, headers=OFF_HEADERS, timeout=45)
    r.raise_for_status()
    data = r.json()
    for product in data.get("products") or []:
        nut = off_pick_nutriments(product)
        if nut:
            return nut
    return None


def usda_parse_nutrients(food: dict) -> NutritionPer100g | None:
    out = {k: None for k in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sodium_mg")}
    for fn in food.get("foodNutrients") or []:
        nid = fn.get("nutrientId")
        val = fn.get("value")
        if val is None or nid is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if nid == USDA_IDS["kcal"]:
            out["kcal"] = val
        elif nid == USDA_IDS["protein"]:
            out["protein_g"] = val
        elif nid == USDA_IDS["fat"]:
            out["fat_g"] = val
        elif nid == USDA_IDS["carbs"]:
            out["carbs_g"] = val
        elif nid == USDA_IDS["fiber"]:
            out["fiber_g"] = val
        elif nid == USDA_IDS["sodium_mg"]:
            out["sodium_mg"] = val
    if out["kcal"] is None:
        return None
    desc = food.get("description") or food.get("lowercaseDescription") or ""
    return NutritionPer100g(
        kcal=out["kcal"],
        protein_g=out["protein_g"],
        fat_g=out["fat_g"],
        carbs_g=out["carbs_g"],
        fiber_g=out["fiber_g"],
        sodium_mg=out["sodium_mg"],
        source="USDA FDC",
        matched_name=str(desc)[:120],
    )


def usda_search(
    query: str, api_key: str, session: requests.Session, retries: int = 4
) -> NutritionPer100g | None:
    params = {"api_key": api_key, "query": query[:200], "pageSize": 5}
    for attempt in range(retries):
        r = session.get(USDA_SEARCH, params=params, timeout=45)
        if r.status_code == 429:
            time.sleep(2.0 * (attempt + 1))
            continue
        r.raise_for_status()
        foods = r.json().get("foods") or []
        for food in foods:
            nut = usda_parse_nutrients(food)
            if nut:
                return nut
        return None
    return None


def scale(n: NutritionPer100g, grams: float) -> dict[str, float]:
    factor = grams / 100.0
    def s(v: float | None) -> float:
        return round((v or 0) * factor, 2) if v is not None else 0.0

    return {
        "kcal": s(n.kcal),
        "protein_g": s(n.protein_g),
        "fat_g": s(n.fat_g),
        "carbs_g": s(n.carbs_g),
        "fiber_g": s(n.fiber_g),
        "sodium_mg": s(n.sodium_mg),
    }


def enrich_items(
    items: list[LineItem],
    session: requests.Session,
    usda_key: str,
    off_delay: float,
) -> None:
    for item in items:
        q = item.food
        nut = None
        try:
            nut = off_search(q, session)
            time.sleep(off_delay)
        except requests.RequestException as e:
            print(f"[WARN] Open Food Facts failed for {q!r}: {e}", file=sys.stderr)

        if nut is None:
            try:
                nut = usda_search(q, usda_key, session)
                time.sleep(1.1)
            except requests.RequestException as e:
                print(f"[WARN] USDA failed for {q!r}: {e}", file=sys.stderr)

        item.nutrition = nut
        if nut:
            item.scaled = scale(nut, item.grams)


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich food log CSV with OFF + USDA nutrition.")
    ap.add_argument("csv_path", type=Path, help="Path to food_export.csv")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text tables")
    ap.add_argument(
        "--off-delay",
        type=float,
        default=0.55,
        help="Seconds between Open Food Facts requests (default 0.55)",
    )
    args = ap.parse_args()

    usda_key = (os.environ.get("USDA_API_KEY") or "DEMO_KEY").strip()
    log_date, items = parse_log(args.csv_path)
    if not items:
        sys.exit("No food rows found in CSV.")

    session = requests.Session()
    enrich_items(items, session, usda_key, args.off_delay)

    totals = {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0, "fiber_g": 0.0, "sodium_mg": 0.0}

    out_rows = []
    for item in items:
        row = {
            "food": item.food,
            "amount": item.amount_raw,
            "unit": item.unit or "",
            "grams_est": round(item.grams, 2),
            "grams_note": item.grams_note,
            "source": item.nutrition.source if item.nutrition else "",
            "matched": item.nutrition.matched_name if item.nutrition else "",
            **{k: item.scaled.get(k, 0) for k in totals},
        }
        if not item.nutrition:
            row["note"] = "no match"
        out_rows.append(row)
        if item.scaled:
            for k in totals:
                totals[k] += item.scaled.get(k, 0)

    for k in totals:
        totals[k] = round(totals[k], 2)

    payload = {"date": log_date, "lines": out_rows, "totals": totals}

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    if log_date:
        print(f"Log date: {log_date}\n")
    print(
        f"{'Food':<50} {'g':>7} {'kcal':>7} {'prot':>6} {'fat':>6} {'carb':>6} {'fib':>5} {'Na+':>7}  Source"
    )
    print("-" * 120)
    for item in items:
        if item.nutrition and item.scaled:
            sc = item.scaled
            print(
                f"{item.food[:50]:<50} {item.grams:7.1f} {sc['kcal']:7.0f} "
                f"{sc['protein_g']:6.1f} {sc['fat_g']:6.1f} {sc['carbs_g']:6.1f} "
                f"{sc['fiber_g']:5.1f} {sc['sodium_mg']:7.0f}  {item.nutrition.source}"
            )
        else:
            print(f"{item.food[:50]:<50} {item.grams:7.1f} {'—':>7} {'—':>6} {'—':>6} {'—':>6} {'—':>5} {'—':>7}  (no match)")
    print("-" * 120)
    print(
        f"{'TOTAL':<50} {'':>7} {totals['kcal']:7.0f} {totals['protein_g']:6.1f} "
        f"{totals['fat_g']:6.1f} {totals['carbs_g']:6.1f} {totals['fiber_g']:5.1f} {totals['sodium_mg']:7.0f}"
    )
    print("\nNotes:")
    print("- Per-100g data is matched to the product name; verify the DB hit is dry vs cooked if ambiguous.")
    print("- Eggs: 50 g per large egg assumed for scaling.")
    print("- tbsp: 13.6 g if food name contains ‘oil’, else 14.8 g.")
    print("- Get a free USDA_API_KEY if DEMO_KEY rate-limits you.")


if __name__ == "__main__":
    main()
