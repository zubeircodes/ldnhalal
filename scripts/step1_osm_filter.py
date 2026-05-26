import json
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
INPUT  = Path("data/raw/londonRestaurants.json")
OUTPUT = Path("data/processed/london_halal_osm.json")

# ── Halal detection ──────────────────────────────────────────────────────────
def is_halal(tags: dict) -> bool:
    """
    Returns True if OSM tags indicate halal.
    OSM values for diet:halal are 'yes' or 'only' (not the word 'halal').
    Cuisine can contain 'halal' as a semicolon-separated value e.g. 'chicken;halal'
    """
    # Strongest signal: explicit diet tag
    if tags.get("diet:halal") in ("yes", "only"):
        return True

    # Secondary signal: cuisine contains halal
    cuisine = tags.get("cuisine", "").lower()
    if "halal" in cuisine.split(";"):
        return True

    return False

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"Loading {INPUT} ...")
    with open(INPUT, "r", encoding="utf-8") as f:
        data = json.load(f)

    elements = data.get("elements", [])
    print(f"Total elements: {len(elements):,}")

    halal = []
    skipped_no_coords = 0

    for el in elements:
        tags = el.get("tags", {})

        # Coords — nodes have lat/lon directly; ways/relations use center
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")

        if not lat or not lon:
            skipped_no_coords += 1
            continue

        if not is_halal(tags):
            continue

        halal.append({
            "osm_id":   el.get("id"),
            "osm_type": el.get("type"),
            "name":     tags.get("name", "Unknown"),
            "lat":      lat,
            "lon":      lon,
            "amenity":  tags.get("amenity"),
            "cuisine":  tags.get("cuisine"),
            "diet_halal": tags.get("diet:halal"),
            "address": ", ".join(filter(None, [
                tags.get("addr:housenumber", ""),
                tags.get("addr:street", ""),
                tags.get("addr:city", ""),
                tags.get("addr:postcode", ""),
            ])) or "Address Pending",
            "phone":    tags.get("phone"),
            "website":  tags.get("website"),
            "opening_hours": tags.get("opening_hours"),
            "tags":     tags,   # full tags preserved — nothing lost
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(halal, f, indent=2, ensure_ascii=False)

    print(f"Skipped (no coordinates): {skipped_no_coords:,}")
    print(f"Halal candidates found:   {len(halal):,}")
    print(f"Saved to {OUTPUT}")

if __name__ == "__main__":
    main()