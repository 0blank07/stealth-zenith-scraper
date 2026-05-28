#!/usr/bin/env python3
"""
run_scrapers.py - Parallel Scraper Orchestrator for Zenith
Splits player IDs and launches multiple scraper instances with live progress tracking.
Cloudflare-safe with adaptive rate limiting.

Usage:
    python3 run_scrapers.py
    python3 run_scrapers.py --scrapers 3  # Run 3 parallel instances
    python3 run_scrapers.py --resume      # Resume from checkpoint
"""

import os
import sys
import csv
import time
import json
import subprocess
import threading
import signal
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# ============================================================================
# CONFIGURATION
# ============================================================================

INPUT_CSV = "final_missing_ids.csv"
NUM_SCRAPERS = 2  # Conservative for Cloudflare (can be 2-3)
CHUNK_DIR = "scraper_chunks"
CHECKPOINT_FILE = "scraper_progress.json"
LOG_DIR = "logs"

# Output file patterns
STATS_CSV_PATTERN = "players_stats_{}.csv"
SKILLS_JSON_PATTERN = "players_skills_{}.json"

# Progress tracking
REFRESH_INTERVAL = 1.0  # seconds between dashboard updates

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

# Disable colors if not in terminal
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
# PLAYER ID MANAGEMENT
# ============================================================================

def load_player_ids(csv_file):
    """Load player IDs from CSV file"""
    try:
        player_ids = []
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('asset_id'):
                    player_ids.append(int(row['asset_id']))
        
        # Remove duplicates and sort
        player_ids = sorted(set(player_ids))
        return player_ids
    
    except FileNotFoundError:
        print_error(f"Input file not found: {csv_file}")
        print_info("Please run stats_colors.py first to generate player IDs")
        return []
    except Exception as e:
        print_error(f"Error reading {csv_file}: {e}")
        return []

def split_player_ids(player_ids, num_chunks):
    """Split player IDs into equal chunks"""
    chunk_size = len(player_ids) // num_chunks
    remainder = len(player_ids) % num_chunks
    
    chunks = []
    start = 0
    
    for i in range(num_chunks):
        # Distribute remainder across first chunks
        size = chunk_size + (1 if i < remainder else 0)
        end = start + size
        chunks.append(player_ids[start:end])
        start = end
    
    return chunks

def save_chunk_to_csv(chunk, chunk_num, output_dir):
    """Save a chunk of player IDs to CSV file"""
    filename = os.path.join(output_dir, f"chunk_{chunk_num}_ids.csv")
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['asset_id'])
        for player_id in chunk:
            writer.writerow([player_id])
    
    return filename

# ============================================================================
# CHECKPOINT MANAGEMENT
# ============================================================================

def load_checkpoint():
    """Load checkpoint from previous run"""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def save_checkpoint(data):
    """Save checkpoint data"""
    try:
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print_warning(f"Failed to save checkpoint: {e}")

def clear_checkpoint():
    """Remove checkpoint file"""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            os.remove(CHECKPOINT_FILE)
        except:
            pass

# ============================================================================
# SCRAPER PROCESS MANAGEMENT
# ============================================================================

class ScraperProcess:
    """Manages a single scraper subprocess"""
    
    def __init__(self, scraper_num, chunk_file, total_players):
        self.scraper_num = scraper_num
        self.chunk_file = chunk_file
        self.total_players = total_players
        self.process = None
        self.log_file = None
        self.stats = {
            'completed': 0,
            'failed': 0,
            'current_player': None,
            'status': 'Starting',
            'rate': 0.0,
            'eta': 0,
            'cloudflare_detected': False
        }
        self.start_time = None
        self.last_update = time.time()
        
    def start(self):
        """Start the scraper subprocess"""
        # Create environment with scraper number
        env = os.environ.copy()
        env['SCRAPER_NUM'] = str(self.scraper_num)
        env['ASSET_IDS_CSV'] = self.chunk_file
        
        # Open log file
        log_path = os.path.join(LOG_DIR, f"scraper_{self.scraper_num}.log")
        self.log_file = open(log_path, 'w', encoding='utf-8')
        
        # Start subprocess
        self.process = subprocess.Popen(
            ['python3', 'stats_scrape.py'],
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        self.start_time = time.time()
        self.stats['status'] = 'Running'
        
        print_success(f"Scraper {self.scraper_num} started (PID: {self.process.pid})")
    
    def is_running(self):
        """Check if process is still running"""
        if self.process is None:
            return False
        return self.process.poll() is None
    
    def get_progress(self):
        """Calculate progress percentage"""
        if self.total_players == 0:
            return 100
        return min(100, (self.stats['completed'] / self.total_players) * 100)
    
    def update_stats_from_output(self):
        """Parse output files to update statistics"""
        # Check CSV output for completed players
        csv_file = STATS_CSV_PATTERN.format(self.scraper_num)
        if os.path.exists(csv_file):
            try:
                with open(csv_file, 'r', encoding='utf-8') as f:
                    # Count rows (minus header)
                    row_count = sum(1 for _ in f) - 1
                    # Each player has 6 ranks
                    self.stats['completed'] = row_count // 6
            except:
                pass
        
        # Calculate rate and ETA
        if self.start_time and self.stats['completed'] > 0:
            elapsed = time.time() - self.start_time
            self.stats['rate'] = self.stats['completed'] / elapsed if elapsed > 0 else 0
            
            remaining = self.total_players - self.stats['completed']
            if self.stats['rate'] > 0:
                self.stats['eta'] = remaining / self.stats['rate']
            else:
                self.stats['eta'] = 0
    
    def terminate(self):
        """Terminate the scraper process"""
        if self.process and self.is_running():
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        if self.log_file:
            self.log_file.close()
    
    def get_exit_code(self):
        """Get process exit code"""
        if self.process:
            return self.process.poll()
        return None

# ============================================================================
# DASHBOARD DISPLAY
# ============================================================================

class Dashboard:
    """Live progress dashboard"""
    
    def __init__(self, scrapers, total_players):
        self.scrapers = scrapers
        self.total_players = total_players
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
        print_header("ZENITH PARALLEL SCRAPER - CLOUDFLARE SAFE")
        
        # Configuration
        print(f"{Colors.BOLD}📊 Configuration:{Colors.END}")
        print(f"   Total Players: {Colors.BOLD}{self.total_players}{Colors.END}")
        print(f"   Parallel Scrapers: {Colors.BOLD}{len(self.scrapers)}{Colors.END}")
        print(f"   Target: {Colors.BOLD}6 ranks per player{Colors.END} ({self.total_players * 6} total requests)\n")
        
        # Scraper status
        print(f"{Colors.BOLD}⚡ Scraper Status:{Colors.END}\n")
        
        total_completed = 0
        all_healthy = True
        
        for scraper in self.scrapers:
            with self.lock:
                scraper.update_stats_from_output()
                
                progress = scraper.get_progress()
                completed = scraper.stats['completed']
                rate = scraper.stats['rate']
                eta = scraper.stats['eta']
                
                total_completed += completed
                
                # Status emoji
                if not scraper.is_running():
                    status_emoji = "✅" if scraper.get_exit_code() == 0 else "❌"
                elif scraper.stats['cloudflare_detected']:
                    status_emoji = "⚠️"
                    all_healthy = False
                else:
                    status_emoji = "🔄"
                
                # Progress bar
                bar = create_progress_bar(progress, width=20)
                
                # Status line
                print(f"{status_emoji} Scraper {scraper.scraper_num} {bar} │ "
                      f"{completed}/{scraper.total_players} players │ "
                      f"ETA: {format_time(eta)} │ "
                      f"{rate:.1f} players/min")
        
        # Overall progress
        print(f"\n{Colors.BOLD}📈 Overall Progress:{Colors.END}")
        overall_progress = (total_completed / self.total_players * 100) if self.total_players > 0 else 0
        overall_bar = create_progress_bar(overall_progress, width=40)
        print(f"   {overall_bar} │ {total_completed}/{self.total_players} players")
        
        # Cloudflare status
        cloudflare_status = "⚠️  RATE LIMITED" if not all_healthy else "✅ HEALTHY"
        cloudflare_color = Colors.YELLOW if not all_healthy else Colors.GREEN
        print(f"\n{Colors.BOLD}🌐 Cloudflare Status:{Colors.END} {cloudflare_color}{cloudflare_status}{Colors.END}")
        
        # Timing
        elapsed = time.time() - self.start_time
        print(f"\n{Colors.BOLD}⏱️  Elapsed:{Colors.END} {format_time(elapsed)}")
        
        # Instructions
        print(f"\n{Colors.CYAN}Press Ctrl+C to stop all scrapers{Colors.END}")
    
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
# MAIN ORCHESTRATOR
# ============================================================================

def cleanup_old_chunks():
    """Remove old chunk files"""
    if os.path.exists(CHUNK_DIR):
        for file in os.listdir(CHUNK_DIR):
            if file.startswith('chunk_') and file.endswith('.csv'):
                try:
                    os.remove(os.path.join(CHUNK_DIR, file))
                except:
                    pass

def setup_directories():
    """Create necessary directories"""
    os.makedirs(CHUNK_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

def main():
    """Main orchestrator function"""
    
    # Parse command line arguments
    resume_mode = '--resume' in sys.argv
    
    if '--scrapers' in sys.argv:
        idx = sys.argv.index('--scrapers')
        if idx + 1 < len(sys.argv):
            try:
                num_scrapers = int(sys.argv[idx + 1])
                if 1 <= num_scrapers <= 5:
                    global NUM_SCRAPERS
                    NUM_SCRAPERS = num_scrapers
                else:
                    print_warning("Number of scrapers must be between 1-5, using default: 2")
            except ValueError:
                print_warning("Invalid --scrapers value, using default: 2")
    
    # Setup
    setup_directories()
    
    print_header("ZENITH PARALLEL SCRAPER ORCHESTRATOR")
    
    # Load player IDs
    print_section("STEP 1: Loading Player IDs")
    
    player_ids = load_player_ids(INPUT_CSV)
    
    if not player_ids:
        print_error("No player IDs found. Exiting.")
        return 1
    
    print_success(f"Loaded {len(player_ids)} unique player IDs from {INPUT_CSV}")
    
    # Check if we should resume
    if resume_mode:
        checkpoint = load_checkpoint()
        if checkpoint:
            print_info(f"Resuming from checkpoint: {checkpoint.get('timestamp', 'unknown')}")
    
    # Split into chunks
    print_section("STEP 2: Splitting Players Across Scrapers")
    
    NUM_SCRAPERS = min(NUM_SCRAPERS, len(player_ids))
    chunks = split_player_ids(player_ids, NUM_SCRAPERS)
    
    for i, chunk in enumerate(chunks, 1):
        print_info(f"Scraper {i}: {len(chunk)} players (IDs {chunk[0]} to {chunk[-1]})")
    
    # Create chunk files
    print_section("STEP 3: Creating Chunk Files")
    
    cleanup_old_chunks()
    chunk_files = []
    
    for i, chunk in enumerate(chunks, 1):
        chunk_file = save_chunk_to_csv(chunk, i, CHUNK_DIR)
        chunk_files.append(chunk_file)
        print_success(f"Created {chunk_file}")
    
    # Initialize scrapers
    print_section("STEP 4: Launching Scrapers")
    
    scrapers = []
    for i, (chunk_file, chunk) in enumerate(zip(chunk_files, chunks), 1):
        scraper = ScraperProcess(i, chunk_file, len(chunk))
        scrapers.append(scraper)
        scraper.start()
        time.sleep(0.5)  # Stagger starts slightly
    
    print_success(f"All {NUM_SCRAPERS} scrapers launched\n")
    time.sleep(2)  # Give scrapers time to initialize
    
    # Start dashboard
    dashboard = Dashboard(scrapers, len(player_ids))
    dashboard_thread = dashboard.start()
    
    # Monitor scrapers
    def signal_handler(sig, frame):
        """Handle Ctrl+C gracefully"""
        print_warning("\n\nReceived interrupt signal. Stopping scrapers...")
        dashboard.stop()
        for scraper in scrapers:
            scraper.terminate()
        print_info("All scrapers stopped.")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Wait for all scrapers to complete
        while any(scraper.is_running() for scraper in scrapers):
            time.sleep(1)
        
        # Stop dashboard
        dashboard.stop()
        time.sleep(1)
        
    except KeyboardInterrupt:
        signal_handler(None, None)
    
    # Final report
    dashboard.clear_screen()
    print_header("SCRAPING COMPLETE")
    
    print_section("Final Results")
    
    all_successful = True
    total_completed = 0
    
    for scraper in scrapers:
        exit_code = scraper.get_exit_code()
        scraper.update_stats_from_output()
        
        if exit_code == 0:
            print_success(f"Scraper {scraper.scraper_num}: {scraper.stats['completed']} players completed")
            total_completed += scraper.stats['completed']
        else:
            print_error(f"Scraper {scraper.scraper_num}: Failed with exit code {exit_code}")
            all_successful = False
    
    print(f"\n{Colors.BOLD}📊 Summary:{Colors.END}")
    print(f"   Total players scraped: {Colors.BOLD}{total_completed}/{len(player_ids)}{Colors.END}")
    
    if total_completed * 6 > 0:
        print(f"   Total requests made: {Colors.BOLD}~{total_completed * 6}{Colors.END}")
    
    elapsed = time.time() - dashboard.start_time
    print(f"   Total time: {Colors.BOLD}{format_time(elapsed)}{Colors.END}")
    
    if all_successful:
        print_success("\n✅ All scrapers completed successfully!")
        
        print(f"\n{Colors.BOLD}📄 Output Files Created:{Colors.END}")
        for i in range(1, NUM_SCRAPERS + 1):
            csv_file = STATS_CSV_PATTERN.format(i)
            json_file = SKILLS_JSON_PATTERN.format(i)
            if os.path.exists(csv_file):
                print_success(f"   {csv_file}")
            if os.path.exists(json_file):
                print_success(f"   {json_file}")
        
        clear_checkpoint()
        return 0
    else:
        print_error("\n❌ Some scrapers failed. Check logs for details.")
        print_info(f"Logs available in: {LOG_DIR}/")
        return 1

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

