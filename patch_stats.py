import sys
import re

with open('stats_scrape.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Block 1: Fix skill name extraction
old1 = """                skill_name = "UNKNOWN"
                if image_url and image_url in locked_skills_dict:
                    html_name = locked_skills_dict[image_url].get('skill_name')
                    if html_name:
                        skill_name = html_name.upper()

                if skill_name == "UNKNOWN" and image_url:
                    name_match = re.search(r'skill[_/]S\\d+[_/](.+?)[_/]\\d+', image_url)
                    if name_match:
                        skill_name = name_match.group(1).replace('_', ' ').upper()

                is_locked = False"""

new1 = """                skill_name = "UNKNOWN"
                if image_url:
                    # New URL format: skill_DEFENDING_2?verify=...
                    name_match = re.search(r'skill_([a-zA-Z_]+)_\\d+', image_url)
                    if name_match:
                        skill_name = name_match.group(1).replace('_', ' ').upper()
                    else:
                        # Fallback for old URL format
                        name_match = re.search(r'skill[_/]S\\d+[_/](.+?)[_/]\\d+', image_url)
                        if name_match:
                            skill_name = name_match.group(1).replace('_', ' ').upper()

                is_locked = False"""

# Block 2: Remove broken HTML container parsing
old2 = """        # 6. SKILLS
        skills_urls = []
        skills_container = soup.find('div', class_=lambda x: x and 'w-full rounded bg-surface-900 py-2' in str(x))
        if skills_container:
            skill_imgs = skills_container.find_all('img')
            for img in skill_imgs:
                src = img.get('src', '')
                if src and 'skill_' in src:
                    if src not in skills_urls:
                        skills_urls.append(src)
        player_data['skills'] = ",".join(skills_urls) if skills_urls else "" """

new2 = """        # 6. SKILLS
        # Skills are now extracted directly from JavaScript data in step 13 to avoid broken HTML containers
        player_data['skills'] = \"\" """

# Block 3: Populate skills correctly from JS object
old3 = """        # 13. SKILLS DATA
        if save_skills_to_json_flag:
            skills_data = parse_skills_from_javascript(html, soup)
            if skills_data:
                save_skills_to_json(player_id, rank, training_level, skills_data)"""

new3 = """        # 13. SKILLS DATA
        skills_data = parse_skills_from_javascript(html, soup)
        if skills_data:
            if save_skills_to_json_flag:
                save_skills_to_json(player_id, rank, training_level, skills_data)
            
            try:
                import json
                skills_dict = json.loads(skills_data)
                if 'skills' in skills_dict:
                    skills_urls = [s.get('image', '') for s in skills_dict['skills'] if s.get('image')]
                    player_data['skills'] = ",".join(skills_urls)
            except Exception as e:
                pass"""

if old1 in content and old2 in content and old3 in content:
    content = content.replace(old1, new1)
    content = content.replace(old2, new2)
    content = content.replace(old3, new3)
    with open('stats_scrape.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("stats_scrape.py patched successfully.")
else:
    print("Failed to find exact strings in stats_scrape.py for patching.")
