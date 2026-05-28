blank@zenith-production:~/zenith_scraper$ ls
GEMINI.md                debug_output                          import_skill_tree_perfect.py       persi
__pycache__              delete_recent_players.py              ition                              persi
append_player_stats.py   e                                     log_204720.txt                     playe
append_skills.py         final_missing_ids.csv                 logs                               rende
archive                  fix_colors_not_main.py                master_import.py                   repor
backups                  fix_missing_colors.py.backup.notmain  monitor_new_cards.py               requi
check_schema.py          fix_null_colors.py                    network_trace_20260405_210440.log  run_s
debug_header_capture.py  fix_null_colors.py.backup             null_remaining.txt                 run_s
debug_html.txt           headers.json                          patch_stats.py                     scrap
blank@zenith-production:~/zenith_scraper$ python3 weekly_update.py

======================================================================
              ZENITH WEEKLY UPDATE - MASTER ORCHESTRATOR
======================================================================

[19:20:39] ℹ️ Started: Thursday, May 28, 2026 at 07:20 PM IST
[19:20:39] ℹ️ Log file: logs/weekly_update_2026-05-28_192039.log

──────────────────────────────────────────────────────────────────────
DATABASE CONNECTION
──────────────────────────────────────────────────────────────────────

[19:20:39] ✅ Connected to database successfully
[19:20:43] ℹ️ Initial database state:
[19:20:43] ℹ️   player_stats: 280,161 rows, 50,798 players
[19:20:43] ℹ️   skill_level_boosts: 1,011,259 rows, 50,799 players
[19:20:43] ℹ️   player_available_skills: 1,621,506 rows, 50,799 players
[19:20:43] ℹ️   player_skills_meta: 280,254 rows, 0 players

──────────────────────────────────────────────────────────────────────
PRE-FLIGHT CHECK
──────────────────────────────────────────────────────────────────────

[19:20:43] ✅ All required scripts found

======================================================================
                          PIPELINE EXECUTION
======================================================================


──────────────────────────────────────────────────────────────────────
DISCOVERY
──────────────────────────────────────────────────────────────────────

[19:20:43] ▶ Discovering new players from Renderz API
[19:20:43] ℹ️ Executing: /home/blank/zenith_scraper/venv/bin/python3 stats_colors.py
  [19:20:45] ✅ Loaded 50,798 existing player_ids from database
       [19:21:17] ⚠️ POST not fired naturally — trying scroll trigger
   [19:21:20] ⚠️ Trying hard reload
   [19:21:51] ❌ Fatal error: Could not capture native browser API request
[19:21:51] ❌ Discovery failed with exit code 1
[19:21:51] ❌ Discovery failed - aborting pipeline
blank@zenith-production:~/zenith_scraper$
