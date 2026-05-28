import psycopg2
import sys

try:
    conn = psycopg2.connect(host='localhost', port=5432, database='zenith_data', user='zenith_bot', password='zenith6Z@')
    cur = conn.cursor()
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'player_stats';")
    print("--- player_stats columns ---")
    for row in cur.fetchall():
        print(row)
        
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'player_available_skills';")
    print("\n--- player_available_skills columns ---")
    for row in cur.fetchall():
        print(row)
except Exception as e:
    print(f"Error: {e}")
