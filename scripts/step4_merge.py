import sqlite3
import json
from pathlib import Path
from datetime import datetime

DB_PATH     = Path("db/london_halal.db")
OUTPUT_JSON = Path("data/final/london_halal_final.json")
OUTPUT_DB   = Path("data/final/london_halal_final_summary.json")

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # ── Pull all halal restaurants ────────────────────────────────────────────
    cursor.execute("""
        SELECT
            osm_id,
            osm_type,
            name,
            google_name,
            lat,
            lon,
            amenity,
            cuisine,
            diet_halal,
            address,
            google_address,
            phone,
            website,
            opening_hours,
            status,
            osm_halal,
            google_halal,
            final_halal,
            google_place_id,
            halal_type,
            halal_certified,
            certification_body,
            last_checked,
            tags_json
        FROM restaurants
        WHERE status IN ('confirmed', 'google_only')
        ORDER BY name
    """)

    rows = cursor.fetchall()
    print(f"Total halal restaurants: {len(rows):,}")

    # ── Build output records ──────────────────────────────────────────────────
    results = []
    for row in rows:
        # Prefer Google name if available, fall back to OSM name
        display_name = row["google_name"] or row["name"] or "Unknown"

        # Prefer Google address if available
        display_address = row["google_address"] or row["address"] or "Address Pending"

        record = {
            "id":                 row["osm_id"],
            "name":               display_name,
            "osm_name":           row["name"],
            "lat":                row["lat"],
            "lon":                row["lon"],
            "address":            display_address,
            "phone":              row["phone"],
            "website":            row["website"],
            "opening_hours":      row["opening_hours"],
            "cuisine":            row["cuisine"],

            # Halal confidence
            "status":             row["status"],
            "osm_halal":          bool(row["osm_halal"]),
            "google_halal":       row["google_halal"],
            "halal_type":         row["halal_type"],
            "halal_certified":    row["halal_certified"],
            "certification_body": row["certification_body"],

            # Google enrichment (step 5 will populate photos/reviews)
            "google_place_id":    row["google_place_id"],
            "photos":             [],
            "reviews":            [],

            # Meta
            "last_checked":       row["last_checked"],
            "source":             "osm+google" if row["osm_halal"] else "google",
        }
        results.append(record)

    # ── Save final JSON ───────────────────────────────────────────────────────
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # ── Summary stats ─────────────────────────────────────────────────────────
    confirmed    = sum(1 for r in results if r["status"] == "confirmed")
    google_only  = sum(1 for r in results if r["status"] == "google_only")
    osm_and_google = sum(1 for r in results if r["osm_halal"] and r["google_place_id"])
    has_place_id = sum(1 for r in results if r["google_place_id"])
    has_phone    = sum(1 for r in results if r["phone"])
    has_website  = sum(1 for r in results if r["website"])
    has_hours    = sum(1 for r in results if r["opening_hours"])

    summary = {
        "generated":           datetime.now().isoformat(),
        "total_halal":         len(results),
        "confirmed":           confirmed,
        "google_only":         google_only,
        "osm_and_google":      osm_and_google,
        "has_google_place_id": has_place_id,
        "has_phone":           has_phone,
        "has_website":         has_website,
        "has_opening_hours":   has_hours,
        "ready_for_enrichment": has_place_id,
    }

    with open(OUTPUT_DB, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    conn.close()

    print(f"\n── Summary ────────────────────────────────────────────")
    print(f"Total halal restaurants:    {len(results):,}")
    print(f"  Confirmed (OSM+Google):   {confirmed:,}")
    print(f"  Google-only discoveries:  {google_only:,}")
    print(f"Has google_place_id:        {has_place_id:,}  ← ready for step 5")
    print(f"Has phone:                  {has_phone:,}")
    print(f"Has website:                {has_website:,}")
    print(f"Has opening hours:          {has_hours:,}")
    print(f"\nSaved to {OUTPUT_JSON}")
    print(f"Next → run step5_enrich.py to pull photos, reviews, full details")

if __name__ == "__main__":
    main()