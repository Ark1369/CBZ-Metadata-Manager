#To Run - pip install requests and then python cbz_metadata_manager.py 
#To Run via UV - pip install uv and then uv run cbz_metadata_manager.py

# Requires-Python: >=3.8
# Requires-Dist: requests

import os
import zipfile
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json
import requests
import logging
from difflib import get_close_matches, SequenceMatcher
import re
from urllib.parse import quote
import webbrowser
from urllib.parse import urlparse, parse_qs
import sqlite3
from datetime import datetime
import tkinter.simpledialog
from threading import Thread
import unicodedata
from collections import defaultdict
import time
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread, Lock

# Setup logging
logging.basicConfig(filename='cbz_metadata.log', level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')

DUMP_PATH = "series.jsonl"
CACHE_PATH = "api_cache.json"
DATABASE_PATH = "metadata_database.db"

# Database initialization
def init_database():
    """Initialize the SQLite database for storing series metadata"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Create series_metadata table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS series_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_name TEXT UNIQUE NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create series_aliases table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS series_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_name TEXT NOT NULL,
            alias TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (series_name) REFERENCES series_metadata (series_name) ON DELETE CASCADE,
            UNIQUE(series_name, alias)
        )
    ''')
    
    # Create indexes for faster lookups
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_series_name ON series_metadata(series_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_aliases_series ON series_aliases(series_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_aliases_alias ON series_aliases(alias)')
    
    conn.commit()
    conn.close()

def center_window(window, width=None, height=None):
    """Center a window on the screen"""
    # Force the window to update its geometry
    window.update_idletasks()
    window.update()
    
    # Get window dimensions
    if width and height:
        window_width = width
        window_height = height
    else:
        window_width = window.winfo_reqwidth()
        window_height = window.winfo_reqheight()
        
        # Fallback to specified dimensions if required dimensions are too small
        if window_width < 100:
            window_width = width or 400
        if window_height < 100:
            window_height = height or 300
    
    # Get screen dimensions
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    
    # Calculate position
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2
    
    # Ensure window doesn't go off screen
    x = max(0, x)
    y = max(0, y)
    
    window.geometry(f"{window_width}x{window_height}+{x}+{y}")


class ToolTip:
    """Create a tooltip for a given widget"""
    def __init__(self, widget, text='widget info'):
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.tipwindow = None
        self.id = None
        self.x = self.y = 0
    
    def enter(self, event=None):
        self.schedule()
    
    def leave(self, event=None):
        self.unschedule()
        self.hidetip()
    
    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.showtip)
    
    def unschedule(self):
        id = self.id
        self.id = None
        if id:
            self.widget.after_cancel(id)
    
    def showtip(self, event=None):
        # Get widget position relative to screen
        x = self.widget.winfo_rootx() + 25
        y = self.widget.winfo_rooty() + 20
        
        # Creates a toplevel window
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        label = tk.Label(tw, text=self.text, justify='left',
                      background="#ffffe0", relief='solid', borderwidth=1,
                      font=("tahoma", "8", "normal"), wraplength=300)
        label.pack(ipadx=1)
    
    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()
            
class SeriesDatabase:
    """Class to handle series metadata database operations"""
    
    def __init__(self, db_path="series.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Create series_metadata table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS series_metadata (
                    series_name TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            
            # Create series_aliases table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS series_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_name TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    FOREIGN KEY (series_name) REFERENCES series_metadata (series_name) ON DELETE CASCADE,
                    UNIQUE(series_name, alias)
                )
            ''')
            
            conn.commit()
        except Exception as e:
            logging.error(f"Error initializing database: {e}")
        finally:
            conn.close()
    
    def save_series_metadata(self, series_name, metadata):
        """Save or update series metadata in the database"""
        if not series_name or not series_name.strip():
            raise ValueError("Series name cannot be empty")
        
        series_name = series_name.strip()
        metadata_json = json.dumps(metadata, ensure_ascii=False, indent=2)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO series_metadata 
                (series_name, metadata_json, updated_at) 
                VALUES (?, ?, ?)
            ''', (series_name, metadata_json, datetime.now().isoformat()))
            
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error saving series metadata: {e}")
            return False
        finally:
            conn.close()
    
    def load_series_metadata(self, series_name):
        """Load series metadata from the database"""
        if not series_name or not series_name.strip():
            return None
        
        series_name = series_name.strip()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'SELECT metadata_json FROM series_metadata WHERE series_name = ?',
                (series_name,)
            )
            result = cursor.fetchone()
            
            if result:
                return json.loads(result[0])
            return None
        except Exception as e:
            logging.error(f"Error loading series metadata: {e}")
            return None
        finally:
            conn.close()
    
    def get_all_series(self):
        """Get all series names from the database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'SELECT series_name, updated_at FROM series_metadata ORDER BY updated_at DESC'
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Error getting all series: {e}")
            return []
        finally:
            conn.close()
    
    def delete_series(self, series_name):
        """Delete a series from the database (aliases are deleted automatically via CASCADE)"""
        if not series_name or not series_name.strip():
            return False
        
        series_name = series_name.strip()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM series_metadata WHERE series_name = ?', (series_name,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logging.error(f"Error deleting series metadata: {e}")
            return False
        finally:
            conn.close()
    
    def search_series(self, search_term):
        """Search for series by name (case-insensitive partial match)"""
        if not search_term or not search_term.strip():
            return []
        
        search_term = f"%{search_term.strip()}%"
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'SELECT series_name, updated_at FROM series_metadata WHERE series_name LIKE ? ORDER BY updated_at DESC',
                (search_term,)
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Error searching series: {e}")
            return []
        finally:
            conn.close()
    
    def save_series_aliases(self, series_name, aliases):
        """Save aliases for a series (replaces all existing aliases)"""
        if not series_name or not series_name.strip():
            raise ValueError("Series name cannot be empty")
        
        series_name = series_name.strip()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Delete existing aliases
            cursor.execute('DELETE FROM series_aliases WHERE series_name = ?', (series_name,))
            
            # Insert new aliases
            if aliases:
                alias_data = [(series_name, alias.strip()) for alias in aliases if alias.strip()]
                cursor.executemany(
                    'INSERT INTO series_aliases (series_name, alias) VALUES (?, ?)',
                    alias_data
                )
            
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error saving series aliases: {e}")
            return False
        finally:
            conn.close()
    
    def load_series_aliases(self, series_name):
        """Load aliases for a series"""
        if not series_name or not series_name.strip():
            return []
        
        series_name = series_name.strip()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                'SELECT alias FROM series_aliases WHERE series_name = ? ORDER BY alias',
                (series_name,)
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Error loading series aliases: {e}")
            return []
        finally:
            conn.close()
    
    def get_all_series_with_aliases(self):
        """Get all series with their aliases for matching"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
    
        try:
            cursor.execute('''
                SELECT sm.series_name, sm.updated_at, 
                       GROUP_CONCAT(sa.alias, '|') as aliases
                FROM series_metadata sm
                LEFT JOIN series_aliases sa ON sm.series_name = sa.series_name
                GROUP BY sm.series_name, sm.updated_at
                ORDER BY sm.updated_at DESC
            ''')
    
            results = []
            for row in cursor.fetchall():
                series_name = row[0]
                updated_at = row[1]
                aliases = row[2].split('|') if row[2] else []
                results.append((series_name, updated_at, aliases))
    
            return results
    
        except Exception as e:
            logging.error(f"Error getting series with aliases: {e}")
            return []
    
        finally:
            conn.close()



# ==============================================================================
# 4. ALIAS EDITOR DIALOG
# ==============================================================================


class AliasEditorDialog(tk.Toplevel):
    """Dialog for editing series aliases"""
    
    def __init__(self, parent, series_name, current_aliases=None):
        super().__init__(parent)
        self.parent = parent
        self.series_name = series_name
        self.aliases = current_aliases or []
        self.result = None
        
        self.title(f"Edit Aliases - {series_name}")
        self.transient(parent)
        self.grab_set()
        
        self.create_widgets()
        self.populate_aliases()
        
        # Center the dialog LAST
        self.geometry("500x400")
        self.update_idletasks()
        center_window(self, 500, 400)

        
    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Info label
        info_label = ttk.Label(main_frame, text=f"Series: {self.series_name}")
        info_label.pack(anchor='w', pady=(0, 10))
        ToolTip(info_label, f"Editing aliases for the series: {self.series_name}")
        
        # Aliases frame
        aliases_frame = ttk.LabelFrame(main_frame, text="Aliases (one per line)", padding=5)
        aliases_frame.pack(fill='both', expand=True, pady=(0, 10))
        
        # Text widget for aliases
        self.aliases_text = tk.Text(aliases_frame, height=15, width=50)
        scrollbar = ttk.Scrollbar(aliases_frame, orient='vertical', command=self.aliases_text.yview)
        self.aliases_text.configure(yscrollcommand=scrollbar.set)
        
        self.aliases_text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        ToolTip(self.aliases_text, "Enter alternative names for this series, one per line.\nThese will be used for automatic file matching.")
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x')
        
        save_btn = ttk.Button(button_frame, text="Save", command=self.save_aliases)
        save_btn.pack(side='left', padx=(0, 5))
        ToolTip(save_btn, "Save the aliases and close the dialog")
        
        cancel_btn = ttk.Button(button_frame, text="Cancel", command=self.cancel)
        cancel_btn.pack(side='left')
        ToolTip(cancel_btn, "Cancel editing and close the dialog without saving")

    def populate_aliases(self):
        """Populate the text widget with current aliases"""
        if self.aliases:
            self.aliases_text.insert('1.0', '\n'.join(self.aliases))

    def save_aliases(self):
        """Save the aliases and close dialog"""
        content = self.aliases_text.get('1.0', 'end-1c')
        self.result = [line.strip() for line in content.split('\n') if line.strip()]
        self.destroy()

    def cancel(self):
        """Cancel and close dialog"""
        self.result = None
        self.destroy()


# Initialize database
series_db = SeriesDatabase()

local_dump = []
if os.path.exists(DUMP_PATH):
    try:
        with open(DUMP_PATH, 'r', encoding='utf-8') as f:
            local_dump = [json.loads(line) for line in f if line.strip()]
    except Exception as e:
        logging.error(f"Failed to load local dump: {e}")

if os.path.exists(CACHE_PATH):
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            api_cache = json.load(f)
    except Exception as e:
        logging.error(f"Failed to load API cache: {e}")
        api_cache = {}
else:
    api_cache = {}

def is_url(text):
    return text.strip().lower().startswith(("http://", "https://"))
    
def get_metadata_from_direct_url(url):
    try:
        parsed = urlparse(url)
        if "mangabaka.dev" not in parsed.netloc:
            return []

        # Extract first numeric segment from the path
        path_parts = parsed.path.strip("/").split("/")
        entry_id = next((part for part in path_parts if part.isdigit()), None)

        if not entry_id:
            logging.warning(f"No entry ID found in Mangabaka URL: {url}")
            return []

        # Call Mangabaka entry endpoint
        response = requests.get(
            f"https://mangabaka.dev/api/entry?id={entry_id}",
            timeout=10,
            headers={'User-Agent': 'CBZ-Metadata-Tool/1.0'}
        )
        response.raise_for_status()

        entry = response.json()
        if isinstance(entry, dict):
            return [MetadataGUI.extract_metadata(entry)]

    except Exception as e:
        logging.error(f"Direct URL fetch failed: {e}")

    return []
    
def save_api_cache():
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(api_cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Failed to save API cache: {e}")

def normalize_romaji_cached(text, cache={}):
    """Normalize romaji with caching for performance - IMPROVED VERSION"""
    if not text or text in cache:
        return cache.get(text, "")
    
    original_text = text
    text = text.lower()
    
    # Handle macrons - more comprehensive mapping
    macron_map = {
        'ā': 'aa', 'ī': 'ii', 'ū': 'uu', 'ē': 'ee', 'ō': 'ou',
        'â': 'aa', 'ê': 'ee', 'î': 'ii', 'ô': 'ou', 'û': 'uu',
        'à': 'a', 'è': 'e', 'ì': 'i', 'ò': 'o', 'ù': 'u',
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
    }
    for k, v in macron_map.items():
        text = text.replace(k, v)
    
    # Unicode normalization
    text = unicodedata.normalize("NFKD", text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    
    # LESS aggressive cleanup - preserve more characters that might be important
    # Replace various dashes with spaces but keep other punctuation for now
    text = text.replace("–", " ").replace("—", " ").replace("-", " ")
    
    # Remove only clearly problematic symbols, keep more punctuation
    text = re.sub(r"[^\w\s'.!?:;]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    
    cache[original_text] = text
    return text

def build_merge_map(local_dump):
    """Build a map of merged entries to avoid duplicates"""
    merge_map = {}  # merged_id -> target_id
    active_ids = set()
    
    for entry in local_dump:
        entry_id = entry.get("id")
        state = entry.get("state", "").lower()
        merged_with = entry.get("merged_with")
        
        if state == "merged" and merged_with:
            merge_map[entry_id] = merged_with
        elif state == "active":
            active_ids.add(entry_id)
    
    return merge_map, active_ids

def resolve_merged_entry(entry_id, merge_map):
    """Resolve a merged entry to its final target"""
    original_id = entry_id
    visited = set()
    
    # Follow the merge chain to the end
    while entry_id in merge_map and entry_id not in visited:
        visited.add(entry_id)
        entry_id = merge_map[entry_id]
        
        # Prevent infinite loops
        if len(visited) > 10:
            logging.warning(f"Merge chain too long for ID {original_id}, stopping at {entry_id}")
            break
    
    return entry_id

def filter_merged_entries(entries, merge_map):
    """Filter out merged entries and resolve to final targets"""
    seen_final_ids = set()
    filtered_entries = []
    
    for entry in entries:
        entry_id = entry.get("id")
        state = entry.get("state", "").lower()
        
        # Skip if this is a merged entry
        if state == "merged":
            continue
        
        # Resolve any merges that might point to this entry
        final_id = resolve_merged_entry(entry_id, merge_map)
        
        # Only include if we haven't seen this final ID before
        if final_id not in seen_final_ids:
            seen_final_ids.add(final_id)
            
            # If the final_id is different from current entry_id,
            # we need to find the actual target entry
            if final_id != entry_id:
                # Find the target entry in the dump
                target_entry = next((e for e in local_dump if e.get("id") == final_id), None)
                if target_entry:
                    filtered_entries.append(target_entry)
                else:
                    logging.warning(f"Target entry {final_id} not found for merged entry {entry_id}")
            else:
                filtered_entries.append(entry)
    
    return filtered_entries

def find_best_match_merge_aware(title):
    """IMPROVED: Optimized search that handles merged entries properly"""
    if not local_dump:
        return []
    
    search_term = title.strip()
    if not search_term:
        return []
    
    # Build merge map once (you might want to cache this globally)
    merge_map, active_ids = build_merge_map(local_dump)
    
    # Pre-normalize search term once
    search_term_norm = normalize_romaji_cached(search_term)
    search_words = set(search_term_norm.split())
    search_len = len(search_term_norm)
    
    matches = []
    processed_final_ids = set()  # Track final IDs to avoid duplicates
    
    for entry in local_dump:
        entry_id = entry.get("id")
        state = entry.get("state", "").lower()
        
        # Skip merged entries entirely - they're obsolete
        if state == "merged":
            continue
        
        # Resolve this entry to its final ID (in case something points to it)
        final_id = resolve_merged_entry(entry_id, merge_map)
        
        # Skip if we've already processed this final ID
        if final_id in processed_final_ids:
            continue
        
        # Get the actual entry to use (might be different if there were merges)
        if final_id != entry_id:
            actual_entry = next((e for e in local_dump if e.get("id") == final_id), None)
            if not actual_entry:
                continue
        else:
            actual_entry = entry
        
        # Now do the matching logic on the actual entry
        texts_to_check = []
        
        # Primary titles
        for field in ["title", "native_title", "romanized_title"]:
            val = actual_entry.get(field)
            if val:
                texts_to_check.append(val)
        
        # Secondary titles
        secondary = actual_entry.get("secondary_titles")
        if isinstance(secondary, dict):
            for lang_titles in secondary.values():
                if isinstance(lang_titles, list):
                    for t in lang_titles:
                        if isinstance(t, dict) and t.get("title"):
                            texts_to_check.append(t["title"])
        
        best_score = 0
        best_match_text = None
        
        for text in texts_to_check:
            text_norm = normalize_romaji_cached(text)
            
            # Exact match check first
            if text_norm == search_term_norm:
                best_score = 100
                best_match_text = text
                break
            
            # BALANCED: More selective substring matching
            if search_term_norm in text_norm:
                ratio = search_len / len(text_norm)
                if ratio > 0.4:  # Slightly more restrictive than 0.3
                    score = min(95, int(65 + (ratio * 30)))  # Better scoring
                    if score > best_score:
                        best_score = score
                        best_match_text = text
            
            # BALANCED: Reverse substring check (text in search term) - more restrictive
            elif text_norm in search_term_norm and len(text_norm) >= 4:  # Minimum length requirement
                ratio = len(text_norm) / search_len
                if ratio > 0.4:  # More restrictive
                    score = min(85, int(50 + (ratio * 35)))
                    if score > best_score:
                        best_score = score
                        best_match_text = text
            
            # BALANCED: More selective word overlap check
            elif best_score < 75:
                text_words = set(text_norm.split())
                overlap = search_words & text_words
                
                if overlap and len(overlap) >= min(2, len(search_words)):  # Need at least 2 words or all words
                    overlap_ratio = len(overlap) / len(search_words) if search_words else 0
                    text_coverage = len(overlap) / len(text_words) if text_words else 0
                    
                    # Stricter requirements
                    if overlap_ratio >= 0.5:  # Back to more restrictive
                        word_score = int(45 + (overlap_ratio * 30))
                        if word_score > best_score:
                            best_score = word_score
                            best_match_text = text
                    elif text_coverage >= 0.6 and overlap_ratio >= 0.3:  # Good coverage + decent overlap
                        word_score = int(40 + (text_coverage * 25))
                        if word_score > best_score:
                            best_score = word_score
                            best_match_text = text
            
            # BALANCED: More restrictive fuzzy character-level matching
            if best_score < 50 and len(search_term_norm) <= 6:  # Only for very short terms
                # Simple character overlap for short terms
                search_chars = set(search_term_norm.replace(' ', ''))
                text_chars = set(text_norm.replace(' ', ''))
                char_overlap = len(search_chars & text_chars)
                
                # Much stricter character matching
                if char_overlap >= max(3, len(search_chars) * 0.8):  # Need most characters
                    char_score = int(30 + (char_overlap / len(search_chars)) * 20)
                    if char_score > best_score:
                        best_score = char_score
                        best_match_text = text
        
        # BALANCED: Higher threshold to avoid irrelevant results
        if best_score >= 65:  # Balanced threshold
            matches.append((actual_entry, best_score, best_match_text))
            processed_final_ids.add(final_id)
            
            # Early termination for perfect matches
            if best_score == 100 and len(matches) >= 30:
                break
    
    # Sort by score and return balanced number of results
    matches.sort(key=lambda x: x[1], reverse=True)
    result_entries = [m[0] for m in matches[:30]]  # Balanced result count
    
    # Debug logging
    logging.info(f"Merge-aware search for '{title}' found {len(result_entries)} unique entries")
    for i, (entry, score, match_text) in enumerate(matches[:5]):
        logging.info(f"  Result {i}: ID={entry.get('id')}, Score={score}, Title='{entry.get('title')}', Match='{match_text}'")
    
    return result_entries

# Global cache for merge map to avoid rebuilding it every time
_merge_map_cache = None
_merge_map_cache_size = 0

def get_cached_merge_map():
    """Get cached merge map or build it if needed"""
    global _merge_map_cache, _merge_map_cache_size
    
    if not local_dump:
        return {}, set()
    
    current_size = len(local_dump)
    
    # Rebuild cache if dump size changed
    if _merge_map_cache is None or _merge_map_cache_size != current_size:
        logging.info("Building merge map cache...")
        _merge_map_cache = build_merge_map(local_dump)
        _merge_map_cache_size = current_size
        merge_map, active_ids = _merge_map_cache
        logging.info(f"Merge map built: {len(merge_map)} merges, {len(active_ids)} active entries")
    
    return _merge_map_cache

def find_best_match_cached_merge_aware(title):
    """IMPROVED: Version that uses cached merge map for better performance"""
    if not local_dump:
        return []
    
    search_term = title.strip()
    if not search_term:
        return []
    
    # Use cached merge map
    merge_map, active_ids = get_cached_merge_map()
    
    # IMPROVED search logic
    search_term_norm = normalize_romaji_cached(search_term)
    search_words = set(search_term_norm.split())
    search_len = len(search_term_norm)
    
    matches = []
    processed_final_ids = set()
    
    for entry in local_dump:
        entry_id = entry.get("id")
        state = entry.get("state", "").lower()
        
        # Skip merged entries
        if state == "merged":
            continue
        
        # Resolve to final ID
        final_id = resolve_merged_entry(entry_id, merge_map)
        
        if final_id in processed_final_ids:
            continue
        
        # Get actual entry to use
        if final_id != entry_id:
            actual_entry = next((e for e in local_dump if e.get("id") == final_id), None)
            if not actual_entry:
                continue
        else:
            actual_entry = entry
        
        # Collect all searchable texts
        texts_to_check = []
        for field in ["title", "native_title", "romanized_title"]:
            val = actual_entry.get(field)
            if val:
                texts_to_check.append(val)
        
        secondary = actual_entry.get("secondary_titles")
        if isinstance(secondary, dict):
            for lang_titles in secondary.values():
                if isinstance(lang_titles, list):
                    for t in lang_titles:
                        if isinstance(t, dict) and t.get("title"):
                            texts_to_check.append(t["title"])
        
        best_score = 0
        best_match_text = None
        
        for text in texts_to_check:
            text_norm = normalize_romaji_cached(text)
            
            # Exact match
            if text_norm == search_term_norm:
                best_score = 100
                best_match_text = text
                break
            
            # IMPROVED substring matching
            elif search_term_norm in text_norm:
                ratio = search_len / len(text_norm)
                if ratio > 0.3:
                    score = min(95, int(60 + (ratio * 35)))
                    if score > best_score:
                        best_score = score
                        best_match_text = text
            
            # Reverse substring
            elif text_norm in search_term_norm:
                ratio = len(text_norm) / search_len
                if ratio > 0.3:
                    score = min(90, int(50 + (ratio * 40)))
                    if score > best_score:
                        best_score = score
                        best_match_text = text
            
            # IMPROVED word overlap
            elif best_score < 80:
                text_words = set(text_norm.split())
                overlap = search_words & text_words
                
                if overlap:
                    overlap_ratio = len(overlap) / len(search_words) if search_words else 0
                    text_coverage = len(overlap) / len(text_words) if text_words else 0
                    
                    if overlap_ratio >= 0.4:
                        word_score = int(40 + (overlap_ratio * 40))
                        if word_score > best_score:
                            best_score = word_score
                            best_match_text = text
                    elif text_coverage >= 0.5:
                        word_score = int(35 + (text_coverage * 35))
                        if word_score > best_score:
                            best_score = word_score
                            best_match_text = text
            
            # Character-level fuzzy matching for short terms
            if best_score < 60 and len(search_term_norm) <= 8:
                search_chars = set(search_term_norm.replace(' ', ''))
                text_chars = set(text_norm.replace(' ', ''))
                char_overlap = len(search_chars & text_chars)
                
                if char_overlap >= max(2, len(search_chars) * 0.6):
                    char_score = int(25 + (char_overlap / len(search_chars)) * 25)
                    if char_score > best_score:
                        best_score = char_score
                        best_match_text = text
        
        # BALANCED: Tighter threshold
        if best_score >= 65:
            matches.append((actual_entry, best_score, best_match_text))
            processed_final_ids.add(final_id)
            
            # Less aggressive early termination
            if best_score == 100 and len(matches) >= 20:
                break
    
    matches.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in matches[:30]]

def build_search_index(local_dump):
    """Build a search index for faster lookups - call this once when loading data"""
    word_to_entries = defaultdict(set)
    entry_texts = {}
    
    for i, entry in enumerate(local_dump):
        entry_id = entry.get("id")
        if not entry_id:
            continue
            
        all_texts = []
        
        # Collect all searchable text
        for field in ["title", "native_title", "romanized_title"]:
            val = entry.get(field)
            if val:
                all_texts.append(val)
        
        secondary = entry.get("secondary_titles")
        if isinstance(secondary, dict):
            for lang_titles in secondary.values():
                if isinstance(lang_titles, list):
                    for t in lang_titles:
                        if isinstance(t, dict) and t.get("title"):
                            all_texts.append(t["title"])
        
        # Store normalized texts for this entry
        entry_texts[entry_id] = [(text, normalize_romaji_cached(text)) for text in all_texts]
        
        # IMPROVED: Index more comprehensively
        for text, text_norm in entry_texts[entry_id]:
            # Index full words
            for word in text_norm.split():
                if len(word) > 1:
                    word_to_entries[word].add(entry_id)
            
            # IMPROVED: Index character n-grams for better partial matching
            if len(text_norm.replace(' ', '')) >= 3:
                clean_text = text_norm.replace(' ', '')
                for i in range(len(clean_text) - 2):
                    trigram = clean_text[i:i+3]
                    word_to_entries[f"__{trigram}"].add(entry_id)
    
    return word_to_entries, entry_texts

def find_best_match_indexed(title, word_index, entry_texts):
    """IMPROVED: Ultra-fast search using pre-built index"""
    if not title.strip():
        return []
    
    search_term_norm = normalize_romaji_cached(title.strip())
    search_words = set(search_term_norm.split())
    
    # Find candidate entries using index
    candidate_ids = set()
    
    # Word-based candidates
    for word in search_words:
        if word in word_index:
            candidate_ids.update(word_index[word])
    
    # IMPROVED: Also try n-gram matching for partial matches
    if len(candidate_ids) < 20:  # If we don't have many candidates, try harder
        clean_search = search_term_norm.replace(' ', '')
        if len(clean_search) >= 3:
            for i in range(len(clean_search) - 2):
                trigram = clean_search[i:i+3]
                trigram_key = f"__{trigram}"
                if trigram_key in word_index:
                    candidate_ids.update(word_index[trigram_key])
    
    if not candidate_ids:
        return []
    
    # Score only the candidates with IMPROVED scoring
    matches = []
    for entry_id in candidate_ids:
        if entry_id not in entry_texts:
            continue
            
        entry = next((e for e in local_dump if e.get("id") == entry_id), None)
        if not entry:
            continue
        
        best_score = 0
        best_match_text = None
        
        for text, text_norm in entry_texts[entry_id]:
            # Exact match
            if text_norm == search_term_norm:
                best_score = 100
                best_match_text = text
                break
            
            # BALANCED substring matching
            elif search_term_norm in text_norm:
                ratio = len(search_term_norm) / len(text_norm)
                if ratio > 0.4:
                    score = min(95, int(65 + (ratio * 30)))
                    if score > best_score:
                        best_score = score
                        best_match_text = text
            
            # Reverse substring with restrictions
            elif text_norm in search_term_norm and len(text_norm) >= 4:
                ratio = len(text_norm) / len(search_term_norm)
                if ratio > 0.4:
                    score = min(85, int(50 + (ratio * 35)))
                    if score > best_score:
                        best_score = score
                        best_match_text = text
            
            # Word overlap
            else:
                text_words = set(text_norm.split())
                overlap = search_words & text_words
                
                if overlap and len(overlap) >= min(2, len(search_words)):
                    overlap_ratio = len(overlap) / len(search_words) if search_words else 0
                    text_coverage = len(overlap) / len(text_words) if text_words else 0
                    
                    if overlap_ratio >= 0.5:
                        word_score = int(45 + (overlap_ratio * 30))
                        if word_score > best_score:
                            best_score = word_score
                            best_match_text = text
                    elif text_coverage >= 0.6 and overlap_ratio >= 0.3:
                        word_score = int(40 + (text_coverage * 25))
                        if word_score > best_score:
                            best_score = word_score
                            best_match_text = text
                
                # Character-level matching for very short terms only
                if best_score < 50 and len(search_term_norm) <= 6:
                    search_chars = set(search_term_norm.replace(' ', ''))
                    text_chars = set(text_norm.replace(' ', ''))
                    char_overlap = len(search_chars & text_chars)
                    
                    if char_overlap >= max(3, len(search_chars) * 0.8):
                        char_score = int(30 + (char_overlap / len(search_chars)) * 20)
                        if char_score > best_score:
                            best_score = char_score
                            best_match_text = text
        
        # BALANCED: Stricter threshold
        if best_score >= 65:
            matches.append((entry, best_score, best_match_text))
    
    matches.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in matches[:30]]
    
def get_metadata_from_dump_or_api(title, local_only=False):
    """Fixed version with optimized search and better error handling"""
    if not title or not title.strip():
        return []
    
    # Handle direct URL case
    if is_url(title):
        if not local_only:
            return get_metadata_from_direct_url(title)
        else:
            return []
    
    logging.info(f"Fetching metadata for: {title}")
    
    # LOCAL SEARCH - Use merge-aware version
    matches = find_best_match_cached_merge_aware(title)  # Handles merged entries
    if matches:
        try:
            # Extract metadata from matches
            metadata_results = []
            for match in matches:
                metadata = MetadataGUI.extract_metadata(match)
                metadata_results.append(metadata)
            
            # DEBUG: Log what we found locally
            logging.info(f"Found {len(metadata_results)} local matches")
            for i, meta in enumerate(metadata_results[:3]):  # Log first 3
                logging.info(f"  Local match {i}: ID={meta.get('entry_id')}, Title='{meta.get('Title')}'")
            
            return metadata_results
            
        except Exception as e:
            logging.error(f"Error processing local matches: {e}")
            # Continue to API search if local processing fails
    
    # If local_only mode and no matches, return empty
    if local_only:
        logging.info("No local matches found and local_only=True")
        return []
    
    # API SEARCH - Only if not local_only
    query_key = title.lower().strip()
    
    # Check API cache first
    if query_key in api_cache:
        logging.info("Loaded metadata from API cache")
        cached_result = api_cache[query_key]
        
        # Handle different cache formats
        if isinstance(cached_result, dict):
            return [cached_result]
        elif isinstance(cached_result, list):
            return cached_result
        else:
            logging.warning(f"Invalid cache format for '{query_key}': {type(cached_result)}")
            # Clear invalid cache entry
            del api_cache[query_key]
    
    # Make API request
    try:
        encoded_title = quote(title)
        api_url = f"https://mangabaka.dev/api/search?query={encoded_title}"
        logging.info(f"Making API request to: {api_url}")
        
        response = requests.get(
            api_url,
            timeout=10,
            headers={'User-Agent': 'CBZ-Metadata-Tool/1.0'}
        )
        response.raise_for_status()
        
        data = response.json()
        logging.info(f"API returned {len(data) if isinstance(data, list) else 'non-list'} results")
        
        if data and isinstance(data, list):
            # Process API results
            results = []
            for i, item in enumerate(data):
                try:
                    metadata = MetadataGUI.extract_metadata(item)
                    results.append(metadata)
                    
                    # DEBUG: Log API results
                    if i < 3:  # Log first 3
                        logging.info(f"  API result {i}: ID={metadata.get('entry_id')}, Title='{metadata.get('Title')}'")
                        
                except Exception as e:
                    logging.error(f"Error extracting metadata from API result {i}: {e}")
                    continue
            
            if results:
                # Cache the results - store the full list, not just first result
                api_cache[query_key] = results
                save_api_cache()
                logging.info(f"Cached {len(results)} API results")
                return results
            else:
                logging.warning("API returned data but no valid metadata could be extracted")
        else:
            logging.warning(f"API returned unexpected data format: {type(data)}")
            
    except requests.exceptions.Timeout:
        logging.error(f"API request timeout for: {title}")
    except requests.exceptions.ConnectionError:
        logging.error(f"API connection error for: {title}")
    except requests.exceptions.HTTPError as e:
        logging.error(f"API HTTP error for '{title}': {e.response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"API request error for '{title}': {e}")
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error for '{title}': {e}")
    except Exception as e:
        logging.error(f"Unexpected API error for '{title}': {e}")
    
    # Return empty list if everything fails
    logging.info(f"No metadata found for: {title}")
    return []

# Indexed search for very large datasets
def get_metadata_from_dump_or_api_indexed(title, local_only=False, word_index=None, entry_texts=None):
    """Ultra-fast version using pre-built search index"""
    if not title or not title.strip():
        return []
    
    if is_url(title):
        if not local_only:
            return get_metadata_from_direct_url(title)
        else:
            return []
    
    logging.info(f"Fetching metadata (indexed) for: {title}")
    
    # LOCAL SEARCH with index
    if word_index and entry_texts:
        matches = find_best_match_indexed(title, word_index, entry_texts)
        if matches:
            try:
                metadata_results = []
                for match in matches:
                    metadata = MetadataGUI.extract_metadata(match)
                    metadata_results.append(metadata)
                
                logging.info(f"Found {len(metadata_results)} indexed local matches")
                return metadata_results
                
            except Exception as e:
                logging.error(f"Error processing indexed matches: {e}")
    
    # Fall back to regular search if index not available
    return get_metadata_from_dump_or_api(title, local_only)

# Helper function to initialize the search index (call once when loading data)
def initialize_search_index():
    """Call this once when your application starts to build the search index"""
    if local_dump:
        logging.info("Building search index...")
        word_index, entry_texts = build_search_index(local_dump)
        logging.info(f"Search index built: {len(word_index)} words, {len(entry_texts)} entries")
        return word_index, entry_texts
    return None, None

def create_comicinfo_xml(metadata):
    comic_info = ET.Element("ComicInfo")
    # Add XML schema attributes for better compatibility
    comic_info.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    comic_info.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")
    
    for key, value in metadata.items():
        if value and str(value).strip():  # Only add non-empty values
            element = ET.SubElement(comic_info, key)
            element.text = str(value).strip()
    
    # Create XML declaration and return properly formatted XML
    xml_str = ET.tostring(comic_info, encoding='unicode', method='xml')
    return f'<?xml version="1.0" encoding="utf-8"?>\n{xml_str}'

def insert_comicinfo_into_cbz(cbz_path, xml_data):
    try:
        # Create a temporary file to avoid corruption
        temp_path = cbz_path + ".tmp"
        
        with zipfile.ZipFile(cbz_path, 'r') as original:
            with zipfile.ZipFile(temp_path, 'w', compression=zipfile.ZIP_DEFLATED) as new_zip:
                # Copy all files except existing ComicInfo.xml
                for item in original.infolist():
                    if item.filename != "ComicInfo.xml":
                        data = original.read(item.filename)
                        new_zip.writestr(item, data)
                
                # Add new ComicInfo.xml
                new_zip.writestr("ComicInfo.xml", xml_data.encode('utf-8'))
        
        # Replace original with updated version
        os.replace(temp_path, cbz_path)
        logging.info(f"Successfully inserted metadata into: {cbz_path}")
        
    except Exception as e:
        logging.error(f"Failed to insert metadata into {cbz_path}: {e}")
        # Clean up temp file if it exists
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise

def extract_volume_from_filename(filename):
    """Extract volume number from filename"""
    patterns = [
        r'[Vv]ol\.?\s*(\d+)',
        r'[Vv]olume\s*(\d+)',
        r'\bv\.?\s*0*(\d+)\b',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            return match.group(1)
    
    return None

def extract_anilist_id_from_url(url):
    """Extract AniList manga ID from URL - handles comma-separated AND newline-separated URLs"""
    try:
        if not url or not isinstance(url, str):
            raise ValueError("URL is empty or not a string")
            
        original_url = url  # Keep original for error messages
        
        # Handle both comma-separated AND newline-separated URLs
        urls = []
        if ',' in url:
            # Comma-separated
            urls = [u.strip() for u in url.split(',') if u.strip()]
        else:
            # Newline-separated (or single URL)
            urls = [u.strip() for u in url.split('\n') if u.strip()]
        
        # Find the first AniList URL
        anilist_url = None
        for u in urls:
            if 'anilist.co' in u.lower():
                anilist_url = u.strip()
                break
        
        if not anilist_url:
            available_domains = [urlparse(u).netloc for u in urls[:5]]  # Show first 5 domains
            raise ValueError(f"No AniList URL found. Available domains: {available_domains}")
        
        # Clean the URL
        if not anilist_url.startswith(('http://', 'https://')):
            # Try to find a URL pattern within the string
            import re
            url_match = re.search(r'https?://[^\s]+', anilist_url)
            if url_match:
                anilist_url = url_match.group(0)
            else:
                raise ValueError(f"No valid URL pattern found in: {anilist_url}")
        
        parsed = urlparse(anilist_url)
        if 'anilist.co' not in parsed.netloc.lower():
            raise ValueError(f"URL is not an AniList URL: {anilist_url}")
        
        # Handle URLs like https://anilist.co/manga/12345/title-name
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) < 2:
            raise ValueError(f"URL path too short: {parsed.path}")
        
        if path_parts[0] != 'manga':
            raise ValueError(f"URL is not a manga URL (found: {path_parts[0]}): {anilist_url}")
        
        # Extract only the numeric part from path_parts[1]
        import re
        numeric_match = re.search(r'^\d+', path_parts[1])
        if not numeric_match:
            raise ValueError(f"No numeric ID found in URL path segment: {path_parts[1]}")
        
        return numeric_match.group(0)
        
    except Exception as e:
        logging.error(f"Error extracting AniList ID from URL '{url[:200]}...': {e}")
        raise  # Re-raise the exception so calling code can handle it with specific error messages



def rate_limit(max_calls_per_minute=60):
    """Decorator to limit API calls per minute"""
    min_interval = 60.0 / max_calls_per_minute
    last_called = [0.0]
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            left_to_wait = min_interval - elapsed
            if left_to_wait > 0:
                time.sleep(left_to_wait)
            ret = func(*args, **kwargs)
            last_called[0] = time.time()
            return ret
        return wrapper
    return decorator

@rate_limit(max_calls_per_minute=85)  # 90 rate limit for AniList

def make_anilist_request(query, variables, max_retries=3):
    """Make a rate-limited request to AniList API with retry logic"""
    for attempt in range(max_retries):
        try:
            response = requests.post(
                'https://graphql.anilist.co',
                json={'query': query, 'variables': variables},
                timeout=15,  # Increased timeout
                headers={'User-Agent': 'CBZ-Metadata-Tool/2.0'}
            )
            
            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                print(f"Rate limited. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
                continue
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            print(f"Request timeout on attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            raise
        except requests.exceptions.RequestException as e:
            print(f"Request error on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            raise
    
    return None

def fetch_anilist_metadata(anilist_id, max_pages_per_type=50):
    """Fetch metadata from AniList GraphQL API with pagination for both staff and characters"""
    if not anilist_id:
        return None
    
    # Validate that anilist_id is purely numeric
    if not str(anilist_id).isdigit():
        logging.error(f"Invalid AniList ID format: '{anilist_id}' - must be numeric")
        return None
    
    # First query to get basic info, characters, and staff
    query = '''
    query ($id: Int) {
        Media(id: $id, type: MANGA) {
            id
            title {
                romaji
                english
                native
            }
            characters(perPage: 100, sort: FAVOURITES_DESC) {
                pageInfo {
                    hasNextPage
                    currentPage
                }
                edges {
                    role
                    node {
                        name {
                            first
                            middle
                            last
                            full
                            native
                            alternative
                        }
                    }
                }
            }
            staff(perPage: 100, sort: FAVOURITES_DESC) {
                pageInfo {
                    hasNextPage
                    currentPage
                }
                edges {
                    role
                    node {
                        name {
                            full
                        }
                    }
                }
            }
        }
    }
    '''
    
    variables = {'id': int(anilist_id)}
    
    try:
        # Fetch first page
        print(f"Fetching initial data for AniList ID: {anilist_id}")
        data = make_anilist_request(query, variables)
        
        if not data or not ('data' in data and data['data']['Media']):
            logging.error(f"No data found for AniList ID: {anilist_id}")
            return None
        
        media_data = data['data']['Media']
        all_staff = media_data['staff']['edges'].copy()
        all_characters = media_data['characters']['edges'].copy()
        
        # Check if there are more pages of staff data
        staff_page_info = media_data['staff']['pageInfo']
        staff_current_page = staff_page_info['currentPage']
        staff_pages_fetched = 0
        
        # Fetch additional pages of staff if needed (with limits)
        while staff_page_info['hasNextPage'] and staff_pages_fetched < max_pages_per_type:
            staff_current_page += 1
            staff_pages_fetched += 1
            
            print(f"Fetching staff page {staff_current_page}...")
            
            # Query for next page of staff
            staff_query = '''
            query ($id: Int, $page: Int) {
                Media(id: $id, type: MANGA) {
                    staff(page: $page, perPage: 100, sort: FAVOURITES_DESC) {
                        pageInfo {
                            hasNextPage
                            currentPage
                        }
                        edges {
                            role
                            node {
                                name {
                                    full
                                }
                            }
                        }
                    }
                }
            }
            '''
            
            staff_variables = {'id': int(anilist_id), 'page': staff_current_page}
            
            try:
                staff_data = make_anilist_request(staff_query, staff_variables)
                if staff_data and 'data' in staff_data and staff_data['data']['Media']:
                    staff_page = staff_data['data']['Media']['staff']
                    all_staff.extend(staff_page['edges'])
                    staff_page_info = staff_page['pageInfo']
                else:
                    print(f"No more staff data available at page {staff_current_page}")
                    break
            except Exception as e:
                print(f"Error fetching staff page {staff_current_page}: {e}")
                break
        
        if staff_pages_fetched >= max_pages_per_type and staff_page_info.get('hasNextPage'):
            print(f"Reached maximum staff pages limit ({max_pages_per_type}). Some staff may not be included.")
        
        # Check if there are more pages of character data
        char_page_info = media_data['characters']['pageInfo']
        char_current_page = char_page_info['currentPage']
        char_pages_fetched = 0
        
        # Fetch additional pages of characters if needed (with limits)
        while char_page_info['hasNextPage'] and char_pages_fetched < max_pages_per_type:
            char_current_page += 1
            char_pages_fetched += 1
            
            print(f"Fetching characters page {char_current_page}...")
            
            # Query for next page of characters
            char_query = '''
            query ($id: Int, $page: Int) {
                Media(id: $id, type: MANGA) {
                    characters(page: $page, perPage: 100, sort: FAVOURITES_DESC) {
                        pageInfo {
                            hasNextPage
                            currentPage
                        }
                        edges {
                            role
                            node {
                                name {
                                    first
                                    middle
                                    last
                                    full
                                    native
                                    alternative
                                }
                            }
                        }
                    }
                }
            }
            '''
            
            char_variables = {'id': int(anilist_id), 'page': char_current_page}
            
            try:
                char_data = make_anilist_request(char_query, char_variables)
                if char_data and 'data' in char_data and char_data['data']['Media']:
                    char_page = char_data['data']['Media']['characters']
                    all_characters.extend(char_page['edges'])
                    char_page_info = char_page['pageInfo']
                else:
                    print(f"No more character data available at page {char_current_page}")
                    break
            except Exception as e:
                print(f"Error fetching characters page {char_current_page}: {e}")
                break
        
        if char_pages_fetched >= max_pages_per_type and char_page_info.get('hasNextPage'):
            print(f"Reached maximum character pages limit ({max_pages_per_type}). Some characters may not be included.")
        
        # Update media_data with all staff and characters
        media_data['staff']['edges'] = all_staff
        media_data['characters']['edges'] = all_characters
        
        print(f"Result: Total staff members fetched: {len(all_staff)} (from {staff_pages_fetched + 1} pages)")
        print(f"Result: Total characters fetched: {len(all_characters)} (from {char_pages_fetched + 1} pages)")
        
        return parse_anilist_data(media_data)
            
    except requests.exceptions.RequestException as e:
        logging.error(f"AniList API request error: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"AniList JSON decode error: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected AniList API error: {e}")
        return None

def construct_character_name(name_obj):
    """Construct the most complete character name from available fields"""
    if not name_obj:
        return None
    
    # Try to construct from first, middle, last with proper null checks
    first = (name_obj.get('first') or '').strip()
    middle = (name_obj.get('middle') or '').strip()
    last = (name_obj.get('last') or '').strip()
    
    # If we have individual components, construct the full name
    if first or middle or last:
        name_parts = []
        if first:
            name_parts.append(first)
        if middle:
            name_parts.append(middle)
        if last:
            name_parts.append(last)
        
        if name_parts:
            constructed_name = ' '.join(name_parts)
            # Prefer constructed name if it's more complete than the 'full' field
            full_name = (name_obj.get('full') or '').strip()
            if len(constructed_name) > len(full_name):
                return constructed_name
    
    # Fall back to 'full' field if construction didn't work or wasn't better
    full_name = (name_obj.get('full') or '').strip()
    if full_name:
        return full_name
    
    # Last resort: try alternative names
    alternatives = name_obj.get('alternative', [])
    if alternatives and alternatives[0]:
        alt_name = alternatives[0]
        if alt_name:  # Check if alternative name is not None
            return alt_name.strip()
    
    return None

def parse_anilist_data(media_data):
    """Parse AniList API response and extract relevant metadata"""
    metadata = {}
    
    try:
        # Extract characters (main, supporting and background)
        characters = []
        if media_data.get('characters', {}).get('edges'):
            for char_edge in media_data['characters']['edges']:
                name_obj = char_edge.get('node', {}).get('name', {})
                char_name = construct_character_name(name_obj)
                char_role = char_edge.get('role', '').upper()
                
                if char_name and char_role in ['MAIN', 'SUPPORTING', 'BACKGROUND']:
                    characters.append(char_name)
        metadata['Characters'] = ', '.join(characters)
        
        # Extract staff information
        staff_roles = {
            'Writer': [],
            'Penciller': [],
            'Inker': [],
            'Colorist': [],
            'Letterer': [],
            'CoverArtist': [],
            'Editor': [],
            'Translator': []
        }
        
        if media_data.get('staff', {}).get('edges'):
            for staff_edge in media_data['staff']['edges']:
                role = staff_edge.get('role', '').lower()
                staff_name = staff_edge.get('node', {}).get('name', {}).get('full')
                
                if not staff_name:
                    continue
                
                # Standard role mappings (these will also be applied in addition to art roles)
                role_mappings = {
                    'story': 'Writer',
                    'story & art': 'Writer',
                    'original creator': 'Writer',
                    'original story': 'Writer',  # NEW: Added mapping
                    'author': 'Writer',
                    'writer': 'Writer',
                    'artist': 'Penciller',
                    'illustrator': 'Penciller',
                    'inking': 'Inker',
                    'color': 'Colorist',
                    'coloring': 'Colorist',
                    'lettering (english)': 'Letterer',
                    'touch-up art & lettering (english)': 'Letterer',
                    'cover': 'CoverArtist',
                    'cover art': 'CoverArtist',
                    'assistant': 'CoverArtist',  # NEW: Added mapping
                    'assistant (former)': 'CoverArtist',  # NEW: Added mapping
                    'assistant (Former)': 'CoverArtist',  # NEW: Added mapping (capital F)
                    'editor': 'Editor',
                    'editorial': 'Editor',
                    'translation': 'Translator',
                    'translator (english)': 'Translator'
                }
                
                # Regex patterns for complex role formats
                regex_mappings = [
                    (r'touch-up art & lettering.*', 'Letterer'),  # Matches "Touch-up Art & Lettering (English: vol 34)"
                    (r'^translator \(english.*', 'Translator'),   # Matches "Translator (English: vol 36)" - more specific
                    (r'editing \(.*\)', 'Editor')                 # FIXED: Changed from ^editing to editing and added closing parenthesis
                ]
                
                # Check for roles that should be added to all art-related fields
                art_roles = ['character design', 'art', 'story & art']
                is_art_role = any(art_role in role for art_role in art_roles)
                
                # Exclude touch-up art & lettering AND any touch-up roles from being added to all art fields
                is_touchup_lettering = 'touch-up art & lettering' in role.lower()
                is_touchup = 'touch-up' in role.lower()
                
                if is_art_role and not is_touchup_lettering and not is_touchup:
                    # Add to all art-related fields
                    for art_field in ['Penciller', 'Inker', 'Colorist']:
                        staff_roles[art_field].append(staff_name)
                
                # Apply regex mappings first (more specific)
                matched = False
                for pattern, target_field in regex_mappings:
                    if re.match(pattern, role.lower(), re.IGNORECASE):
                        staff_roles[target_field].append(staff_name)
                        matched = True
                        break  # Stop after first match to avoid duplicates
                
                # Apply standard role mappings only if no regex match
                if not matched:
                    role_lower = role.lower()
                    if role_lower in role_mappings:
                        staff_roles[role_mappings[role_lower]].append(staff_name)
        
        # Convert staff lists to comma-separated strings
        for role, names in staff_roles.items():
            if names:
                # Remove duplicates while preserving order
                unique_names = []
                seen = set()
                for name in names:
                    if name not in seen:
                        unique_names.append(name)
                        seen.add(name)
                metadata[role] = ', '.join(unique_names)
            else:
                metadata[role] = ''
        
        return metadata
    
    except Exception as e:
        print(f"Error parsing AniList data: {e}")
        return {}
        
def count_pages_in_cbz(path):
    try:
        with zipfile.ZipFile(path, 'r') as cbz:
            image_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
            count = 0
            for name in cbz.namelist():
                if name.lower().endswith(image_extensions) and not name.startswith('__MACOSX/'):
                    count += 1
            return count
    except Exception as e:
        logging.error(f"Error counting pages in {path}: {e}")
        return 0

def auto_extract_title(filename):
    name = os.path.splitext(os.path.basename(filename))[0]
    
    # Remove common brackets and their contents
    name = re.sub(r'[\[\(].*?[\]\)]', '', name)
    
    # Remove volume/chapter information
    name = re.sub(r'(v|vol|volume|ch|chapter)[\s_]*\d+', '', name, flags=re.I)
    
    # Remove common separators and clean up
    name = re.sub(r'[_\-]+', ' ', name)
    name = re.sub(r'\s+', ' ', name)  # Multiple spaces to single space
    
    cleaned = name.strip().title()
    logging.info(f"Auto-extracted title from filename '{filename}': '{cleaned}'")
    return cleaned

class SeriesManagerDialog(tk.Toplevel):
    """Dialog for managing saved series metadata"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Series Database Manager")
        self.transient(parent)
        self.grab_set()
        
        self.selected_series = None
        self.load_to_all = True
        self.match_mode = False
        self.create_widgets()
        self.refresh_series_list()
        
        # Center the dialog LAST
        self.geometry("900x600")
        self.update_idletasks()
        center_window(self, 900, 600)

        
    def create_widgets(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Search frame
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill='x', pady=(0, 10))
        
        search_label = ttk.Label(search_frame, text="Search Series:")
        search_label.pack(side='left')
        ToolTip(search_label, "Search through saved series by name or alias")
        
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side='left', fill='x', expand=True, padx=(5, 0))
        self.search_entry.bind('<KeyRelease>', self.on_search)
        ToolTip(self.search_entry, "Type to filter the series list by name or alias")
        
        # Series list frame
        list_frame = ttk.LabelFrame(main_frame, text="Saved Series", padding=5)
        list_frame.pack(fill='both', expand=True, pady=(0, 10))
        
        # Treeview for series list (fixed column order)
        columns = ('Series', 'Aliases', 'Last Updated')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=15)
        self.tree.heading('Series', text='Series Name')
        self.tree.heading('Aliases', text='Aliases')
        self.tree.heading('Last Updated', text='Last Updated')
        self.tree.column('Series', width=300)
        self.tree.column('Aliases', width=300)
        self.tree.column('Last Updated', width=150)
        ToolTip(self.tree, "Double-click on a series to load its metadata.\nShows series name, aliases, and last update date.")
        
        # Scrollbar for treeview
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        self.tree.bind('<Double-1>', self.on_series_select)
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x')
        
        # Load buttons
        load_all_btn = ttk.Button(button_frame, text="Load to All Files", command=self.load_to_all_files)
        load_all_btn.pack(side='left', padx=(0, 5))
        ToolTip(load_all_btn, "Load selected series metadata to all files in the current list")
        
        load_selected_btn = ttk.Button(button_frame, text="Load to Selected File", command=self.load_to_selected_file)
        load_selected_btn.pack(side='left', padx=(0, 5))
        ToolTip(load_selected_btn, "Load selected series metadata only to the currently selected file")
        
        # Match buttons
        match_file_btn = ttk.Button(button_frame, text="Match - File", command=self.match_current_file)
        match_file_btn.pack(side='left', padx=(0, 5))
        ToolTip(match_file_btn, "Try to automatically match the current file with a series from the database")
        
        match_all_btn = ttk.Button(button_frame, text="Match - All", command=self.match_all_files)
        match_all_btn.pack(side='left', padx=(0, 5))
        ToolTip(match_all_btn, "Try to automatically match all files with series from the database")
        
        # Alias button
        edit_aliases_btn = ttk.Button(button_frame, text="Edit Aliases", command=self.edit_aliases)
        edit_aliases_btn.pack(side='left', padx=(0, 5))
        ToolTip(edit_aliases_btn, "Edit the aliases for the selected series")
        
        # Other buttons
        delete_btn = ttk.Button(button_frame, text="Delete Selected", command=self.delete_selected_series)
        delete_btn.pack(side='left', padx=(0, 5))
        ToolTip(delete_btn, "Delete the selected series from the database (cannot be undone)")
        
        refresh_btn = ttk.Button(button_frame, text="Refresh", command=self.refresh_series_list)
        refresh_btn.pack(side='left', padx=(0, 15))
        ToolTip(refresh_btn, "Refresh the series list from the database")
        
        close_btn = ttk.Button(button_frame, text="Close", command=self.destroy)
        close_btn.pack(side='right')
        ToolTip(close_btn, "Close the series manager dialog")
        
    def refresh_series_list(self):
        """Refresh the series list with aliases - FIXED VERSION"""
        self.search_var.set("")
        results = series_db.get_all_series_with_aliases()
        self.populate_tree(results)
        
    def populate_tree(self, series_list):
        """Populate the treeview with series data including aliases - FIXED VERSION"""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
    
        for series_data in series_list:
            try:
                if len(series_data) == 3:
                    # Database returns: (series_name, updated_at, aliases)
                    series_name, updated_at, aliases = series_data
    
                    # Format the date safely
                    try:
                        if updated_at:
                            dt = datetime.fromisoformat(updated_at)
                            formatted_date = dt.strftime('%Y-%m-%d %H:%M')
                        else:
                            formatted_date = ""
                    except Exception as date_error:
                        print(f"Date formatting error for {series_name}: {date_error}")
                        formatted_date = str(updated_at) if updated_at else ""
    
                    # Join aliases safely - FIXED: Handle string and list cases
                    if aliases:
                        if isinstance(aliases, list):
                            valid_aliases = [a.strip() for a in aliases if a and a.strip()]
                            aliases_text = ", ".join(valid_aliases)
                        elif isinstance(aliases, str):
                            # Handle case where aliases is a delimited string
                            alias_list = [a.strip() for a in aliases.split('|') if a and a.strip()]
                            aliases_text = ", ".join(alias_list)
                        else:
                            aliases_text = str(aliases)
                    else:
                        aliases_text = ""
    
                    # Insert in CORRECT order: Series Name, Aliases, Last Updated
                    self.tree.insert('', 'end', values=(series_name, aliases_text, formatted_date))
    
                elif len(series_data) == 2:
                    # Fallback for old format without aliases
                    series_name, updated_at = series_data
                    try:
                        dt = datetime.fromisoformat(updated_at)
                        formatted_date = dt.strftime('%Y-%m-%d %H:%M')
                    except:
                        formatted_date = str(updated_at)
                    
                    self.tree.insert('', 'end', values=(series_name, "", formatted_date))
                else:
                    # Fallback for unexpected structure
                    series_name = series_data[0] if len(series_data) > 0 else "Unknown"
                    self.tree.insert('', 'end', values=(series_name, "Error", "Error"))
    
            except Exception as e:
                print(f"Error processing series data {series_data}: {e}")
                safe_name = str(series_data[0]) if len(series_data) > 0 else "Error"
                self.tree.insert('', 'end', values=(safe_name, "Error", "Error"))

    def on_search(self, event=None):
        """Handle search input - FIXED VERSION"""
        search_term = self.search_var.get().strip()
        if search_term:
            results = series_db.search_series(search_term)
            # Convert to format expected by populate_tree and load aliases
            results_with_aliases = []
            for series_name, updated_at in results:
                aliases = series_db.load_series_aliases(series_name)
                results_with_aliases.append((series_name, updated_at, aliases))
            self.populate_tree(results_with_aliases)
        else:
            # Use the same method as refresh_series_list
            results = series_db.get_all_series_with_aliases()
            self.populate_tree(results)

    def edit_aliases(self):
        """Edit aliases for the selected series"""
        series_name = self.get_selected_series()
        if not series_name:
            messagebox.showwarning("No Selection", "Please select a series to edit aliases.")
            return
        
        # Load current aliases
        current_aliases = series_db.load_series_aliases(series_name)
        
        # Open alias editor
        dialog = AliasEditorDialog(self, series_name, current_aliases)
        self.wait_window(dialog)
        
        if dialog.result is not None:
            # Save the aliases
            if series_db.save_series_aliases(series_name, dialog.result):
                messagebox.showinfo("Success", f"Aliases updated for '{series_name}'")
                self.refresh_series_list()
            else:
                messagebox.showerror("Error", f"Failed to update aliases for '{series_name}'")
        
    def get_selected_series(self):
        """Get the currently selected series name"""
        selection = self.tree.selection()
        if selection:
            item = self.tree.item(selection[0])
            return item['values'][0]  # Series name is in the first column
        return None
    
    def on_series_select(self, event):
        """Handle double-click on series - default to load to all files"""
        self.load_to_all_files()
    
    def load_to_all_files(self):
        """Load the selected series metadata to all opened files"""
        series_name = self.get_selected_series()
        if not series_name:
            messagebox.showwarning("No Selection", "Please select a series to load.")
            return
        
        self.selected_series = series_name
        self.load_to_all = True
        self.match_mode = False
        self.destroy()
    
    def load_to_selected_file(self):
        """Load the selected series metadata to selected file only"""
        series_name = self.get_selected_series()
        if not series_name:
            messagebox.showwarning("No Selection", "Please select a series to load.")
            return
        
        self.selected_series = series_name
        self.load_to_all = False
        self.match_mode = False
        self.destroy()
    
    def match_current_file(self):
        """Match current file with series DB based on filename"""
        self.selected_series = None
        self.load_to_all = False
        self.match_mode = True
        self.destroy()
    
    def match_all_files(self):
        """Match all files with series DB based on filenames"""
        self.selected_series = None
        self.load_to_all = True
        self.match_mode = True
        self.destroy()
    
    def delete_selected_series(self):
        """Delete the selected series"""
        series_name = self.get_selected_series()
        if not series_name:
            messagebox.showwarning("No Selection", "Please select a series to delete.")
            return
        
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete '{series_name}' from the database?\n\nThis will also delete all associated aliases."):
            if series_db.delete_series(series_name):
                messagebox.showinfo("Deleted", f"Successfully deleted '{series_name}' and its aliases")
                self.refresh_series_list()
            else:
                messagebox.showerror("Error", f"Failed to delete '{series_name}'")

class MetadataGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CBZ Metadata Manager")
        self.geometry("1600x1000")
        self.minsize(1200, 800)
        
        self.local_only_mode = tk.BooleanVar(value=False)
        self.metadata_mode = tk.StringVar(value="batch")  # "batch" or "individual"
        self.individual_metadata_cache = {}  # Store individual metadata results
        self.batch_processing = False  # Flag to track batch operations
        self.title_var = tk.StringVar()
        self.dropdown_selection_per_file = {}
        self.bulk_edit_enabled = tk.BooleanVar(value=False)

        self.cbz_paths = []
        self.file_metadata = {}
        self.original_metadata = {}
        self.current_index = 0  
        self.metadata_options = []
        
        self.fields = [
            "Title", "Series", "LocalizedSeries", "AgeRating", "Number", "Count", "Volume", 
            "PageCount", "Summary", "Year", "Month", "Day", "Writer", "Penciller", "Inker", 
            "Colorist", "Letterer", "CoverArtist", "Editor", "Translator", "Publisher", 
            "Imprint", "Genre", "Tags", "LanguageISO", "Web", "Notes", "Format", "Characters", 
            "CommunityRating", "Review", "AlternateSeries", "AlternateNumber", "AlternateCount", 
            "Teams", "Locations", "ScanInformation", "StoryArc", "StoryArcNumber", "SeriesGroup", 
            "MainCharacterOrTeam"
        ]
        
        # Field tooltips dictionary
        self.field_tooltips = {
            "Title": "The title of this specific issue or volume (e.g., 'Attack on Titan #1')",
            "Series": "The name of the manga/comic series (e.g., 'Attack on Titan')",
            "LocalizedSeries": "Series name in local language or alternate region name",
            "Number": "Issue number within the series (e.g., 1, 2, 3.5)",
            "Count": "Total number of issues in the series (if known)",
            "Volume": "Volume number for collected editions or multi-volume series",
            "Summary": "Brief description or synopsis of this issue's content",
            "Notes": "Additional notes about this issue (scanning info, version, etc.)",
            "Year": "Publication year (YYYY format)",
            "Month": "Publication month (1-12)",
            "Day": "Publication day (1-31)",
            "Writer": "Story writer(s) - separate multiple names with commas",
            "Penciller": "Artist who drew the pencil artwork",
            "Inker": "Artist who inked over the pencil work",
            "Colorist": "Artist who colored the artwork",
            "Letterer": "Person who added text/dialogue to the pages",
            "CoverArtist": "Artist who created the cover artwork",
            "Editor": "Editor(s) who worked on this issue",
            "Translator": "Person who translated the work (for localized content)",
            "Publisher": "Publishing company (e.g., 'Kodansha', 'Viz Media')",
            "Imprint": "Specific imprint or label under the publisher",
            "Genre": "Genre classification (e.g., 'Action', 'Romance', 'Horror')",
            "Tags": "Descriptive tags - separate with commas (e.g., 'supernatural, school, drama')",
            "LanguageISO": "Language code (e.g., 'en' for English, 'ja' for Japanese)",
            "Web": "Related website URL or online resource",
            "PageCount": "Total number of pages in this issue",
            "CommunityRating": "User rating (typically 1-5 or 1-10 scale)",
            "Review": "Review text or comments about this issue",
            "AlternateSeries": "Alternate or related series name",
            "AlternateNumber": "Issue number in alternate numbering system",
            "AlternateCount": "Total count in alternate numbering system",
            "Format": "Publication format (e.g., 'TPB', 'One-Shot', 'Annual')",
            "Characters": "Characters featured - separate with commas",
            "Teams": "Teams or groups featured - separate with commas",
            "Locations": "Key locations or settings - separate with commas",
            "ScanInformation": "Information about the scan source or quality",
            "StoryArc": "Name of the story arc this issue belongs to",
            "StoryArcNumber": "Position of this issue within the story arc",
            "SeriesGroup": "Group or collection this series belongs to",
            "MainCharacterOrTeam": "Primary character or team for this series",
            "AgeRating": "Content rating indicating appropriate age group"
        }
        
        # Dropdown options for special fields
        self.age_rating_options = [
            "Unknown", "Rating Pending", "Early Childhood", "Everyone", "G", "Everyone 10+", 
            "PG", "Kids to Adults", "Teen", "MA15+", "Mature 17+", "M", "R18+", 
            "Adults Only 18+", "X18+"
        ]
        
        self.format_options = [
            "Special", "Reference", "Director's Cut", "Box Set", "Box-Set", "Annual", 
            "Anthology", "Epilogue", "One Shot", "One-Shot", "Prologue", "TPB", 
            "Trade Paper Back", "Omnibus", "Compendium", "Absolute", "Graphic Novel", 
            "GN", "FCBD"
        ]
        
        self.before_entries = {}
        self.after_entries = {}
        self.dropdown_var = tk.StringVar()
        self.title_entry = tk.StringVar()
        self.create_widgets()
        
        # Center the main window AFTER all widgets are created
        self.update_idletasks()
        center_window(self, 1600, 1000)
        
      
    def disable_middle_click_paste(self, widget):
        """Disable middle-click paste functionality for text widgets"""
        # Disable middle-click paste for Linux/Unix systems
        widget.bind("<Button-2>", lambda e: "break")
        widget.bind("<ButtonRelease-2>", lambda e: "break")
        
        # Also disable shift+middle-click which can also trigger paste
        widget.bind("<Shift-Button-2>", lambda e: "break")
        widget.bind("<Shift-ButtonRelease-2>", lambda e: "break")
        
        # For Combobox widgets, we need different handling
        if isinstance(widget, ttk.Combobox):
            # Disable middle-click on combobox
            widget.bind("<Button-2>", lambda e: "break")
            widget.bind("<ButtonRelease-2>", lambda e: "break")
            widget.bind("<Shift-Button-2>", lambda e: "break")
            widget.bind("<Shift-ButtonRelease-2>", lambda e: "break")
        else:
            # For Text widgets, prevent selection copying to primary selection
            widget.bind("<Button-1>", lambda e: widget.after_idle(lambda: widget.selection_clear() if hasattr(widget, 'selection_clear') else None))

    def get_field_value_for_xml(self, field):
        """Get field value properly formatted for XML insertion"""
        widget = self.after_entries[field]
        
        if isinstance(widget, ttk.Combobox):
            return widget.get().strip()
        elif isinstance(widget, tk.Text):
            content = widget.get("1.0", tk.END)
            # Remove the automatic newline that Text widget adds at the end
            if content.endswith('\n'):
                content = content[:-1]
            
            # Special handling for Web field - convert line breaks to comma-separated
            if field == "Web":
                # Split by any whitespace, filter empty strings, join with commas
                urls = [url.strip() for url in content.split() if url.strip()]
                content = ', '.join(urls)
            
            return content.strip()
        else:
            return widget.get().strip()
            
    def create_widgets(self):
        main_frame = ttk.Frame(self)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
    
        # === Top Metadata Section ===
        top_frame = ttk.LabelFrame(main_frame, text="File Selection & Metadata Source", padding=10)
        top_frame.pack(fill='x', pady=(0, 10))
    
        file_frame = ttk.Frame(top_frame)
        file_frame.pack(fill='x', pady=(0, 10))
    
        ttk.Label(file_frame, text="CBZ Files:").pack(anchor='w')
        file_list_frame = ttk.Frame(file_frame)
        file_list_frame.pack(fill='x')
    
        self.file_listbox = tk.Listbox(file_list_frame, height=4)
        self.file_listbox.pack(side='left', fill='both', expand=True)
        self.file_listbox.bind('<<ListboxSelect>>', self.on_file_select)
        ToolTip(self.file_listbox, "List of selected CBZ files. Click on a file to select it for editing.")
    
        file_scroll = ttk.Scrollbar(file_list_frame, orient='vertical', command=self.file_listbox.yview)
        file_scroll.pack(side='right', fill='y')
        self.file_listbox.configure(yscrollcommand=file_scroll.set)
    
        # File selection buttons with tooltips
        select_folder_btn = ttk.Button(file_list_frame, text="Select Folder", command=self.browse_cbz_folder)
        select_folder_btn.pack(side='right', padx=(5, 0))
        ToolTip(select_folder_btn, "Browse and select a folder containing CBZ files")
        
        select_cbz_btn = ttk.Button(file_list_frame, text="Select CBZ", command=self.browse_cbz_files)
        select_cbz_btn.pack(side='right', padx=(5, 0))
        ToolTip(select_cbz_btn, "Browse and select individual CBZ files")
    
        title_frame = ttk.Frame(top_frame)
        title_frame.pack(fill='x', pady=(0, 10))
    
        mode_frame = ttk.Frame(title_frame)
        mode_frame.pack(fill='x', pady=(0, 5))
    
        ttk.Label(mode_frame, text="Metadata Mode:").pack(anchor='w')
        mode_radio_frame = ttk.Frame(mode_frame)
        mode_radio_frame.pack(fill='x')
    
        batch_radio = ttk.Radiobutton(mode_radio_frame, text="Same metadata for all files", variable=self.metadata_mode, value="batch")
        batch_radio.pack(anchor='w')
        ToolTip(batch_radio, "Apply the same metadata to all selected files (batch mode)")
        
        individual_radio = ttk.Radiobutton(mode_radio_frame, text="Different metadata per file", variable=self.metadata_mode, value="individual")
        individual_radio.pack(anchor='w')
        ToolTip(individual_radio, "Use different metadata for each file based on filename matching")
    
        local_only_check = ttk.Checkbutton(top_frame, text="🗂️ Local Only Mode (No API requests)", variable=self.local_only_mode)
        local_only_check.pack(anchor='w', pady=(2, 0))
        ToolTip(local_only_check, "Enable to work only with local database, disable online metadata fetching")
    
        ttk.Label(title_frame, text="Manga Title:").pack(anchor='w')
        title_entry_frame = ttk.Frame(title_frame)
        title_entry_frame.pack(fill='x')
    
        self.title_var = tk.StringVar()
        self.title_entry = ttk.Entry(title_entry_frame, textvariable=self.title_var, font=('TkDefaultFont', 10))
        self.title_entry.pack(side='left', fill='x', expand=True)
        self.disable_middle_click_paste(self.title_entry)
        ToolTip(self.title_entry, "Enter the manga/comic series title to search for metadata")
    
        # Metadata fetch buttons with tooltips
        fetch_anilist_btn = ttk.Button(title_entry_frame, text="Fetch AniList", command=self.fetch_anilist_metadata_gui)
        fetch_anilist_btn.pack(side='right', padx=(5, 0))
        ToolTip(fetch_anilist_btn, "Fetch metadata from AniList database")
        
        refetch_btn = ttk.Button(title_entry_frame, text="Re-Fetch This File", command=self.fetch_metadata_for_current_file)
        refetch_btn.pack(side='right', padx=(5, 0))
        ToolTip(refetch_btn, "Re-fetch metadata for the currently selected file")
        
        fetch_metadata_btn = ttk.Button(title_entry_frame, text="Fetch Metadata", command=self.fetch_metadata_smart)
        fetch_metadata_btn.pack(side='right', padx=(5, 0))
        ToolTip(fetch_metadata_btn, "Search for and fetch metadata from online sources")
    
        self.progress_frame = ttk.Frame(title_frame)
        self.progress_var = tk.StringVar(value="")
        self.progress_label = ttk.Label(self.progress_frame, textvariable=self.progress_var)
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode='determinate')
    
        series_db_frame = ttk.Frame(top_frame)
        series_db_frame.pack(fill='x', pady=(10, 0))
    
        ttk.Label(series_db_frame, text="Series Database:", font=('TkDefaultFont', 9, 'bold')).pack(side='left')
        
        # Series database buttons with tooltips
        save_aliases_btn = ttk.Button(series_db_frame, text="📂 Save Series + Aliases", command=self.save_current_series_with_aliases)
        save_aliases_btn.pack(side='left', padx=(10, 5))
        ToolTip(save_aliases_btn, "Save current series metadata to database with alias editing")
        
        save_quick_btn = ttk.Button(series_db_frame, text="📂 Save (Quick)", command=self.save_current_series)
        save_quick_btn.pack(side='left', padx=(0, 5))
        ToolTip(save_quick_btn, "Quickly save current series metadata to database")
        
        load_series_btn = ttk.Button(series_db_frame, text="📚 Load Series", command=self.load_series_from_db)
        load_series_btn.pack(side='left', padx=(0, 5))
        ToolTip(load_series_btn, "Load previously saved series metadata from database")
        
        manage_series_btn = ttk.Button(series_db_frame, text="🗂️ Manage Series", command=self.open_series_manager)
        manage_series_btn.pack(side='left', padx=(0, 5))
        ToolTip(manage_series_btn, "Open series database manager to view, edit, and organize saved series")
        
        ttk.Separator(series_db_frame, orient='vertical').pack(side='left', fill='y', padx=5)
        
        match_file_btn = ttk.Button(series_db_frame, text="🔍 Match File", command=self._match_current_file_with_db)
        match_file_btn.pack(side='left', padx=(0, 5))
        ToolTip(match_file_btn, "Try to match current file with saved series using filename analysis")
        
        match_all_btn = ttk.Button(series_db_frame, text="🔍 Match All", command=self._match_all_files_with_db)
        match_all_btn.pack(side='left', padx=(0, 5))
        ToolTip(match_all_btn, "Try to match all files with saved series using filename analysis")
    
        self.dropdown = ttk.Combobox(top_frame, textvariable=self.dropdown_var, state='readonly', font=('TkDefaultFont', 10))
        self.dropdown.pack(fill='x', pady=(10, 0))
        self.dropdown.bind("<<ComboboxSelected>>", self.update_metadata_from_dropdown)
        ToolTip(self.dropdown, "Select from available metadata options found during search")
    
        nav_frame = ttk.Frame(main_frame)
        nav_frame.pack(fill='x', pady=(0, 10))
    
        # Navigation and utility buttons with tooltips
        prev_btn = ttk.Button(nav_frame, text="◄ Previous", command=self.prev_file)
        prev_btn.pack(side='left', padx=(0, 5))
        ToolTip(prev_btn, "Navigate to the previous file in the list")
        
        next_btn = ttk.Button(nav_frame, text="Next ►", command=self.next_file)
        next_btn.pack(side='left', padx=(0, 15))
        ToolTip(next_btn, "Navigate to the next file in the list")
        
        auto_fill_btn = ttk.Button(nav_frame, text="Auto-Fill Volume", command=self.fill_volume_info)
        auto_fill_btn.pack(side='left', padx=(0, 5))
        ToolTip(auto_fill_btn, "Automatically extract volume/issue numbers from filename")
        
        count_pages_btn = ttk.Button(nav_frame, text="Count Pages", command=self.fill_page_count)
        count_pages_btn.pack(side='left', padx=(0, 5))
        ToolTip(count_pages_btn, "Count and fill in the number of pages in the CBZ file")
        
        bulk_edit_check = ttk.Checkbutton(nav_frame, text="📝 Bulk Edit All Files", variable=self.bulk_edit_enabled)
        bulk_edit_check.pack(side='right')
        ToolTip(bulk_edit_check, "When enabled, changes apply to all files instead of just the current file")
    
        metadata_frame = ttk.LabelFrame(main_frame, text="Metadata Editor", padding=5)
        metadata_frame.pack(fill='both', expand=True, pady=(0, 10))
    
        canvas = tk.Canvas(metadata_frame)
        scrollbar = ttk.Scrollbar(metadata_frame, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
    
        self.canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(self.canvas_window, width=e.width))
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.configure(yscrollcommand=scrollbar.set)
    
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
    
        scroll_frame.grid_columnconfigure(1, weight=1)
        scroll_frame.grid_columnconfigure(3, weight=1)
    
        # Column headers
        ttk.Label(scroll_frame, text="Field", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=0, sticky='w', padx=(5, 0))
        ttk.Label(scroll_frame, text="Original Metadata", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=1, sticky='ew', padx=5)
        ttk.Label(scroll_frame, text="→", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=2, sticky='w', padx=(0, 5))
        ttk.Label(scroll_frame, text="Updated Metadata", font=('TkDefaultFont', 9, 'bold')).grid(row=1, column=3, sticky='ew', padx=5)
    
        self.copy_buttons = {}
        self.form_rows = {}
    
        # Create form fields with tooltips
        for i, field in enumerate(self.fields):
            row = i + 2
    
            label = ttk.Label(scroll_frame, text=field, font=('TkDefaultFont', 8))
            label.grid(row=row, column=0, sticky='nw', padx=(5, 10), pady=2)
            ToolTip(label, self.field_tooltips.get(field, f"Metadata field: {field}"))
    
            before = tk.Text(scroll_frame, height=1, width=50, wrap='word', font=('TkDefaultFont', 8), state='disabled', bg='#f0f0f0')
            before.grid(row=row, column=1, sticky='nsew', padx=(5, 2), pady=2)
            self.disable_middle_click_paste(before)
            ToolTip(before, f"Original {field} metadata from the CBZ file (read-only)")
    
            copy_btn = ttk.Button(scroll_frame, text="→", width=3, command=lambda f=field: self.copy_field(f))
            copy_btn.grid(row=row, column=2, padx=(2, 2), pady=2)
            ToolTip(copy_btn, f"Copy original {field} value to the updated field")
    
            if field == "AgeRating":
                after = ttk.Combobox(scroll_frame, values=self.age_rating_options, font=('TkDefaultFont', 8), width=50)
                after.bind('<<ComboboxSelected>>', lambda e, f=field: self.on_dropdown_change(f))
                after.bind('<KeyRelease>', lambda e, f=field: self.on_text_change(f))
                self.disable_middle_click_paste(after)  # Add this line
                ToolTip(after, "Select age rating from predefined options or enter custom value")
            elif field == "Format":
                after = ttk.Combobox(scroll_frame, values=self.format_options, font=('TkDefaultFont', 8), width=50)
                after.bind('<<ComboboxSelected>>', lambda e, f=field: self.on_dropdown_change(f))
                after.bind('<KeyRelease>', lambda e, f=field: self.on_text_change(f))
                self.disable_middle_click_paste(after)  # Add this line
                ToolTip(after, "Select publication format from predefined options or enter custom value")
            else:
                after = tk.Text(scroll_frame, height=1, width=50, wrap='word', font=('TkDefaultFont', 8))
                after.bind('<KeyRelease>', lambda e, f=field: self.on_text_change(f))
                self.disable_middle_click_paste(after)  # Add this line
                ToolTip(after, self.field_tooltips.get(field, f"Enter {field} metadata"))
            
            after.grid(row=row, column=3, sticky='nsew', padx=(2, 2), pady=2)
    
            clear_btn = ttk.Button(scroll_frame, text="✙", width=3, command=lambda f=field: self.clear_field(f))
            clear_btn.grid(row=row, column=4, padx=(2, 5), pady=2)
            ToolTip(clear_btn, f"Clear the {field} field")
    
            self.before_entries[field] = before
            self.after_entries[field] = after
            self.copy_buttons[field] = copy_btn
            self.form_rows[field] = (label, before, copy_btn, after, clear_btn)
    
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        def _bind_to_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        def _unbind_from_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")
        
        # Only enable mousewheel scrolling when mouse enters the metadata canvas area
        canvas.bind('<Enter>', _bind_to_mousewheel)
        canvas.bind('<Leave>', _unbind_from_mousewheel)
        scrollbar.bind('<Enter>', _bind_to_mousewheel) 
        scrollbar.bind('<Leave>', _unbind_from_mousewheel)
    
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x')
    
        # Main action buttons with tooltips
        insert_btn = ttk.Button(button_frame, text="Insert Metadata into All CBZs", command=self.insert_metadata, style='Accent.TButton')
        insert_btn.pack(side='left', padx=(0, 10))
        insert_btn.bind("<Button-3>", lambda e: self._reset_thread_count())  # Right-click
        ToolTip(insert_btn, "Left Click - Apply the current metadata to all selected CBZ files, Right Click - Modify CPU Thread Setting")
        
        copy_all_btn = ttk.Button(button_frame, text="Copy All Fields", command=self.copy_all_fields)
        copy_all_btn.pack(side='left', padx=(0, 5))
        ToolTip(copy_all_btn, "Copy all original metadata values to the updated fields")
        
        clear_all_btn = ttk.Button(button_frame, text="Clear All Fields", command=self.clear_all_fields)
        clear_all_btn.pack(side='left', padx=(0, 5))
        ToolTip(clear_all_btn, "Clear all metadata fields (both original and updated)")


    def on_dropdown_change(self, field):
        """Handle dropdown field changes"""
        self.on_text_change(field)

    def on_text_change(self, field):
        """Update metadata for one or all files based on bulk edit mode"""
        if not self.cbz_paths:
            return
    
        if field in ["AgeRating", "Format"]:
            value = self.after_entries[field].get()
        else:
            value = self.after_entries[field].get("1.0", tk.END).strip()
    
        if self.bulk_edit_enabled.get():
            for file in self.cbz_paths:
                if file in self.file_metadata:
                    self.file_metadata[file][field] = value
        else:
            if self.current_index < len(self.cbz_paths):
                current_file = self.cbz_paths[self.current_index]
                if current_file in self.file_metadata:
                    self.file_metadata[current_file][field] = value

    def adjust_text_widget_height(self, widget, content):
        """Resize height of text widget based on line count (max 6 lines)"""
        lines = content.count("\n") + 1
        widget.configure(height=min(lines, 6))
                
    def populate_dropdown_for_current_file(self):
        """Populate metadata dropdown based on current file (individual mode)"""
        if not self.cbz_paths or self.current_index >= len(self.cbz_paths):
            return
    
        current_file = self.cbz_paths[self.current_index]
    
        if self.metadata_mode.get() == "individual" and current_file in self.individual_metadata_cache:
            cache_data = self.individual_metadata_cache[current_file]
            self.metadata_options = cache_data.get('options', [])
    
            dropdown_values = []
            for meta in self.metadata_options:
                title_text = meta.get("Title", "Unknown")
                type_text = meta.get("type", "")
                year_text = meta.get("Year", "")
                content_rating_text = meta.get("content_rating", "")
            
                parts = [title_text]
                if type_text:
                    parts.append(f"({type_text.title()})")
                if year_text:
                    parts.append(f"({year_text})")
                if content_rating_text:
                    parts.append(f"({content_rating_text.title()})")
            
                dropdown_values.append(" ".join(parts))
    
            self.dropdown['values'] = dropdown_values
    
            # Restore user's last selection if available
            selected_idx = self.dropdown_selection_per_file.get(current_file, 0)
            if selected_idx < len(dropdown_values):
                self.dropdown.set(dropdown_values[selected_idx])
                self.update_metadata_from_dropdown()
            elif dropdown_values:
                self.dropdown.set(dropdown_values[0])
                self.update_metadata_from_dropdown()
            else:
                self.dropdown.set("No matches found")
                
    def browse_cbz_files(self):
        file_paths = filedialog.askopenfilenames(
            title="Select CBZ Files",
            filetypes=[("CBZ files", "*.cbz")]
        )
        self._process_cbz_files(file_paths)
    
    def browse_cbz_folder(self):
        folder = filedialog.askdirectory(title="Select Folder Containing CBZs")
        if not folder:
            return
    
        cbz_files = []
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".cbz"):
                    cbz_files.append(os.path.join(root, f))
    
        self._process_cbz_files(cbz_files)
        
    def _process_cbz_files(self, paths):
        if not paths:
            return
    
        self.cbz_paths = list(paths)
        self.file_listbox.delete(0, tk.END)
        self.file_metadata.clear()
        self.original_metadata.clear()
    
        first_title = auto_extract_title(os.path.basename(self.cbz_paths[0]))
        self.title_var.set(first_title)
    
        for path in self.cbz_paths:
            self.file_listbox.insert(tk.END, os.path.basename(path))
            meta = {field: "" for field in self.fields}
            self.file_metadata[path] = meta.copy()
    
            try:
                with zipfile.ZipFile(path, 'r') as cbz:
                    if "ComicInfo.xml" in cbz.namelist():
                        with cbz.open("ComicInfo.xml") as xml_file:
                            tree = ET.parse(xml_file)
                            root = tree.getroot()
                            for field in self.fields:
                                element = root.find(field)
                                if element is not None and element.text:
                                    meta[field] = element.text.strip()
            except Exception as e:
                logging.warning(f"Failed to read ComicInfo.xml from {path}: {e}")
    
            self.original_metadata[path] = meta.copy()
    
        if self.cbz_paths:
            self.file_listbox.select_set(0)
            self.current_index = 0
            self.load_metadata(0)


    def clear_all_fields(self):
        """Clear all metadata fields for current file"""
        if not self.cbz_paths:
            return
            
        for field in self.fields:
            self.after_entries[field].delete("1.0", tk.END)
            if self.current_index < len(self.cbz_paths):
                current_file = self.cbz_paths[self.current_index]
                self.file_metadata[current_file][field] = ""
                
    def save_current_series(self):
            """Save current series metadata to database"""
            if not self.cbz_paths:
                messagebox.showerror("Error", "No CBZ files loaded")
                return
            
            # Get series name from current metadata
            current_file = self.cbz_paths[self.current_index] if self.current_index < len(self.cbz_paths) else self.cbz_paths[0]
            current_metadata = self.file_metadata.get(current_file, {})
            series_name = current_metadata.get('Series', '').strip()
            
            if not series_name:
                # Prompt for series name
                series_name = tk.simpledialog.askstring(
                    "Series Name", 
                    "Enter series name:",
                    initialvalue=self.title_entry.get().strip()
                )
                if not series_name or not series_name.strip():
                    messagebox.showwarning("Warning", "Series name is required")
                    return
                series_name = series_name.strip()
            
            # Create series metadata (use metadata from first file as template)
            series_metadata = current_metadata.copy()
            
            # Remove file-specific fields
            file_specific_fields = ['Number', 'Volume', 'PageCount']
            for field in file_specific_fields:
                if field in series_metadata:
                    series_metadata[field] = ""
            
            try:
                if series_db.save_series_metadata(series_name, series_metadata):
                    messagebox.showinfo("Success", f"Series '{series_name}' saved to database")
                else:
                    messagebox.showerror("Error", f"Failed to save series '{series_name}'")
            except Exception as e:
                logging.error(f"Error saving series: {e}")
                messagebox.showerror("Error", f"Failed to save series: {str(e)}")
                
    def load_series_from_db(self):
        """Load series metadata from database"""
        dialog = SeriesManagerDialog(self)
        self.wait_window(dialog)
        
        if dialog.match_mode:
            # Handle match operations
            if dialog.load_to_all:
                self._match_all_files_with_db()
            else:
                self._match_current_file_with_db()
        elif dialog.selected_series:
            # Handle regular load operations
            series_metadata = series_db.load_series_metadata(dialog.selected_series)
            if series_metadata:
                if self.cbz_paths:
                    if dialog.load_to_all:
                        # Apply series metadata to ALL loaded files
                        files_to_update = self.cbz_paths
                        message_suffix = f"to all {len(self.cbz_paths)} files"
                    else:
                        # Apply series metadata to SELECTED file only
                        if self.current_index < len(self.cbz_paths):
                            files_to_update = [self.cbz_paths[self.current_index]]
                            current_filename = os.path.basename(self.cbz_paths[self.current_index])
                            message_suffix = f"to '{current_filename}'"
                        else:
                            messagebox.showwarning("Warning", "No file selected. Please select a file first.")
                            return
                    
                    # Apply metadata to selected files
                    for cbz_path in files_to_update:
                        # Keep existing file-specific data
                        existing_metadata = self.file_metadata.get(cbz_path, {})
                        file_specific_data = {
                            'Volume': existing_metadata.get('Volume', ''),
                            'Number': existing_metadata.get('Number', ''),
                            'PageCount': existing_metadata.get('PageCount', '')
                        }
                        
                        # Update with series metadata
                        self.file_metadata[cbz_path] = series_metadata.copy()
                        
                        # Restore file-specific data
                        self.file_metadata[cbz_path].update(file_specific_data)
                        
                        # Auto-extract volume if not already set
                        if not file_specific_data['Volume']:
                            volume = extract_volume_from_filename(os.path.basename(cbz_path))
                            if volume:
                                self.file_metadata[cbz_path]['Volume'] = volume
                    
                    # Refresh display
                    self.load_metadata(self.current_index)
                    messagebox.showinfo("Success", f"Loaded series metadata for '{dialog.selected_series}' {message_suffix}")
                else:
                    messagebox.showwarning("Warning", "No CBZ files loaded. Load files first, then apply series metadata.")
            else:
                messagebox.showerror("Error", f"Failed to load series metadata for '{dialog.selected_series}'")
                
    def save_current_series_with_aliases(self):
        """Save current series metadata to database with aliases"""
        if not self.cbz_paths:
            messagebox.showerror("Error", "No CBZ files loaded")
            return
        
        # Get series name from current metadata
        current_file = self.cbz_paths[self.current_index] if self.current_index < len(self.cbz_paths) else self.cbz_paths[0]
        current_metadata = self.file_metadata.get(current_file, {})
        series_name = current_metadata.get('Series', '').strip()
        
        if not series_name:
            # Prompt for series name
            series_name = tk.simpledialog.askstring(
                "Series Name", 
                "Enter series name:",
                initialvalue=self.title_entry.get().strip()
            )
            if not series_name or not series_name.strip():
                messagebox.showwarning("Warning", "Series name is required")
                return
            series_name = series_name.strip()
        
        # Create series metadata
        series_metadata = current_metadata.copy()
        
        # Remove file-specific fields
        file_specific_fields = ['Number', 'Volume', 'PageCount']
        for field in file_specific_fields:
            if field in series_metadata:
                series_metadata[field] = ""
        
        try:
            # Save series metadata
            if series_db.save_series_metadata(series_name, series_metadata):
                # Open alias editor for new series
                current_aliases = series_db.load_series_aliases(series_name)
                dialog = AliasEditorDialog(self, series_name, current_aliases)
                self.wait_window(dialog)
                
                if dialog.result is not None:
                    series_db.save_series_aliases(series_name, dialog.result)
                
                messagebox.showinfo("Success", f"Series '{series_name}' saved to database")
            else:
                messagebox.showerror("Error", f"Failed to save series '{series_name}'")
        except Exception as e:
            logging.error(f"Error saving series: {e}")
            messagebox.showerror("Error", f"Failed to save series: {str(e)}")
    
    # Update the _find_best_match method to use aliases:
    def _find_best_match(self, extracted_title, all_series):
        """Find the best matching series title from the database (now includes aliases)"""
        if not extracted_title or not all_series:
            return None
        
        # Clean the extracted title for comparison
        cleaned_extracted = self._clean_title_for_matching(extracted_title)
        normalized_extracted = self._normalize_for_comparison(cleaned_extracted)
        
        # Get all series with their aliases
        series_with_variants = []
        for series_item in all_series:
            if len(series_item) == 3:
                series_name, updated_at, aliases = series_item
            else:
                # Fallback for old format
                series_name = series_item[0] if isinstance(series_item, tuple) else series_item
                aliases = []
            
            # Load metadata for localized titles
            series_metadata = series_db.load_series_metadata(series_name)
            if not isinstance(series_metadata, dict):
                series_metadata = {'Series': series_name}
            
            # Collect all title variants
            all_titles = [series_name]
            
            # Add aliases
            if aliases:
                all_titles.extend(aliases)
            
            # Add LocalizedSeries from metadata
            if series_metadata and 'LocalizedSeries' in series_metadata and series_metadata['LocalizedSeries']:
                localized_titles = [title.strip() for title in series_metadata['LocalizedSeries'].split(',') if title.strip()]
                all_titles.extend(localized_titles)
            
            # Add other alternative title fields from metadata
            if series_metadata and isinstance(series_metadata, dict):
                for field in ['Native', 'Romaji', 'Secondary']:
                    alt_title = series_metadata.get(field, '').strip()
                    if alt_title and alt_title not in all_titles:
                        all_titles.append(alt_title)
            
            # Remove duplicates while preserving order
            unique_titles = []
            for title in all_titles:
                if title not in unique_titles:
                    unique_titles.append(title)
            
            series_with_variants.append((series_name, unique_titles))
        
        # First try exact match against all title variants (after cleaning)
        for series_name, title_variants in series_with_variants:
            for title_variant in title_variants:
                cleaned_variant = self._clean_title_for_matching(title_variant)
                if self._normalize_for_comparison(cleaned_variant) == normalized_extracted:
                    return series_name
        
        # Then try substring matching (both ways) with cleaned titles
        best_matches = []
        
        for series_name, title_variants in series_with_variants:
            for title_variant in title_variants:
                cleaned_variant = self._clean_title_for_matching(title_variant)
                normalized_variant = self._normalize_for_comparison(cleaned_variant)
                
                # Check if extracted title is contained in variant title
                if normalized_extracted in normalized_variant:
                    score = len(normalized_variant) - len(normalized_extracted)
                    best_matches.append((series_name, score, 'contains'))
                
                # Check if variant title is contained in extracted title
                elif normalized_variant in normalized_extracted:
                    score = len(normalized_extracted) - len(normalized_variant)
                    best_matches.append((series_name, score, 'contained'))
        
        # Return the match with smallest length difference (most similar)
        if best_matches:
            best_matches.sort(key=lambda x: (x[1], x[2]))
            return best_matches[0][0]
        
        # Finally, try fuzzy matching against all variants
        return self._fuzzy_match_with_variants(normalized_extracted, series_with_variants)
        
    
    def _extract_volume_from_filename(self, filename):
        """Extract volume number from filename"""
        import re
        
        # Common volume patterns
        volume_patterns = [
            r'(?:vol|volume|v)[\s._-]*(\d+)',
            r'(\d+)(?:st|nd|rd|th)?\s*(?:vol|volume)',
            r'v(\d+)',
            r'volume\s*(\d+)'
        ]
        
        filename_lower = filename.lower()
        
        for pattern in volume_patterns:
            match = re.search(pattern, filename_lower)
            if match:
                return match.group(1)
        
        return ""
    
    def _clean_title_for_matching(self, title):
        """
        Clean title by:
          1) removing ALL [bracketed] or (parenthetical) tags,
          2) stripping chapter/volume markers,
          3) trimming trailing punctuation/spaces.
        """
        import re
    
        cleaned = title
    
        # 1) Nuke bracket- and paren- tags in-place
        #    (removes "[...]" and "(...)" anywhere, but leaves text before/after intact)
        cleaned = re.sub(r'\[.*?\]|\(.*?\)', '', cleaned)
    
        # 2) Remove chapter/volume markers _after_ that
        chapter_vol_patterns = [
            r'\bCh\.?\s*\d+.*$',      # Ch. 12, Ch12…
            r'\bChapter\s*\d+.*$',    # Chapter 3…
            r'\bC\d+.*$',             # C12…
            r'\bVol\.?\s*\d+.*$',     # Vol. 1…
            r'\bVolume\s*\d+.*$',     # Volume 2…
            r'\bV\d+.*$',             # V2…
            r'\d+\.\d+.*$',           # 1.1, 2.5…
        ]
        for pat in chapter_vol_patterns:
            cleaned = re.sub(pat, '', cleaned, flags=re.IGNORECASE).strip()
    
        # 3) Trim off any leftover punctuation / separators at end
        cleaned = re.sub(r'[\s\-_–—~\.\,\:\!\?\[\]\(\)\{\}]+$', '', cleaned)
    
        # Collapse multiple spaces to one
        cleaned = ' '.join(cleaned.split())
    
        return cleaned
    
    def _normalize_for_comparison(self, title):
        """Normalize title for comparison by removing special characters and converting to lowercase"""
        # Remove special characters and normalize spacing
        normalized = re.sub(r'[^\w\s]', '', title.lower())
        normalized = ' '.join(normalized.split())
        return normalized
    
    def _fuzzy_match_with_variants(self, normalized_extracted, series_with_variants):
        """Perform fuzzy matching against all title variants"""
        try:
            from difflib import SequenceMatcher
            
            best_match = None
            best_ratio = 0.0
            threshold = 0.8  # Minimum similarity threshold
            
            for series_name, title_variants in series_with_variants:
                for title_variant in title_variants:
                    cleaned_variant = self._clean_title_for_matching(title_variant)
                    normalized_variant = self._normalize_for_comparison(cleaned_variant)
                    
                    ratio = SequenceMatcher(None, normalized_extracted, normalized_variant).ratio()
                    if ratio > best_ratio and ratio >= threshold:
                        best_ratio = ratio
                        best_match = series_name
            
            return best_match
        except Exception as e:
            logging.error(f"Error in fuzzy matching: {e}")
            return None
        
    def _match_current_file_with_db(self):
        """Match current file with series DB based on filename"""
        if not self.cbz_paths or self.current_index >= len(self.cbz_paths):
            messagebox.showwarning("Warning", "No file selected.")
            return
        
        current_file = self.cbz_paths[self.current_index]
        filename = os.path.basename(current_file)
        extracted_title = self._extract_title_from_filename(filename)
        
        if not extracted_title:
            messagebox.showwarning("Warning", f"Could not extract title from filename: {filename}")
            return
        
        # Search for matching series in DB (now includes aliases)
        all_series = series_db.get_all_series_with_aliases()
        best_match = self._find_best_match(extracted_title, all_series)
        
        if best_match:
            series_metadata = series_db.load_series_metadata(best_match)
            if series_metadata:
                # Apply to current file only
                existing_metadata = self.file_metadata.get(current_file, {})
                file_specific_data = {
                    'Volume': existing_metadata.get('Volume', ''),
                    'Number': existing_metadata.get('Number', ''),
                    'PageCount': existing_metadata.get('PageCount', '')
                }
                
                self.file_metadata[current_file] = series_metadata.copy()
                self.file_metadata[current_file].update(file_specific_data)
                
                # Auto-extract volume if not already set
                if not file_specific_data['Volume']:
                    volume = self._extract_volume_from_filename(filename)
                    if volume:
                        self.file_metadata[current_file]['Volume'] = volume
                
                self.load_metadata(self.current_index)
                messagebox.showinfo("Match Found", f"Matched '{filename}' with series '{best_match}'")
            else:
                messagebox.showerror("Error", f"Failed to load metadata for matched series '{best_match}'")
        else:
            messagebox.showinfo("No Match", f"No matching series found for '{filename}'\nExtracted title: '{extracted_title}'")
    
    def _match_all_files_with_db(self):
        """Match all files with series DB based on filenames"""
        if not self.cbz_paths:
            messagebox.showwarning("Warning", "No CBZ files loaded.")
            return
        
        all_series = series_db.get_all_series_with_aliases()
        if not all_series:
            messagebox.showwarning("Warning", "No series found in database.")
            return
        
        matched_files = 0
        match_results = []
        
        for cbz_path in self.cbz_paths:
            filename = os.path.basename(cbz_path)
            extracted_title = self._extract_title_from_filename(filename)
            
            if not extracted_title:
                match_results.append(f"❌ {filename} - Could not extract title")
                continue
            
            # Search for matching series (now includes aliases)
            best_match = self._find_best_match(extracted_title, all_series)
            
            if best_match:
                series_metadata = series_db.load_series_metadata(best_match)
                if series_metadata:
                    # Apply to this file
                    existing_metadata = self.file_metadata.get(cbz_path, {})
                    file_specific_data = {
                        'Volume': existing_metadata.get('Volume', ''),
                        'Number': existing_metadata.get('Number', ''),
                        'PageCount': existing_metadata.get('PageCount', '')
                    }
                    
                    self.file_metadata[cbz_path] = series_metadata.copy()
                    self.file_metadata[cbz_path].update(file_specific_data)
                    
                    # Auto-extract volume if not already set
                    if not file_specific_data['Volume']:
                        volume = self._extract_volume_from_filename(filename)
                        if volume:
                            self.file_metadata[cbz_path]['Volume'] = volume
                    
                    matched_files += 1
                    match_results.append(f"✅ {filename} → {best_match}")
                else:
                    match_results.append(f"❌ {filename} - Failed to load '{best_match}' metadata")
            else:
                match_results.append(f"❌ {filename} - No match found")
        
        # Refresh display
        self.load_metadata(self.current_index)
        
        # Show results
        result_text = f"Matching Results: {matched_files}/{len(self.cbz_paths)} files matched\n\n"
        result_text += "\n".join(match_results)
        
        # Show results in a scrollable dialog
        self._show_match_results(result_text)
    
    def _show_match_results(self, results_text):
        """Show match results in a scrollable dialog"""
        dialog = tk.Toplevel(self)
        dialog.title("Match Results")
        dialog.transient(self)
        dialog.grab_set()
        
        # Main frame
        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.pack(fill='both', expand=True)
        
        # Text widget with scrollbar
        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill='both', expand=True, pady=(0, 10))
        
        text_widget = tk.Text(text_frame, wrap='word', font=('Consolas', 10))
        scrollbar = ttk.Scrollbar(text_frame, orient='vertical', command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        
        text_widget.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Insert results
        text_widget.insert('1.0', results_text)
        text_widget.configure(state='disabled')  # Make read-only
        
        # Close button
        ttk.Button(main_frame, text="Close", command=dialog.destroy).pack(pady=(0, 0))
        
        # Set size and center AFTER all content is created
        dialog.geometry("800x600")
        dialog.update_idletasks()
        center_window(dialog, 800, 600)

        
    def open_series_manager(self):
        """Open the series database manager"""
        dialog = SeriesManagerDialog(self)
        self.wait_window(dialog)
        
        if hasattr(dialog, 'selected_series') and dialog.selected_series:
            # Load selected series to files
            series_metadata = series_db.load_series_metadata(dialog.selected_series)
            if series_metadata:
                if dialog.load_to_all:
                    # Apply to all files
                    for cbz_path in self.cbz_paths:
                        existing_metadata = self.file_metadata.get(cbz_path, {})
                        file_specific_data = {
                            'Volume': existing_metadata.get('Volume', ''),
                            'Number': existing_metadata.get('Number', ''),
                            'PageCount': existing_metadata.get('PageCount', '')
                        }
                        
                        self.file_metadata[cbz_path] = series_metadata.copy()
                        self.file_metadata[cbz_path].update(file_specific_data)
                    
                    messagebox.showinfo("Success", f"Loaded '{dialog.selected_series}' metadata to all files")
                else:
                    # Apply to current file only
                    if self.cbz_paths and self.current_index < len(self.cbz_paths):
                        current_file = self.cbz_paths[self.current_index]
                        existing_metadata = self.file_metadata.get(current_file, {})
                        file_specific_data = {
                            'Volume': existing_metadata.get('Volume', ''),
                            'Number': existing_metadata.get('Number', ''),
                            'PageCount': existing_metadata.get('PageCount', '')
                        }
                        
                        self.file_metadata[current_file] = series_metadata.copy()
                        self.file_metadata[current_file].update(file_specific_data)
                        
                        messagebox.showinfo("Success", f"Loaded '{dialog.selected_series}' metadata to current file")
                    else:
                        messagebox.showwarning("Warning", "No file selected")
                
                # Refresh display
                self.load_metadata(self.current_index)
            else:
                messagebox.showerror("Error", f"Failed to load metadata for '{dialog.selected_series}'")
        
        elif hasattr(dialog, 'match_mode') and dialog.match_mode:
            # Handle matching modes
            if dialog.load_to_all:
                self._match_all_files_with_db()
            else:
                self._match_current_file_with_db()
        
    def load_metadata(self, idx):
        """Load metadata for the specified file index"""
        if not self.cbz_paths or idx >= len(self.cbz_paths):
            return
    
        self.current_index = idx
        current_file = self.cbz_paths[idx]
    
        before_meta = self.original_metadata.get(current_file, {field: "" for field in self.fields})
        after_meta = self.file_metadata.get(current_file, {field: "" for field in self.fields})
    
        for field in self.fields:
            before_val = before_meta.get(field, "")
            after_val = after_meta.get(field, "")
    
            # Special formatting for Web field
            if field == "Web":
                before_val = before_val.replace(",", "\n")
                after_val = after_val.replace(",", "\n")
    
            # Update before field (read-only)
            self.before_entries[field].config(state='normal')
            self.before_entries[field].delete("1.0", tk.END)
            self.before_entries[field].insert("1.0", before_val)
            self.before_entries[field].config(state='disabled')
    
            # Update after field (editable) - handle different widget types
            if field in ["AgeRating", "Format"]:
                self.after_entries[field].set(after_val)
            else:
                self.after_entries[field].delete("1.0", tk.END)
                self.after_entries[field].insert("1.0", after_val)
    
                max_lines = max(self._estimate_line_count(before_val), self._estimate_line_count(after_val))
    
                # Force minimum height for Web and Summary fields
                if field == "Web":
                    max_lines = max(max_lines, 12)
                elif field == "Summary":
                    max_lines = max(max_lines, 10)
    
                self.before_entries[field].configure(height=max_lines)
                self.after_entries[field].configure(height=max_lines)
    
    def _estimate_line_count(self, text):
        """Estimate required height in lines for a given text block"""
        import textwrap
        avg_chars_per_line = 100
        lines = textwrap.wrap(text, avg_chars_per_line)
        return min(len(lines) + 1, 12)


    def copy_field(self, field):
        """Copy value from original to updated field"""
        if self.bulk_edit_enabled.get():
            for file in self.cbz_paths:
                original_value = self.original_metadata.get(file, {}).get(field, "")
                if file in self.file_metadata:
                    self.file_metadata[file][field] = original_value
        else:
            if self.current_index < len(self.cbz_paths):
                current_file = self.cbz_paths[self.current_index]
                original_value = self.original_metadata.get(current_file, {}).get(field, "")
                if current_file in self.file_metadata:
                    self.file_metadata[current_file][field] = original_value
    
        current_file = self.cbz_paths[self.current_index]
        value = self.original_metadata.get(current_file, {}).get(field, "")
        target_widget = self.after_entries.get(field)
        if isinstance(target_widget, tk.Text):
            target_widget.delete("1.0", tk.END)
            target_widget.insert("1.0", value)
            self.after_entries[field].configure(height=min(value.count('\n') + 1, 8))
        elif isinstance(target_widget, ttk.Combobox):
            target_widget.set(value)
    
    def clear_field(self, field):
        """Clear a field in updated metadata"""
        if self.bulk_edit_enabled.get():
            for file in self.cbz_paths:
                if file in self.file_metadata:
                    self.file_metadata[file][field] = ""
        else:
            if self.current_index < len(self.cbz_paths):
                current_file = self.cbz_paths[self.current_index]
                if current_file in self.file_metadata:
                    self.file_metadata[current_file][field] = ""
    
        widget = self.after_entries.get(field)
        if isinstance(widget, tk.Text):
            widget.delete("1.0", tk.END)
            widget.configure(height=1)
        elif isinstance(widget, ttk.Combobox):
            widget.set("")

    def copy_all_fields(self):
        """Copy all fields from before to after"""
        for field in self.fields:
            self.copy_field(field)

    def clear_all_fields(self):
        """Clear all metadata fields for current file"""
        if not self.cbz_paths:
            return
            
        for field in self.fields:
            if field in ["AgeRating", "Format"]:
                self.after_entries[field].set("")
            else:
                self.after_entries[field].delete("1.0", tk.END)
            if self.current_index < len(self.cbz_paths):
                current_file = self.cbz_paths[self.current_index]
                self.file_metadata[current_file][field] = ""


    @staticmethod
    def extract_metadata(entry):
        """Extract metadata from API response entry - WITH UPDATED PUBLISHER LOGIC"""
    
        def safe_list(val):
            if isinstance(val, list):
                return val
            elif isinstance(val, str):
                return [val]
            return []
    
        def safe_get(obj, key, default=""):
            return str(obj.get(key, default)) if obj.get(key) is not None else default
    
        # Handle links
        links = entry.get("links", [])
        if isinstance(links, str):
            links = [links]
        elif not isinstance(links, list):
            links = []
    
        links = [link for link in links if link]
        web_links = MetadataGUI.clean_links('; '.join(links))
    
        # NEW PUBLISHER LOGIC - English publisher first, rest in Imprint
        publishers = entry.get("publishers", [])
        pub_text = ""
        imprint_text = ""
    
        if isinstance(publishers, list) and publishers:
            publisher_infos = []
    
            for p in publishers:
                if isinstance(p, dict) and p.get('name') and p.get('type'):
                    name = p.get('name', '').strip()
                    ptype = p.get('type', '').strip()
                    if name:
                        publisher_infos.append((name, ptype))
    
            if publisher_infos:
                if len(publisher_infos) == 1:
                    # Only one publisher — goes to Publisher field, Imprint empty
                    pub_text = publisher_infos[0][0]
                    imprint_text = ""
                else:
                    # Prefer English type first
                    english = [p for p in publisher_infos if p[1].lower() in ['english', 'en']]
                    others = [p for p in publisher_infos if p not in english]
    
                    selected = english[0] if english else publisher_infos[0]
                    pub_text = selected[0]  # Only the name, without (type)
    
                    # All go to Imprint with (type), including the selected one
                    imprint_list = [f"{name} ({ptype})" for name, ptype in publisher_infos]
                    imprint_text = ", ".join(imprint_list)
        
        # Handle localized titles
        roman = safe_get(entry, "romanized_title")
        native = safe_get(entry, "native_title")
        
        secondary_titles = entry.get("secondary_titles", {})
        if isinstance(secondary_titles, dict):
            en_titles = secondary_titles.get("en", [])
            if isinstance(en_titles, list):
                sec = ", ".join(st.get("title", "") for st in en_titles if isinstance(st, dict))
            else:
                sec = ""
        else:
            sec = ""
            
        localized = ", ".join(filter(None, [roman, native, sec]))
        primary_title = safe_get(entry, "title")
        
        # Map content_rating to AgeRating
        content_rating = safe_get(entry, "content_rating")
        age_rating = ""
        if content_rating:
            # Simple mapping - you can expand this as needed
            rating_map = {
                "safe": "Everyone",
                "suggestive": "Teen", 
                "erotica": "Mature 17+",
                "pornographic": "Adults Only 18+"
            }
            age_rating = rating_map.get(content_rating.lower(), content_rating)
        
        return {
            "Title": primary_title,
            "Series": primary_title,
            "Number": "",
            "Volume": "",
            "Summary": MetadataGUI.clean_html_description(safe_get(entry, "description")),
            "Writer": ", ".join(safe_list(entry.get("authors", []))),
            "Penciller": ", ".join(safe_list(entry.get("artists", []))),
            "Inker": ", ".join(safe_list(entry.get("artists", []))),
            "Colorist": ", ".join(safe_list(entry.get("artists", []))),
            "Publisher": pub_text,
            "Imprint": imprint_text,  # NEW: Additional publishers go here
            "Genre": ", ".join(safe_list(entry.get("genres", []))),
            "Tags": ", ".join(safe_list(entry.get("tags", []))),
            "Year": safe_get(entry, "year"),
            "LanguageISO": safe_get(entry, "lang", "en"),
            "Web": web_links,
            "Count": safe_get(entry, "final_volume") or safe_get(entry, "final_chapter"),
            "PageCount": "",
            "Teams": "",
            "Locations": "",
            "LocalizedSeries": localized,
            "Format": "",  # NEW: Empty by default, user can select
            "AgeRating": age_rating,  # NEW: Mapped from content_rating
            "type": safe_get(entry, "type"),
            "content_rating": safe_get(entry, "content_rating"),
            "entry_id": safe_get(entry, "id"),
            "all_titles": {
                "primary": safe_get(entry, "title"),
                "romanized": roman,
                "native": native,
                "secondary": sec
            }
        }

    @staticmethod
    def clean_links(raw):
        """Clean and filter web links"""
        if not raw:
            return ""
            
        blacklist = ['amazon.co.jp', 'ja.wikipedia.org']
        links = [link.strip() for link in re.split(r'[;,]', raw) if link.strip()]
        cleaned = []
        seen = set()
        
        for link in links:
            # Normalize URL
            link_lower = link.lower().rstrip('/')
            if not link_lower.startswith(('http://', 'https://')):
                link = 'https://' + link
                link_lower = 'https://' + link_lower
            
            # Skip blacklisted domains
            if any(bad in link_lower for bad in blacklist):
                continue
                
            # Remove duplicates
            if link_lower not in seen:
                seen.add(link_lower)
                cleaned.append(link)
        
        return ', '.join(cleaned)

    @staticmethod
    def clean_html_description(text):
        """Clean HTML tags from description text for XML compatibility"""
        if not text:
            return ""
        
        # Replace HTML tags with plain text equivalents
        html_replacements = {
            '<br>': '\n', '<br/>': '\n', '<br />': '\n', '</br>': '\n',
            '<i>': '', '</i>': '', '<b>': '', '</b>': '',
            '<strong>': '', '</strong>': '', '<em>': '', '</em>': '',
            '<u>': '', '</u>': '', '<p>': '\n', '</p>': '\n'
        }
        
        cleaned = text
        for tag, replacement in html_replacements.items():
            cleaned = cleaned.replace(tag, replacement)
        
        # Remove any remaining HTML tags and clean up whitespace
        cleaned = re.sub(r'<[^>]+>', '', cleaned)
        cleaned = re.sub(r'\n\s*\n\s*\n+', '\n\n', cleaned)
        
        return cleaned.strip()

    def save_current_metadata(self):
        """Save current form data to metadata storage"""
        if not self.cbz_paths or self.current_index >= len(self.cbz_paths):
            return
            
        current_file = self.cbz_paths[self.current_index]
        metadata = {}
        for field in self.fields:
            if field in ["AgeRating", "Format"]:
                metadata[field] = self.after_entries[field].get()
            else:
                metadata[field] = self.after_entries[field].get("1.0", tk.END).strip()
        self.file_metadata[current_file] = metadata
        
    def on_file_select(self, event):
        """Enhanced file selection handler"""
        selection = self.file_listbox.curselection()
        if not selection:
            return
    
        self.save_current_metadata()
        self.load_metadata(selection[0])
        self.populate_dropdown_for_current_file()
    
        current_file = self.cbz_paths[selection[0]]
    
        if self.metadata_mode.get() == "individual" and current_file in self.individual_metadata_cache:
            cache_data = self.individual_metadata_cache[current_file]
            self.metadata_options = cache_data.get('options', [])
    
            dropdown_values = []
            for meta in self.metadata_options:
                title_text = meta.get("Title", "Unknown")
                type_text = meta.get("type", "")
                year_text = meta.get("Year", "")
                content_rating_text = meta.get("content_rating", "")
    
                parts = [title_text]
                if type_text:
                    parts.append(f"({type_text.title()})")
                if year_text:
                    parts.append(f"({year_text})")
                if content_rating_text:
                    parts.append(f"({content_rating_text.title()})")
    
                dropdown_values.append(" ".join(parts))
    
            self.dropdown['values'] = dropdown_values
    
            if dropdown_values:
                self.dropdown.set(dropdown_values[0])
                self.update_metadata_from_dropdown()
            else:
                self.dropdown.set("No matches found")


    def fetch_metadata_smart(self):
        """Smart fetch that chooses between batch and individual based on mode"""
        mode = self.metadata_mode.get()
        if mode == "batch":
            self.fetch_metadata_batch_fixed()
        else:
            # In individual mode, we should fetch for ALL files, not just current
            self.fetch_metadata_individual()

    def fetch_metadata_batch_fixed(self):
        """FIXED version of fetch_metadata_batch"""
        title = self.title_var.get().strip()
        if not title:
            messagebox.showerror("Error", "Please enter a manga title")
            return
            
        try:
            local_only = self.local_only_mode.get()
            
            # Use the optimized search function
            raw_entries = find_best_match_merge_aware(title)  # This returns raw entries
            
            if not raw_entries:
                messagebox.showinfo("No Results", "No metadata found for this title")
                return
            
            # Extract metadata from raw entries
            self.metadata_options = []
            for entry in raw_entries:
                metadata = self.extract_metadata(entry)
                self.metadata_options.append(metadata)
            
            # Update dropdown with results - FIXED LOGIC
            dropdown_values = []
            for i, meta in enumerate(self.metadata_options):
                # Try different title sources in order of preference
                title_text = (meta.get("Title") or 
                             meta.get("all_titles", {}).get("romanized") or 
                             meta.get("all_titles", {}).get("native") or 
                             "Unknown")
                
                type_text = meta.get("type", "")
                year_text = meta.get("Year", "")
                content_rating_text = meta.get("content_rating", "")
                
                # Build display name
                parts = [title_text]
                if type_text:
                    parts.append(f"({type_text.title()})")
                if year_text:
                    parts.append(f"({year_text})")
                if content_rating_text:
                    parts.append(f"({content_rating_text.title()})")
                
                display_name = " ".join(parts)
                dropdown_values.append(display_name)
                

            self.dropdown['values'] = dropdown_values
            self.dropdown.set("Select a metadata match...")
            
            if len(self.metadata_options) == 1:
                # Auto-select if only one result
                self.dropdown.set(dropdown_values[0])
                self.update_metadata_from_dropdown()
            else:
                messagebox.showinfo("Multiple Results", 
                                  f"Found {len(self.metadata_options)} matches. Please select one from the dropdown.")
                
        except Exception as e:
            logging.error(f"Error fetching metadata: {e}")
            messagebox.showerror("Error", f"Failed to fetch metadata: {str(e)}")

    def fetch_metadata_individual(self):
        """Fetch different metadata for each file based on filename"""
        if not self.cbz_paths:
            messagebox.showerror("Error", "No CBZ files loaded")
            return
        
        # Test title extraction first
        test_titles = []
        for cbz_path in self.cbz_paths[:3]:  # Test first 3 files
            filename = os.path.basename(cbz_path)
            title = self._extract_title_from_filename(filename)
            test_titles.append(f"{filename} → '{title}'")
        
        # Show user what titles will be extracted
        preview_msg = "Will extract these titles from filenames:\n\n" + "\n".join(test_titles)
        if len(self.cbz_paths) > 3:
            preview_msg += f"\n... and {len(self.cbz_paths) - 3} more files"
        
        result = messagebox.askyesno("Confirm Title Extraction", 
                                   preview_msg + "\n\nProceed with metadata fetch for ALL files?")
        if not result:
            return
        
        # Show progress UI
        self.progress_frame.pack(fill='x', pady=(5, 0))
        self.progress_bar.pack(fill='x')
        self.progress_label.pack(anchor='w')
        
        # Start fetching in background thread
        thread = Thread(target=self._fetch_individual_threaded, daemon=True)
        thread.start()
    
    def fetch_metadata_for_current_file(self):
        """Fetch metadata only for the currently selected file"""
        if not self.cbz_paths or self.current_index >= len(self.cbz_paths):
            return
    
        cbz_path = self.cbz_paths[self.current_index]
        filename = os.path.basename(cbz_path)
    
        # Use user-entered title if available, fallback to filename
        title = self.title_var.get().strip()
        if not title:
            title = self._extract_title_from_filename(filename)
    
        result = messagebox.askyesno("Re-Fetch Metadata",
            f"Refetch metadata for:\n\n{filename}\n\nUsing Title: '{title}'?\n(This will overwrite previous matches)")
    
        if not result:
            return
    
        try:
            local_only = self.local_only_mode.get()
            # Use the same function as batch mode but for individual file
            raw_entries = find_best_match_merge_aware(title)
            
            if not raw_entries:
                messagebox.showinfo("No Matches", f"No metadata found for '{title}'")
                return
            
            # Extract metadata from raw entries
            metadata_options = []
            for entry in raw_entries:
                metadata = self.extract_metadata(entry)
                metadata_options.append(metadata)
    
            self.individual_metadata_cache[cbz_path] = {
                'options': metadata_options,
                'title_used': title
            }
            self.dropdown_selection_per_file[cbz_path] = 0
            self.populate_dropdown_for_current_file()
            messagebox.showinfo("Success", f"Updated metadata options for:\n{filename}")
        except Exception as e:
            logging.error(f"Error refetching metadata: {e}")
            messagebox.showerror("Error", f"Failed to refetch metadata: {e}")
    
    def _fetch_individual_threaded(self):
        """Background thread for individual metadata fetching"""
        try:
            total_files = len(self.cbz_paths)
            self.individual_metadata_cache = {}
            successful_fetches = 0
            failed_extractions = []
            failed_fetches = []
    
            for i, cbz_path in enumerate(self.cbz_paths):
                filename = os.path.basename(cbz_path)
                self.after(0, self._update_progress, i, total_files, filename)
    
                title = self._extract_title_from_filename(filename)
                if not title or len(title.strip()) < 2:
                    failed_extractions.append(filename)
                    continue
    
                try:
                    local_only = self.local_only_mode.get()
                    
                    if local_only:
                        # Use optimized local search
                        raw_entries = find_best_match_merge_aware(title)
                        
                        if raw_entries:
                            # Extract metadata from raw entries
                            metadata_options = []
                            for entry in raw_entries:
                                metadata = self.extract_metadata(entry)
                                metadata_options.append(metadata)
                        else:
                            metadata_options = []
                    else:
                        # Use the full search function (local + API)
                        metadata_options = get_metadata_from_dump_or_api(title, local_only=local_only)
    
                    print(f"[R] Matches for '{title}': {len(metadata_options)}")
    
                    # Only cache if we found matches
                    if metadata_options:
                        self.individual_metadata_cache[cbz_path] = {
                            'options': metadata_options,
                            'title_used': title
                        }
                        successful_fetches += 1
                    else:
                        failed_fetches.append(filename)
    
                except Exception as e:
                    failed_fetches.append(f"{filename} (error: {str(e)})")
                    logging.error(f"Error fetching metadata for {filename}: {e}")
    
            self.after(0, self._finish_individual_fetch, successful_fetches, total_files,
                       failed_extractions, failed_fetches)
    
        except Exception as e:
            error_msg = f"Failed to fetch metadata: {str(e)}"
            logging.error(error_msg)
            self.after(0, lambda: messagebox.showerror("Error", error_msg))
            self.after(0, self._hide_progress)

    def _update_progress(self, current, total, filename):
        """Update progress bar and label"""
        progress = (current / total) * 100
        self.progress_bar['value'] = progress
        self.progress_var.set(f"Processing: {filename} ({current + 1}/{total})")
        self.update_idletasks()

    def _finish_individual_fetch(self, successful, total, failed_extractions, failed_fetches):
        """Finish individual fetch process"""
        self._hide_progress()
        
        # Create detailed results message
        results = []
        if successful > 0:
            results.append(f"✓ Successfully fetched metadata for {successful}/{total} files")
        
        if failed_extractions:
            results.append(f"\n⚠ Could not extract titles from {len(failed_extractions)} files:")
            for filename in failed_extractions[:5]:  # Show first 5
                results.append(f"  • {filename}")
            if len(failed_extractions) > 5:
                results.append(f"  ... and {len(failed_extractions) - 5} more")
        
        if failed_fetches:
            results.append(f"\n⚠ No metadata found for {len(failed_fetches)} files:")
            for filename in failed_fetches[:5]:  # Show first 5
                results.append(f"  • {filename}")
            if len(failed_fetches) > 5:
                results.append(f"  ... and {len(failed_fetches) - 5} more")
        
        if successful == 0:
            results.append("\n💡 Tips for better results:")
            results.append("• Make sure filenames contain the manga title")
            results.append("• Try removing extra text like '[Group]' or quality tags")
            results.append("• Use 'Same metadata for all files' if they're the same series")
            
            messagebox.showinfo("No Results", "\n".join(results))
            return

        # Refresh current display
        if self.cbz_paths and self.current_index < len(self.cbz_paths):
            self.load_metadata(self.current_index)
        
        # Update file listbox to show which files have metadata
        self._update_file_listbox_indicators()
        
        # Show results
        messagebox.showinfo("Fetch Complete", "\n".join(results))

    def _hide_progress(self):
        """Hide progress UI"""
        self.progress_frame.pack_forget()

    def _extract_title_from_filename(self, filename):
        """Extract title from filename, removing file extension and chapter/volume info"""
        # Remove file extension
        name = os.path.splitext(filename)[0]
        
        # Clean the title by removing chapter/volume patterns
        cleaned_title = self._clean_title_for_matching(name)
        
        return cleaned_title.strip()

    def _update_file_listbox_indicators(self):
        """Update file listbox to show which files have metadata"""
        if not hasattr(self, 'file_listbox'):
            return
        
        # Clear current items
        self.file_listbox.delete(0, tk.END)
        
        # Re-populate with indicators
        for cbz_path in self.cbz_paths:
            filename = os.path.basename(cbz_path)
            # Add indicator if metadata was fetched individually
            if cbz_path in self.individual_metadata_cache:
                indicator = "✓ "
            else:
                indicator = ""
            
            self.file_listbox.insert(tk.END, f"{indicator}{filename}")

    def fetch_anilist_metadata_gui(self):
        """Fetch AniList metadata for ALL files based on current mode"""
        if not self.cbz_paths:
            messagebox.showerror("Error", "No CBZ files loaded")
            return
        
        mode = self.metadata_mode.get()
        
        if mode == "batch":
            self.fetch_anilist_metadata_batch()
        else:
            self.fetch_anilist_metadata_individual_all()
    
    def fetch_anilist_metadata_batch(self):
        """Fetch AniList metadata once and apply to ALL files (batch mode)"""
        # In batch mode, all files should have the same metadata
        # So we can use any file to get the AniList link
        sample_file = self.cbz_paths[0]
        current_metadata = self.file_metadata.get(sample_file, {})
        web_links = current_metadata.get('Web', '')
        
        if not web_links:
            messagebox.showerror("Error", "No web links found in metadata. Please fetch Mangabaka metadata first.")
            return
        
        # Extract AniList URL
        anilist_url = None
        for link in web_links.split(','):
            link = link.strip()
            if 'anilist.co' in link.lower():
                anilist_url = link
                break
        
        if not anilist_url:
            messagebox.showerror("Error", "No AniList link found in web links. Make sure Mangabaka metadata includes an AniList link.")
            return
        
        # Extract AniList ID
        anilist_id = extract_anilist_id_from_url(anilist_url)
        if not anilist_id:
            messagebox.showerror("Error", f"Could not extract AniList ID from URL: {anilist_url}")
            return
        
        try:
            # Show progress
            self.update_idletasks()
            
            # Fetch AniList metadata ONCE
            anilist_metadata = fetch_anilist_metadata(anilist_id)
            
            if not anilist_metadata:
                messagebox.showerror("Error", "Failed to fetch metadata from AniList")
                return
            
            # Apply the SAME AniList metadata to ALL files
            files_updated = 0
            for cbz_path in self.cbz_paths:
                if cbz_path in self.file_metadata:
                    self.file_metadata[cbz_path].update(anilist_metadata)
                    files_updated += 1
            
            # Refresh current display
            self.load_metadata(self.current_index)
            
            # Show success message
            updated_fields = [field for field, value in anilist_metadata.items() if value]
            messagebox.showinfo("Success", 
                              f"Successfully applied AniList metadata to {files_updated} files!\n\n"
                              f"Updated fields: {', '.join(updated_fields)}")
                              
        except Exception as e:
            logging.error(f"Error in fetch_anilist_metadata_batch: {e}")
            messagebox.showerror("Error", f"Failed to fetch AniList metadata: {str(e)}")
  
    def fetch_anilist_metadata_individual_all(self):
        """Fetch AniList metadata for each file individually (individual mode)"""
        if not self.cbz_paths:
            return
        
        # Confirm with user since this will make multiple API calls
        result = messagebox.askyesno("Fetch AniList Metadata", 
                                    f"This will fetch AniList metadata for all {len(self.cbz_paths)} files.\n\n"
                                    f"Each file may have different AniList links, so this will make separate API calls.\n\n"
                                    f"Continue?")
        if not result:
            return
        
        try:
            files_updated = 0
            files_with_no_links = []
            files_with_errors = []
            
            print(f"\n{'='*60}")
            print(f"🔍 ANILIST METADATA FETCH STARTING")
            print(f"{'='*60}")
            
            # Process each file
            for i, cbz_path in enumerate(self.cbz_paths):
                filename = os.path.basename(cbz_path)
                print(f"📁 Processing [{i+1}/{len(self.cbz_paths)}]: {filename}")
                
                # Update progress (you might want to add a progress bar here)
                self.update_idletasks()
                
                current_metadata = self.file_metadata.get(cbz_path, {})
                web_links = current_metadata.get('Web', '')
                
                if not web_links:
                    files_with_no_links.append(filename)
                    print(f"   ❌ No web links found in metadata")
                    continue
                
                # Extract AniList URL for this file
                anilist_url = None
                for link in web_links.split(','):
                    link = link.strip()
                    if 'anilist.co' in link.lower():
                        anilist_url = link
                        break
                
                if not anilist_url:
                    files_with_no_links.append(filename)
                    print(f"   ❌ No AniList link found in web links")
                    continue
                
                # Extract AniList ID
                try:
                    anilist_id = extract_anilist_id_from_url(anilist_url)
                    if not anilist_id:
                        files_with_errors.append(filename)
                        print(f"   ❌ Could not extract AniList ID from URL: {anilist_url}")
                        continue
                    
                    # Validate ID is numeric
                    if not str(anilist_id).isdigit():
                        files_with_errors.append(filename)
                        print(f"   ❌ Invalid AniList ID format: '{anilist_id}' (must be numeric)")
                        continue
                        
                except Exception as e:
                    files_with_errors.append(filename)
                    print(f"   ❌ Error extracting AniList ID: {str(e)}")
                    continue
                
                # Fetch AniList metadata for this file
                try:
                    anilist_metadata = fetch_anilist_metadata(anilist_id)
                    
                    if anilist_metadata:
                        current_metadata.update(anilist_metadata)
                        self.file_metadata[cbz_path] = current_metadata
                        files_updated += 1
                        print(f"   ✅ Successfully fetched metadata (ID: {anilist_id})")
                    else:
                        files_with_errors.append(filename)
                        print(f"   ❌ AniList API returned no data for ID: {anilist_id}")
                        
                except Exception as e:
                    files_with_errors.append(filename)
                    print(f"   ❌ AniList API error: {str(e)}")
            
            # Print summary to console
            print(f"\n{'='*60}")
            print(f"📊 ANILIST FETCH RESULTS SUMMARY")
            print(f"{'='*60}")
            print(f"✅ Successfully updated: {files_updated}/{len(self.cbz_paths)} files")
            
            if files_with_no_links:
                print(f"⚠️  No AniList links: {len(files_with_no_links)} files")
                for filename in files_with_no_links:
                    print(f"   • {filename}")
            
            if files_with_errors:
                print(f"❌ Errors: {len(files_with_errors)} files")
                for filename in files_with_errors:
                    print(f"   • {filename}")
            
            if files_with_no_links or files_with_errors:
                print(f"\n💡 TROUBLESHOOTING TIPS:")
                if files_with_no_links:
                    print(f"   • Make sure to fetch Mangabaka metadata first")
                    print(f"   • Check that the Mangabaka entries include AniList links")
                if files_with_errors:
                    print(f"   • Check that AniList URLs are properly formatted")
                    print(f"   • Verify the AniList manga IDs are valid")
                    print(f"   • Some entries might not have AniList pages")
            
            print(f"{'='*60}\n")
            
            # Refresh current display
            self.load_metadata(self.current_index)
            
            # Show simple success message
            if files_updated > 0:
                messagebox.showinfo("AniList Fetch Complete", 
                                  f"✅ Successfully updated {files_updated}/{len(self.cbz_paths)} files!\n\n"
                                  f"Check console for detailed results.")
            else:
                messagebox.showwarning("AniList Fetch Complete", 
                                     f"❌ No files were updated.\n\n"
                                     f"Check console for detailed error information.")
            
        except Exception as e:
            logging.error(f"Error in fetch_anilist_metadata_individual_all: {e}")
            messagebox.showerror("Error", f"Failed to fetch AniList metadata: {str(e)}")


    def update_metadata_from_dropdown(self, *args):
        """Updated method to handle both batch and individual modes"""
        selection_idx = self.dropdown.current()
        if selection_idx < 0 or selection_idx >= len(self.metadata_options):
            return
    
        selected_metadata = self.metadata_options[selection_idx].copy()
    
        if self.cbz_paths and self.current_index < len(self.cbz_paths):
            current_file = self.cbz_paths[self.current_index]
            self.dropdown_selection_per_file[current_file] = selection_idx
    
        mode = self.metadata_mode.get()
    
        if mode == "batch":
            for cbz_path in self.cbz_paths:
                volume = extract_volume_from_filename(os.path.basename(cbz_path))
                if volume:
                    selected_metadata["Volume"] = volume
                if "Web" in selected_metadata:
                    selected_metadata["Web"] = self.clean_links(selected_metadata["Web"])
                self.file_metadata[cbz_path].update(selected_metadata)
        else:
            if self.cbz_paths and self.current_index < len(self.cbz_paths):
                current_file = self.cbz_paths[self.current_index]
                volume = extract_volume_from_filename(os.path.basename(current_file))
                if volume:
                    selected_metadata["Volume"] = volume
                if "Web" in selected_metadata:
                    selected_metadata["Web"] = self.clean_links(selected_metadata["Web"])
                self.file_metadata[current_file].update(selected_metadata)
    
        self.load_metadata(self.current_index)

        
    def insert_metadata(self):
        """Insert metadata into all CBZ files - parallel processing version"""
        if not self.cbz_paths:
            messagebox.showerror("Error", "No CBZ files loaded")
            return
        
        # Get thread count (use saved preference or show dialog)
        if not hasattr(self, '_saved_thread_count'):
            thread_count = self._get_thread_count_from_user()
            if thread_count is None:  # User cancelled
                return
            self._saved_thread_count = thread_count
        else:
            thread_count = self._saved_thread_count
        
        # Confirm action with parallel processing info
        result = messagebox.askyesno(
            "Confirm Metadata Insertion", 
            f"Insert metadata into {len(self.cbz_paths)} CBZ files?\n\n"
            "This will modify the files and cannot be undone.\n\n"
            f"Using {thread_count} parallel threads for processing.\n\n"
            "(Right-click Insert Metadata into All CBZs button to change thread count)"
        )
        if not result:
            return
            
        # Save current form data
        self.save_current_metadata()
        
        # Initialize parallel processing variables
        self._progress_lock = Lock()
        self._processed_count = 0
        self._max_workers = thread_count
        
        # Show progress UI
        self._show_insertion_progress()
        
        # Start processing in background thread
        thread = Thread(target=self._insert_metadata_threaded, daemon=True)
        thread.start()
    
    def _show_insertion_progress(self):
        """Show progress UI for metadata insertion"""
        # Create progress frame if it doesn't exist
        if not hasattr(self, 'insertion_progress_frame'):
            # Insert progress UI above the button frame
            # Find the button frame's parent (main_frame) - since self IS the root window
            button_frame_parent = self.button_frame.master if hasattr(self, 'button_frame') else self
            
            self.insertion_progress_frame = ttk.Frame(button_frame_parent)
            
            # Progress bar
            self.insertion_progress_bar = ttk.Progressbar(
                self.insertion_progress_frame, 
                mode='determinate'
            )
            self.insertion_progress_bar.pack(fill='x', pady=(0, 5))
            
            # Status label
            self.insertion_status_var = tk.StringVar(value="")
            self.insertion_status_label = ttk.Label(
                self.insertion_progress_frame, 
                textvariable=self.insertion_status_var
            )
            self.insertion_status_label.pack(anchor='w')
            
            # Cancel button
            self.insertion_cancel_var = tk.BooleanVar(value=False)
            cancel_btn = ttk.Button(
                self.insertion_progress_frame,
                text="Cancel Operation",
                command=lambda: self.insertion_cancel_var.set(True)
            )
            cancel_btn.pack(anchor='center', pady=(5, 0))
        
        # Reset and show progress
        self.insertion_progress_bar['value'] = 0
        self.insertion_status_var.set("Initializing parallel processing...")
        self.insertion_cancel_var.set(False)
        
        # Pack the progress frame above the buttons
        self.insertion_progress_frame.pack(fill='x', pady=(5, 10), before=self.button_frame if hasattr(self, 'button_frame') else None)
        
        # Disable buttons during processing
        self._disable_insertion_buttons()
        
    def _get_thread_count_from_user(self):
        """Show dialog to get thread count from user"""
        # Create dialog window
        dialog = tk.Toplevel(self)
        dialog.title("Thread Configuration")
        dialog.geometry("600x600")
        dialog.resizable(True, True)
        dialog.transient(self)
        dialog.grab_set()
        
        # Center the dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
        # Variables
        result = None
        cpu_count = os.cpu_count() or 1
        default_threads = min(32, cpu_count + 4)
        
        # Use saved preference if available
        if hasattr(self, '_saved_thread_count'):
            default_threads = self._saved_thread_count
        
        # Main frame
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill='both', expand=True)
        
        # Title
        title_label = ttk.Label(main_frame, text="Parallel Processing Configuration", 
                               font=('TkDefaultFont', 12, 'bold'))
        title_label.pack(pady=(0, 15))
        
        # Info section
        info_frame = ttk.LabelFrame(main_frame, text="System Information", padding="10")
        info_frame.pack(fill='x', pady=(0, 15))
        
        ttk.Label(info_frame, text=f"CPU Cores: {cpu_count}").pack(anchor='w')
        ttk.Label(info_frame, text=f"Recommended: {min(32, cpu_count + 4)} threads").pack(anchor='w')
        ttk.Label(info_frame, text="Range: 1-64 threads").pack(anchor='w')
        
        # Thread count selection
        thread_frame = ttk.LabelFrame(main_frame, text="Thread Count", padding="10")
        thread_frame.pack(fill='x', pady=(0, 15))
        
        # Scale widget
        thread_var = tk.IntVar(value=default_threads)
        
        # Scale with proper configuration
        scale_frame = ttk.Frame(thread_frame)
        scale_frame.pack(fill='x', pady=(0, 10))
        
        thread_scale = ttk.Scale(scale_frame, from_=1, to=64, 
                                orient='horizontal', variable=thread_var,
                                length=350)
        thread_scale.pack(side='top', fill='x')
        
        # Scale labels
        label_frame = ttk.Frame(scale_frame)
        label_frame.pack(fill='x', pady=(5, 0))
        ttk.Label(label_frame, text="1", font=('TkDefaultFont', 8)).pack(side='left')
        ttk.Label(label_frame, text="64", font=('TkDefaultFont', 8)).pack(side='right')
        ttk.Label(label_frame, text="32", font=('TkDefaultFont', 8)).pack()
        
        # Current value display
        value_label = ttk.Label(thread_frame, text=f"Selected: {default_threads} threads",
                               font=('TkDefaultFont', 10, 'bold'))
        value_label.pack(pady=(5, 0))
        
        # Update label when scale changes
        def update_label(*args):
            current_val = int(thread_var.get())
            value_label.config(text=f"Selected: {current_val} threads")
            # Update entry field
            if entry_var.get() != str(current_val):
                entry_var.set(str(current_val))
        
        thread_var.trace('w', update_label)
        
        # Entry for precise input
        entry_frame = ttk.Frame(thread_frame)
        entry_frame.pack(fill='x', pady=(10, 0))
        
        ttk.Label(entry_frame, text="Or enter exact value:").pack(side='left')
        entry_var = tk.StringVar(value=str(default_threads))
        thread_entry = ttk.Entry(entry_frame, width=10, textvariable=entry_var)
        thread_entry.pack(side='right')
        
        # Sync entry with scale
        def update_scale(*args):
            try:
                val = int(entry_var.get())
                if 1 <= val <= 64 and val != thread_var.get():
                    thread_var.set(val)
            except ValueError:
                pass
        
        entry_var.trace('w', update_scale)
        
        # Performance tips
        tips_frame = ttk.LabelFrame(main_frame, text="Performance Tips", padding="10")
        tips_frame.pack(fill='x', pady=(0, 15))
        
        tips_text = tk.Text(tips_frame, height=4, wrap='word', font=('TkDefaultFont', 8),
                           relief='flat')
        tips_text.pack(fill='x')
        tips_text.insert('1.0', 
            "• More threads = faster processing for multiple files\n"
            "• Too many threads may cause system slowdown\n"
            "• For SSDs: 8-16 threads usually optimal\n"
            "• For HDDs: 2-4 threads recommended")
        tips_text.config(state='disabled')
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x', pady=(10, 0))
        
        def on_ok():
            nonlocal result
            try:
                value = int(thread_var.get())
                if 1 <= value <= 64:
                    result = value
                    dialog.destroy()
                else:
                    messagebox.showerror("Invalid Input", "Thread count must be between 1 and 64.",
                                       parent=dialog)
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid number.",
                                   parent=dialog)
        
        def on_cancel():
            dialog.destroy()
        
        def on_reset():
            recommended = min(32, cpu_count + 4)
            thread_var.set(recommended)
            entry_var.set(str(recommended))
        
        # Button layout
        ttk.Button(button_frame, text="Reset to Recommended", 
                  command=on_reset).pack(side='left')
        
        right_buttons = ttk.Frame(button_frame)
        right_buttons.pack(side='right')
        
        ttk.Button(right_buttons, text="Cancel", command=on_cancel).pack(side='right', padx=(5, 0))
        ttk.Button(right_buttons, text="OK", command=on_ok, default='active').pack(side='right')
        
        # Handle window close
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)
        
        # Key bindings
        dialog.bind('<Return>', lambda e: on_ok())
        dialog.bind('<Escape>', lambda e: on_cancel())
        
        # Focus on entry and select all
        thread_entry.focus_set()
        thread_entry.select_range(0, 'end')
        
        # Wait for dialog to close
        dialog.wait_window()
        
        return result
    
    def _reset_thread_count(self):
        """Reset saved thread count (utility method for right-click menu)"""
        if hasattr(self, '_saved_thread_count'):
            delattr(self, '_saved_thread_count')
        
        # Show new dialog
        thread_count = self._get_thread_count_from_user()
        if thread_count is not None:
            self._saved_thread_count = thread_count
            messagebox.showinfo("Thread Count Updated", 
                               f"Thread count updated to {thread_count} threads for this session.")
        
        return thread_count
    
    def _hide_insertion_progress(self):
        """Hide progress UI and clean up parallel processing variables"""
        if hasattr(self, 'insertion_progress_frame'):
            self.insertion_progress_frame.pack_forget()
        
        # Clean up parallel processing variables
        if hasattr(self, '_progress_lock'):
            del self._progress_lock
        if hasattr(self, '_processed_count'):
            del self._processed_count
        if hasattr(self, '_max_workers'):
            del self._max_workers
        
        # Re-enable buttons
        self._enable_insertion_buttons()
    
    def _disable_insertion_buttons(self):
        """Disable buttons during metadata insertion"""
        # Store references to buttons for easy access
        if not hasattr(self, '_insertion_buttons'):
            self._insertion_buttons = []
            
            # Find all buttons in button_frame
            if hasattr(self, 'button_frame'):
                for child in self.button_frame.winfo_children():
                    if isinstance(child, ttk.Button):
                        self._insertion_buttons.append(child)
        
        # Disable all buttons
        for btn in self._insertion_buttons:
            btn.configure(state='disabled')
        
        # Also disable other important UI elements
        if hasattr(self, 'title_entry'):
            self.title_entry.configure(state='disabled')
        if hasattr(self, 'file_listbox'):
            self.file_listbox.configure(state='disabled')
    
    def _enable_insertion_buttons(self):
        """Re-enable buttons after metadata insertion"""
        # Re-enable all buttons
        if hasattr(self, '_insertion_buttons'):
            for btn in self._insertion_buttons:
                btn.configure(state='normal')
        
        # Re-enable other UI elements
        if hasattr(self, 'title_entry'):
            self.title_entry.configure(state='normal')
        if hasattr(self, 'file_listbox'):
            self.file_listbox.configure(state='normal')
    
    def _insert_metadata_threaded(self):
        """Background thread for metadata insertion - now with parallel processing"""
        try:
            total_files = len(self.cbz_paths)
            success_count = 0
            error_files = []
            
            # Pre-process all metadata to avoid repeated lookups
            processed_metadata = {}
            for cbz_path in self.cbz_paths:
                raw_metadata = self.file_metadata.get(cbz_path, {field: "" for field in self.fields})
                metadata = {}
                
                for field in self.fields:
                    if field in raw_metadata and raw_metadata[field]:
                        if field == "Web":
                            # Handle Web field specially - convert line breaks to comma-separated
                            content = raw_metadata[field]
                            if isinstance(content, str) and '\n' in content:
                                urls = [url.strip() for url in content.split() if url.strip()]
                                metadata[field] = ', '.join(urls)
                            else:
                                metadata[field] = content
                        else:
                            metadata[field] = raw_metadata[field]
                
                processed_metadata[cbz_path] = metadata
            
            # Use ThreadPoolExecutor for parallel processing
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                # Submit all tasks
                future_to_path = {
                    executor.submit(self._process_single_cbz_file, cbz_path, processed_metadata[cbz_path]): cbz_path 
                    for cbz_path in self.cbz_paths
                }
                
                # Process completed tasks as they finish
                for future in as_completed(future_to_path):
                    # Check for cancellation
                    if hasattr(self, 'insertion_cancel_var') and self.insertion_cancel_var.get():
                        # Cancel remaining tasks
                        for remaining_future in future_to_path:
                            remaining_future.cancel()
                        executor.shutdown(wait=False)
                        self.after(0, self._handle_insertion_cancelled, success_count, error_files)
                        return
                    
                    cbz_path = future_to_path[future]
                    filename = os.path.basename(cbz_path)
                    
                    # Update progress counter thread-safely
                    with self._progress_lock:
                        self._processed_count += 1
                        current_count = self._processed_count
                    
                    # Update progress on main thread
                    self.after(0, self._update_insertion_progress, current_count - 1, total_files, filename)
                    
                    try:
                        result = future.result()
                        if result is True:
                            success_count += 1
                        else:
                            error_files.append(f"{filename}: {result}")
                    except Exception as e:
                        error_msg = str(e)
                        error_files.append(f"{filename}: {error_msg}")
                        logging.error(f"Failed to process {cbz_path}: {e}")
            
            # Finished successfully
            self.after(0, self._finish_insertion, success_count, total_files, error_files)
            
        except Exception as e:
            # Handle unexpected errors
            error_msg = f"Unexpected error during metadata insertion: {str(e)}"
            logging.error(error_msg)
            self.after(0, lambda: self._handle_insertion_error(error_msg, 0))
    
    def _process_single_cbz_file(self, cbz_path, metadata):
        """Process a single CBZ file (called by thread pool)"""
        try:
            # Auto-fill page count if not already set
            if not metadata.get('PageCount'):
                try:
                    page_count = count_pages_in_cbz(cbz_path)
                    metadata['PageCount'] = str(page_count)
                except Exception as e:
                    logging.warning(f"Could not count pages in {os.path.basename(cbz_path)}: {e}")
                    # Continue without page count
            
            # Create and insert XML
            xml_data = create_comicinfo_xml(metadata)
            insert_comicinfo_into_cbz(cbz_path, xml_data)
            return True
            
        except Exception as e:
            return str(e)
    
    def _update_insertion_progress(self, current, total, filename):
        """Update progress bar and status (called on main thread)"""
        if hasattr(self, 'insertion_progress_bar'):
            progress = (current / total) * 100
            self.insertion_progress_bar['value'] = progress
            
        if hasattr(self, 'insertion_status_var'):
            # Truncate long filenames for display
            display_name = filename
            if len(display_name) > 50:
                display_name = display_name[:47] + "..."
            
            # Show parallel processing status with user-selected thread count
            remaining = total - current
            self.insertion_status_var.set(
                f"Processing: {display_name} ({current + 1}/{total}) "
                f"[{remaining} remaining, using {getattr(self, '_max_workers', 1)} threads]"
            )
        
        # Force UI update
        self.update_idletasks()
    
    def _finish_insertion(self, success_count, total_files, error_files):
        """Handle successful completion (called on main thread)"""
        self._hide_insertion_progress()
        
        # Show results
        if error_files:
            if len(error_files) > 10:  # Limit error display for readability
                shown_errors = error_files[:10]
                error_display = "\n".join(shown_errors) + f"\n... and {len(error_files) - 10} more errors"
            else:
                error_display = "\n".join(error_files)
            
            # Create scrollable error dialog for many errors
            if len(error_files) > 5:
                self._show_detailed_results(success_count, total_files, error_files)
            else:
                error_msg = (f"Processed {success_count}/{total_files} files successfully.\n\n"
                            f"Errors in {len(error_files)} files:\n{error_display}")
                messagebox.showwarning("Partial Success", error_msg)
        else:
            messagebox.showinfo("Success", 
                               f"✅ Successfully inserted metadata into all {success_count} CBZ files "
                               f"using {getattr(self, '_max_workers', 1)} parallel threads!")
    
    def _handle_insertion_cancelled(self, success_count, error_files):
        """Handle user cancellation (called on main thread)"""
        self._hide_insertion_progress()
        messagebox.showinfo("Operation Cancelled", 
                           f"❌ Operation cancelled by user.\n\n"
                           f"Successfully processed {success_count} files before cancellation.")
    
    def _handle_insertion_error(self, error_msg, success_count):
        """Handle critical error (called on main thread)"""
        self._hide_insertion_progress()
        messagebox.showerror("Critical Error", 
                            f"💥 Critical error occurred:\n{error_msg}\n\n"
                            f"Successfully processed {success_count} files before error.")
    
    def _show_detailed_results(self, success_count, total_files, error_files):
        """Show detailed results in a scrollable window"""
        result_window = tk.Toplevel(self)
        result_window.title("Metadata Insertion Results")
        result_window.transient(self)
        result_window.grab_set()
        
        # Summary
        summary_text = f"Processed {success_count}/{total_files} files successfully"
        if error_files:
            summary_text += f" • {len(error_files)} errors"
        
        ttk.Label(result_window, text=summary_text, font=('TkDefaultFont', 10, 'bold')).pack(pady=10)
        
        if error_files:
            ttk.Label(result_window, text="Files with errors:").pack(anchor='w', padx=10)
            
            # Scrollable text widget for errors
            text_frame = ttk.Frame(result_window)
            text_frame.pack(fill='both', expand=True, padx=10, pady=(0, 10))
            
            text_widget = tk.Text(text_frame, wrap='word', height=15)
            scrollbar = ttk.Scrollbar(text_frame, orient='vertical', command=text_widget.yview)
            text_widget.configure(yscrollcommand=scrollbar.set)
            
            text_widget.pack(side='left', fill='both', expand=True)
            scrollbar.pack(side='right', fill='y')
            
            # Insert error details
            for error in error_files:
                text_widget.insert('end', f"• {error}\n")
            
            text_widget.configure(state='disabled')  # Make read-only
        
        # Close button
        ttk.Button(result_window, text="Close", 
                  command=result_window.destroy).pack(pady=10)
        
        # Center the dialog LAST
        result_window.geometry("600x400")
        result_window.update_idletasks()
        center_window(result_window, 600, 400)
                
    def next_file(self):
        """Navigate to next file"""
        if not self.cbz_paths:
            return
            
        self.save_current_metadata()
        self.current_index = (self.current_index + 1) % len(self.cbz_paths)
        self.load_metadata(self.current_index)
        self.populate_dropdown_for_current_file()
        
        # Update listbox selection
        self.file_listbox.select_clear(0, tk.END)
        self.file_listbox.select_set(self.current_index)
        self.file_listbox.see(self.current_index)

    def prev_file(self):
        """Navigate to previous file"""
        if not self.cbz_paths:
            return
            
        self.save_current_metadata()
        self.current_index = (self.current_index - 1) % len(self.cbz_paths)
        self.load_metadata(self.current_index)
        self.populate_dropdown_for_current_file()
        
        # Update listbox selection
        self.file_listbox.select_clear(0, tk.END)
        self.file_listbox.select_set(self.current_index)
        self.file_listbox.see(self.current_index)

    def fill_volume_info(self):
        """Auto-fill volume information for all files - FIXED VERSION"""
        if not self.cbz_paths:
            return
            
        updated_count = 0
        for cbz_path in self.cbz_paths:
            volume = extract_volume_from_filename(os.path.basename(cbz_path))
            if volume:
                # Ensure the file has metadata dict
                if cbz_path not in self.file_metadata:
                    self.file_metadata[cbz_path] = {field: "" for field in self.fields}
                
                self.file_metadata[cbz_path]["Volume"] = volume
                updated_count += 1
        
        # FIXED: Refresh current display AND update file indicators
        self.load_metadata(self.current_index)
        
        # Update file listbox indicators if in individual mode  
        if hasattr(self, '_update_file_listbox_indicators'):
            self._update_file_listbox_indicators()
        
        if updated_count > 0:
            messagebox.showinfo("Volume Info", f"Updated volume information for {updated_count} files")
        else:
            messagebox.showinfo("Volume Info", "No volume information could be extracted from filenames")


    def fill_page_count(self):
        """Count and fill page count for all files"""
        if not self.cbz_paths:
            return
            
        updated_count = 0
        failed_files = []
        
        for cbz_path in self.cbz_paths:
            try:
                page_count = count_pages_in_cbz(cbz_path)
                if page_count > 0:
                    # Ensure the file has metadata dict
                    if cbz_path not in self.file_metadata:
                        self.file_metadata[cbz_path] = {field: "" for field in self.fields}
                    
                    self.file_metadata[cbz_path]["PageCount"] = str(page_count)
                    updated_count += 1
            except Exception as e:
                filename = os.path.basename(cbz_path)
                failed_files.append(filename)
                logging.error(f"Failed to count pages for {cbz_path}: {e}")
        
        # FIXED: Refresh current display AND update file indicators
        self.load_metadata(self.current_index)
        
        # Update file listbox indicators if in individual mode
        if hasattr(self, '_update_file_listbox_indicators'):
            self._update_file_listbox_indicators()
        
        # Show results
        if updated_count > 0:
            if failed_files:
                messagebox.showinfo("Page Count", 
                    f"Updated page count for {updated_count}/{len(self.cbz_paths)} files.\n\n"
                    f"Failed to process {len(failed_files)} files:\n" + 
                    "\n".join(failed_files[:5]) + 
                    (f"\n... and {len(failed_files) - 5} more" if len(failed_files) > 5 else ""))
            else:
                messagebox.showinfo("Page Count", f"Successfully updated page count for all {updated_count} files!")
        else:
            messagebox.showwarning("Page Count", "Could not count pages for any files")



if __name__ == '__main__':
    try:
        app = MetadataGUI()
        app.mainloop()
    except Exception as e:
        logging.error(f"Application error: {e}")
        print(f"Error starting application: {e}")
        input("Press Enter to exit...")
