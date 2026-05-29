blank@zenith-production:~/zenith_scraper$ ls
GEMINI.md                             null_remaining.txt
__pycache__                           patch_stats.py
append_player_stats.py                persistent_profile
append_skills.py                      persistent_trace_20260405_211418.log
archive                               prompt.md
backups                               renderz_template.json
check_schema.py                       reports
debug_header_capture.py               requirements.txt
debug_html.txt                        run_scrapers.py
debug_output                          run_scrapers.py.backup
delete_recent_players.py              scraper_chunks
e                                     stats_colors.py
fix_colors_not_main.py                stats_colors.py.backup
fix_missing_colors.py.backup.notmain  stats_colors.py.backup.1
fix_null_colors.py                    stats_colors_replacement.py
fix_null_colors.py.backup             stats_scrape.py
headers.json                          test_24id.py
import_skill_tree_perfect.py          test_import_debug.py
ition                                 test_single.py
log_204720.txt                        test_skills_fix.py
logs                                  test_skills_html.py
master_import.py                      user_data
monitor_new_cards.py                  venv
network_trace_20260405_210440.log     weekly_update.py
blank@zenith-production:~/zenith_scraper$ python3 delete_recent_players.py
============================================================
ZENITH SCRAPER - CLEANUP & DATABASE UPGRADE
============================================================
⏳ Taking a safety backup of the database to backups/backup_before_delete_20260529_101914.sql...
✅ Backup completed successfully!

⏳ Checking database schema for 'created_at' clock...
   ➕ Adding 'created_at' timestamp to player_stats...
   ➕ Adding 'created_at' timestamp to player_available_skills...
   ➕ Adding 'created_at' timestamp to skill_level_boosts...
   ➕ Adding 'created_at' timestamp to player_skills_meta...
✅ Database schema upgraded. All future scrapes will be timestamped!

⏳ Finding the most recently added players based on RenderZ date...
   📅 Latest date found: 'May 28, 2026'
🗑️  Found 1117 players added on May 28, 2026. Deleting...
   ✓ Deleted 34644 rows from player_available_skills
   ✓ Deleted 8755 rows from skill_level_boosts
   ✓ Deleted 6702 rows from player_skills_meta
   ✓ Deleted 6702 rows from player_stats

✅ Cleanup of 'May 28, 2026' complete!
🚀 You can now run `xvfb-run python3 weekly_update.py` for a fresh scrape.
blank@zenith-production:~/zenith_scraper$
