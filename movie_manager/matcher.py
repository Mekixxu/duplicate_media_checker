import re
import os
import subprocess
import hashlib
import logging
import tempfile
import shutil
import zlib
from rapidfuzz import fuzz

# Cache for ffmpeg path
_FFMPEG_PATH = None

def get_ffmpeg_path():
    global _FFMPEG_PATH
    if _FFMPEG_PATH:
        return _FFMPEG_PATH
    
    # Check system PATH
    path = shutil.which("ffmpeg")
    if path:
        _FFMPEG_PATH = path
        return path
        
    # Check current directory
    local_path = os.path.join(os.getcwd(), "ffmpeg.exe")
    if os.path.exists(local_path):
        _FFMPEG_PATH = local_path
        return local_path

    # Check ffprobe location (if metadata module is available)
    try:
        from . import metadata
        ffprobe_path = metadata.get_ffprobe_path()
        if ffprobe_path:
            dir_path = os.path.dirname(ffprobe_path)
            potential_path = os.path.join(dir_path, "ffmpeg.exe")
            if os.path.exists(potential_path):
                _FFMPEG_PATH = potential_path
                return potential_path
    except ImportError:
        pass # Ignore if metadata module not found/circular import issue

    return None

def normalize_filename(filename):
    """
    Cleans up filename for better matching.
    Removes extensions, common keywords (1080p, etc.), and separates words.
    """
    # Remove extension
    if '.' in filename:
        name = filename.rsplit('.', 1)[0]
    else:
        name = filename
    
    # Common separators to spaces
    name = re.sub(r'[._\-]', ' ', name)
    
    # Remove common video terms (simplified list)
    stop_words = [
        r'1080p', r'720p', r'2160p', r'4k', r'bluray', r'web-dl', r'x264', r'x265', 
        r'hevc', r'aac', r'dts', r'ac3', r'h264', r'remux', r'proper', r'repack',
        r'dvdrip', r'bdrip', r'hdrip'
    ]
    
    for word in stop_words:
        name = re.sub(word, '', name, flags=re.IGNORECASE)
        
    # Extra spaces
    name = re.sub(r'\s+', ' ', name).strip()
    
    return name.lower()

def extract_id(filename):
    """
    Attempts to extract IDs like RBC-1007.
    Pattern: Letters(2-5)-Numbers(3-5)
    """
    match = re.search(r'([a-zA-Z]{2,5})-?(\d{3,5})', filename)
    if match:
        return f"{match.group(1).upper()}-{match.group(2)}"
    return None

def get_file_hash(filepath, limit_bytes=1024*1024):
    """
    Calculates hash of the first N bytes of a file.
    """
    try:
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            buf = f.read(limit_bytes)
            hasher.update(buf)
        return hasher.hexdigest()
    except Exception:
        return None

def extract_first_frame_hash(filepath):
    """
    Reads the first 4KB and last 4KB of the file and calculates CRC32.
    This avoids ffmpeg overhead and is extremely fast.
    """
    try:
        if not os.path.exists(filepath):
            return None
            
        file_size = os.path.getsize(filepath)
        chunk_size = 4096 # 4KB is a standard page size, better than 128 bytes
        
        with open(filepath, 'rb') as f:
            # Read Header
            header = f.read(chunk_size)
            crc = zlib.crc32(header)
            
            # Read Tail if file is large enough
            if file_size > chunk_size:
                # If file is smaller than 2 chunks, we might overlap, but that's fine for hash
                seek_pos = max(0, file_size - chunk_size)
                f.seek(seek_pos)
                tail = f.read(chunk_size)
                crc = zlib.crc32(tail, crc) # Update CRC with tail
                
            return f"{crc & 0xFFFFFFFF:08x}"
            
    except Exception as e:
        logging.error(f"File hash check failed for {filepath}: {e}")
    return None

def find_duplicates(files, aliases_map=None):
    """
    Groups files based on size/extension, duration, and name similarity.
    Optimized for large datasets (O(N) instead of O(N^2)).
    """
    groups = []
    processed_indices = set()
    
    # Pre-calculate normalized names and IDs
    for f in files:
        f['norm_name'] = normalize_filename(f['filename'])
        f['id_code'] = extract_id(f['filename'])
        f['match_reason'] = [] # Init empty list

    # --- Strategy 1: Exact Size Grouping (O(N)) ---
    # Most reliable indicator for exact duplicates
    size_map = {}
    for i, f in enumerate(files):
        size = f.get('size', 0)
        if size > 0:
            if size not in size_map:
                size_map[size] = []
            size_map[size].append(i)
            
    for size, indices in size_map.items():
        if len(indices) > 1:
            # Check extension within size group
            # We can have multiple groups for same size if extensions differ (though rare for exact dupes)
            # Or we can just group them all if they share size
            
            # Sub-group by extension
            ext_map = {}
            for idx in indices:
                ext = files[idx].get('extension', '')
                if ext not in ext_map: ext_map[ext] = []
                ext_map[ext].append(idx)
                
            for ext, ext_indices in ext_map.items():
                if len(ext_indices) > 1:
                    current_group = []
                    for idx in ext_indices:
                        if idx in processed_indices: continue
                        processed_indices.add(idx)
                        f = files[idx]
                        if not f['match_reason']: f['match_reason'].append('size_exact')
                        current_group.append(f)
                    
                    if len(current_group) > 1:
                        groups.append({
                            'id': f"group_size_{size}_{ext}",
                            'files': current_group,
                            'primary_name': current_group[0]['filename'],
                            'status': 'suspected',
                            'reasons': ['size_exact'],
                            'total_size': sum(f.get('size', 0) for f in current_group)
                        })

    # --- Strategy 2: Duration Window (O(N log N)) ---
    # Sort by duration
    # Only check neighbors within +/- 2 seconds
    
    # Filter out already processed
    remaining_indices = [i for i in range(len(files)) if i not in processed_indices]
    if not remaining_indices:
        # Sort groups by total_size descending
        groups.sort(key=lambda x: x.get('total_size', 0), reverse=True)
        return groups
        
    sorted_by_duration = sorted(remaining_indices, key=lambda i: files[i].get('duration', 0))
    
    # Iterate
    i = 0
    while i < len(sorted_by_duration):
        current_idx = sorted_by_duration[i]
        if current_idx in processed_indices:
            i += 1
            continue
            
        current_file = files[current_idx]
        d1 = current_file.get('duration', 0)
        
        # Skip short files
        if d1 < 120: 
            i += 1
            continue
            
        # Window search
        current_group_indices = [current_idx]
        
        j = i + 1
        while j < len(sorted_by_duration):
            candidate_idx = sorted_by_duration[j]
            if candidate_idx in processed_indices:
                j += 1
                continue
                
            candidate = files[candidate_idx]
            d2 = candidate.get('duration', 0)
            
            # If duration diff > 2s, stop window
            if abs(d2 - d1) > 2:
                break
                
            # Check Name Similarity within this duration window
            # Fuzzy match
            ratio = fuzz.token_set_ratio(current_file['norm_name'], candidate['norm_name'])
            
            is_match = False
            if ratio > 85:
                is_match = True
                candidate['match_reason'].append('name_fuzzy')
            elif current_file['id_code'] and candidate['id_code'] and current_file['id_code'] == candidate['id_code']:
                 is_match = True
                 candidate['match_reason'].append('id_match')
                 
            if is_match:
                current_group_indices.append(candidate_idx)
                
            j += 1
            
        if len(current_group_indices) > 1:
            group_files = []
            reasons = set()
            for idx in current_group_indices:
                processed_indices.add(idx)
                f = files[idx]
                group_files.append(f)
                reasons.update(f['match_reason'])
            
            if not reasons: reasons.add('duration_name_match')
            
            groups.append({
                'id': f"group_dur_{d1}_{i}",
                'files': group_files,
                'primary_name': group_files[0]['filename'],
                'status': 'suspected',
                'reasons': list(reasons),
                'total_size': sum(f.get('size', 0) for f in group_files)
            })
            
        i += 1

    # Sort groups by total_size descending
    groups.sort(key=lambda x: x.get('total_size', 0), reverse=True)

    return groups

def verify_group_deep(group):
    """
    Performs deep content verification on a group.
    Extracts first frame hash.
    Returns updated group with 'confirmed' status if matches found.
    """
    if len(group['files']) < 2:
        return group
        
    # Calculate frame hashes
    hashes = {}
    
    # We use the first file as the "pivot" usually, but here we want to find ANY matches within group
    # Let's compute hash for all
    for f in group['files']:
        # If already has hash, skip (maybe useful later for caching)
        if 'content_hash' not in f:
            h = extract_first_frame_hash(f['path'])
            f['content_hash'] = h
    
    # Check if hashes match
    # If all have same hash -> Confirmed
    # If mixed -> Split group? Or just mark confirmed subset?
    # Requirement: "If matched -> Confirmed Duplicate"
    
    # For simplicity, if the Pivot matches any other, we mark group as confirmed
    # But ideally we should probably flag individual files
    
    pivot = group['files'][0]
    pivot_hash = pivot.get('content_hash')
    
    if not pivot_hash:
        return group # Failed to extract
        
    confirmed_count = 1 # Pivot itself
    for f in group['files'][1:]:
        if f.get('content_hash') == pivot_hash:
            f['match_reason'].append('content_match')
            confirmed_count += 1
            
    if confirmed_count > 1:
        group['status'] = 'confirmed'
        group['reasons'].append('content_match')
    elif confirmed_count == 1 and len(group['files']) > 1:
        # We checked files, but no matches found (other than self)
        # Mark as mismatch so user knows we tried
        group['status'] = 'content_mismatch'
        
    return group
