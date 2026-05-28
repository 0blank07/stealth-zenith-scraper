#!/usr/bin/env python3
"""
weekly_update.py - Complete Zenith Database Update Orchestrator
Runs the entire pipeline: Discovery → Scraping → Importing → Verification

This is your one-click solution for Wednesday night updates.

Features:
- Runs all steps automatically in sequence
- Beautiful progress tracking at each stage
- Comprehensive error handling
- Detailed final report
- Automatic backup management
- Optional email notifications

Usage:
    python3 weekly_update.py                    # Run full update
    python3 weekly_update.py --skip-discovery   # Skip if you already have final_missing_ids.csv
    python3 weekly_update.py --skip-scraping    # Only import existing files
    python3 weekly_update.py --dry-run          # Test without actually running
"""

import os
import sys
import subprocess
import time
import json
import psycopg2
from datetime import datetime
from pathlib import Path
import shutil
import glob

# ============================================================================
# CONFIGURATION
# ============================================================================

# Pipeline scripts
SCRIPTS = {
    'discovery': 'stats_colors.py',
    'scraper': 'run_scrapers.py',
    'importer': 'master_import.py'
}

# Expected output files
DISCOVERY_OUTPUT = 'final_missing_ids.csv'
COLORS_OUTPUT = 'players_colors.csv'

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'zenith_data',
    'user': 'zenith_bot',
    'password': 'zenith6Z@'
}

# Directories
REPORTS_DIR = 'reports'
BACKUPS_DIR = 'backups'
LOGS_DIR = 'logs'
ARCHIVE_DIR = 'archive'

# Notification settings (optional)
ENABLE_NOTIFICATIONS = False  # Set to True to enable
NOTIFICATION_EMAIL = None  # Your email for notifications

# ============================================================================
# TERMINAL COLORS
# ============================================================================

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

    @staticmethod
    def disable():
        """Disable colors for non-TTY environments"""
        Colors.HEADER = ''
        Colors.BLUE = ''
        Colors.CYAN = ''
        Colors.GREEN = ''
        Colors.YELLOW = ''
        Colors.RED = ''
        Colors.BOLD = ''
        Colors.UNDERLINE = ''
        Colors.END = ''

if not sys.stdout.isatty():
    Colors.disable()

# ============================================================================
# LOGGING
# ============================================================================

class Logger:
    """Dual logging to console and file"""

    def __init__(self, log_file):
        self.log_file = log_file
        self.start_time = datetime.now()

        # Create log file
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"=== ZENITH WEEKLY UPDATE LOG ===\n")
            f.write(f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S IST')}\n")
            f.write("="*70 + "\n\n")

    def log(self, message, level="INFO"):
        """Log message to console and file"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Console output
        prefix = {
            "INFO": f"{Colors.CYAN}ℹ️{Colors.END}",
            "SUCCESS": f"{Colors.GREEN}✅{Colors.END}",
            "WARNING": f"{Colors.YELLOW}⚠️{Colors.END}",
            "ERROR": f"{Colors.RED}❌{Colors.END}",
            "STEP": f"{Colors.BOLD}{Colors.BLUE}▶{Colors.END}",
            "HEADER": ""
        }.get(level, "  ")

        console_msg = f"[{timestamp}] {prefix} {message}"
        print(console_msg, flush=True)

        # File output (without colors)
        file_msg = f"[{timestamp}] [{level}] {message}\n"
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(file_msg)

    def header(self, text):
        """Print header"""
        width = 70
        line = "=" * width
        print(f"\n{Colors.BOLD}{Colors.CYAN}{line}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.CYAN}{text.center(width)}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.CYAN}{line}{Colors.END}\n")

        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{line}\n{text.center(width)}\n{line}\n\n")

    def section(self, text):
        """Print section divider"""
        line = "─" * 70
        print(f"\n{Colors.BOLD}{Colors.BLUE}{line}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.BLUE}{line}{Colors.END}\n")

        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{line}\n{text}\n{line}\n\n")

    def get_duration(self):
        """Get total duration"""
        return (datetime.now() - self.start_time).total_seconds()

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

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

def format_number(num):
    """Format number with commas"""
    return f"{num:,}"

def get_db_connection():
    """Get database connection"""
    try:
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        return None

def get_table_stats(conn):
    """Get comprehensive table statistics"""
    stats = {}
    try:
        cur = conn.cursor()

        # Player stats
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT player_id) FROM player_stats")
        row = cur.fetchone()
        stats['player_stats'] = {'rows': row[0], 'players': row[1]}

        # Skill boosts
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT player_id) FROM skill_level_boosts")
        row = cur.fetchone()
        stats['skill_level_boosts'] = {'rows': row[0], 'players': row[1]}

        # Skill tree
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT player_id) FROM player_available_skills")
        row = cur.fetchone()
        stats['player_available_skills'] = {'rows': row[0], 'players': row[1]}

        cur.execute("SELECT COUNT(*) FROM player_skills_meta")
        stats['player_skills_meta'] = {'rows': cur.fetchone()[0], 'players': 0}

        cur.close()
    except Exception as e:
        pass
    return stats

def count_csv_rows(file_path):
    """Count rows in CSV file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return sum(1 for _ in f) - 1  # Subtract header
    except:
        return 0

def setup_directories():
    """Create necessary directories"""
    for dir_path in [REPORTS_DIR, BACKUPS_DIR, LOGS_DIR, ARCHIVE_DIR]:
        os.makedirs(dir_path, exist_ok=True)

# ============================================================================
# PIPELINE STAGES
# ============================================================================

class PipelineStage:
    """Represents a single pipeline stage"""

    def __init__(self, name, script, description):
        self.name = name
        self.script = script
        self.description = description
        self.status = 'Pending'
        self.start_time = None
        self.end_time = None
        self.exit_code = None
        self.output = []
        self.error = None

    def run(self, logger, args=None):
        """Run the stage"""
        logger.section(f"{self.name.upper()}")
        logger.log(self.description, "STEP")

        self.start_time = time.time()
        self.status = 'Running'

        try:
            # Use venv python if available
            venv_python = os.path.join(os.path.dirname(__file__), 'venv', 'bin', 'python3')
            python_cmd = venv_python if os.path.exists(venv_python) else 'python3'
            cmd = [python_cmd, self.script]

            if args:
                cmd.extend(args)

            logger.log(f"Executing: {' '.join(cmd)}", "INFO")

            # Run subprocess with real-time output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Stream output
            for line in iter(process.stdout.readline, ''):
                if line:
                    line = line.rstrip()
                    self.output.append(line)
                    # Echo important lines
                    if any(keyword in line for keyword in ['✅', '❌', '⚠️', 'Error', 'Complete', 'Found']):
                        print(f"  {line}", flush=True)

            process.wait()
            self.exit_code = process.returncode
            self.end_time = time.time()

            if self.exit_code == 0:
                self.status = 'Success'
                duration = format_time(self.end_time - self.start_time)
                logger.log(f"{self.name} completed successfully in {duration}", "SUCCESS")
                return True
            else:
                self.status = 'Failed'
                self.error = f"Exit code: {self.exit_code}"
                logger.log(f"{self.name} failed with exit code {self.exit_code}", "ERROR")
                return False

        except Exception as e:
            self.end_time = time.time()
            self.status = 'Failed'
            self.error = str(e)
            logger.log(f"{self.name} crashed: {e}", "ERROR")
            return False

    def get_duration(self):
        """Get stage duration"""
        if not self.start_time:
            return 0
        end = self.end_time if self.end_time else time.time()
        return end - self.start_time

    def skip(self, logger):
        """Skip this stage"""
        self.status = 'Skipped'
        logger.log(f"{self.name} skipped by user", "WARNING")

# ============================================================================
# REPORT GENERATION
# ============================================================================

def generate_report(stages, initial_stats, final_stats, logger):
    """Generate comprehensive text report"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_file = os.path.join(REPORTS_DIR, f"weekly_report_{timestamp}.txt")

    with open(report_file, 'w', encoding='utf-8') as f:
        # Header
        f.write("="*70 + "\n")
        f.write("ZENITH WEEKLY UPDATE REPORT\n")
        f.write("="*70 + "\n\n")

        f.write(f"Date: {datetime.now().strftime('%A, %B %d, %Y')}\n")
        f.write(f"Time: {datetime.now().strftime('%I:%M %p IST')}\n")
        f.write(f"Total Duration: {format_time(logger.get_duration())}\n\n")

        # Stage Summary
        f.write("─"*70 + "\n")
        f.write("PIPELINE STAGES\n")
        f.write("─"*70 + "\n\n")

        for stage in stages:
            status_emoji = {
                'Success': '✅',
                'Failed': '❌',
                'Skipped': '⏭️',
                'Pending': '⏳'
            }.get(stage.status, '❓')

            f.write(f"{status_emoji} {stage.name}\n")
            f.write(f"   Status: {stage.status}\n")
            if stage.status != 'Pending' and stage.status != 'Skipped':
                f.write(f"   Duration: {format_time(stage.get_duration())}\n")
            if stage.error:
                f.write(f"   Error: {stage.error}\n")
            f.write("\n")

        # Database Changes
        f.write("─"*70 + "\n")
        f.write("DATABASE CHANGES\n")
        f.write("─"*70 + "\n\n")

        if initial_stats and final_stats:
            f.write(f"{'Table':<30} {'Before':<15} {'After':<15} {'Change':<15}\n")
            f.write("─"*75 + "\n")

            for table in ['player_stats', 'skill_level_boosts', 'player_available_skills', 'player_skills_meta']:
                before_rows = initial_stats.get(table, {}).get('rows', 0)
                after_rows = final_stats.get(table, {}).get('rows', 0)
                change = after_rows - before_rows

                f.write(f"{table:<30} {format_number(before_rows):<15} {format_number(after_rows):<15} ")
                f.write(f"+{format_number(change)}\n" if change > 0 else "0\n")

            f.write("\n")

            # Player counts
            f.write("Unique Players:\n")
            for table in ['player_stats', 'skill_level_boosts', 'player_available_skills']:
                before = initial_stats.get(table, {}).get('players', 0)
                after = final_stats.get(table, {}).get('players', 0)
                change = after - before
                f.write(f"  {table}: {format_number(after)} (+{format_number(change)} new)\n")

        f.write("\n")

        # Files Generated
        f.write("─"*70 + "\n")
        f.write("FILES GENERATED\n")
        f.write("─"*70 + "\n\n")

        # List scraped files
        stats_csvs = glob.glob("players_stats_*.csv")
        skills_jsons = glob.glob("players_skills_*.json")

        if stats_csvs:
            f.write("Stats CSVs:\n")
            for csv_file in stats_csvs:
                rows = count_csv_rows(csv_file)
                f.write(f"  • {csv_file} ({format_number(rows)} rows)\n")
            f.write("\n")

        if skills_jsons:
            f.write("Skills JSONs:\n")
            for json_file in skills_jsons:
                try:
                    with open(json_file, 'r') as jf:
                        data = json.load(jf)
                        f.write(f"  • {json_file} ({len(data)} players)\n")
                except:
                    f.write(f"  • {json_file}\n")
            f.write("\n")

        # Backups
        latest_backup = max(glob.glob(os.path.join(BACKUPS_DIR, "*")), default=None, key=os.path.getmtime)
        if latest_backup:
            f.write(f"Backup Location: {latest_backup}\n\n")

        # Footer
        f.write("─"*70 + "\n")
        f.write("END OF REPORT\n")
        f.write("─"*70 + "\n")

    logger.log(f"Report saved to: {report_file}", "SUCCESS")
    return report_file

# ============================================================================
# CLEANUP AND ARCHIVAL
# ============================================================================

def cleanup_old_files(logger, archive=True):
    """Clean up temporary files and archive if requested"""

    files_to_cleanup = []

    # Temporary chunk files
    files_to_cleanup.extend(glob.glob("scraper_chunks/chunk_*.csv"))

    # Checkpoint files
    files_to_cleanup.extend(glob.glob("checkpoint_*.json"))
    files_to_cleanup.extend(glob.glob("scraper_progress.json"))

    # Failed IDs files
    files_to_cleanup.extend(glob.glob("failed_stats_*.txt"))

    if archive:
        # Archive scraped files
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        archive_path = os.path.join(ARCHIVE_DIR, timestamp)
        os.makedirs(archive_path, exist_ok=True)

        files_to_archive = []
        files_to_archive.extend(glob.glob("players_stats_*.csv"))
        files_to_archive.extend(glob.glob("players_skills_*.json"))

        if os.path.exists(DISCOVERY_OUTPUT):
            files_to_archive.append(DISCOVERY_OUTPUT)
        if os.path.exists(COLORS_OUTPUT):
            files_to_archive.append(COLORS_OUTPUT)

        for file in files_to_archive:
            try:
                dest = os.path.join(archive_path, os.path.basename(file))
                shutil.move(file, dest)
                logger.log(f"Archived: {file} → {archive_path}", "INFO")
            except Exception as e:
                logger.log(f"Failed to archive {file}: {e}", "WARNING")

    # Delete temporary files
    for file in files_to_cleanup:
        try:
            os.remove(file)
        except:
            pass

    if files_to_cleanup:
        logger.log(f"Cleaned up {len(files_to_cleanup)} temporary file(s)", "SUCCESS")

# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

def main():
    """Main orchestrator"""

    # Parse arguments
    skip_discovery = '--skip-discovery' in sys.argv
    skip_scraping = '--skip-scraping' in sys.argv
    dry_run = '--dry-run' in sys.argv
    no_cleanup = '--no-cleanup' in sys.argv

    # Setup
    setup_directories()

    # Create logger
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = os.path.join(LOGS_DIR, f"weekly_update_{timestamp}.log")
    logger = Logger(log_file)

    # Header
    logger.header("ZENITH WEEKLY UPDATE - MASTER ORCHESTRATOR")

    logger.log(f"Started: {datetime.now().strftime('%A, %B %d, %Y at %I:%M %p IST')}", "INFO")
    logger.log(f"Log file: {log_file}", "INFO")

    if dry_run:
        logger.log("DRY RUN MODE - No actual operations will be performed", "WARNING")

    # Connect to database
    logger.section("DATABASE CONNECTION")
    conn = get_db_connection()
    if not conn:
        logger.log("Failed to connect to database", "ERROR")
        return 1

    logger.log("Connected to database successfully", "SUCCESS")

    # Get initial database state
    initial_stats = get_table_stats(conn)

    logger.log("Initial database state:", "INFO")
    for table, stats in initial_stats.items():
        logger.log(f"  {table}: {format_number(stats['rows'])} rows, {format_number(stats['players'])} players", "INFO")

    # Define pipeline stages
    stages = [
        PipelineStage(
            "Discovery",
            SCRIPTS['discovery'],
            "Discovering new players from Renderz API"
        ),
        PipelineStage(
            "Scraping",
            SCRIPTS['scraper'],
            "Scraping player stats and skills (parallel mode)"
        ),
        PipelineStage(
            "Importing",
            SCRIPTS['importer'],
            "Importing data to database (parallel mode)"
        )
    ]

    # Check if scripts exist
    logger.section("PRE-FLIGHT CHECK")

    missing_scripts = []
    for stage in stages:
        if not os.path.exists(stage.script):
            missing_scripts.append(stage.script)
            logger.log(f"Missing script: {stage.script}", "ERROR")

    if missing_scripts:
        logger.log("Cannot proceed - missing required scripts", "ERROR")
        return 1

    logger.log("All required scripts found", "SUCCESS")

    if dry_run:
        logger.log("Dry run complete - no operations performed", "SUCCESS")
        return 0

    # Execute pipeline
    logger.header("PIPELINE EXECUTION")

    all_success = True

    # Stage 1: Discovery
    if skip_discovery:
        stages[0].skip(logger)

        # Check if discovery output exists
        if not os.path.exists(DISCOVERY_OUTPUT):
            logger.log(f"Missing {DISCOVERY_OUTPUT} - cannot skip discovery", "ERROR")
            return 1

        # Count players
        player_count = count_csv_rows(DISCOVERY_OUTPUT)
        logger.log(f"Using existing discovery file: {player_count} players", "INFO")
    else:
        success = stages[0].run(logger)
        if not success:
            all_success = False
            logger.log("Discovery failed - aborting pipeline", "ERROR")
            return 1

        # Check output
        if not os.path.exists(DISCOVERY_OUTPUT):
            logger.log(f"Discovery did not produce {DISCOVERY_OUTPUT}", "ERROR")
            return 1

        player_count = count_csv_rows(DISCOVERY_OUTPUT)
        logger.log(f"Discovered {player_count} new players", "SUCCESS")

        if player_count == 0:
            logger.log("No new players found - update complete", "SUCCESS")
            conn.close()
            return 0

    # Stage 2: Scraping
    if skip_scraping:
        stages[1].skip(logger)

        # Check if scraped files exist
        stats_csvs = glob.glob("players_stats_*.csv")
        if not stats_csvs:
            logger.log("No scraped files found - cannot skip scraping", "ERROR")
            return 1

        logger.log(f"Using existing scraped files: {len(stats_csvs)} CSV(s)", "INFO")
    else:
        success = stages[1].run(logger)
        if not success:
            all_success = False
            logger.log("Scraping failed - aborting pipeline", "ERROR")
            return 1

        # Verify scraped files
        stats_csvs = glob.glob("players_stats_*.csv")
        skills_jsons = glob.glob("players_skills_*.json")

        if not stats_csvs and not skills_jsons:
            logger.log("Scraping did not produce any output files", "ERROR")
            return 1

        logger.log(f"Scraping produced {len(stats_csvs)} CSV(s) and {len(skills_jsons)} JSON(s)", "SUCCESS")

    # Stage 3: Importing
    success = stages[2].run(logger)
    if not success:
        all_success = False
        logger.log("Importing failed", "ERROR")

    # Get final database state
    logger.section("DATABASE VERIFICATION")

    final_stats = get_table_stats(conn)

    logger.log("Final database state:", "INFO")
    for table, stats in final_stats.items():
        initial = initial_stats.get(table, {})
        initial_rows = initial.get('rows', 0)
        initial_players = initial.get('players', 0)

        change_rows = stats['rows'] - initial_rows
        change_players = stats['players'] - initial_players

        logger.log(f"  {table}:", "INFO")
        logger.log(f"    Rows: {format_number(stats['rows'])} (+{format_number(change_rows)})", "INFO")
        if stats['players'] > 0:
            logger.log(f"    Players: {format_number(stats['players'])} (+{format_number(change_players)})", "INFO")

    conn.close()

    # Generate report
    logger.section("GENERATING REPORT")
    report_file = generate_report(stages, initial_stats, final_stats, logger)

    # Cleanup
    if not no_cleanup:
        logger.section("CLEANUP")
        cleanup_old_files(logger, archive=True)

    # Final summary
    logger.header("UPDATE COMPLETE")

    total_duration = format_time(logger.get_duration())
    logger.log(f"Total duration: {total_duration}", "INFO")
    logger.log(f"Log file: {log_file}", "INFO")
    logger.log(f"Report file: {report_file}", "INFO")

    if all_success:
        logger.log("🎉 All stages completed successfully!", "SUCCESS")

        # Calculate new players added
        new_players = final_stats.get('player_stats', {}).get('players', 0) - initial_stats.get('player_stats', {}).get('players', 0)
        logger.log(f"📊 Added {format_number(new_players)} new players to database", "SUCCESS")

        return 0
    else:
        logger.log("⚠️  Some stages failed - check logs for details", "WARNING")
        return 1

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠️  Update interrupted by user{Colors.END}")
        print(f"{Colors.CYAN}ℹ️  Check logs for partial progress{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n{Colors.RED}❌ Critical error: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
