import psycopg2
import sys

try:
    conn = psycopg2.connect(host='localhost', port=5432, database='zenith_data', user='zenith_bot', password='zenith6Z@')
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_name = 'skills';")
    print('Table skills exists:', cur.fetchone() is not None)
    
    if True: # Let's also check 'skill_definitions' or similar
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'skill%';")
        print("\nAll skill tables:")
        for r in cur.fetchall():
            print(f" - {r[0]}")
except Exception as e:
    print(f"Error: {e}")
