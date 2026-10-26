import threading
import time
import logging
import uuid
import subprocess
import sys
import shutil
from scanner import scan_directories
from metadata import enrich_metadata
from matcher import find_duplicates, find_version_clusters, verify_group_deep
from online import get_aliases_for_files
from report import generate_report
from send2trash import send2trash
from app_paths import get_app_dir
from store import save_session, load_session
import os
import json

class ScanManager:
    # ... (existing code)

    # --- File Management ---
    def get_file(self, target_uuid):
        return next((f for f in self.files if f['uuid'] == target_uuid), None)

    _KEEP_POLICIES = ('keep_largest', 'keep_newest', 'keep_oldest')

    @staticmethod
    def _pick_keeper(group_files, policy):
        if policy == 'keep_newest':
            return max(group_files, key=lambda f: f.get('mtime', 0))
        if policy == 'keep_oldest':
            return min(group_files, key=lambda f: f.get('mtime', 0))
        return max(group_files, key=lambda f: f.get('size', 0))  # keep_largest

    def cleanup_confirmed(self, policy='keep_largest'):
        """
        For every confirmed duplicate group, keeps one file according to the
        policy and moves the others to trash. Excluded files are never
        deleted. Returns (success, message).
        """
        if policy not in self._KEEP_POLICIES:
            return False, f"Unknown policy: {policy}"
        try:
            cleaned_groups = 0
            deleted_uuids = set()
            freed = 0

            surviving_groups = []
            for g in self.groups:
                candidates = [f for f in g['files'] if not f.get('excluded')]
                if g['status'] != 'confirmed' or len(candidates) < 2:
                    surviving_groups.append(g)
                    continue

                keeper = self._pick_keeper(candidates, policy)
                for f in g['files']:
                    if f is keeper or f.get('excluded'):
                        continue
                    if os.path.exists(f['path']):
                        send2trash(f['path'])
                        self._log(f"Moved to trash: {f['path']}")
                    deleted_uuids.add(f['uuid'])
                    freed += f.get('size', 0)
                cleaned_groups += 1

            if not deleted_uuids:
                return False, "No confirmed groups to clean up."

            self.groups = surviving_groups
            self.files = [f for f in self.files if f['uuid'] not in deleted_uuids]
            self._prune_version_groups(deleted_uuids)

            freed_mb = freed / 1024 / 1024
            self._log(f"Cleanup: {cleaned_groups} groups cleaned, {len(deleted_uuids)} files trashed, {freed_mb:.1f} MB freed.")
            self._save_session()
            generate_report(self.files, self.groups, self.output_file)
            return True, f"Cleaned {cleaned_groups} groups, trashed {len(deleted_uuids)} files, freed {freed_mb:.1f} MB."
        except Exception as e:
            self._log(f"Cleanup error: {e}")
            return False, str(e)

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
            self._prune_version_groups({target_uuid})
            
            # Clean up empty groups
            self.groups = [g for g in self.groups if len(g['files']) > 1]
            
            self._save_session()
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
            self._prune_version_groups(target_set)
            
            # Clean up empty/single groups
            self.groups = [g for g in self.groups if len(g['files']) > 1]
            
            self._save_session()
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
            removed_uuids = set()
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
                removed_uuids.add(file_obj['uuid'])
                deleted_count += 1
            
            self._prune_version_groups(removed_uuids)

            # After deleting others, this group is no longer a duplicate group
            # So we remove the group entirely from the groups list
            self.groups = [g for g in self.groups if g['id'] != group_id]
            
            self._save_session()
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
        self.version_groups = [] # Same movie, different versions (quality compare candidates)
        self.compare_result = None

        # Runtime files live in a writable dir (exe dir / cwd / %LOCALAPPDATA%)
        self.output_file = os.path.join(get_app_dir(), "movie_report.html")
        self.exclusions_file = os.path.join(get_app_dir(), "exclusions.json")
        self.exclusions = self._load_exclusions() # Load exclusions

        self._stop_event = threading.Event()
        self._thread = None

        self._load_session_state()

    def _load_session_state(self):
        """Restores the previous scan session so results survive restarts."""
        session = load_session()
        if not session:
            return
        files, groups, version_groups, aliases = session
        if files:
            self.files = files
            self.groups = groups or []
            self.version_groups = version_groups or []
            self.aliases_map = aliases or {}
            logging.info(f"Restored session: {len(self.files)} files, {len(self.groups)} groups.")

    def _save_session(self):
        save_session(self.files, self.groups, self.aliases_map, self.version_groups)

    def _load_exclusions(self):
        """Loads exclusions from JSON file."""
        try:
            if os.path.exists(self.exclusions_file):
                with open(self.exclusions_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logging.error(f"Error loading exclusions: {e}")
        return {} # { "filepath": size }

    def _save_exclusions(self):
        """Saves exclusions to JSON file."""
        try:
            with open(self.exclusions_file, 'w', encoding='utf-8') as f:
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
                        
            self._save_session()
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
            "version_groups": self.version_groups,
            "aliases_count": len(self.aliases_map)
        }

    # --- Step 1: Scan & Metadata ---
    def start_scan(self, paths, extensions=None, ignore_hidden=True, ignore_system=True):
        if self.is_busy():
            return False, "Task already in progress"

        # Keep the previous session data: the scan worker uses it for
        # incremental reuse and replaces it wholesale when finished.
        self._reset_state(full=False)
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

    # --- Step 5: Version Clustering (same movie, different versions) ---
    def start_version_clustering(self, threshold=85):
        if self.is_busy():
            return False, "Task already in progress"
        if not self.files:
            return False, "No files to cluster. Please scan first."

        self._start_thread(self._run_version_clustering, float(threshold))
        return True, "Version clustering started"

    # --- Step 6: Quality Compare (via videodiff) ---
    def start_compare(self, uuid_a, uuid_b):
        if self.is_busy():
            return False, "Task already in progress"
        fa = self.get_file(uuid_a)
        fb = self.get_file(uuid_b)
        if not fa or not fb:
            return False, "File not found"
        if fa['uuid'] == fb['uuid']:
            return False, "Select two different files"

        self._start_thread(self._run_compare, fa, fb)
        return True, "Comparison started"

    def start_batch_compare(self, uuids):
        """版本组批量对比：N 个文件跑 N-1 个相邻对（按体积降序）。"""
        if self.is_busy():
            return False, "Task already in progress"
        uniq = []
        seen = set()
        for u in (uuids or []):
            if u and u not in seen and self.get_file(u):
                seen.add(u)
                uniq.append(u)
        if len(uniq) < 2:
            return False, "Select at least two existing files"

        self._start_thread(self._run_batch_compare, uniq)
        return True, "Batch comparison started"

    # --- Control ---
    def stop_scan(self):
        if self.status != "idle":
            self._stop_event.set()
            self.status = "cancelled"
            self.message = "Stopping..."
            return True
        return False

    def is_busy(self):
        return self.status in ["scanning", "enriching", "searching", "matching", "comparing"]

    # --- Internal Helpers ---
    def _reset_state(self, full=False):
        self._stop_event.clear()
        self.progress = 0
        self.logs = []
        if full:
            self.files = []
            self.aliases_map = {}
            self.groups = []
            self.version_groups = []

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

            # Incremental scan: files whose path+size+mtime are unchanged keep
            # their uuid and cached metadata (no ffprobe/dHash re-run).
            prev_by_path = {f['path']: f for f in self.files if f.get('uuid')}
            skip_paths = set()
            reused = 0
            for f in raw_files:
                p = prev_by_path.get(f['path'])
                if p and p.get('size') == f['size'] and p.get('mtime') == f['mtime']:
                    f['uuid'] = p['uuid']
                    f['duration'] = p.get('duration', 0.0)
                    f['size_mb'] = p.get('size_mb')
                    if p.get('image_hash'):
                        f['image_hash'] = p['image_hash']
                    if p.get('content_hash'):
                        f['content_hash'] = p['content_hash']
                    skip_paths.add(f['path'])
                    reused += 1

            for f in raw_files:
                if not f.get('uuid'):
                    f['uuid'] = str(uuid.uuid4())

            if reused:
                self._log(f"Incremental scan: reusing metadata for {reused}/{len(raw_files)} files.")

            def meta_progress(curr, total, msg):
                if self._stop_event.is_set(): return
                pct = 30 + (curr / total * 70)
                self.progress = int(pct)
                self.message = msg

            self.files = enrich_metadata(raw_files, progress_callback=meta_progress,
                                         skip_paths=skip_paths,
                                         stop_check=self._stop_event.is_set)

            if self._check_stop(): return

            # Refresh groups left from the previous session so they point at
            # the fresh file dicts (same uuids), dropping vanished files.
            if self.groups:
                files_by_uuid = {f['uuid']: f for f in self.files}
                refreshed = []
                for g in self.groups:
                    g['files'] = [files_by_uuid[f['uuid']] for f in g['files'] if f['uuid'] in files_by_uuid]
                    if len(g['files']) > 1:
                        refreshed.append(g)
                self.groups = refreshed

            self.status = "scanned" # Intermediate state
            self.progress = 100
            self.message = "Scan complete. Ready for filtering or matching."
            self._log("Scan & Metadata extraction finished.")

            # Persist session + auto-save report
            self._save_session()
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
            
            self._save_session()
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
            self._save_session()
            
            if id_to_split:
                processed_ids = set(id_to_split.keys())
                new_groups = []
                dissolved = 0
                for g in self.groups:
                    if g['id'] not in processed_ids:
                        new_groups.append(g)
                        continue
                    for split in id_to_split[g['id']]:
                        # A verified singleton is NOT a duplicate: drop it so
                        # the list stays clean. Keep it only if hashing failed
                        # (e.g. network file unreadable) so it stays suspect.
                        if len(split['files']) >= 2 or any(
                                f.get('content_hash') is None for f in split['files']):
                            new_groups.append(split)
                        else:
                            dissolved += 1
                self.groups = new_groups
                if dissolved:
                    self._log(f"Deep verification dissolved {dissolved} group(s) as non-duplicates.")
            
            # Update report
            generate_report(self.files, self.groups, self.output_file)
            
        except Exception as e:
            self.status = "error"
            self.message = str(e)
            self._log(f"Deep search error: {e}")

    def _prune_version_groups(self, removed_uuids):
        """Drops deleted files from version groups (called by delete flows)."""
        if not self.version_groups or not removed_uuids:
            return
        for g in self.version_groups:
            g['files'] = [f for f in g['files'] if f['uuid'] not in removed_uuids]
        self.version_groups = [g for g in self.version_groups if len(g['files']) > 1]

    def _run_version_clustering(self, threshold):
        try:
            self.status = "matching"
            self.message = "Clustering movie versions..."
            self.progress = 30
            self._log(f"Version clustering (threshold={threshold:.0f})...")

            version_groups = find_version_clusters(self.files, self.aliases_map, threshold)

            self.version_groups = version_groups
            self._log(f"Found {len(version_groups)} version clusters.")

            # Persist + write report BEFORE flipping to the terminal status,
            # so anything waiting on status sees fully saved state.
            self._save_session()
            generate_report(self.files, self.groups, self.output_file)

            self.status = "scanned"
            self.progress = 100
            self.message = "Version clustering complete."
        except Exception as e:
            self.status = "error"
            self.message = f"Error: {str(e)}"
            self._log(f"Version clustering error: {e}")
            logging.exception("Version clustering error")

    @staticmethod
    def _videodiff_cmd():
        """Resolves the videodiff launcher: VIDEODIFF env > PATH > module."""
        env_path = os.environ.get('VIDEODIFF')
        if env_path:
            return [env_path]
        which = shutil.which('videodiff')
        if which:
            return [which]
        return [sys.executable, '-m', 'videodiff']

    def _run_compare(self, fa, fb):
        try:
            self.status = "comparing"
            self.message = "Comparing video quality (videodiff)..."
            self.progress = 50
            self.compare_result = self._compare_one(fa, fb)
            if self.compare_result.get('error'):
                self._log('Compare failed: ' + self.compare_result['error'])
                self.message = "Compare failed."
            else:
                self._log("Compare complete.")
            self.status = "scanned"
            self.progress = 100
            self.message = self.message if self.message.startswith("Compare") else "Compare complete."
        except subprocess.TimeoutExpired:
            self.compare_result = {'error': '对比超时（超过 60 分钟）'}
            self.status = "scanned"
            self.progress = 100
            self.message = "Compare timed out."
        except Exception as e:
            self.compare_result = {'error': str(e)}
            self.status = "error"
            self.message = 'Error: ' + str(e)
            self._log('Compare error: ' + str(e))

    def _compare_one(self, fa, fb, preflight_done=False):
        """对比一对文件，返回单个 compare 结果 dict（含 error 字段）。

        preflight_done: 批量模式下 doctor 已通过，跳过重复预检。
        """
        try:
            da, db = fa.get('duration') or 0, fb.get('duration') or 0
            align_warning = bool(da and db and abs(da - db) / max(da, db) > 0.005)
            head = {
                'a': {'uuid': fa['uuid'], 'name': fa['filename']},
                'b': {'uuid': fb['uuid'], 'name': fb['filename']},
                'align_warning': align_warning,
            }

            cmd = self._videodiff_cmd()
            if not preflight_done:
                # Preflight: doctor --json (exit 10 = environment not usable)
                try:
                    pre = subprocess.run(cmd + ['doctor', '--json'], capture_output=True,
                                         text=True, encoding='utf-8', errors='replace', timeout=120)
                    if pre.returncode != 0:
                        detail = ''
                        try:
                            import json as _json
                            detail = _json.loads(pre.stdout).get('error', {}).get('message', '')
                        except Exception:
                            detail = (pre.stderr or pre.stdout or '').strip()[:300]
                        return {**head, 'error': 'videodiff 环境不可用: ' + (
                            detail or 'ffmpeg 或指标 filter 缺失（需要带 ssim/psnr/libvmaf 的完整版 ffmpeg）')}
                except FileNotFoundError:
                    return {**head, 'error': '未找到 videodiff。请安装 video_diff 项目'
                                             '（pip install <video_diff 路径>）或设置 VIDEODIFF 环境变量。'}

            proc = subprocess.run(
                cmd + ['compare', fa['path'], fb['path'], '--json'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=3600)

            result = None
            try:
                import json as _json
                result = _json.loads(proc.stdout) if proc.stdout.strip() else None
            except Exception:
                result = None

            if proc.returncode == 0 and result and not result.get('error'):
                return {**head, 'result': result, 'error': None}

            msg = ''
            if result and result.get('error'):
                msg = result['error'].get('message', '')
            if not msg:
                tail = (proc.stderr or proc.stdout or '').strip().splitlines()
                msg = tail[-1] if tail else 'exit code %d' % proc.returncode
            return {**head, 'error': 'videodiff: ' + msg}
        except subprocess.TimeoutExpired:
            return {'a': {'uuid': fa['uuid'], 'name': fa['filename']},
                    'b': {'uuid': fb['uuid'], 'name': fb['filename']},
                    'error': '对比超时（超过 60 分钟）'}

    def _run_batch_compare(self, uuids):
        """版本组批量对比：对 N 个文件按体积降序跑 N-1 个相邻对。

        第一个（体积最大）作为锚点，逐对与下一个比较，如 A vs B、B vs C……
        单对失败不中断整批；结果列表按顺序存入 compare_result['pairs']。
        """
        try:
            self.status = "comparing"
            self.message = "Preparing batch compare..."
            self.progress = 5

            files = [self.get_file(u) for u in uuids]
            files = [f for f in files if f]
            if len(files) < 2:
                self.compare_result = {'error': '至少需要两个文件'}
                self.status = "scanned"
                self.progress = 100
                return

            files.sort(key=lambda f: f.get('size', 0), reverse=True)

            # 批量前先跑第一对：doctor 预检包含在内，环境问题直接终止整批
            probe = self._compare_one(files[0], files[1])
            first_err = str(probe.get('error') or '')
            if '环境不可用' in first_err or '未找到 videodiff' in first_err:
                self.compare_result = {'error': first_err}
                self.status = "scanned"
                self.progress = 100
                self.message = "Batch compare failed (environment)."
                return

            pairs = [probe]  # 第一对已经跑完，直接复用
            for i in range(1, len(files) - 1):
                if self._stop_event.is_set():
                    self._log("Batch compare cancelled.")
                    break
                pair = self._compare_one(files[i], files[i + 1], preflight_done=True)
                pairs.append(pair)
                self.progress = int((i + 1) / (len(files) - 1) * 95)
                self.message = f"Batch compare {i + 1}/{len(files) - 1}: {pair['a']['name']} vs {pair['b']['name']}"
                if pair.get('error'):
                    self._log('Pair failed: ' + pair['error'])

            self.compare_result = {'pairs': pairs, 'error': None}
            self._log(f"Batch compare complete: {len(pairs)} pair(s).")
            self.status = "scanned"
            self.progress = 100
            self.message = "Batch compare complete."
        except Exception as e:
            self.compare_result = {'error': str(e)}
            self.status = "error"
            self.message = 'Error: ' + str(e)
            self._log('Batch compare error: ' + str(e))

# Global instance
manager = ScanManager()
