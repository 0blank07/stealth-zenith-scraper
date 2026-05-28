# Zenith Scraper (stealth-zenith-scraper)

A comprehensive, parallelized data pipeline for discovering, scraping, and importing player statistics and skills from RenderZ (likely for FIFA/FC Mobile) into a PostgreSQL database.

## Project Overview

The Zenith Scraper is designed for high-throughput, Cloudflare-aware data extraction. It follows a modular "Discovery → Scraping → Importing" architecture, orchestrated by a central update script.

### Key Technologies
- **Language:** Python 3.x
- **Database:** PostgreSQL
- **Web Scraping:** `aiohttp`, `requests`, `BeautifulSoup4`
- **Browser Automation:** `Playwright` with `playwright-stealth` (for Discovery)
- **Data Formats:** CSV, JSON
- **Database Drivers:** `psycopg2`, `ijson`

---

## System Architecture

### 1. Discovery (`stats_colors.py`)
Identifies new or missing player IDs by interacting with the RenderZ search API.
- Uses Playwright to capture real browser headers and bypass initial bot detection.
- Compares discovered IDs against the local `player_stats` table.
- Outputs: `final_missing_ids.csv` (the work list for scrapers).

### 2. Scraping (`run_scrapers.py` & `stats_scrape.py`)
Parallelized scraping of player details and skill trees.
- `run_scrapers.py`: Orchestrates multiple `stats_scrape.py` instances. Splits the work list into chunks for parallel execution.
- `stats_scrape.py`: Cloudflare-aware scraper. Implements exponential backoff, adaptive rate limiting, and checkpoint/resume functionality.
- Outputs: `players_stats_{num}.csv` and `players_skills_{num}.json`.

### 3. Importing (`master_import.py`)
Orchestrates the parallel import of all scraped data into PostgreSQL.
- Discovers all output files from the scrapers.
- Parallelizes the execution of sub-importers:
    - `append_player_stats.py`: Imports core stats into `player_stats`.
    - `append_skills.py`: Imports skill data into `player_available_skills` and `player_skills_meta`.
    - `import_skill_tree_perfect.py`: Processes complex skill tree data.

### 4. Orchestration (`weekly_update.py`)
The "one-click" solution for the entire pipeline.
- Executes Discovery → Scraping → Importing → Verification in sequence.
- Provides real-time progress tracking, error handling, and automated backups.

---

## Database Configuration

The project expects a PostgreSQL database named `zenith_data`.

- **User:** `zenith_bot`
- **Tables:**
    - `player_stats`: Core player information and attributes.
    - `player_available_skills`: Skills associated with players.
    - `player_skills_meta`: Metadata for player skills.
    - `skill_level_boosts`: Stat boosts associated with skill levels.

Database credentials and settings can be managed via environment variables or `.env` file (see `append_player_stats.py`).

---

## Building and Running

### Prerequisites
1. **Python 3.x**
2. **PostgreSQL** installed and running.
3. **Virtual Environment:** Recommended to use the existing `.venv`.
4. **Dependencies:**
   ```bash
   pip install ijson aiohttp requests beautifulsoup4 psycopg2-binary playwright playwright-stealth python-dotenv
   playwright install chromium
   ```

### Key Commands
- **Full Update:**
  ```bash
  python weekly_update.py
  ```
- **Discovery Only:**
  ```bash
  python stats_colors.py
  ```
- **Scraping Only (Resume):**
  ```bash
  python run_scrapers.py --resume
  ```
- **Import Only:**
  ```bash
  python master_import.py
  ```

---

## Development Conventions

- **Parallelism:** The project extensively uses `subprocess` and `threading` to maximize throughput.
- **Resilience:** Most long-running scripts include checkpoint files (`.json`) to allow resuming after failure.
- **Logging:** Logs are stored in the `logs/` directory. Terminal output is color-coded for readability.
- **Safety:** Scrapers are designed to be "Cloudflare-safe" with adaptive sleep intervals and human-like request patterns.
- **Error Handling:** Extensive use of try-except blocks with detailed reporting to console and log files.
