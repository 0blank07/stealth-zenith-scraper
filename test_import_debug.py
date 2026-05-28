import csv

csv_file = 'archive/2026-02-14_210138/players_stats_1.csv'

def normalize_column_name(name):
    return name.lower().replace(' ', '_').replace('-', '_').replace("'", "").strip()

with open(csv_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    csv_headers = reader.fieldnames
    
    print(f"CSV Headers count: {len(csv_headers)}")
    print(f"CSV Headers: {csv_headers[:10]}...")  # First 10
    
    db_columns = [normalize_column_name(h) for h in csv_headers if normalize_column_name(h)]
    print(f"\nDB Columns count: {len(db_columns)}")
    print(f"DB Columns: {db_columns[:10]}...")
    
    # Test first row
    first_row = next(reader)
    print(f"\nFirst row keys: {len(first_row.keys())}")
    print(f"First row values count: {len([v for v in first_row.values()])}")
    
    # Test the value extraction
    values = []
    for col in db_columns:
        csv_header = next((h for h in csv_headers if normalize_column_name(h) == col), None)
        val = first_row.get(csv_header, '') if csv_header else ''
        values.append(val)
    
    print(f"\nExtracted values count: {len(values)}")
    print(f"Expected: {len(db_columns)}")
    print(f"Match: {len(values) == len(db_columns)}")
