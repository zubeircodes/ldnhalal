import sqlite3
import requests
import json
import time
import boto3
from botocore.config import Config
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY       = os.getenv("GOOGLE_MAPS_API_KEY")
CF_ACCOUNT_ID        = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_ACCESS_KEY        = os.getenv("CLOUDFLARE_R2_ACCESS_KEY")
CF_SECRET_KEY        = os.getenv("CLOUDFLARE_R2_SECRET_KEY")
CF_BUCKET            = os.getenv("CLOUDFLARE_R2_BUCKET")

DB_PATH              = Path("db/london_halal.db")
MAX_PHOTOS           = 5
MAX_REVIEWS          = 10

# ── Google Places API (New) ───────────────────────────────────────────────────
PLACES_DETAIL_URL    = "https://places.googleapis.com/v1/places"

FIELD_MASK = ",".join([
    "id",
    "displayName",
    "formattedAddress",
    "nationalPhoneNumber",
    "websiteUri",
    "regularOpeningHours",
    "rating",
    "userRatingCount",
    "priceLevel",
    "types",
    "primaryType",
    "photos",
    "reviews",
    "googleMapsUri",
])

# ── Cloudflare R2 client ──────────────────────────────────────────────────────
def make_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{CF_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=CF_ACCESS_KEY,
        aws_secret_access_key=CF_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

# ── Fetch full place details from Google ─────────────────────────────────────
def fetch_place_details(place_id):
    url = f"{PLACES_DETAIL_URL}/{place_id}"
    headers = {
        "Content-Type":    "application/json",
        "X-Goog-Api-Key":  GOOGLE_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"    ⚠ Places detail error: {e}")
        return None

# ── Fetch and upload a photo to R2 ───────────────────────────────────────────
def fetch_photo_url(photo_name):
    """Get the actual photo URL from Google"""
    url = f"https://places.googleapis.com/v1/{photo_name}/media"
    params = {
        "maxHeightPx": 800,
        "maxWidthPx":  800,
        "key": GOOGLE_API_KEY,
        "skipHttpRedirect": "false",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "image/jpeg")
    except Exception as e:
        print(f"    ⚠ Photo fetch error: {e}")
        return None, None

def upload_photo_to_r2(r2, place_id, photo_index, photo_data, content_type):
    """Upload photo bytes to Cloudflare R2"""
    key = f"photos/{place_id}/photo_{photo_index}.jpg"
    try:
        r2.put_object(
            Bucket=CF_BUCKET,
            Key=key,
            Body=photo_data,
            ContentType=content_type,
        )
        return f"https://{CF_BUCKET}.{CF_ACCOUNT_ID}.r2.cloudflarestorage.com/{key}"
    except Exception as e:
        print(f"    ⚠ R2 upload error: {e}")
        return None

# ── Parse opening hours ───────────────────────────────────────────────────────
def parse_hours(opening_hours):
    if not opening_hours:
        return None
    periods = opening_hours.get("weekdayDescriptions", [])
    return "\n".join(periods) if periods else None

# ── Parse reviews ─────────────────────────────────────────────────────────────
def parse_reviews(reviews):
    if not reviews:
        return []
    result = []
    for r in reviews[:MAX_REVIEWS]:
        result.append({
            "author":   r.get("authorAttribution", {}).get("displayName", ""),
            "rating":   r.get("rating"),
            "text":     r.get("text", {}).get("text", ""),
            "time":     r.get("relativePublishTimeDescription", ""),
        })
    return result

# ── Parse price level ─────────────────────────────────────────────────────────
PRICE_MAP = {
    "PRICE_LEVEL_FREE":           "$",
    "PRICE_LEVEL_INEXPENSIVE":    "$",
    "PRICE_LEVEL_MODERATE":       "$$",
    "PRICE_LEVEL_EXPENSIVE":      "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}

# ── Add schema columns if not already present ─────────────────────────────────
def ensure_columns(cursor):
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(restaurants)")}
    new_cols = {
        "google_rating":       "REAL",
        "google_review_count": "INTEGER",
        "price_level":         "TEXT",
        "hours_text":          "TEXT",
        "photos_json":         "TEXT",
        "reviews_json":        "TEXT",
        "google_maps_uri":     "TEXT",
        "primary_type":        "TEXT",
        "enriched":            "INTEGER DEFAULT 0",
    }
    for col, dtype in new_cols.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE restaurants ADD COLUMN {col} {dtype}")
            print(f"  Added column: {col}")

# ── Auto-tag cuisine from OSM + Google ───────────────────────────────────────
CUISINE_MAP = {
    'pakistani': 'pakistani', 'turkish': 'turkish', 'lebanese': 'lebanese',
    'indian': 'indian', 'chinese': 'chinese', 'caribbean': 'caribbean',
    'african': 'african', 'bangladeshi': 'bangladeshi', 'afghan': 'afghan',
    'persian': 'persian', 'malaysian': 'malaysian', 'korean': 'korean',
    'mediterranean': 'mediterranean', 'middle_eastern': 'middle_eastern',
    'burger': 'burger', 'pizza': 'pizza', 'kebab': 'kebab',
    'chicken': 'chicken', 'fish_and_chips': 'seafood', 'seafood': 'seafood',
    'american': 'american', 'italian': 'italian', 'mexican': 'mexican',
    'japanese': 'japanese', 'thai': 'thai', 'vietnamese': 'vietnamese',
    'coffee_shop': 'cafe', 'sandwich': 'sandwich', 'halal': 'fully_halal',
}

GOOGLE_TYPE_MAP = {
    'pakistani_restaurant': 'pakistani', 'indian_restaurant': 'indian',
    'turkish_restaurant': 'turkish', 'lebanese_restaurant': 'lebanese',
    'chinese_restaurant': 'chinese', 'hamburger_restaurant': 'burger',
    'pizza_restaurant': 'pizza', 'kebab_shop': 'kebab',
    'chicken_shop': 'chicken', 'seafood_restaurant': 'seafood',
    'korean_restaurant': 'korean', 'japanese_restaurant': 'japanese',
    'mediterranean_restaurant': 'mediterranean',
    'middle_eastern_restaurant': 'middle_eastern',
    'african_restaurant': 'african', 'caribbean_restaurant': 'caribbean',
    'mexican_restaurant': 'mexican', 'italian_restaurant': 'italian',
    'fast_food_restaurant': 'fast_food', 'cafe': 'cafe',
}

def assign_tag(cursor, restaurant_id, tag_name):
    cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
    row = cursor.fetchone()
    if row:
        cursor.execute("""
            INSERT OR IGNORE INTO restaurant_tags (restaurant_id, tag_id, added_by)
            VALUES (?, ?, 'system')
        """, (restaurant_id, row[0]))

def assign_cuisine_tags(cursor, restaurant_id, cuisine_string):
    if not cuisine_string:
        return
    for part in [c.strip().lower() for c in cuisine_string.split(';')]:
        tag = CUISINE_MAP.get(part)
        if tag:
            assign_tag(cursor, restaurant_id, tag)

def assign_google_type_tags(cursor, restaurant_id, types):
    for t in types:
        tag = GOOGLE_TYPE_MAP.get(t)
        if tag:
            assign_tag(cursor, restaurant_id, tag)

def assign_area_tag(cursor, restaurant_id, lat, lon):
    if lon > -0.05:
        area = 'east_london'
    elif lon < -0.25:
        area = 'west_london'
    elif lat > 51.55:
        area = 'north_london'
    elif lat < 51.45:
        area = 'south_london'
    else:
        area = 'central_london'
    assign_tag(cursor, restaurant_id, area)

def assign_price_tag(cursor, restaurant_id, price_level):
    price_tag_map = {
        "$": "budget", "$$": "mid_range",
        "$$$": "premium", "$$$$": "premium"
    }
    tag = price_tag_map.get(price_level)
    if tag:
        assign_tag(cursor, restaurant_id, tag)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Add new columns if needed
    ensure_columns(cursor)
    conn.commit()

    # Init R2
    r2 = make_r2_client()
    print("R2 client initialised ✓\n")

    # Only process unenriched halal restaurants
    cursor.execute("""
        SELECT osm_id, name, google_place_id, lat, lon, cuisine
        FROM restaurants
        WHERE status IN ('confirmed', 'google_only')
        AND   enriched = 0
        AND   google_place_id IS NOT NULL
    """)
    restaurants = cursor.fetchall()
    total = len(restaurants)
    print(f"Restaurants to enrich: {total:,}\n")

    enriched   = 0
    failed     = 0
    photos_uploaded = 0

    for i, row in enumerate(restaurants, 1):
        osm_id    = row["osm_id"]
        name      = row["name"]
        place_id  = row["google_place_id"]
        lat       = row["lat"]
        lon       = row["lon"]
        cuisine   = row["cuisine"]

        print(f"[{i}/{total}] {name}")

        # Fetch full details from Google
        details = fetch_place_details(place_id)
        if not details:
            failed += 1
            time.sleep(0.3)
            continue

        # ── Parse fields ──────────────────────────────────────────────────────
        phone        = details.get("nationalPhoneNumber")
        website      = details.get("websiteUri")
        rating       = details.get("rating")
        review_count = details.get("userRatingCount")
        price_raw    = details.get("priceLevel", "")
        price_level  = PRICE_MAP.get(price_raw, "")
        primary_type = details.get("primaryType", "")
        google_types = details.get("types", [])
        maps_uri     = details.get("googleMapsUri", "")
        hours_text   = parse_hours(details.get("regularOpeningHours"))
        reviews      = parse_reviews(details.get("reviews", []))
        reviews_json = json.dumps(reviews, ensure_ascii=False)

        # ── Photos → R2 ───────────────────────────────────────────────────────
        photo_urls = []
        photos_data = details.get("photos", [])[:MAX_PHOTOS]
        for idx, photo in enumerate(photos_data, 1):
            photo_name = photo.get("name")
            if not photo_name:
                continue
            photo_data, content_type = fetch_photo_url(photo_name)
            if photo_data:
                url = upload_photo_to_r2(r2, place_id, idx, photo_data, content_type)
                if url:
                    photo_urls.append(url)
                    photos_uploaded += 1
            time.sleep(0.1)

        photos_json = json.dumps(photo_urls, ensure_ascii=False)

        # ── Update DB ─────────────────────────────────────────────────────────
        cursor.execute("""
            UPDATE restaurants SET
                phone              = COALESCE(phone, ?),
                website            = COALESCE(website, ?),
                google_rating      = ?,
                google_review_count = ?,
                price_level        = ?,
                primary_type       = ?,
                hours_text         = ?,
                photos_json        = ?,
                reviews_json       = ?,
                google_maps_uri    = ?,
                enriched           = 1,
                last_checked       = ?
            WHERE osm_id = ?
        """, (
            phone, website, rating, review_count,
            price_level, primary_type, hours_text,
            photos_json, reviews_json, maps_uri,
            datetime.now().isoformat(), osm_id
        ))

        # ── Auto-tagging ──────────────────────────────────────────────────────
        assign_cuisine_tags(cursor, osm_id, cuisine)
        assign_google_type_tags(cursor, osm_id, google_types)
        assign_area_tag(cursor, osm_id, lat, lon)
        assign_price_tag(cursor, osm_id, price_level)

        enriched += 1
        print(f"    ✓ {rating}★  {review_count} reviews  {len(photo_urls)} photos  {price_level}")

        # Commit every 20 restaurants
        if i % 20 == 0:
            conn.commit()
            print(f"    ── checkpoint saved ({i}/{total}) ──")

        time.sleep(0.3)

    conn.commit()
    conn.close()

    print(f"\n── Results ────────────────────────────────────────────")
    print(f"Successfully enriched:  {enriched:,}")
    print(f"Failed:                 {failed:,}")
    print(f"Photos uploaded to R2:  {photos_uploaded:,}")
    print(f"\nYour database is now ready for the website.")

if __name__ == "__main__":
    main()