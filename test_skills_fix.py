import json
from bs4 import BeautifulSoup
from stats_scrape import parse_player_page, parse_skills_from_javascript

# Read the HTML we captured live from RenderZ
with open('debug_html.txt', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

# Test parsing
player_data = parse_player_page(html, 19073376, 0, 0, save_skills_to_json_flag=False)

print("=== TEST RESULTS ===")
if player_data:
    print("\\n1. EXTRACTED SKILL URLs (for CSV):")
    urls = player_data['skills'].split(',') if player_data['skills'] else []
    print(f"Total Count: {len(urls)}")
    for u in urls:
        print(f" - {u.split('?')[0]}") # Print just the base URL for readability
else:
    print("Player parsing failed.")

print("\\n2. EXTRACTED SKILL NAMES (for JSON/DB):")
skills_json = parse_skills_from_javascript(html, soup)
if skills_json:
    data = json.loads(skills_json)
    print(f"Total Count: {len(data['skills'])}")
    for s in data['skills']:
        print(f" - Name: {s['name']}, Image: {s['image'].split('?')[0]}")
else:
    print("No skills JSON extracted.")
