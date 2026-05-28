import ijson
import psycopg2
from psycopg2.extras import execute_batch
import sys

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'zenith_data',
    'user': 'zenith_bot',
    'password': 'zenith6Z@'
}

SKILL_STAT_MAPPING = {
    'acc': 'acceleration', 'agg': 'aggression', 'agi': 'agility', 'awa': 'awareness',
    'bal': 'balance', 'bac': 'ball_control', 'cro': 'crossing', 'cur': 'curve',
    'dri': 'dribbling', 'div': 'diving', 'fin': 'finishing', 'fre': 'free_kick',
    'gkd': 'gk_diving', 'han': 'gk_handling', 'gkk': 'gk_kicking', 'gkp': 'gk_positioning',
    'ref': 'gk_reflexes', 'hea': 'heading', 'jmp': 'jumping',
    'kic': 'kicking', 'lpa': 'long_passing', 'lsh': 'long_shot', 'mar': 'marking',
    'pac': 'pace', 'pen': 'penalties', 'pos': 'positioning', 'rea': 'reactions',
    'sho': 'shot_power', 'sli': 'sliding_tackle', 'spd': 'sprint_speed',
    'sta': 'stamina', 'stan': 'standing_tackle', 'str': 'strength', 'spa': 'short_passing',
    'vis': 'vision', 'vol': 'volley', 'frk': 'free_kick', 'awr': 'awareness',
    'stt': 'standing_tackle', 'slt': 'sliding_tackle', 'lsa': 'long_shot', 'mrk': 'marking',
}

def normalize_boost_name(boost_name):
    """Translate abbreviated or inconsistent boost names to database column format"""
    if boost_name in SKILL_STAT_MAPPING:
        return SKILL_STAT_MAPPING[boost_name]

    normalized = boost_name.lower().strip()

    replacements = {
        'shotpower': 'shot_power', 'longpassing': 'long_passing',
        'shortpassing': 'short_passing', 'ballcontrol': 'ball_control',
        'sprintspeed': 'sprint_speed', 'freekick': 'free_kick',
        'longshot': 'long_shot', 'standingtackle': 'standing_tackle',
        'slidingtackle': 'sliding_tackle', 'gkdiving': 'gk_diving',
        'gkhandling': 'gk_handling', 'gkkicking': 'gk_kicking',
        'gkpositioning': 'gk_positioning', 'gkreflexes': 'gk_reflexes',
	'handling': 'gk_handling', 'reflexes': 'gk_reflexes',
    }

    return replacements.get(normalized, normalized)

def insert_batch(cur, batch):
    """Insert a batch of rows into skill_level_boosts"""
    if not batch:
        return

    boost_columns = [
        'boost_pace', 'boost_shooting', 'boost_passing', 'boost_dribbling',
        'boost_defending', 'boost_physical', 'boost_acceleration', 'boost_sprint_speed',
        'boost_finishing', 'boost_shot_power', 'boost_long_shot', 'boost_positioning',
        'boost_volley', 'boost_penalties', 'boost_short_passing', 'boost_long_passing',
        'boost_crossing', 'boost_curve', 'boost_free_kick', 'boost_vision',
        'boost_ball_control', 'boost_agility', 'boost_reactions', 'boost_balance',
        'boost_composure', 'boost_interceptions', 'boost_heading', 'boost_marking',
        'boost_standing_tackle', 'boost_sliding_tackle', 'boost_awareness',
        'boost_jumping', 'boost_stamina', 'boost_strength', 'boost_aggression',
        'boost_gk_diving', 'boost_gk_handling', 'boost_gk_kicking',
        'boost_gk_positioning', 'boost_gk_reflexes', 'boost_long_shot_accuracy',
        'boost_free_kick_accuracy'
    ]

    columns = ['player_id', 'skill_id', 'level_number', 'positions'] + boost_columns
    placeholders = ', '.join(['%s'] * len(columns))
    columns_str = ', '.join(columns)

    # Skip duplicates - only insert new players
    query = f"""
    INSERT INTO skill_level_boosts ({columns_str})
    VALUES ({placeholders})
    ON CONFLICT (player_id, skill_id, level_number) DO NOTHING
    """

    values = []
    for row in batch:
        row_values = [
            row.get('player_id'),
            row.get('skill_id'),
            row.get('level_number'),
            row.get('positions', [])
        ]
        for col in boost_columns:
            row_values.append(row.get(col))

        values.append(tuple(row_values))

    try:
        execute_batch(cur, query, values, page_size=100)
    except Exception as e:
        print(f"Error inserting batch: {e}")
        raise


def process_skills_ijson(conn, json_file):
    """Process skills using ijson for memory-efficient streaming"""
    cur = conn.cursor()

    batch = []
    player_count = 0
    total_inserted = 0
    skipped = 0
    batch_size = 500
    seen_players = set()
    seen_keys = set()  # Track unique (player_id, skill_id, level) combinations

    print(f"Opening {json_file} with ijson streaming parser...")

    with open(json_file, 'rb') as f:
        parser = ijson.kvitems(f, '')

        for player_key, player_data in parser:
            player_id = player_data.get('player_id')
            if not player_id:
                continue

            player_count += 1
            if player_count % 100 == 0:
                print(f"Processed {player_count} players, {total_inserted} rows inserted...")

            skills_list = player_data.get('skills', {}).get('skills', [])
            skills_found = False

            for skill in skills_list:
                skill_id = skill.get('id')
                if not skill_id:
                    continue

                levels = skill.get('levels', [])
                for level_data in levels:
                    level_num = level_data.get('level')
                    positions = level_data.get('positions', [])
                    boosts = level_data.get('boosts', {})

                    unique_key = (player_id, skill_id, level_num)
                    if unique_key in seen_keys:
                        skipped += 1
                        continue

                    seen_keys.add(unique_key)
                    skills_found = True

                    row = {
                        'player_id': player_id,
                        'skill_id': skill_id,
                        'level_number': level_num,
                        'positions': positions
                    }

                    for boost_key, boost_value in boosts.items():
                        normalized_name = normalize_boost_name(boost_key)
                        column_name = f'boost_{normalized_name}'
                        row[column_name] = boost_value

                    batch.append(row)

                    if len(batch) >= batch_size:
                        insert_batch(cur, batch)
                        total_inserted += len(batch)
                        conn.commit()
                        batch = []

            if skills_found and player_id not in seen_players:
                print(f"✅ Player {player_id} skills imported successfully")
                seen_players.add(player_id)

    if batch:
        insert_batch(cur, batch)
        total_inserted += len(batch)
        conn.commit()

    cur.close()

    print("\n" + "=" * 60)
    print(f"✓ COMPLETE!")
    print(f"✓ Players processed: {player_count}")
    print(f"✓ Players with skills logged: {len(seen_players)}")
    print(f"✓ Total rows inserted: {total_inserted}")
    print(f"✓ Duplicates skipped: {skipped}")
    print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 append_skills.py <json_file>")
        print("Example: python3 append_skills.py players_skills_1.json")
        sys.exit(1)
    
    json_file = sys.argv[1]
    
    try:
        print("Connecting to database...")
        conn = psycopg2.connect(**DB_CONFIG)
        print("✓ Connected to database")
        
        # Check existing player count
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT player_id) FROM skill_level_boosts")
        before_count = cur.fetchone()[0]
        print(f"📊 Players in database before import: {before_count}")
        cur.close()

        process_skills_ijson(conn, json_file)
        
        # Check after import
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT player_id) FROM skill_level_boosts")
        after_count = cur.fetchone()[0]
        print(f"📊 Players in database after import: {after_count}")
        print(f"📊 New players added: {after_count - before_count}")
        cur.close()

        conn.close()
        print("✓ Database connection closed")

    except FileNotFoundError:
        print(f"❌ Error: {json_file} not found")
        sys.exit(1)
    except ImportError:
        print("❌ Error: ijson library not installed")
        print("Install it with: pip3 install ijson")
        sys.exit(1)
    except psycopg2.Error as e:
        print(f"❌ Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
