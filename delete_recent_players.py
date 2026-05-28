import os
import sys
import subprocess
import psycopg2
from datetime import datetime

# Database Configuration (Matches your project)
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'zenith_data',
    'user': 'zenith_bot',
    'password': 'zenith6Z@'
}

def get_pg_dump_path():
    """Find pg_dump executable dynamically"""
    paths_to_try = [
        "pg_dump",  # if it's in PATH
        r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\14\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\13\bin\pg_dump.exe",
    ]
    for path in paths_to_try:
        try:
            subprocess.run([path, "--version"], capture_output=True, check=True)
            return path
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None

def backup_db():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"backups/backup_before_delete_{timestamp}.sql"
    os.makedirs("backups", exist_ok=True)
    
    print(f"⏳ Taking a safety backup of the database to {backup_file}...")
    
    pg_dump_path = get_pg_dump_path()
    if not pg_dump_path:
        print("❌ Could not find pg_dump! Please add PostgreSQL bin to your PATH.")
        print("Aborting for safety.")
        sys.exit(1)
        
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_CONFIG['password']
    
    cmd = [
        pg_dump_path,
        "-U", DB_CONFIG['user'],
        "-h", DB_CONFIG['host'],
        "-p", str(DB_CONFIG['port']),
        "-d", DB_CONFIG['database'],
        "-F", "c", # custom compressed format
        "-f", backup_file
    ]
    
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
        print("✅ Backup completed successfully!\n")
        return backup_file
    except subprocess.CalledProcessError as e:
        print(f"❌ Backup failed! Error: {e.stderr.decode('utf-8', errors='ignore')}")
        print("Aborting deletion for safety.")
        sys.exit(1)

def main():
    print("="*60)
    print("ZENITH SCRAPER - BAD DATA CLEANUP UTILITY")
    print("="*60)
    
    # 1. Take Backup
    backup_db()
    
    # 2. Connect to DB and find bad players
    print("⏳ Connecting to database to identify bad data...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        player_ids_to_delete = set()
        
        # Strategy A: Find players added in the last 24 hours (if there is a timestamp column)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'player_stats' AND data_type LIKE '%timestamp%';")
        ts_cols = [row[0] for row in cur.fetchall()]
        
        if ts_cols:
            ts_col = ts_cols[0]
            print(f"   ✓ Found timestamp column '{ts_col}'. Finding players added in the last 24 hours...")
            cur.execute(f"SELECT DISTINCT player_id FROM player_stats WHERE {ts_col} >= NOW() - INTERVAL '24 HOURS';")
            for row in cur.fetchall():
                player_ids_to_delete.add(row[0])
        else:
            # Strategy B: If no timestamp, explicitly find the players affected by the bug
            print("   ℹ️ No timestamp column found. Filtering explicitly for players affected by the 'UNKNOWN' skill bug...")
            
            # Find players with empty skills or broken skillmovelogo in player_stats
            cur.execute("SELECT DISTINCT player_id FROM player_stats WHERE skills = '' OR skills LIKE '%skillmovelogo%';")
            for row in cur.fetchall():
                player_ids_to_delete.add(row[0])
                
            # Find players where unlock_requirement_skillname is UNKNOWN or 'skill'
            cur.execute("SELECT DISTINCT player_id FROM player_available_skills WHERE unlock_requirement_skillname IN ('UNKNOWN', 'skill', 'UNKNOWN_SKILL');")
            for row in cur.fetchall():
                player_ids_to_delete.add(row[0])
                
        if not player_ids_to_delete:
            print("✅ No recently added or bad players found! Your database is clean.")
            return

        print(f"\n🗑️  Found {len(player_ids_to_delete)} players to delete.")
        
        # Convert to list for the SQL IN clause
        player_ids_list = list(player_ids_to_delete)
        
        # 3. Delete from all tables
        tables = ['player_available_skills', 'skill_level_boosts', 'player_skills_meta', 'player_stats']
        
        for table in tables:
            # Verify the table has a player_id column
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}' AND column_name = 'player_id';")
            if cur.fetchone():
                query = f"DELETE FROM {table} WHERE player_id = ANY(%s);"
                cur.execute(query, (player_ids_list,))
                print(f"   ✓ Deleted {cur.rowcount} rows from {table}")
        
        conn.commit()
        print("\n✅ Cleanup complete! You can now safely run `python weekly_update.py`.")
        
    except Exception as e:
        print(f"\n❌ Error during cleanup: {e}")
        if 'conn' in locals():
            conn.rollback()

if __name__ == '__main__':
    main()
