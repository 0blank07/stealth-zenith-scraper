#!/usr/bin/env python3
"""
Simple monitor - runs discovery and checks for new players
"""

import os
import subprocess
import csv
from datetime import datetime

LAST_COUNT_FILE = "/home/blank/zenith_scraper/last_card_count.txt"
LOCK_FILE = "/home/blank/zenith_scraper/.scraper_running.lock"

def log(message, emoji="ℹ️"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {emoji} {message}", flush=True)

def is_scraper_running():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            os.remove(LOCK_FILE)
    return False

def get_new_player_count():
    """Run discovery and check how many new players found"""
    try:
        # Run the discovery script
        result = subprocess.run(
            ["/home/blank/zenith_scraper/venv/bin/python3", 
             "/home/blank/zenith_scraper/stats_colors.py"],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        # Check if final_missing_ids.csv was created
        if os.path.exists('final_missing_ids.csv'):
            with open('final_missing_ids.csv', 'r') as f:
                reader = csv.reader(f)
                next(reader)  # Skip header
                count = sum(1 for row in reader)
            return count
        return 0
        
    except Exception as e:
        log(f"Discovery error: {e}", "❌")
        return 0

def trigger_scraper():
    log("Triggering full scraper...", "🚀")
    
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    try:
        subprocess.run(
            ["/home/blank/zenith_scraper/venv/bin/python3", 
             "/home/blank/zenith_scraper/weekly_update.py"],
            timeout=3600
        )
        log("Scraper completed", "✅")
    except Exception as e:
        log(f"Scraper error: {e}", "❌")
    finally:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)

def main():
    log("=" * 60)
    log("Checking for new Renderz cards...", "🔍")
    
    if is_scraper_running():
        log("Scraper already running, skipping", "⏳")
        return
    
    # Run discovery and check for new players
    new_count = get_new_player_count()
    
    log(f"New players found: {new_count}", "📊")
    
    if new_count > 0:
        log(f"NEW CARDS DETECTED! {new_count} new players", "🚨")
        trigger_scraper()
    else:
        log("No new cards detected", "✅")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped", "⏹️")
    except Exception as e:
        log(f"Fatal: {e}", "❌")
