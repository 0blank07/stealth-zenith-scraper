import os
import sys
import subprocess
import psycopg2
from datetime import datetime

# Database Configuration
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
        "pg_dump",
        r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\14\bin\pg_dump.exe",
    ]
    for path in paths_to_try:
        try:
            subprocess.run([path, "--version"], capture_output=True, check=True)
            return path
        except:
            continue
    return "pg_dump"

def backup_db():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"backups/backup_before_delete_{timestamp}.sql"
    os.makedirs("backups", exist_ok=True)
    
    print(f"⏳ Taking a safety backup of the database to {backup_file}...")
    pg_dump_path = get_pg_dump_path()
    
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_CONFIG['password']
    
    cmd = [
        pg_dump_path, "-U", DB_CONFIG['user'], "-h", DB_CONFIG['host'],
        "-p", str(DB_CONFIG['port']), "-d", DB_CONFIG['database'],
        "-F", "c", "-f", backup_file
    ]
    
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
        print("✅ Backup completed successfully!\n")
    except Exception as e:
        print(f"⚠️ Backup failed, but continuing... (Error: {e})")

def main():
    print("="*60)
    print("ZENITH SCRAPER - PRECISION 24HR CLEANUP")
    print("="*60)
    
    backup_db()
    
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Find all player IDs added in the last 24 hours using our new 'created_at' clock
        print("⏳ Searching for players added to the database in the last 24 hours...")
        
        cur.execute("""
            SELECT DISTINCT player_id 
            FROM player_stats 
            WHERE created_at >= NOW() - INTERVAL '24 HOURS';
        """)
        rows = cur.fetchall()
        player_ids_to_delete = [r[0] for r in rows]
        
        if not player_ids_to_delete:
            print("✅ No players found from the last 24 hours. Your database is clean!")
            return

        print(f"🗑️  Found {len(player_ids_to_delete)} players to delete. Proceeding...")
        
        # Delete from all tables
        tables = ['player_available_skills', 'skill_level_boosts', 'player_skills_meta', 'player_stats']
        for table in tables:
            cur.execute(f"DELETE FROM {table} WHERE player_id = ANY(%s);", (player_ids_to_delete,))
            print(f"   ✓ Deleted {cur.rowcount} rows from {table}")
        
        conn.commit()
        print(f"\n✅ Precision cleanup complete!")
        print("🚀 You can now run your update command for a fresh scrape.")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    main()
