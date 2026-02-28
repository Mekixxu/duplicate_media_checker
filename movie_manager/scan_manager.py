import threading
import time
import logging
import uuid
from scanner import scan_directories
from metadata import enrich_metadata
from matcher import find_duplicates, verify_group_deep
from online import get_aliases_for_files
from report import generate_report
from send2trash import send2trash
import os
import json

class ScanManager:
    # ... (existing code)

    # --- File Management ---
    def delete_file(self, target_uuid):
        """
        Moves a file to trash and updates internal lists.
        """
        try:
            # Find file
            file_obj = next((f for f in self.files if f['uuid'] == target_uuid), None)
            if not file_obj:
                return False, "File not found in list"
            
            path = file_obj['path']
            if os.path.exists(path):
                send2trash(path)
                self._log(f"Moved to trash: {path}")
            else:
                self._log(f"File already gone: {path}")

            # Remove from main list
            self.files = [f for f in self.files if f['uuid'] != target_uuid]
            
            # Remove from groups
            for group in self.groups:
                group['files'] = [f for f in group['files'] if f['uuid'] != target_uuid]
            
            # Clean up empty groups
            self.groups = [g for g in self.groups if len(g['files']) > 1]
            
            return True, "File deleted"
        except Exception as e:
            self._log(f"Delete error: {e}")
            return False, str(e)

    def delete_files_batch(self, target_uuids):
        """
        Batch deletes files by UUIDs.
        """
        try:
            target_set = set(target_uuids)
            deleted_count = 0
            
            # Identify files to delete
            to_delete = [f for f in self.files if f['uuid'] in target_set]
            
            for file_obj in to_delete:
                path = file_obj['path']
                if os.path.exists(path):
                    send2trash(path)
                    self._log(f"Moved to trash: {path}")
                deleted_count += 1
                
            # Update lists
            self.files = [f for f in self.files if f['uuid'] not in target_set]
            
            # Update groups
            for group in self.groups:
                group['files'] = [f for f in group['files'] if f['uuid'] not in target_set]
            
            # Clean up empty/single groups
            self.groups = [g for g in self.groups if len(g['files']) > 1]
            
            return True, f"Deleted {deleted_count} files."
            
        except Exception as e:
            self._log(f"Batch delete error: {e}")
            return False, str(e)

    def delete_group_others(self, group_id, keep_uuid):
        """
        Deletes all files in a group EXCEPT the one with keep_uuid.
        """
        try:
            group = next((g for g in self.groups if g['id'] == group_id), None)
            if not group:
                return False, "Group not found"
            
            deleted_count = 0
            for file_obj in group['files']:
                if file_obj['uuid'] == keep_uuid:
                    continue
                
                # Delete logic
                path = file_obj['path']
                if os.path.exists(path):
                    send2trash(path)
                    self._log(f"Moved to trash: {path}")
                
                # Remove from main list
                self.files = [f for f in self.files if f['uuid'] != file_obj['uuid']]
                deleted_count += 1
            
            # After deleting others, this group is no longer a duplicate group
            # So we remove the group entirely from the groups list
            self.groups = [g for g in self.groups if g['id'] != group_id]
            
            return True, f"Deleted {deleted_count} files. Group dissolved."
            
        except Exception as e:
            self._log(f"Delete group error: {e}")
            return False, str(e)
    def __init__(self):
        self.status = "idle"  # idle, scanning, enriching, scanned, searching, matching, completed, error, cancelled
        self.progress = 0
        self.message = "Ready"
        self.logs = []
        
        # Data Storage
        self.files = []      # List of file dicts (with 'uuid')
        self.aliases_map = {} # {filename: set(aliases)}
        self.groups = []     # List of duplicate groups
        self.exclusions = self._load_exclusions() # Load exclusions
        
        self._stop_event = threading.Event()
        self._thread = None
        self.output_file = "movie_report.html"
        self.exclusions_file = "exclusions.json"

    def _load_exclusions(self):
        """Loads exclusions from JSON file."""
        try:
            if os.path.exists("exclusions.json"):
                with open("exclusions.json", 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logging.error(f"Error loading exclusions: {e}")
        return {} # { "filepath": size }

    def _save_exclusions(self):
        """Saves exclusions to JSON file."""
        try:
            with open("exclusions.json", 'w', encoding='utf-8') as f:
                json.dump(self.exclusions, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving exclusions: {e}")

    def exclude_files_batch(self, target_uuids):
        """
        Marks files as excluded from duplicates.
        Record path and size.
        """
        try:
            target_set = set(target_uuids)
            excluded_count = 0
            
            # Identify files
            for file_obj in self.files:
                if file_obj['uuid'] in target_set:
                    # Record exclusion
                    # Use path as key
                    self.exclusions[file_obj['path']] = file_obj['size']
                    file_obj['excluded'] = True
                    excluded_count += 1
            
            self._save_exclusions()
            
            # Update groups to reflect exclusion status
            for group in self.groups:
                for f in group['files']:
                    if f['uuid'] in target_set:
                        f['excluded'] = True
                        
            return True, f"Excluded {excluded_count} files."
            
        except Exception as e:
            self._log(f"Batch exclude error: {e}")
            return False, str(e)

    def get_status(self):
        return {
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "logs": self.logs[-20:]  # Return last 20 logs
        }

    def get_results(self):
        return {
            "files": self.files,
            "groups": self.groups,
            "aliases_count": len(self.aliases_map)
        }

    # --- Step 1: Scan & Metadata ---
    def start_scan(self, paths, extensions=None, ignore_hidden=True, ignore_system=True):
        if self.is_busy():
            return False, "Task already in progress"
            
        self._reset_state(full=True)
        self._start_thread(self._run_scan, paths, extensions, ignore_hidden, ignore_system)
        return True, "Scan started"

    # --- Step 2: TMDB Search (Optional) ---
    def start_tmdb_search(self, api_key, target_uuids=None):
        if self.is_busy():
            return False, "Task already in progress"
        if not self.files:
            return False, "No files to search. Please scan first."
            
        self._start_thread(self._run_tmdb, api_key, target_uuids)
        return True, "TMDB search started"

    # --- Step 3: Matching ---
    def start_matching(self, target_uuids=None):
        if self.is_busy():
            return False, "Task already in progress"
        if not self.files:
            return False, "No files to match. Please scan first."
            
        self._start_thread(self._run_matching, target_uuids)
        return True, "Matching started"

    # --- Step 4: Deep Search ---
    def start_deep_search(self, target_group_ids=None):
        if self.is_busy():
            return False, "Task already in progress"
        if not self.groups:
             return False, "No groups to verify."
             
        self._start_thread(self._run_deep_search, target_group_ids)
        return True, "Deep search started"

    # --- Control ---
    def stop_scan(self):
        if self.status != "idle":
            self._stop_event.set()
            self.status = "cancelled"
            self.message = "Stopping..."
            return True
        return False

    def is_busy(self):
        return self.status in ["scanning", "enriching", "searching", "matching"]

    # --- Internal Helpers ---
    def _reset_state(self, full=False):
        self._stop_event.clear()
        self.progress = 0
        self.logs = []
        if full:
            self.files = []
            self.aliases_map = {}
            self.groups = []

    def _start_thread(self, target, *args):
        self._thread = threading.Thread(target=target, args=args)
        self._thread.daemon = True
        self._thread.start()

    def _log(self, msg):
        logging.info(msg)
        self.logs.append(f"{time.strftime('%H:%M:%S')} - {msg}")

    def _check_stop(self):
        if self._stop_event.is_set():
            self.status = "cancelled"
            self.message = "Cancelled by user."
            self._log("Operation cancelled.")
            return True
        return False

    # --- Workers ---
    def _run_scan(self, paths, extensions, ignore_hidden=True, ignore_system=True):
        try:
            # 1. Scanning
            self.status = "scanning"
            self.message = "Scanning directories..."
            self.progress = 5
            self._log(f"Scanning paths: {paths}")
            
            raw_files = scan_directories(paths, extensions, ignore_hidden, ignore_system)
            
            if self._check_stop(): return

            if not raw_files:
                self.status = "scanned"
                self.message = "No video files found."
                self._log("No files found.")
                self.progress = 100
                return

            self._log(f"Found {len(raw_files)} files.")
            self.progress = 30

            # 2. Metadata
            self.status = "enriching"
            self.message = "Extracting metadata..."
            
            # Assign UUIDs here
            for f in raw_files:
                f['uuid'] = str(uuid.uuid4())
            
            def meta_progress(curr, total, msg):
                if self._stop_event.is_set(): return
                pct = 30 + (curr / total * 70) 
                self.progress = int(pct)
                self.message = msg
                
            self.files = enrich_metadata(raw_files, progress_callback=meta_progress)
            
            if self._check_stop(): return
            
            self.status = "scanned" # Intermediate state
            self.progress = 100
            self.message = "Scan complete. Ready for filtering or matching."
            self._log("Scan & Metadata extraction finished.")
            
            # Auto-save report just in case
            generate_report(self.files, self.groups, self.output_file)

        except Exception as e:
            self.status = "error"
            self.message = f"Error: {str(e)}"
            self._log(f"Error occurred: {e}")
            logging.exception("Scan error")

    def _run_tmdb(self, api_key, target_uuids):
        try:
            self.status = "searching"
            self.message = "Searching TMDB..."
            self.progress = 0
            self._log("Starting TMDB search...")
            
            # Filter files to process
            if target_uuids:
                target_set = set(target_uuids)
                files_to_process = [f for f in self.files if f['uuid'] in target_set]
            else:
                files_to_process = self.files
                
            self._log(f"Processing {len(files_to_process)} files for aliases...")
            
            def online_progress(curr, total, msg):
                if self._stop_event.is_set(): return
                pct = (curr / total * 100)
                self.progress = int(pct)
                self.message = msg
                
            new_aliases = get_aliases_for_files(files_to_process, api_key, progress_callback=online_progress)
            
            # Update main map
            self.aliases_map.update(new_aliases)
            
            if self._check_stop(): return
            
            self.status = "scanned" # Return to scanned state (ready for next step)
            self.progress = 100
            self.message = f"TMDB Search complete. Found {len(new_aliases)} new aliases."
            self._log(f"TMDB Search finished. Total aliases: {len(self.aliases_map)}")

        except Exception as e:
            self.status = "error"
            self.message = f"Error: {str(e)}"
            self._log(f"Error: {e}")

    def _run_matching(self, target_uuids=None):
        try:
            self.status = "matching"
            self.message = "Finding duplicates..."
            self.progress = 0
            self._log("Starting duplicate matching...")
            
            # Filter files to match
            if target_uuids:
                target_set = set(target_uuids)
                files_to_process = [f for f in self.files if f['uuid'] in target_set]
            else:
                files_to_process = self.files
                
            # Run Matcher
            self._log(f"Matching {len(files_to_process)} files...")
            groups = find_duplicates(files_to_process, self.aliases_map)
            
            # Post-process groups to apply exclusions
            for group in groups:
                for file_obj in group['files']:
                    # Check if excluded
                    if file_obj['path'] in self.exclusions:
                         # Check size consistency
                         if self.exclusions[file_obj['path']] == file_obj['size']:
                             file_obj['excluded'] = True
                         else:
                             # Size changed, exclusion invalid
                             del self.exclusions[file_obj['path']]
            
            # Save updated exclusions (if any invalid removed)
            self._save_exclusions()

            self.groups = groups
            self._log(f"Found {len(self.groups)} potential duplicate groups.")
            
            self.status = "scanned" # Back to scanned state
            self.progress = 100
            self.message = "Matching complete."
            
            generate_report(self.files, self.groups, self.output_file)
            
        except Exception as e:
            self.status = "error"
            self.message = f"Error: {str(e)}"
            self._log(f"Match error: {e}")
            logging.exception("Match error")

    def _run_deep_search(self, target_group_ids):
        try:
            self.status = "matching" 
            self.message = "Performing deep content verification..."
            self.progress = 0
            
            groups_to_process = self.groups
            if target_group_ids:
                target_set = set(target_group_ids)
                groups_to_process = [g for g in self.groups if g['id'] in target_set]
            
            total = len(groups_to_process)
            id_to_split = {}
            for i, group in enumerate(groups_to_process):
                if self._check_stop(): return
                
                self.message = f"Verifying group: {group['primary_name']}"
                splits = verify_group_deep(group)
                id_to_split[group['id']] = splits
                
                self.progress = int((i + 1) / total * 100)
                
            self.status = "completed"
            self.message = "Deep verification complete."
            self._log(f"Deep verified {total} groups.")
            
            if id_to_split:
                processed_ids = set(id_to_split.keys())
                new_groups = []
                for g in self.groups:
                    if g['id'] in processed_ids:
                        new_groups.extend(id_to_split[g['id']])
                    else:
                        new_groups.append(g)
                self.groups = new_groups
            
            # Update report
            generate_report(self.files, self.groups, self.output_file)
            
        except Exception as e:
            self.status = "error"
            self.message = str(e)
            self._log(f"Deep search error: {e}")

# Global instance
manager = ScanManager()
