blank@zenith-production:~/zenith_scraper$ xvfb-run python3 weekly_update.py

======================================================================
              ZENITH WEEKLY UPDATE - MASTER ORCHESTRATOR
======================================================================

[11:58:09] ℹ️ Started: Friday, May 29, 2026 at 11:58 AM IST
[11:58:09] ℹ️ Log file: logs/weekly_update_2026-05-29_115809.log

──────────────────────────────────────────────────────────────────────
DATABASE CONNECTION
──────────────────────────────────────────────────────────────────────

[11:58:09] ✅ Connected to database successfully
[11:58:13] ℹ️ Initial database state:
[11:58:13] ℹ️   player_stats: 276,267 rows, 50,149 players
[11:58:13] ℹ️   skill_level_boosts: 1,006,127 rows, 50,150 players
[11:58:13] ℹ️   player_available_skills: 1,601,316 rows, 50,150 players
[11:58:13] ℹ️   player_skills_meta: 276,360 rows, 0 players

──────────────────────────────────────────────────────────────────────
PRE-FLIGHT CHECK
──────────────────────────────────────────────────────────────────────

[11:58:13] ✅ All required scripts found

======================================================================
                          PIPELINE EXECUTION
======================================================================


──────────────────────────────────────────────────────────────────────
DISCOVERY
──────────────────────────────────────────────────────────────────────

[11:58:13] ▶ Discovering new players from Renderz API
[11:58:13] ℹ️ Executing: /home/blank/zenith_scraper/venv/bin/python3 stats_colors.py
  [11:58:13] ✅ Loaded 50,149 existing player_ids from database
  [11:58:46] ⚠️ POST not fired naturally — trying scroll trigger
  [11:58:49] ⚠️ Trying hard reload
  [11:59:20] ❌ Fatal error: Could not capture native browser API request
[11:59:20] ❌ Discovery failed with exit code 1
[11:59:20] ❌ Discovery failed - aborting pipeline
blank@zenith-production:~/zenith_scraper$
