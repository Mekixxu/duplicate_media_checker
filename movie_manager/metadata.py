import logging
import subprocess
import json
import shutil
import os
import re

# Cache for ffprobe path
_FFPROBE_PATH = None

def get_ffprobe_path():
    global _FFPROBE_PATH
    if _FFPROBE_PATH:
        return _FFPROBE_PATH
        
    # Check system PATH
    path = shutil.which("ffprobe")
    if path:
        _FFPROBE_PATH = path
        return path
        
    # Check current directory (maybe user put it there)
    local_path = os.path.join(os.getcwd(), "ffprobe.exe")
    if os.path.exists(local_path):
        _FFPROBE_PATH = local_path
        return local_path
        
    return None

def parse_duration_str(time_str):
    """
    Parses duration string "HH:MM:SS.mmm" or "SS.mmm" into seconds (float).
    """
    if not time_str or time_str == 'N/A':
        return 0.0
        
    try:
        # Check if it's already a number
        # Replace comma with dot for European locales
        clean_str = str(time_str).replace(',', '.')
        return float(clean_str)
    except ValueError:
        pass
        
    # Try HH:MM:SS format
    try:
        parts = time_str.split(':')
        if len(parts) == 3:
            h, m, s = parts
            return float(h) * 3600 + float(m) * 60 + float(s.replace(',', '.'))
        elif len(parts) == 2:
            m, s = parts
            return float(m) * 60 + float(s.replace(',', '.'))
    except Exception:
        pass
        
    return 0.0

# Common video extensions that ffprobe should process
VIDEO_EXTENSIONS_FOR_FFPROBE = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
    '.m4v', '.mpg', '.mpeg', '.3gp', '.ts', '.mts', '.m2ts',
    '.rmvb', '.vob'
}

def get_duration_ffprobe(file_path):
    """
    Uses ffprobe to get the duration of a video file.
    Returns duration in seconds (float).
    """
    # Skip ffprobe for non-video files to save time and avoid errors
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in VIDEO_EXTENSIONS_FOR_FFPROBE:
        return 0.0

    ffprobe_exe = get_ffprobe_path()
    if not ffprobe_exe:
        return 0

    try:
        # 1. Try JSON output for robust parsing
        cmd = [
            ffprobe_exe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path
        ]
        
        # Run the command with creationflags to hide window on Windows
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
        result = subprocess.run(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True,
            encoding='utf-8', 
            errors='replace',
            startupinfo=startupinfo
        )
        
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                duration = 0.0
                
                # Check format
                if 'format' in data and 'duration' in data['format']:
                     d = parse_duration_str(data['format']['duration'])
                     if d > 0: return d

                # Check streams
                if 'streams' in data:
                    for stream in data['streams']:
                        if stream.get('codec_type') == 'video':
                            if 'duration' in stream:
                                d = parse_duration_str(stream['duration'])
                                if d > 0: return d
                            if 'tags' in stream:
                                tags = stream['tags']
                                for key in ['DURATION', 'duration', 'DURATION-eng', 'length', 'LENGTH']:
                                    if key in tags:
                                        d = parse_duration_str(tags[key])
                                        if d > 0: return d
            except json.JSONDecodeError:
                pass

        # 2. Fallback: Parse standard stderr output (useful for some tricky files)
        # remove -v quiet and json format
        cmd_fallback = [
            ffprobe_exe,
            "-i", file_path
        ]
        
        result = subprocess.run(
            cmd_fallback, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True, 
            encoding='utf-8',
            errors='replace',
            startupinfo=startupinfo
        )
        
        # Duration: 00:23:45.67, start: 0.000000, bitrate: 1234 kb/s
        match = re.search(r'Duration:\s+(\d{1,2}:\d{2}:\d{2}\.\d+)', result.stderr)
        if match:
            return parse_duration_str(match.group(1))
            
        logging.debug(f"Could not find duration for {file_path}")
        return 0.0
        
    except Exception as e:
        logging.debug(f"Error getting duration for {file_path}: {e}")
        return 0

from concurrent.futures import ThreadPoolExecutor, as_completed

# Image types we can fingerprint with Pillow (dHash). Pillow is optional;
# without it image hashing is skipped and images still match by name/size.
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tif', '.tiff'}

def compute_image_dhash(file_path, size=8):
    """
    Difference hash (dHash): resistant to resizing/re-encoding, good enough
    to catch near-duplicate photos. Returns 16-char hex string or None.
    """
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            img = img.convert('L').resize((size + 1, size))
            px = list(img.getdata())
        w = size + 1
        bits = 0
        for r in range(size):
            for c in range(size):
                bits = (bits << 1) | (1 if px[r * w + c] > px[r * w + c + 1] else 0)
        return f"{bits:0{size * size // 4}x}"
    except Exception:
        return None

def enrich_metadata(files, progress_callback=None, skip_paths=None, stop_check=None):
    """
    Iterates through the file list and adds duration and other metadata.
    Uses multithreading to speed up ffprobe calls.
    progress_callback: function(current, total, message)
    skip_paths: set of paths whose metadata is already known (incremental
        scan) - ffprobe/dHash are skipped for them.
    stop_check: callable returning True when the user pressed Stop; queued
        work is cancelled and the loop exits early.
    """
    # Pre-check FFmpeg
    if not get_ffprobe_path():
        logging.error("CRITICAL: ffprobe.exe not found! Durations will be 0.")
        if progress_callback:
            progress_callback(0, len(files), "Error: FFmpeg not found! Cannot get duration.")

    skip_paths = skip_paths or set()
    total_files = len(files)
    completed_count = 0
    cancelled = False

    def process_file(file):
        if stop_check and stop_check():
            return None
        if file['path'] in skip_paths:
            return file.get('duration', 0.0), file.get('size_mb'), file.get('image_hash')
        image_hash = None
        duration = 0.0
        if file.get('extension') in IMAGE_EXTENSIONS:
            image_hash = compute_image_dhash(file['path'])
        else:
            duration = get_duration_ffprobe(file['path'])
        size_mb = round(file['size'] / (1024 * 1024), 2)
        return duration, size_mb, image_hash

    # Use ThreadPoolExecutor
    # FFprobe is I/O + Process bound.
    # For 20k files, max_workers=10-20 is usually a good balance to avoid spawning too many subprocesses.
    executor = ThreadPoolExecutor(max_workers=16)
    try:
        future_to_file = {executor.submit(process_file, f): f for f in files}

        for future in as_completed(future_to_file):
            completed_count += 1
            file_obj = future_to_file[future]
            try:
                result = future.result()
                if result is not None:
                    duration, size_mb, image_hash = result
                    file_obj['duration'] = duration
                    file_obj['size_mb'] = size_mb
                    if image_hash is not None:
                        file_obj['image_hash'] = image_hash
            except Exception as e:
                logging.error(f"Metadata error for {file_obj['filename']}: {e}")

            if stop_check and stop_check():
                cancelled = True
                break

            if progress_callback and completed_count % 50 == 0:
                 progress_callback(completed_count, total_files, f"Analyzing: {completed_count}/{total_files}")
    finally:
        executor.shutdown(wait=not cancelled, cancel_futures=cancelled)

    if progress_callback and not cancelled:
        progress_callback(total_files, total_files, "Analysis complete.")

    return files
