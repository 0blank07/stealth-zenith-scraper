import ijson
import psycopg2
from psycopg2.extras import execute_batch
import sys

# Database Configuration
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'zenith_data',
    'user': 'zenith_bot',
    'password': 'zenith6Z@'
}

# Skill Points per Rank (Standard logic)
RANK_POINTS = { 0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5 }

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

def process_skills_tree(json_file):
    conn = get_db_connection()
    cur = conn.cursor()
    
    print(f"🚀 Starting import from {json_file}...")
    
    skills_batch = []
    meta_batch = []
    batch_size = 1000
    player_count = 0
    seen_players = set()
    
    # Query for inserting into player_available_skills
    skills_query = """
        INSERT INTO player_available_skills 
        (player_id, rank, training_level, skill_id, is_locked, 
         unlock_requirement_type, unlock_requirement_skillname, 
         unlock_requirement_level, unlock_requirement_text, 
         prerequisite_skill_id, prerequisite_level)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (player_id, rank, training_level, skill_id) 
        DO UPDATE SET 
            is_locked = EXCLUDED.is_locked,
            unlock_requirement_type = EXCLUDED.unlock_requirement_type,
            unlock_requirement_skillname = EXCLUDED.unlock_requirement_skillname,
            unlock_requirement_level = EXCLUDED.unlock_requirement_level,
            unlock_requirement_text = EXCLUDED.unlock_requirement_text,
            prerequisite_skill_id = EXCLUDED.prerequisite_skill_id,
            prerequisite_level = EXCLUDED.prerequisite_level
    """

    # Query for inserting into player_skills_meta
    meta_query = """
        INSERT INTO player_skills_meta 
        (player_id, rank, training_level, available_points)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (player_id, rank, training_level) 
        DO UPDATE SET available_points = EXCLUDED.available_points
    """

    try:
        with open(json_file, 'rb') as f:
            # Iterates over dictionary items since your JSON is {"id": {...}, "id2": {...}}
            parser = ijson.kvitems(f, '')
            
            for key, player in parser:
                # Try to get player_id from value, fallback to key
                player_id = player.get('player_id')
                if not player_id and key.isdigit():
                    player_id = int(key)
                
                if not player_id:
                    continue

                if player_id in seen_players:
                    continue
                seen_players.add(player_id)

                player_count += 1
                if player_count % 100 == 0:
                    print(f"Processed {player_count} players...")

                # 1. Process Metadata (Points per Rank)
                for rank in range(6):
                    training_level = 0 
                    points = RANK_POINTS.get(rank, 0)
                    meta_batch.append((player_id, rank, training_level, points))

                # 2. Process Skill Tree (Available Skills)
                skills_tree = player.get('skills', {}).get('skills', [])
                
                for skill_node in skills_tree:
                    skill_id = skill_node.get('id')
                    is_locked = skill_node.get('locked', False)
                    
                    # Parse Requirement (Single Object)
                    # Structure seen: "unlock_requirement": { "type": "...", "skill_name": "...", "required_level": 2, "text": "..." }
                    req = skill_node.get('unlock_requirement')
                    req_type, req_name, req_lvl, req_text = None, None, None, None
                    
                    if req:
                        req_type = req.get('type')
                        req_name = req.get('skill_name') # Snake_case
                        req_lvl = req.get('required_level') # Snake_case
                        req_text = req.get('text')
                    
                    # Parse Prerequisites (List)
                    prereqs = skill_node.get('prerequisites', [])
                    prereq_id = None
                    prereq_lvl = None
                    if prereqs:
                        p = prereqs[0] 
                        if isinstance(p, dict):
                            prereq_id = p.get('id')
                            prereq_lvl = p.get('level')
                        else:
                            prereq_id = p 

                    # Insert for all ranks
                    for rank in range(6):
                        skills_batch.append((
                            player_id, rank, 0, skill_id, is_locked,
                            req_type, req_name, req_lvl, req_text,
                            prereq_id, prereq_lvl
                        ))

                # Commit Batch
                if len(skills_batch) >= batch_size:
                    execute_batch(cur, skills_query, skills_batch)
                    execute_batch(cur, meta_query, meta_batch)
                    conn.commit()
                    skills_batch = []
                    meta_batch = []

            # Final Commit
            if skills_batch:
                execute_batch(cur, skills_query, skills_batch)
                execute_batch(cur, meta_query, meta_batch)
                conn.commit()

        print(f"✅ Success! Processed {player_count} players.")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 import_skill_tree_perfect.py <json_file>")
    else:
        process_skills_tree(sys.argv[1])

