import json
import sqlite3
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
INPUT_JSON = Path("data/raw/londonRestaurants.json")
DB_PATH    = Path("db/london_halal.db")

# ── Halal detection (same logic as step 1) ───────────────────────────────────
def is_osm_halal(tags: dict) -> bool:
    if tags.get("diet:halal") in ("yes", "only"):
        return True
    cuisine = tags.get("cuisine", "").lower()
    if "halal" in cuisine.split(";"):
        return True
    return False

# ── Database setup ───────────────────────────────────────────────────────────
def create_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            osm_id          TEXT PRIMARY KEY,
            osm_type        TEXT,
            name            TEXT,
            lat             REAL,
            lon             REAL,
            amenity         TEXT,
            cuisine         TEXT,
            diet_halal      TEXT,
            address         TEXT,
            phone           TEXT,
            website         TEXT,
            opening_hours   TEXT,

            -- Pipeline tracking
            status          TEXT DEFAULT 'pending',
            -- pending      → not yet checked by Google
            -- osm_halal    → OSM flagged it, awaiting Google validation
            -- confirmed    → Google agrees it's halal
            -- rejected     → Google says not halal
            -- google_only  → Google found it, wasn't in OSM halal list
            -- checked      → Google checked, not halal

            -- Halal signals
            osm_halal       INT  DEFAULT 0,   -- 1 = OSM flagged halal
            google_halal    INT  DEFAULT -1,  -- -1=unchecked, 0=no, 1=yes
            final_halal     INT  DEFAULT -1,  -- adjudicated result

            -- Google enrichment (populated in step 2/3)
            google_place_id TEXT,
            google_name     TEXT,
            google_address  TEXT,
            google_rating   REAL,
            last_checked    TEXT,

            -- Full OSM tags preserved — nothing lost
            tags_json       TEXT
        )
    """)

    # Index for fast status queries — every subsequent script uses this
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_status ON restaurants(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_latlon ON restaurants(lat, lon)
    """)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"Loading {INPUT_JSON} ...")
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    elements = data.get("elements", [])
    print(f"Total elements in JSON: {len(elements):,}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    create_table(cursor)

    inserted   = 0
    skipped    = 0
    osm_halal  = 0

    for el in elements:
        tags    = el.get("tags", {})
        lat     = el.get("lat") or el.get("center", {}).get("lat")
        lon     = el.get("lon") or el.get("center", {}).get("lon")

        if not lat or not lon:
            skipped += 1
            continue

        halal   = is_osm_halal(tags)
        status  = "osm_halal" if halal else "pending"
        if halal:
            osm_halal += 1

        address = ", ".join(filter(None, [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:city", ""),
            tags.get("addr:postcode", ""),
        ])) or "Address Pending"

        cursor.execute("""
            INSERT OR IGNORE INTO restaurants (
                osm_id, osm_type, name, lat, lon,
                amenity, cuisine, diet_halal,
                address, phone, website, opening_hours,
                status, osm_halal,
                tags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(el.get("id")),
            el.get("type"),
            tags.get("name", "Unknown"),
            lat, lon,
            tags.get("amenity"),
            tags.get("cuisine"),
            tags.get("diet:halal"),
            address,
            tags.get("phone"),
            tags.get("website"),
            tags.get("opening_hours"),
            status,
            1 if halal else 0,
            json.dumps(tags, ensure_ascii=False),
        ))
        inserted += 1

    conn.commit()
    conn.close()

    print(f"\n── Results ───────────────────────────────")
    print(f"Inserted into DB:     {inserted:,}")
    print(f"Skipped (no coords):  {skipped:,}")
    print(f"Status = osm_halal:   {osm_halal:,}")
    print(f"Status = pending:     {inserted - osm_halal:,}")
    print(f"DB saved to:          {DB_PATH}")
    print(f"\nNext → run step2_google_validate.py against the {osm_halal} osm_halal rows")

if __name__ == "__main__":
    main()