#!/usr/bin/env python3
import os
import csv
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
import sys
from datetime import datetime

load_dotenv()

DB_HOST = "localhost"
DB_NAME = "zenith_data"
DB_USER = "zenith_bot"
DB_PASS = os.getenv("DB_PASSWORD", "zenith6Z@")
TABLE_NAME = "player_stats"
BATCH_SIZE = 50

# [ ... Keep your existing COLUMN_MAPPING and INT_COLUMNS dictionaries here ... ]
# (Copy-paste them from your original script to save space here)
COLUMN_MAPPING = {
    'playerid': 'player_id', 'traininglevel': 'training_level', 'alternateposition': 'alternate_position',
    'nationregion': 'nation_region', 'skillmovesstars': 'skill_moves_stars', 'strongfootside': 'strong_foot_side',
    'strongfootstars': 'strong_foot_stars', 'weakfootstars': 'weak_foot_stars', 'heightftin': 'height_ft_in',
    'heightcm': 'height_cm', 'weightkg': 'weight_kg', 'longshot': 'long_shot', 'shotpower': 'shot_power',
    'shortpassing': 'short_passing', 'longpassing': 'long_passing', 'freekick': 'free_kick',
    'ballcontrol': 'ball_control', 'standingtackle': 'standing_tackle', 'slidingtackle': 'sliding_tackle',
    'workrateattack': 'work_rate_attack', 'workratedefense': 'work_rate_defense', 'gkdiving': 'gk_diving',
    'gkpositioning': 'gk_positioning', 'gkhandling': 'gk_handling', 'gkreflexes': 'gk_reflexes',
    'gkkicking': 'gk_kicking', 'staminastat': 'stamina_stat', 'dribblinghead': 'dribbling_head',
    'isuntradable': 'is_untradable', 'dateadded': 'date_added', 'leagueimage': 'league_image',
    'traitsname': 'traits_name', 'playerimage': 'player_image', 'cardbackground': 'card_background',
    'nationflag': 'nation_flag', 'clubflag': 'club_flag', 'colorrating': 'color_rating',
    'colorposition': 'color_position', 'colorname': 'color_name', 'colorlevel': 'color_level',
    'asset_id': 'player_id'
}

INT_COLUMNS = {
    'player_id', 'rank', 'training_level', 'skill_moves_stars', 'strong_foot_stars', 'weak_foot_stars',
    'height_cm', 'weight_kg', 'ovr', 'stamina_stat', 'pace', 'acceleration', 'sprint_speed', 'shooting',
    'finishing', 'long_shot', 'shot_power', 'positioning', 'volley', 'penalties', 'passing', 'short_passing',
    'long_passing', 'vision', 'crossing', 'curve', 'free_kick', 'dribbling_head', 'dribbling', 'balance',
    'agility', 'reactions', 'ball_control', 'defending', 'marking', 'standing_tackle', 'sliding_tackle',
    'awareness', 'heading', 'physical', 'strength', 'aggression', 'jumping', 'diving', 'gk_diving',
    'gk_positioning', 'handling', 'gk_handling', 'reflexes', 'gk_reflexes', 'kicking', 'gk_kicking', 'id'
}

def get_db_connection():
    return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)

def normalize_column_name(header):
    header_lower = header.lower().replace(' ', '_').replace('-', '_')
    return COLUMN_MAPPING.get(header_lower, header_lower)

def safe_int_convert(val):
    if not val or val.strip() == '': return 0
    try: return int(float(val.strip()))
    except: return 0

def import_stats_csv(csv_file, conn):
    print(f"📥 Importing stats from {csv_file}...")
    cur = conn.cursor()

    rows_processed = 0
    rows_inserted = 0
    batch_num = 0

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        csv_headers = reader.fieldnames

        db_columns = [normalize_column_name(h) for h in csv_headers if normalize_column_name(h)]
        col_names = ", ".join([f'"{col}"' for col in db_columns])
        placeholders = ", ".join(["%s"] * len(db_columns))
        
        # KEY CHANGE: ON CONFLICT DO NOTHING
        query = f"""
            INSERT INTO {TABLE_NAME} ({col_names}) 
            VALUES ({placeholders})
            ON CONFLICT (player_id, rank, training_level) DO NOTHING
        """

        batch = []
        for row_num, row in enumerate(reader, 1):
            values = []
            for col in db_columns:
                csv_header = next((h for h in csv_headers if normalize_column_name(h) == col), None)
                val = row.get(csv_header, '') if csv_header else ''
                values.append(safe_int_convert(val) if col in INT_COLUMNS else (val.strip() if val.strip() else ''))

            batch.append(tuple(values))
            rows_processed += 1

            if len(batch) >= BATCH_SIZE:
                batch_num += 1
                try:
                    execute_batch(cur, query, batch, page_size=10)
                    rows_inserted += len(batch)
                    print(f"✅ Batch #{batch_num}: {len(batch)} processed")
                except Exception as e:
                    print(f"⚠️ Batch #{batch_num} failed: {e}")
                batch = []

        if batch:
            batch_num += 1
            try:
                execute_batch(cur, query, batch, page_size=10)
                rows_inserted += len(batch)
                print(f"✅ Final Batch #{batch_num}: {len(batch)} processed")
            except Exception as e:
                print(f"⚠️ Final Batch failed: {e}")

    conn.commit()
    cur.close()
    print(f"✅ Stats: {rows_processed} rows processed.")
    return rows_inserted

# ... [KEEP import_colors_csv and main as they are] ...
# (Copy paste import_colors_csv and main from your original script)
def import_colors_csv(csv_file, conn):
    """Import colors - UPDATE existing records using asset_id=player_id"""
    print(f"🎨 Importing colors from {csv_file}...")
    cur = conn.cursor()

    rows_processed = 0
    rows_updated = 0

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        csv_headers = reader.fieldnames

        batch = []
        for row_num, row in enumerate(reader, 1):
            try:
                asset_id = safe_int_convert(row.get('asset_id', ''))
                if not asset_id:
                    continue

                # UPDATE query for colors
                update_query = """
                    UPDATE player_stats
                    SET color_rating = %s, color_position = %s, color_name = %s, color_level = %s
                    WHERE player_id = %s AND rank = 0 AND training_level = 0
                """

                values = [
                    row.get('color_rating', '').strip(),
                    row.get('color_position', '').strip(),
                    row.get('color_name', '').strip(),
                    row.get('color_level', '').strip(),
                    asset_id
                ]

                batch.append(values)
                rows_processed += 1

                if len(batch) >= BATCH_SIZE:
                    try:
                        execute_batch(cur, update_query, batch, page_size=10)
                        rows_updated += len(batch)
                        print(f"✅ Colors Batch: {rows_updated} updated ({rows_processed} processed)")
                    except Exception as e:
                        print(f"⚠️ Colors Batch failed: {e}")
                    batch = []

            except Exception as e:
                print(f"⚠️ Colors row {row_num} error: {e}")
                continue

        # Final batch
        if batch:
            try:
                execute_batch(cur, update_query, batch, page_size=10)
                rows_updated += len(batch)
                print(f"✅ Colors Final Batch: {rows_updated} total")
            except Exception as e:
                print(f"⚠️ Colors Final Batch failed: {e}")

    conn.commit()
    cur.close()
    print(f"✅ Colors: {rows_processed} processed, {rows_updated} updated")
    return rows_updated

def main():
    if len(sys.argv) != 3:
        print("❌ Usage: python3 append_playerstats.py <stats_csv> <colors_csv>")
        sys.exit(1)

    stats_file, colors_file = sys.argv[1], sys.argv[2]

    if not os.path.exists(stats_file):
        print(f"❌ {stats_file} not found!")
        sys.exit(1)

    print(f"🚀 Starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📁 Stats: {stats_file}")
    print(f"🎨 Colors: {colors_file}")
    print("-" * 60)

    conn = None
    try:
        conn = get_db_connection()

        # 1. Import stats
        stats_inserted = import_stats_csv(stats_file, conn)

        # 2. Import colors
        colors_updated = 0
        if os.path.exists(colors_file):
            colors_updated = import_colors_csv(colors_file, conn)
        else:
            print("ℹ️ Colors file not found, skipping")

        print("\n" + "="*60)
        print("🎉 PERFECT IMPORT COMPLETED!")
        print(f"📊 Stats inserted: {stats_inserted:,}")
        print(f"🎨 Colors updated:  {colors_updated:,}")
        print("="*60)

    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            conn.close()
            print("🔌 DB closed")

if __name__ == "__main__":
    main()
