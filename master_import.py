#!/usr/bin/env python3
"""
master_import.py - Parallel Database Import Orchestrator for Zenith
Automatically discovers and imports all scraped data files in parallel.

Features:
- Auto-discovers all CSV/JSON files from scrapers
- Runs 3 imports simultaneously (player_stats, skills, skill_tree)
- Real-time progress tracking with live dashboard
- Handles multiple scraper outputs (merges automatically)
- Comprehensive error handling and reporting

Usage:
    python3 master_import.py
    python3 master_import.py --dry-run    # Test without importing
    python3 master_import.py --verify     # Only verify database after import
"""

import os
import sys
import glob
import subprocess
import threading
import time
import json
import psycopg2
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import re

# ============================================================================
# CONFIGURATION
# ============================================================================

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'zenith_data',
    'user': 'zenith_bot',
    'password': 'zenith6Z@'
}

# File patterns
STATS_CSV_PATTERN = "players_stats_*.csv"
SKILLS_JSON_PATTERN = "players_skills_*.json"
COLORS_CSV = "players_colors.csv"

# Import scripts
IMPORT_SCRIPTS = {
    'player_stats': 'append_player_stats.py',
    'skills': 'append_skills.py',
    'skill_tree': 'import_skill_tree_perfect.py'
}

# Target tables
TABLES = {
    'player_stats': 'player_stats',
    'skill_level_boosts': 'skill_level_boosts',
    'player_available_skills': 'player_available_skills',
    'player_skills_meta': 'player_skills_meta'
}

# Progress refresh interval
REFRESH_INTERVAL = 0.5  # seconds

# Backup directory
BACKUP_DIR = "backups"

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
# UTILITY FUNCTIONS
# ============================================================================

def print_header(text):
    """Print styled header"""
    width = 70
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'═' * width}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text.center(width)}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'═' * width}{Colors.END}\n")

def print_section(text):
    """Print section divider"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'─' * 70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'─' * 70}{Colors.END}\n")

def print_success(text):
    """Print success message"""
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_warning(text):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_error(text):
    """Print error message"""
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_info(text):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ️  {text}{Colors.END}")

def format_number(num):
    """Format number with commas"""
    return f"{num:,}"

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

def create_progress_bar(percentage, width=20):
    """Create a text-based progress bar"""
    filled = int(width * percentage / 100)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}] {percentage:3.0f}%"

# ============================================================================
# FILE DISCOVERY
# ============================================================================

class DiscoveredFiles:
    """Container for discovered files"""
    def __init__(self):
        self.stats_csvs = []
        self.skills_jsons = []
        self.colors_csv = None
        
    def is_empty(self):
        """Check if any files were found"""
        return not self.stats_csvs and not self.skills_jsons
    
    def summary(self):
        """Get summary string"""
        parts = []
        if self.stats_csvs:
            parts.append(f"{len(self.stats_csvs)} stats CSV(s)")
        if self.skills_jsons:
            parts.append(f"{len(self.skills_jsons)} skills JSON(s)")
        if self.colors_csv:
            parts.append("1 colors CSV")
        return ", ".join(parts) if parts else "No files"

def discover_files():
    """Auto-discover all scraped files"""
    files = DiscoveredFiles()
    
    # Find stats CSVs
    files.stats_csvs = sorted(glob.glob(STATS_CSV_PATTERN))
    
    # Find skills JSONs
    files.skills_jsons = sorted(glob.glob(SKILLS_JSON_PATTERN))
    
    # Check for colors CSV
    if os.path.exists(COLORS_CSV):
        files.colors_csv = COLORS_CSV
    
    return files

def estimate_row_counts(files):
    """Estimate total rows to be imported"""
    counts = {
        'stats': 0,
        'skills': 0,
        'skill_tree': 0
    }
    
    # Count stats CSV rows
    for csv_file in files.stats_csvs:
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                # Subtract 1 for header
                counts['stats'] += sum(1 for _ in f) - 1
        except:
            pass
    
    # Estimate skills (rough estimate: ~100-200 skill levels per player)
    # Each CSV row = 1 player at 1 rank, so stats_rows / 6 = unique players
    estimated_players = counts['stats'] // 6 if counts['stats'] > 0 else 0
    counts['skills'] = estimated_players * 150  # Average estimate
    counts['skill_tree'] = estimated_players * 120  # Average estimate
    
    return counts

# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def get_db_connection():
    """Get database connection"""
    try:
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        print_error(f"Database connection failed: {e}")
        return None

def get_table_counts(conn):
    """Get row counts for all tables"""
    counts = {}
    try:
        cur = conn.cursor()
        for table_name, db_table in TABLES.items():
            cur.execute(f"SELECT COUNT(*) FROM {db_table}")
            counts[table_name] = cur.fetchone()[0]
        cur.close()
    except Exception as e:
        print_warning(f"Failed to get table counts: {e}")
    return counts

def get_player_counts(conn):
    """Get unique player counts"""
    counts = {}
    try:
        cur = conn.cursor()
        
        # Player stats
        cur.execute("SELECT COUNT(DISTINCT player_id) FROM player_stats")
        counts['player_stats'] = cur.fetchone()[0]
        
        # Skill boosts
        cur.execute("SELECT COUNT(DISTINCT player_id) FROM skill_level_boosts")
        counts['skill_level_boosts'] = cur.fetchone()[0]
        
        # Skill tree
        cur.execute("SELECT COUNT(DISTINCT player_id) FROM player_available_skills")
        counts['player_available_skills'] = cur.fetchone()[0]
        
        cur.close()
    except Exception as e:
        print_warning(f"Failed to get player counts: {e}")
    return counts

# ============================================================================
# IMPORT PROCESS MANAGEMENT
# ============================================================================

class ImportProcess:
    """Manages a single import subprocess"""
    
    def __init__(self, name, script, args, estimated_rows=0):
        self.name = name
        self.script = script
        self.args = args
        self.estimated_rows = estimated_rows
        self.process = None
        self.log_lines = []
        self.status = 'Pending'
        self.progress = 0
        self.start_time = None
        self.end_time = None
        self.exit_code = None
        self.stats = {
            'inserted': 0,
            'updated': 0,
            'skipped': 0,
            'failed': 0
        }
        
    def start(self):
        """Start the import subprocess"""
        cmd = ['python3', self.script] + self.args
        
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        self.start_time = time.time()
        self.status = 'Running'
        
        # Start log reader thread
        log_thread = threading.Thread(target=self._read_logs, daemon=True)
        log_thread.start()
    
    def _read_logs(self):
        """Read logs from subprocess"""
        for line in iter(self.process.stdout.readline, ''):
            if line:
                line = line.rstrip()
                self.log_lines.append(line)
                self._parse_progress(line)
    
    def _parse_progress(self, line):
        """Parse progress from log lines"""
        # Look for batch completion messages
        if 'Batch' in line and 'processed' in line:
            # Example: "✅ Batch #5: 50 processed"
            match = re.search(r'Batch #(\d+)', line)
            if match:
                batch_num = int(match.group(1))
                # Estimate progress (rough)
                if self.estimated_rows > 0:
                    self.progress = min(99, (batch_num * 50 / self.estimated_rows) * 100)
        
        # Look for completion messages
        if 'COMPLETE' in line.upper() or 'Success' in line:
            self.progress = 100
        
        # Parse statistics
        if 'inserted' in line.lower():
            match = re.search(r'(\d+)\s+(?:rows?\s+)?inserted', line, re.IGNORECASE)
            if match:
                self.stats['inserted'] = int(match.group(1))
        
        if 'updated' in line.lower():
            match = re.search(r'(\d+)\s+(?:rows?\s+)?updated', line, re.IGNORECASE)
            if match:
                self.stats['updated'] = int(match.group(1))
        
        if 'skipped' in line.lower() or 'conflict' in line.lower():
            match = re.search(r'(\d+)\s+(?:rows?\s+)?(?:skipped|conflicts?)', line, re.IGNORECASE)
            if match:
                self.stats['skipped'] = int(match.group(1))
    
    def is_running(self):
        """Check if process is still running"""
        if self.process is None:
            return False
        return self.process.poll() is None
    
    def wait(self):
        """Wait for process to complete"""
        if self.process:
            self.process.wait()
            self.exit_code = self.process.returncode
            self.end_time = time.time()
            
            if self.exit_code == 0:
                self.status = 'Complete'
                self.progress = 100
            else:
                self.status = 'Failed'
    
    def get_duration(self):
        """Get process duration"""
        if self.start_time is None:
            return 0
        end = self.end_time if self.end_time else time.time()
        return end - self.start_time
    
    def get_last_log_lines(self, n=3):
        """Get last N log lines"""
        return self.log_lines[-n:] if len(self.log_lines) >= n else self.log_lines

# ============================================================================
# LIVE DASHBOARD
# ============================================================================

class ImportDashboard:
    """Live import progress dashboard"""
    
    def __init__(self, processes):
        self.processes = processes
        self.start_time = time.time()
        self.running = True
        self.lock = threading.Lock()
    
    def clear_screen(self):
        """Clear terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def render(self):
        """Render the dashboard"""
        self.clear_screen()
        
        # Header
        print_header("ZENITH MASTER IMPORTER - PARALLEL MODE")
        
        # Status
        elapsed = time.time() - self.start_time
        print(f"{Colors.BOLD}⏱️  Elapsed:{Colors.END} {format_time(elapsed)}\n")
        
        # Import status
        print(f"{Colors.BOLD}⚡ Import Progress:{Colors.END}\n")
        
        all_complete = True
        any_failed = False
        
        for proc in self.processes:
            with self.lock:
                if proc.is_running():
                    all_complete = False
                    status_emoji = "🔄"
                    status_color = Colors.CYAN
                elif proc.status == 'Complete':
                    status_emoji = "✅"
                    status_color = Colors.GREEN
                elif proc.status == 'Failed':
                    status_emoji = "❌"
                    status_color = Colors.RED
                    any_failed = True
                    all_complete = False
                else:
                    status_emoji = "⏳"
                    status_color = Colors.YELLOW
                    all_complete = False
                
                # Progress bar
                bar = create_progress_bar(proc.progress, width=20)
                
                # Estimated rows
                if proc.estimated_rows > 0:
                    row_info = f"~{format_number(proc.estimated_rows)} rows"
                else:
                    row_info = "calculating..."
                
                # Duration
                duration = format_time(proc.get_duration())
                
                # Status line
                print(f"{status_emoji} {status_color}{proc.name:25}{Colors.END} {bar} │ {row_info} │ {duration}")
        
        # Recent activity
        print(f"\n{Colors.BOLD}📝 Recent Activity:{Colors.END}")
        
        for proc in self.processes:
            recent_logs = proc.get_last_log_lines(1)
            if recent_logs:
                # Clean up log line (remove emojis and colors for display)
                log = recent_logs[0]
                log = re.sub(r'\[.*?\]', '', log)  # Remove timestamps
                log = re.sub(r'[✅❌⚠️ℹ️🔍]', '', log)  # Remove emojis
                log = log.strip()
                if log:
                    print(f"  [{proc.name}] {log[:60]}...")
        
        # Instructions
        if not all_complete:
            print(f"\n{Colors.CYAN}Press Ctrl+C to stop all imports{Colors.END}")
        else:
            if any_failed:
                print(f"\n{Colors.RED}Some imports failed - check logs above{Colors.END}")
            else:
                print(f"\n{Colors.GREEN}All imports completed successfully!{Colors.END}")
    
    def start(self):
        """Start dashboard in separate thread"""
        def update_loop():
            while self.running:
                self.render()
                time.sleep(REFRESH_INTERVAL)
        
        thread = threading.Thread(target=update_loop, daemon=True)
        thread.start()
        return thread
    
    def stop(self):
        """Stop dashboard updates"""
        self.running = False

# ============================================================================
# BACKUP FUNCTIONS
# ============================================================================

def create_backup_directory():
    """Create timestamped backup directory"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, timestamp)
    os.makedirs(backup_path, exist_ok=True)
    return backup_path

def backup_files(files, backup_path):
    """Copy files to backup directory"""
    import shutil
    
    backed_up = []
    
    try:
        # Backup stats CSVs
        for csv_file in files.stats_csvs:
            dest = os.path.join(backup_path, os.path.basename(csv_file))
            shutil.copy2(csv_file, dest)
            backed_up.append(csv_file)
        
        # Backup skills JSONs
        for json_file in files.skills_jsons:
            dest = os.path.join(backup_path, os.path.basename(json_file))
            shutil.copy2(json_file, dest)
            backed_up.append(json_file)
        
        # Backup colors CSV
        if files.colors_csv:
            dest = os.path.join(backup_path, os.path.basename(files.colors_csv))
            shutil.copy2(files.colors_csv, dest)
            backed_up.append(files.colors_csv)
        
        return backed_up
    except Exception as e:
        print_warning(f"Backup failed: {e}")
        return backed_up

# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

def verify_scripts():
    """Verify all import scripts exist"""
    missing = []
    for name, script in IMPORT_SCRIPTS.items():
        if not os.path.exists(script):
            missing.append(script)
    return missing

def main():
    """Main orchestrator function"""
    
    # Parse arguments
    dry_run = '--dry-run' in sys.argv
    verify_only = '--verify' in sys.argv
    
    print_header("ZENITH MASTER IMPORTER")
    
    # Verify import scripts exist
    missing_scripts = verify_scripts()
    if missing_scripts:
        print_error(f"Missing import scripts: {', '.join(missing_scripts)}")
        return 1
    
    # Connect to database
    print_section("STEP 1: Database Connection")
    conn = get_db_connection()
    if not conn:
        return 1
    print_success("Connected to database successfully")
    
    # Get initial table counts
    initial_counts = get_table_counts(conn)
    initial_players = get_player_counts(conn)
    
    print(f"\n{Colors.BOLD}📊 Current Database State:{Colors.END}")
    print(f"   player_stats: {format_number(initial_counts.get('player_stats', 0))} rows ({format_number(initial_players.get('player_stats', 0))} players)")
    print(f"   skill_level_boosts: {format_number(initial_counts.get('skill_level_boosts', 0))} rows ({format_number(initial_players.get('skill_level_boosts', 0))} players)")
    print(f"   player_available_skills: {format_number(initial_counts.get('player_available_skills', 0))} rows")
    print(f"   player_skills_meta: {format_number(initial_counts.get('player_skills_meta', 0))} rows")
    
    if verify_only:
        conn.close()
        print_success("\nVerification complete!")
        return 0
    
    # Discover files
    print_section("STEP 2: File Discovery")
    files = discover_files()
    
    if files.is_empty():
        print_error("No scraped files found!")
        print_info("Expected files:")
        print_info(f"  - {STATS_CSV_PATTERN}")
        print_info(f"  - {SKILLS_JSON_PATTERN}")
        print_info(f"  - {COLORS_CSV}")
        conn.close()
        return 1
    
    print(f"{Colors.BOLD}📁 Discovered Files:{Colors.END}")
    if files.stats_csvs:
        print_success(f"Stats CSVs: {len(files.stats_csvs)} file(s)")
        for f in files.stats_csvs:
            print(f"   • {f}")
    
    if files.skills_jsons:
        print_success(f"Skills JSONs: {len(files.skills_jsons)} file(s)")
        for f in files.skills_jsons:
            print(f"   • {f}")
    
    if files.colors_csv:
        print_success(f"Colors CSV: {files.colors_csv}")
    else:
        print_warning("Colors CSV not found (will skip color updates)")
    
    # Estimate row counts
    estimated_counts = estimate_row_counts(files)
    print(f"\n{Colors.BOLD}📊 Estimated Import:{Colors.END}")
    print(f"   Stats rows: ~{format_number(estimated_counts['stats'])}")
    print(f"   Skill levels: ~{format_number(estimated_counts['skills'])}")
    print(f"   Skill tree nodes: ~{format_number(estimated_counts['skill_tree'])}")
    
    if dry_run:
        print_info("\n🔍 DRY RUN MODE - No actual import will occur")
        conn.close()
        return 0
    
    # Backup files
    print_section("STEP 3: Creating Backup")
    backup_path = create_backup_directory()
    backed_up = backup_files(files, backup_path)
    print_success(f"Backed up {len(backed_up)} file(s) to: {backup_path}")
    
    # Prepare import processes
    print_section("STEP 4: Preparing Imports")
    
    processes = []
    
    # Process 1: Player stats (merge all CSVs)
    if files.stats_csvs:
        # Use first CSV, colors CSV if available
        args = [files.stats_csvs[0]]
        if files.colors_csv:
            args.append(files.colors_csv)
        else:
            args.append(files.stats_csvs[0])  # Dummy arg
        
        proc = ImportProcess(
            'player_stats',
            IMPORT_SCRIPTS['player_stats'],
            args,
            estimated_counts['stats']
        )
        processes.append(proc)
        
        # If multiple CSVs, import others sequentially (conflicts will be skipped)
        for csv_file in files.stats_csvs[1:]:
            proc = ImportProcess(
                f'player_stats (merge)',
                IMPORT_SCRIPTS['player_stats'],
                [csv_file, files.colors_csv] if files.colors_csv else [csv_file, csv_file],
                0
            )
            processes.append(proc)
    
    # Process 2: Skill boosts (merge all JSONs)
    if files.skills_jsons:
        for json_file in files.skills_jsons:
            proc = ImportProcess(
                f'skill_level_boosts',
                IMPORT_SCRIPTS['skills'],
                [json_file],
                estimated_counts['skills'] // len(files.skills_jsons)
            )
            processes.append(proc)
    
    # Process 3: Skill tree (merge all JSONs)
    if files.skills_jsons:
        for json_file in files.skills_jsons:
            proc = ImportProcess(
                f'skill_tree',
                IMPORT_SCRIPTS['skill_tree'],
                [json_file],
                estimated_counts['skill_tree'] // len(files.skills_jsons)
            )
            processes.append(proc)
    
    print_success(f"Prepared {len(processes)} import process(es)")
    
    # Start imports
    print_section("STEP 5: Running Imports (Parallel)")
    
    # Group processes by type for parallel execution
    # Run player_stats first (sequential for multiple files)
    # Then run skills and skill_tree in parallel
    
    stats_processes = [p for p in processes if 'player_stats' in p.name]
    other_processes = [p for p in processes if 'player_stats' not in p.name]
    
    all_processes = []
    
    # Start stats imports sequentially
    for proc in stats_processes:
        proc.start()
        all_processes.append(proc)
        proc.wait()  # Wait for each stats import to complete
        print_success(f"{proc.name} completed in {format_time(proc.get_duration())}")
    
    # Start other imports in parallel
    for proc in other_processes:
        proc.start()
        all_processes.append(proc)
        time.sleep(0.2)  # Small delay between starts
    
    # Start dashboard
    dashboard = ImportDashboard(other_processes)
    dashboard_thread = dashboard.start()
    
    # Wait for all to complete
    try:
        for proc in other_processes:
            proc.wait()
        
        dashboard.stop()
        time.sleep(0.5)
    except KeyboardInterrupt:
        print_warning("\n\nImport interrupted by user!")
        dashboard.stop()
        return 1
    
    # Final report
    dashboard.clear_screen()
    print_header("IMPORT COMPLETE")
    
    print_section("Import Summary")
    
    all_success = True
    for proc in all_processes:
        if proc.status == 'Complete':
            print_success(f"{proc.name}: {format_time(proc.get_duration())}")
            if proc.stats['inserted'] > 0:
                print(f"   Inserted: {format_number(proc.stats['inserted'])}")
            if proc.stats['updated'] > 0:
                print(f"   Updated: {format_number(proc.stats['updated'])}")
            if proc.stats['skipped'] > 0:
                print(f"   Skipped: {format_number(proc.stats['skipped'])}")
        else:
            print_error(f"{proc.name}: Failed")
            all_success = False
    
    # Get final table counts
    print_section("Database Verification")
    
    final_counts = get_table_counts(conn)
    final_players = get_player_counts(conn)
    
    print(f"{Colors.BOLD}📊 Final Database State:{Colors.END}\n")
    
    # Create comparison table
    print(f"{'Table':<30} {'Before':<15} {'After':<15} {'Change':<15}")
    print("─" * 75)
    
    for table_name in ['player_stats', 'skill_level_boosts', 'player_available_skills', 'player_skills_meta']:
        before = initial_counts.get(table_name, 0)
        after = final_counts.get(table_name, 0)
        change = after - before
        change_str = f"+{format_number(change)}" if change > 0 else "0"
        
        print(f"{table_name:<30} {format_number(before):<15} {format_number(after):<15} {change_str:<15}")
    
    print("\n" + "─" * 75)
    
    # Player counts
    print(f"\n{Colors.BOLD}👥 Unique Players:{Colors.END}")
    for table_name in ['player_stats', 'skill_level_boosts', 'player_available_skills']:
        before = initial_players.get(table_name, 0)
        after = final_players.get(table_name, 0)
        change = after - before
        change_str = f"+{format_number(change)}" if change > 0 else "0"
        print(f"   {table_name}: {format_number(after)} ({change_str} new)")
    
    # Final status
    total_time = time.time() - dashboard.start_time
    
    print(f"\n{Colors.BOLD}⏱️  Total Time:{Colors.END} {format_time(total_time)}")
    print(f"{Colors.BOLD}💾 Backups:{Colors.END} {backup_path}")
    
    conn.close()
    
    if all_success:
        print_success("\n🎉 All imports completed successfully!")
        return 0
    else:
        print_error("\n❌ Some imports failed. Check logs above.")
        return 1

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print_error(f"Critical error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

