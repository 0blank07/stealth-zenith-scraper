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

def upgrade_database_schema(cur):
    """Add a 'created_at' clock to your database for the future"""
    tables = ['player_stats', 'player_available_skills', 'skill_level_boosts', 'player_skills_meta']
    print("⏳ Checking database schema for 'created_at' clock...")
    
    for table in tables:
        cur.execute(f"""
            SELECT COUNT(*) FROM information_schema.columns 
            WHERE table_name = '{table}' AND column_name = 'created_at';
        """)
        if cur.fetchone()[0] == 0:
            print(f"   ➕ Adding 'created_at' timestamp to {table}...")
            cur.execute(f"ALTER TABLE {table} ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();")
    print("✅ Database schema upgraded. All future scrapes will be timestamped!\n")

def main():
    print("="*60)
    print("ZENITH SCRAPER - CLEANUP & DATABASE UPGRADE")
    print("="*60)
    
    backup_db()
    
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # 1. First, upgrade the DB so this never happens again
        upgrade_database_schema(cur)
        conn.commit()
        
        # 2. Find the latest 'date_added' text in the database
        print("⏳ Finding the most recently added players based on RenderZ date...")
        cur.execute("SELECT date_added FROM player_stats WHERE date_added IS NOT NULL AND date_added != '' ORDER BY id DESC LIMIT 1;")
        row = cur.fetchone()
        
        if not row:
            print("❌ No players found in the database to delete.")
            return

        latest_date_text = row[0]
        print(f"   📅 Latest date found: '{latest_date_text}'")
        
        # 3. Get all player IDs matching that exact date text
        cur.execute("SELECT DISTINCT player_id FROM player_stats WHERE date_added = %s;", (latest_date_text,))
        player_ids_to_delete = [r[0] for row in cur.fetchall() for r in [row]] # wait, the fetchall loop was wrong
        
        # Let's fix that query loop
        cur.execute("SELECT DISTINCT player_id FROM player_stats WHERE date_added = %s;", (latest_date_text,))
        rows = cur.fetchall()
        player_ids_to_delete = [r[0] for r in rows]
        
        if not player_ids_to_delete:
            print("✅ No players matching that date found.")
            return

        print(f"🗑️  Found {len(player_ids_to_delete)} players added on {latest_date_text}. Deleting...")
        
        # 4. Delete from all tables
        tables = ['player_available_skills', 'skill_level_boosts', 'player_skills_meta', 'player_stats']
        for table in tables:
            cur.execute(f"DELETE FROM {table} WHERE player_id = ANY(%s);", (player_ids_to_delete,))
            print(f"   ✓ Deleted {cur.rowcount} rows from {table}")
        
        conn.commit()
        print(f"\n✅ Cleanup of '{latest_date_text}' complete!")
        print("🚀 You can now run `xvfb-run python3 weekly_update.py` for a fresh scrape.")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    main()
