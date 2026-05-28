#!/usr/bin/env python3
"""
stats_scrape.py - Enhanced Cloudflare-Aware Player Stats Scraper
Scrapes player stats and skills from Renderz with intelligent rate limiting.

Features:
- Cloudflare detection and adaptive batching
- Checkpoint/resume system
- Human-like request patterns
- Exponential backoff on errors
- Real-time progress tracking

Usage:
    python3 stats_scrape.py                    # Normal mode
    python3 stats_scrape.py --resume           # Resume from checkpoint
    SCRAPER_NUM=1 python3 stats_scrape.py      # Parallel mode (set by orchestrator)
"""

import sys
import os
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Optional
import re
import csv
import json
import requests
import random
import time
from collections import deque

# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_URL = "https://renderz.app/24/player/"

# Get scraper number from environment variable (set by run_scrapers.py)
SCRAPER_NUM = os.environ.get('SCRAPER_NUM', '1')

# Input CSV - can be overridden by environment variable
ASSET_IDS_CSV = os.environ.get('ASSET_IDS_CSV', "final_missing_ids.csv")

# Output files
CSV_OUTPUT = f"players_stats_{SCRAPER_NUM}.csv"
SKILLS_JSON_OUTPUT = f"players_skills_{SCRAPER_NUM}.json"
FAILED_IDS_FILE = f"failed_stats_{SCRAPER_NUM}.txt"
CHECKPOINT_FILE = f"checkpoint_{SCRAPER_NUM}.json"

# Supabase configuration (for database player ID fetching - optional)
SUPABASE_URL = "https://ugszalubwvartwalsejx.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVnc3phbHVid3ZhcnR3YWxzZWp4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1ODY1ODgzOSwiZXhwIjoyMDc0MjM0ODM5fQ.slNT1R_wiGqzhgBv-eH8TCKggcobrXl4quI1da2D5KY"
SUPABASE_TABLE = "all_cards"

# Rank configuration
MIN_RANK = 0
MAX_RANK = 5

# ADAPTIVE SCRAPING PARAMETERS (Cloudflare-safe)
INITIAL_BATCH_SIZE = 10        # Conservative start
MIN_BATCH_SIZE = 3             # Minimum when rate limited
MAX_BATCH_SIZE = 15            # Maximum (don't go higher for Cloudflare)
BATCH_DELAY = 0.0              # Base delay between batches
REQUEST_TIMEOUT = 30           # Max wait per request
MAX_RETRIES = 3                # Retry attempts per request

# Cloudflare detection thresholds
SLOW_RESPONSE_THRESHOLD = 3.0  # seconds - if avg response > this, reduce batch
FAST_RESPONSE_THRESHOLD = 1.0  # seconds - if avg response < this, can increase batch

# Human-like behavior
MIN_REQUEST_DELAY = 0.05       # Minimum delay between requests
MAX_REQUEST_DELAY = 0.20       # Maximum delay between requests
CHECKPOINT_INTERVAL = 10       # Save progress every N players

# User-Agent rotation (looks more human)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

# CSV field definitions
CSV_FIELDS = [
    "player_id", "rank", "training_level", "name", "position", "alternate_position", "team", "league", "nation_region",
    "skill_moves_stars", "strong_foot_side", "strong_foot_stars", "weak_foot_stars",
    "height_ft_in", "height_cm", "weight_kg",
    "work_rate_attack", "work_rate_defense", "date_added",
    "ovr", "stamina_stat",
    "pace", "acceleration", "sprint_speed",
    "shooting", "finishing", "long_shot", "shot_power", "positioning", "volley", "penalties",
    "passing", "short_passing", "long_passing", "vision", "crossing", "curve", "free_kick",
    "dribbling_head", "dribbling", "balance", "agility", "reactions", "ball_control",
    "defending", "marking", "standing_tackle", "sliding_tackle", "awareness", "heading",
    "physical", "strength", "aggression", "jumping",
    "diving", "gk_diving", "gk_positioning", "handling", "gk_handling", "reflexes", "gk_reflexes", "kicking", "gk_kicking",
    "league_image",
    "skills", "traits", "traits_name", "event", "is_untradable",
    "player_image", "card_background", "nation_flag", "club_flag",
]

# Skill stat abbreviation mapping
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

# Global statistics
total_scraped = 0
total_failed = 0
failed_ids = []

# Performance tracking
response_times = deque(maxlen=50)  # Track last 50 response times
current_batch_size = INITIAL_BATCH_SIZE


def format_time(seconds):
    """Format seconds into readable time string"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


# ============================================================================
# LOGGING AND PROGRESS
# ============================================================================

def log(message, level="INFO"):
    """Simple logging with timestamp"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "WARNING": "⚠️ ",
        "ERROR": "❌",
        "DEBUG": "🔍"
    }.get(level, "  ")
    
    print(f"[{timestamp}] {prefix} {message}", flush=True)

def log_progress(current, total, player_name=""):
    """Log scraping progress"""
    percentage = (current / total * 100) if total > 0 else 0
    player_info = f" ({player_name})" if player_name else ""
    log(f"Progress: {current}/{total} players ({percentage:.1f}%){player_info}")

# ============================================================================
# CHECKPOINT SYSTEM
# ============================================================================

def load_checkpoint():
    """Load checkpoint from previous run"""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                checkpoint = json.load(f)
                log(f"Checkpoint loaded: {checkpoint['completed']} players already scraped", "SUCCESS")
                return checkpoint
        except Exception as e:
            log(f"Failed to load checkpoint: {e}", "WARNING")
    return None

def save_checkpoint(completed_ids, failed_ids, total):
    """Save checkpoint for resume capability"""
    try:
        checkpoint = {
            'timestamp': datetime.now().isoformat(),
            'completed': len(completed_ids),
            'failed': len(failed_ids),
            'total': total,
            'completed_ids': completed_ids,
            'failed_ids': failed_ids
        }
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump(checkpoint, f)
    except Exception as e:
        log(f"Failed to save checkpoint: {e}", "WARNING")

def clear_checkpoint():
    """Remove checkpoint file after successful completion"""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            os.remove(CHECKPOINT_FILE)
        except:
            pass

# ============================================================================
# CLOUDFLARE DETECTION AND ADAPTIVE RATE LIMITING
# ============================================================================

def update_response_time(duration):
    """Track response time for adaptive rate limiting"""
    global response_times
    response_times.append(duration)

def get_avg_response_time():
    """Calculate average response time from recent requests"""
    if not response_times:
        return 0
    return sum(response_times) / len(response_times)

def adjust_batch_size():
    """Dynamically adjust batch size based on response times"""
    global current_batch_size
    
    avg_time = get_avg_response_time()
    
    if avg_time > SLOW_RESPONSE_THRESHOLD:
        # Slow responses detected - reduce batch size (Cloudflare likely)
        old_size = current_batch_size
        current_batch_size = max(MIN_BATCH_SIZE, current_batch_size - 2)
        if old_size != current_batch_size:
            log(f"Cloudflare detected (avg {avg_time:.1f}s) - reducing batch size to {current_batch_size}", "WARNING")
        return True  # Cloudflare detected
    
    elif avg_time < FAST_RESPONSE_THRESHOLD and current_batch_size < MAX_BATCH_SIZE:
        # Fast responses - can safely increase batch size
        old_size = current_batch_size
        current_batch_size = min(MAX_BATCH_SIZE, current_batch_size + 1)
        if old_size != current_batch_size:
            log(f"Fast responses (avg {avg_time:.1f}s) - increasing batch size to {current_batch_size}", "INFO")
        return False
    
    return False

async def smart_delay():
    """Add human-like random delay between requests"""
    delay = random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY)
    await asyncio.sleep(delay)

# ============================================================================
# PLAYER ID LOADING
# ============================================================================

def get_player_ids_from_csv() -> List[int]:
    """Load player IDs from CSV file"""
    log(f"Fetching asset_ids from {ASSET_IDS_CSV}...")
    asset_ids = []
    try:
        with open(ASSET_IDS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("asset_id"):
                    asset_ids.append(int(row["asset_id"]))
        asset_ids = sorted(set(asset_ids))
        log(f"Found {len(asset_ids):,} unique asset_ids", "SUCCESS")
        return asset_ids
    except Exception as e:
        log(f"Error reading {ASSET_IDS_CSV}: {e}", "ERROR")
        return []

def get_existing_player_rank_combinations() -> set:
    """Load already scraped player_id + rank combinations from CSV"""
    existing_combinations = set()
    if os.path.exists(CSV_OUTPUT):
        try:
            with open(CSV_OUTPUT, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if 'player_id' in row and row['player_id'] and 'rank' in row:
                        pid = int(row['player_id'])
                        rank = int(row['rank']) if row['rank'] else 0
                        existing_combinations.add((pid, rank))
            log(f"Found {len(existing_combinations)} already scraped player-rank combinations", "INFO")
        except Exception as e:
            log(f"Could not read existing CSV: {e}", "WARNING")
    return existing_combinations

# ============================================================================
# JSON SKILLS SAVING
# ============================================================================

def save_skills_to_json(player_id: int, rank: int, training_level: int, skills_data: str):
    """Save skills data with atomic write protection"""
    if not skills_data or not skills_data.strip():
        return

    try:
        skills_dict = json.loads(skills_data)
    except json.JSONDecodeError:
        return

    if not skills_dict or 'skills' not in skills_dict or not skills_dict['skills']:
        return

    try:
        # Load existing JSON
        if os.path.exists(SKILLS_JSON_OUTPUT):
            try:
                with open(SKILLS_JSON_OUTPUT, 'r', encoding='utf-8') as f:
                    all_skills = json.load(f)
            except json.JSONDecodeError:
                all_skills = {}
        else:
            all_skills = {}

        key = f"{player_id}_R{rank}_L{training_level}"
        all_skills[key] = {
            'player_id': player_id,
            'rank': rank,
            'training_level': training_level,
            'available_points': rank,
            'skills': skills_dict
        }

        # Atomic write with temp file
        import tempfile
        json_path = os.path.abspath(SKILLS_JSON_OUTPUT)
        target_dir = os.path.dirname(json_path)

        fd, temp_path = tempfile.mkstemp(
            dir=target_dir,
            prefix='.tmp_',
            suffix='.json'
        )

        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(all_skills, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, json_path)
        except:
            try:
                os.unlink(temp_path)
            except:
                pass
            raise

    except Exception as e:
        log(f"Save error for player {player_id} R{rank}: {e}", "ERROR")

# ============================================================================
# HTML PARSING FUNCTIONS (from original script)
# ============================================================================

def parse_unlock_requirement(requirement_text: str) -> Optional[Dict]:
    """Parse unlock requirement text into structured format"""
    if not requirement_text or "Unlocks" not in requirement_text:
        return None

    try:
        match = re.search(r'Unlocks after (.+?) is LVL(\d+)', requirement_text, re.IGNORECASE)
        if match:
            return {
                'type': 'skill_level',
                'skill_name': match.group(1).strip(),
                'required_level': int(match.group(2)),
                'text': requirement_text
            }

        match = re.search(r'Unlocks at Rank (\d+)', requirement_text, re.IGNORECASE)
        if match:
            return {
                'type': 'rank',
                'required_rank': int(match.group(1)),
                'text': requirement_text
            }

        return {
            'type': 'other',
            'text': requirement_text
        }
    except:
        return None

def detect_locked_skills(soup: BeautifulSoup) -> Dict[str, Dict]:
    """Detect which skills are locked from HTML and extract skill names"""
    locked_skills = {}

    try:
        skill_buttons = soup.find_all('button', class_=lambda x: x and 'flex w-full flex-col' in str(x))

        for button in skill_buttons:
            try:
                img = button.find('img')
                if not img or not img.get('src'):
                    continue

                skill_image = img.get('src', '')

                skill_name = None
                name_span = button.find('span', class_=lambda x: x and 'text-gray' in str(x) and 'pb-2' in str(x) and 'text-center' in str(x))
                if name_span:
                    skill_name = name_span.get_text(strip=True)

                is_locked = False
                unlock_text = None

                button_classes = button.get('class', [])
                if 'opacity-60' in button_classes:
                    is_locked = True

                lock_icon = button.find('svg')
                if lock_icon and 'M144 144v48H304V144c0-44.2' in str(lock_icon):
                    is_locked = True

                italic_span = button.find('span', class_=lambda x: x and 'italic' in str(x))
                if italic_span:
                    unlock_text = italic_span.get_text(strip=True)
                    if unlock_text:
                        is_locked = True

                if skill_image:
                    locked_skills[skill_image] = {
                        'locked': is_locked,
                        'unlock_requirement_text': unlock_text,
                        'skill_name': skill_name
                    }

            except:
                continue

    except:
        pass

    return locked_skills

def parse_skills_from_javascript(html: str, soup: BeautifulSoup) -> str:
    """Extract skills data from JavaScript and merge with HTML lock status"""
    try:
        locked_skills_dict = detect_locked_skills(soup)

        match = re.search(r'skillsData:\s*(\[.*?\])(?=\s*,(?:priceData|auctionable))', html, re.DOTALL)
        if not match:
            return ""

        skills_data_str = match.group(1)
        processed_skills = []
        parts = skills_data_str.split('},{skill:')

        for idx, part in enumerate(parts):
            if idx == 0:
                part = part.lstrip('[{')
            else:
                part = 'skill:' + part
            if idx == len(parts) - 1:
                part = part.rstrip('}]')

            try:
                skill_id_match = re.search(r'id:(\d+)', part)
                skill_id = int(skill_id_match.group(1)) if skill_id_match else 0

                image_match = re.search(r'image:"([^"]+)"', part)
                image_url = image_match.group(1) if image_match else ""

                skill_name = "UNKNOWN"
                if image_url:
                    # New URL format: skill_DEFENDING_2?verify=...
                    name_match = re.search(r'skill_([a-zA-Z_]+)_\d+', image_url)
                    if name_match:
                        skill_name = name_match.group(1).replace('_', ' ').upper()
                    else:
                        # Fallback for old URL format: skill/S24/NAME/2
                        name_match = re.search(r'skill[_/]S\d+[_/](.+?)[_/]\d+', image_url)
                        if name_match:
                            skill_name = name_match.group(1).replace('_', ' ').upper()

                is_locked = False
                unlock_requirement = None

                if image_url in locked_skills_dict:
                    lock_info = locked_skills_dict[image_url]
                    is_locked = lock_info['locked']
                    if lock_info['unlock_requirement_text']:
                        unlock_requirement = parse_unlock_requirement(lock_info['unlock_requirement_text'])

                levels_match = re.search(r'levels:\[(.+?)\](?=\s*\})', part, re.DOTALL)
                if not levels_match:
                    continue

                levels_str = levels_match.group(1)
                processed_levels = []

                level_objs = re.findall(r'\{id:\d+,level:\d+,unlockedPositions:\[[^\]]*\],abilityModifiers:\{[^}]+\}\}', levels_str)

                for level_obj in level_objs:
                    level_match = re.search(r'level:(\d+)', level_obj)
                    if not level_match:
                        continue
                    level_num = int(level_match.group(1))

                    pos_match = re.search(r'unlockedPositions:\[([^\]]*)\]', level_obj)
                    positions = []
                    if pos_match and pos_match.group(1):
                        positions = re.findall(r'"([^"]+)"', pos_match.group(1))

                    mods_match = re.search(r'abilityModifiers:\{([^}]+)\}', level_obj)
                    boosts = {}
                    if mods_match:
                        for stat_match in re.finditer(r'(\w+):(\d+)', mods_match.group(1)):
                            abbr = stat_match.group(1)
                            val = int(stat_match.group(2))
                            full_name = SKILL_STAT_MAPPING.get(abbr.lower(), abbr)
                            boosts[full_name] = val

                    if boosts:
                        processed_levels.append({
                            'level': level_num,
                            'positions': positions,
                            'boosts': boosts
                        })

                js_requirement = None
                if 'requirement:null' not in part:
                    req_match = re.search(r'requirement:\{skillId:(\d+),level:(\d+)\}', part)
                    if req_match:
                        js_requirement = {
                            'skill_id': int(req_match.group(1)),
                            'level': int(req_match.group(2))
                        }

                if processed_levels:
                    processed_skills.append({
                        'id': skill_id,
                        'name': skill_name,
                        'image': image_url,
                        'locked': is_locked,
                        'unlock_requirement': unlock_requirement,
                        'prerequisite': js_requirement,
                        'levels': processed_levels
                    })

            except Exception as e:
                continue

        if processed_skills:
            return json.dumps({'skills': processed_skills}, ensure_ascii=False)
        return ""

    except Exception as e:
        return ""

def parse_player_page(html: str, player_id: int, rank: int, training_level: int, save_skills_to_json_flag: bool = True) -> Optional[Dict]:
    """Parse complete player data from HTML"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        player_data = {"player_id": player_id, "rank": rank, "training_level": training_level}

        # 1. NAME
        title = soup.find('title')
        if title:
            name_text = title.get_text(strip=True)
            name = name_text.split('FC Mobile')[0].replace('- RenderZ', '').replace('|', '').strip()
            player_data['name'] = name
        else:
            player_data['name'] = ""

        # 2. OVR
        rating_div = soup.find('div', class_=re.compile('rating'))
        if rating_div:
            ovr_text = rating_div.get_text(strip=True)
            numbers = re.findall(r'\d+', ovr_text)
            for num in numbers:
                if 40 <= int(num) <= 150:
                    player_data['ovr'] = num
                    break
        if 'ovr' not in player_data:
            player_data['ovr'] = ""

        # 3. POSITION
        position_div = soup.find('div', class_='position')
        if position_div:
            player_data['position'] = position_div.get_text(strip=True)
        else:
            player_data['position'] = ""

        # 4. Details
        details = soup.find_all('div', class_=re.compile('details-list-item'))
        for detail in details:
            text = detail.get_text(" ", strip=True)

            if 'ALTERNATE POSITION' in text.upper():
                spans = detail.find_all('span')
                if len(spans) >= 2:
                    alt_pos = spans[1].get_text(strip=True)
                    player_data['alternate_position'] = alt_pos

            if 'TEAM' in text.upper() and 'team' not in player_data:
                parts = text.split()
                if len(parts) > 1:
                    for i, part in enumerate(parts):
                        if 'TEAM' in part.upper():
                            player_data['team'] = ' '.join(parts[i + 1:])
                            break

            if 'LEAGUE' in text.upper() and 'league' not in player_data:
                parts = text.split()
                if len(parts) > 1:
                    for i, part in enumerate(parts):
                        if 'LEAGUE' in part.upper():
                            player_data['league'] = ' '.join(parts[i + 1:])
                            break

            if ('NATION' in text.upper() or 'REGION' in text.upper()) and 'nation_region' not in player_data:
                parts = text.split()
                if len(parts) > 1:
                    for i, part in enumerate(parts):
                        if 'NATION' in part.upper() or 'REGION' in part.upper():
                            player_data['nation_region'] = ' '.join(parts[i + 1:])
                            break

            if 'SKILL MOVES' in text.upper():
                match = re.search(r'\((\d)\)', text)
                if match:
                    player_data['skill_moves_stars'] = match.group(1)

            if 'STRONG FOOT' in text.upper() and 'WEAK FOOT' in text.upper():
                foot_match = re.search(r'(LEFT|RIGHT)\s*/\s*\((\d)\)', text, re.IGNORECASE)
                if foot_match:
                    player_data['strong_foot_side'] = foot_match.group(1).upper()
                    player_data['weak_foot_stars'] = foot_match.group(2)
                    player_data['strong_foot_stars'] = "5"
                else:
                    player_data['strong_foot_side'] = "RIGHT"
                    player_data['strong_foot_stars'] = "5"
                    player_data['weak_foot_stars'] = "3"

            if 'HEIGHT' in text.upper():
                match = re.search(r"(\d)'(\d{1,2}).*?(\d+)\s*cm", text)
                if match:
                    player_data['height_ft_in'] = f"{match.group(1)}'{match.group(2)}\""
                    player_data['height_cm'] = match.group(3)

            if 'WEIGHT' in text.upper():
                match = re.search(r'(\d+)\s*kg', text)
                if match:
                    player_data['weight_kg'] = match.group(1)

            if 'WORK RATE' in text.upper():
                match = re.search(r'(\w+)\s*/\s*(\w+)', text)
                if match:
                    player_data['work_rate_attack'] = match.group(1)
                    player_data['work_rate_defense'] = match.group(2)

            if 'ADDED ON' in text.upper():
                match = re.search(r'Added on\s+(.+)', text, re.I)
                if match:
                    player_data['date_added'] = match.group(1).strip()

        for key in ['alternate_position', 'team', 'league', 'nation_region', 'skill_moves_stars',
                'strong_foot_side', 'weak_foot_stars', 'height_ft_in', 'height_cm',
                'weight_kg', 'work_rate_attack', 'work_rate_defense', 'date_added', 'league_image']:
            if key not in player_data:
                player_data[key] = ""

        if 'strong_foot_stars' not in player_data or player_data['strong_foot_stars'] == "":
            player_data['strong_foot_stars'] = "5"

        # 5. NUMERIC STATS
        stat_names = soup.find_all('span', class_=re.compile('player-stat-name'))
        stat_values = soup.find_all('span', class_=re.compile('player-stat-value'))

        stats_dict = {}
        for i, name_elem in enumerate(stat_names):
            if i < len(stat_values):
                stat_name = name_elem.get_text(strip=True).lower().replace(' ', '_')
                stat_value = stat_values[i].get_text(strip=True)
                if stat_value.isdigit():
                    stats_dict[stat_name] = stat_value

        stat_mapping = {
            "pace": "pace", "acceleration": "acceleration", "sprint_speed": "sprint_speed",
            "shooting": "shooting", "finishing": "finishing", "long_shot": "long_shot",
            "shot_power": "shot_power", "positioning": "positioning", "volley": "volley",
            "penalties": "penalties", "passing": "passing", "short_passing": "short_passing",
            "long_passing": "long_passing", "vision": "vision", "crossing": "crossing",
            "curve": "curve", "free_kick": "free_kick", "dribbling": "dribbling",
            "balance": "balance", "agility": "agility", "reactions": "reactions",
            "ball_control": "ball_control", "defending": "defending", "marking": "marking",
            "standing_tackle": "standing_tackle", "sliding_tackle": "sliding_tackle",
            "awareness": "awareness", "heading": "heading", "physical": "physical",
            "strength": "strength", "aggression": "aggression", "jumping": "jumping",
            "stamina_stat": "stamina",
            "diving": "diving", "gk_diving": "gk_diving", "gk_positioning": "gk_positioning",
            "handling": "handling", "gk_handling": "gk_handling", "reflexes": "reflexes",
            "gk_reflexes": "gk_reflexes", "kicking": "kicking", "gk_kicking": "gk_kicking"
        }

        for csv_field, html_stat_key in stat_mapping.items():
            player_data[csv_field] = stats_dict.get(html_stat_key, "")

        # 6. SKILLS
        skills_urls = []
        skills_container = soup.find('div', class_=lambda x: x and 'w-full rounded bg-surface-900 py-2' in str(x))
        if skills_container:
            skill_imgs = skills_container.find_all('img')
            for img in skill_imgs:
                src = img.get('src', '')
                if src and 'skill_' in src:
                    if src not in skills_urls:
                        skills_urls.append(src)
        player_data['skills'] = ",".join(skills_urls) if skills_urls else ""

        # 7. TRAITS
        traits_urls = []
        traits_container = soup.find('div', class_=lambda x: x and 'flex gap-2 w-full flex-wrap justify-center pb-4' in str(x))
        if traits_container:
            all_imgs = traits_container.find_all('img')
            for img in all_imgs:
                src = img.get('src', '')
                if src and ('logo' in src):
                    if src not in traits_urls:
                        traits_urls.append(src)
        player_data['traits'] = ",".join(traits_urls) if traits_urls else ""

        # 7.2 traits_name
        traits_list = []
        traits_container = soup.find('div', class_=lambda c: c and 'flex-wrap' in c and 'pb-4' in c)
        if traits_container:
            spans = traits_container.find_all('span', class_=lambda c: c and 'bg-surface-800' in c)
            for span in spans:
                name = span.get_text(strip=True)
                if name and name not in traits_list:
                    traits_list.append(name)
        player_data['traits_name'] = ", ".join(traits_list) if traits_list else ""

        # 7.3 Dribbling head
        avg_stats = soup.find_all("div", class_=lambda c: c and 'avg-stat' in str(c))

        top_stats = {}
        for stat in avg_stats:
            name_el = stat.find("span", class_=lambda c: c and 'player-stat-name' in str(c))
            value_el = stat.find("span", class_=lambda c: c and 'player-stat-value' in str(c))
            if name_el and value_el:
                key = name_el.get_text(strip=True).strip().lower()
                val = value_el.get_text(strip=True).strip()
                top_stats[key] = val

        player_data["dribbling_head"] = top_stats.get("dribbling", "")

        # 8-11. Images
        player_img = soup.find('img', class_='action-shot')
        if player_img:
            player_data['player_image'] = player_img.get('src', '')
        else:
            player_data['player_image'] = ""

        bg_img = soup.find('img', class_='background')
        if bg_img:
            player_data['card_background'] = bg_img.get('src', '')
        else:
            player_data['card_background'] = ""

        nation_img = soup.find('img', class_='nation')
        if nation_img:
            player_data['nation_flag'] = nation_img.get('src', '')
        else:
            player_data['nation_flag'] = ""

        club_img = soup.find('img', alt='Club')
        if club_img:
            player_data['club_flag'] = club_img.get('src', '')
        else:
            player_data['club_flag'] = ""

        # 12. LEAGUE IMAGE
        league_img = soup.find('img', class_='league')
        if league_img:
            player_data['league_image'] = league_img.get('src', '')
        else:
            player_data['league_image'] = ""

        # 13. SKILLS DATA
        if save_skills_to_json_flag:
            skills_data = parse_skills_from_javascript(html, soup)
            if skills_data:
                save_skills_to_json(player_id, rank, training_level, skills_data)

        # 14. EVENT
        event_name = "Unknown"
        event_span = soup.find('span', class_='text-white text-sm text-center')
        if event_span:
            event_text = event_span.get_text(strip=True)
            if event_text:
                event_name = event_text
        player_data['event'] = event_name

        # 15. IS_UNTRADABLE
        is_untradable = ""
        market_data_div = soup.find('div', class_='market-data')
        if market_data_div:
            span_text = market_data_div.get_text(strip=True).lower()
            if 'not auctionable' in span_text:
                is_untradable = "True"
            noauction_img = market_data_div.find('img', alt=lambda x: x and 'not auctionable' in x.lower())
            if noauction_img:
                is_untradable = "True"
        player_data['is_untradable'] = is_untradable

        if not player_data['name']:
            return None

        return player_data

    except Exception as e:
        log(f"Parse error for player {player_id} R{rank}: {e}", "ERROR")
        return None

def is_valid_player(player_data: Optional[Dict]) -> bool:
    """Check if scraped player data is valid"""
    if not player_data:
        return False
    if player_data.get('name') == 'Filter Players  RenderZ':
        return False
    if not player_data.get('name') or player_data.get('name').strip() == '':
        return False
    if not player_data.get('position') and not player_data.get('ovr'):
        return False
    return True

# ============================================================================
# ASYNC SCRAPING FUNCTIONS
# ============================================================================

async def fetch_player_level(session: aiohttp.ClientSession, player_id: int, rank: int,
                             training_level: int, semaphore: asyncio.Semaphore, save_skills: bool = True) -> Optional[Dict]:
    """Fetch and parse player at specific rank and training level with retry logic"""
    url = f"{BASE_URL}{player_id}?rankUp={rank}&level={training_level}"
    
    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            start_time = time.time()
            
            try:
                # Random user agent for each request
                headers = {"User-Agent": random.choice(USER_AGENTS)}
                
                async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT) as response:
                    duration = time.time() - start_time
                    update_response_time(duration)
                    
                    if response.status == 404:
                        return None

                    if response.status != 200:
                        if attempt < MAX_RETRIES:
                            wait_time = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
                            log(f"HTTP {response.status} for player {player_id} R{rank} - retry {attempt}/{MAX_RETRIES} after {wait_time}s", "WARNING")
                            await asyncio.sleep(wait_time)
                            continue
                        return None

                    html = await response.text()
                    player_data = parse_player_page(html, player_id, rank, training_level, save_skills)
                    
                    if player_data:
                        return player_data

                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(1)
                        continue

            except asyncio.TimeoutError:
                log(f"Timeout for player {player_id} R{rank} (attempt {attempt})", "WARNING")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None
            except Exception as e:
                log(f"Error for player {player_id} R{rank}: {type(e).__name__} (attempt {attempt})", "WARNING")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None

    return None

async def scrape_all_ranks_for_player(session: aiohttp.ClientSession, player_id: int,
    existing_rank_combinations: set,
    semaphore: asyncio.Semaphore) -> List[Dict]:
    """Scrape all ranks for a player with fail-fast validation"""
    results = []

    # Determine which ranks need scraping
    ranks_to_scrape = [
        rank for rank in range(MIN_RANK, MAX_RANK + 1)
        if (player_id, rank) not in existing_rank_combinations
    ]

    # FAIL-FAST: Validate player exists
    test_data = await fetch_player_level(session, player_id, 0, 0, semaphore, save_skills=False)
    if not is_valid_player(test_data):
        return []

    # Scrape missing ranks
    if ranks_to_scrape:
        for idx, rank in enumerate(ranks_to_scrape):
            player_data = await fetch_player_level(session, player_id, rank, 0, semaphore)

            # Fail-fast check on first rank
            if idx == 0 and not is_valid_player(player_data):
                return []

            if player_data:
                results.append(player_data)

            # Human-like delay
            await smart_delay()

    return results

# ============================================================================
# CSV WRITING
# ============================================================================

def write_batch_to_csv(batch: List[Dict], mode='a'):
    """Write a batch of player data to CSV"""
    try:
        with open(CSV_OUTPUT, mode, newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if mode == 'w':
                writer.writeheader()
            if batch:  # Only write rows if batch has data
                writer.writerows(batch)
    except Exception as e:
        log(f"CSV write error: {e}", "ERROR")


def save_failed_ids(failed_ids: List[int]):
    """Save failed IDs to text file"""
    try:
        with open(FAILED_IDS_FILE, 'w') as f:
            for pid in sorted(failed_ids):
                f.write(f"{pid}\n")
        log(f"Saved {len(failed_ids)} failed IDs to {FAILED_IDS_FILE}", "INFO")
    except Exception as e:
        log(f"Failed to save failed IDs: {e}", "ERROR")

# ============================================================================
# MAIN SCRAPER
# ============================================================================

async def main():
    """Main scraping function"""
    global total_scraped, total_failed, failed_ids, current_batch_size

    log("="*70)
    log(f"RENDERZ SCRAPER - CLOUDFLARE-AWARE (Instance #{SCRAPER_NUM})")
    log("="*70)

    # Check for resume
    resume_mode = '--resume' in sys.argv
    checkpoint = None
    completed_ids = []
    
    if resume_mode:
        checkpoint = load_checkpoint()
        if checkpoint:
            completed_ids = checkpoint.get('completed_ids', [])
            failed_ids = checkpoint.get('failed_ids', [])

    # Load existing combinations
    existing_rank_combinations = set()
    
    # Check CSV for existing data
    if os.path.exists(CSV_OUTPUT):
        try:
            with open(CSV_OUTPUT, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if 'player_id' in row and row['player_id'] and 'rank' in row:
                        pid = int(row['player_id'])
                        rank = int(row['rank']) if row['rank'] else 0
                        existing_rank_combinations.add((pid, rank))
            log(f"Found {len(existing_rank_combinations)//6} players already in CSV", "INFO")
        except Exception as e:
            log(f"Could not read existing CSV: {e}", "WARNING")

    # Load player IDs
    all_player_ids = get_player_ids_from_csv()
    
    if not all_player_ids:
        log("No player IDs found. Exiting.", "ERROR")
        return

    # Filter out already-completed players
    if completed_ids:
        all_player_ids = [pid for pid in all_player_ids if pid not in completed_ids]
        log(f"Resuming: {len(all_player_ids)} players remaining", "INFO")

    # Filter out players with all 6 ranks already scraped
    from collections import Counter
    counts_by_player = Counter(p for (p, r) in existing_rank_combinations)
    player_ids_needing_work = [
        pid for pid in all_player_ids
        if counts_by_player.get(pid, 0) < 6
    ]

    if not player_ids_needing_work:
        log("All players fully scraped!", "SUCCESS")
        clear_checkpoint()
        return

    log(f"Players to scrape: {len(player_ids_needing_work)}", "INFO")
    log(f"Target: 6 ranks per player ({len(player_ids_needing_work) * 6} requests)", "INFO")
    log(f"Initial batch size: {current_batch_size} (adaptive)", "INFO")
    log("="*70)

    start_time = datetime.now()
    semaphore = asyncio.Semaphore(current_batch_size)
    processed_players = 0
    checkpoint_counter = 0

    # Initialize CSV if new
    write_header = not os.path.exists(CSV_OUTPUT)
    if write_header:
        write_batch_to_csv([], mode='w')

    connector = aiohttp.TCPConnector(
        limit=MAX_BATCH_SIZE * 2,
        limit_per_host=MAX_BATCH_SIZE,
        ttl_dns_cache=300,
        force_close=False,
        enable_cleanup_closed=True
    )
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for i in range(0, len(player_ids_needing_work), current_batch_size):
            batch_ids = player_ids_needing_work[i:i + current_batch_size]

            # Update semaphore limit dynamically
            semaphore = asyncio.Semaphore(current_batch_size)

            tasks = [
                scrape_all_ranks_for_player(session, pid, existing_rank_combinations, semaphore)
                for pid in batch_ids
            ]

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            all_level_data = []

            for idx, result in enumerate(batch_results):
                player_id = batch_ids[idx]
                
                if isinstance(result, Exception):
                    log(f"Player {player_id} crashed: {result}", "ERROR")
                    failed_ids.append(player_id)
                    total_failed += 1
                elif result:
                    all_level_data.extend(result)
                    completed_ids.append(player_id)
                    total_scraped += 1
                    processed_players += 1
                    
                    # Log progress
                    player_name = result[0].get('name', '') if result else ''
                    if processed_players % 5 == 0:  # Log every 5 players
                        log_progress(processed_players, len(player_ids_needing_work), player_name)
                else:
                    failed_ids.append(player_id)
                    total_failed += 1

            # Write batch to CSV
            if all_level_data:
                write_batch_to_csv(all_level_data)

            # Checkpoint
            checkpoint_counter += len(batch_ids)
            if checkpoint_counter >= CHECKPOINT_INTERVAL:
                save_checkpoint(completed_ids, failed_ids, len(player_ids_needing_work))
                checkpoint_counter = 0

            # Adjust batch size based on response times
            adjust_batch_size()

            # Small delay between batches
            if BATCH_DELAY > 0:
                await asyncio.sleep(BATCH_DELAY)

    # Final summary
    elapsed = datetime.now() - start_time
    
    log("="*70)
    log("SCRAPING COMPLETE", "SUCCESS")
    log("="*70)
    log(f"Players scraped: {total_scraped}", "INFO")
    log(f"Players failed: {total_failed}", "INFO")
    log(f"Total time: {format_time(elapsed.total_seconds())}", "INFO")
    log(f"Output: {CSV_OUTPUT}, {SKILLS_JSON_OUTPUT}", "INFO")

    if failed_ids:
        save_failed_ids(failed_ids)

    clear_checkpoint()

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\nScraping interrupted by user", "WARNING")
        log("Run with --resume to continue from checkpoint", "INFO")
    except Exception as e:
        log(f"Critical error: {e}", "ERROR")
        import traceback
        traceback.print_exc()

