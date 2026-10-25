import re
import os
import subprocess
import logging
import shutil
import zlib
from rapidfuzz import fuzz

from scanner import VIDEO_EXTENSIONS

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

def _hamming_hex(a, b):
    """Hamming distance between two hex hashes; 64 (max) on parse failure."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count('1')
    except (ValueError, TypeError):
        return 64

def _extract_year(filename):
    """Standalone 19xx/20xx token (quality tags like 1080p never match)."""
    m = re.search(r'(?<!\d)(19\d{2}|20\d{2})(?!\d)', filename)
    return int(m.group(1)) if m else None

def _best_name_ratio(file_a, file_b):
    """Best fuzzy ratio across all name variants (filename + aliases)."""
    best = 0
    for a in file_a['_name_variants']:
        for b in file_b['_name_variants']:
            r = fuzz.token_set_ratio(a, b)
            if r > best:
                best = r
                if best == 100:
                    return best
    return best

def find_version_clusters(files, aliases_map=None, threshold=85):
    """
    Clusters video files that look like different VERSIONS of the same movie:
    size and duration are ignored entirely and all video containers share one
    bucket (mkv vs mp4 matches). Guard rails against obvious false positives:
    - files whose extracted release YEARS differ are never merged (kills most
      sequel pairs like "The Raid 2011" vs "The Raid 2 2014");
    - matching id codes cluster unconditionally.
    Returns groups with status 'version', separate from duplicate groups.
    """
    idxs = [i for i, f in enumerate(files) if f.get('extension') in VIDEO_EXTENSIONS]
    for i in idxs:
        f = files[i]
        if 'norm_name' not in f:
            f['norm_name'] = normalize_filename(f['filename'])
        if 'id_code' not in f:
            f['id_code'] = extract_id(f['filename'])
        if '_year' not in f:
            f['_year'] = _extract_year(f['filename'])
    _prepare_name_variants([files[i] for i in idxs], aliases_map)

    n = len(idxs)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Candidate pairs must share at least one significant token; tokens in
    # huge buckets ("the", "1080p"-stripped leftovers) are skipped so the
    # pairwise pass stays bounded on large libraries.
    token_map = {}
    for k, i in enumerate(idxs):
        f = files[i]
        tokens = set()
        for v in f['_name_variants']:
            tokens.update(t for t in v.split() if len(t) >= 3)
        for t in tokens:
            token_map.setdefault(t, []).append(k)

    # Same id code (e.g. RBC-1007 in any naming style) is a strong signal:
    # index by id directly, since "abc 123" and "abc123" share no token.
    id_map = {}
    for k, i in enumerate(idxs):
        code = files[i]['id_code']
        if code:
            id_map.setdefault(code, []).append(k)
    for bucket in id_map.values():
        if len(bucket) < 2:
            continue
        for x in range(len(bucket)):
            for y in range(x + 1, len(bucket)):
                union(bucket[x], bucket[y])

    max_bucket = max(200, n // 5)
    checked = set()
    for bucket in token_map.values():
        if len(bucket) < 2 or len(bucket) > max_bucket:
            continue
        for x in range(len(bucket)):
            for y in range(x + 1, len(bucket)):
                ka, kb = bucket[x], bucket[y]
                pair = (ka, kb) if ka < kb else (kb, ka)
                if pair in checked:
                    continue
                checked.add(pair)
                fa, fb = files[idxs[ka]], files[idxs[kb]]
                ya, yb = fa.get('_year'), fb.get('_year')
                if ya and yb and ya != yb:
                    continue
                if _best_name_ratio(fa, fb) >= threshold:
                    union(ka, kb)

    comp = {}
    for k in range(n):
        comp.setdefault(find(k), []).append(k)

    groups = []
    gid = 0
    for members in comp.values():
        if len(members) < 2:
            continue
        gfiles = sorted((files[idxs[k]] for k in members),
                        key=lambda f: f.get('size', 0), reverse=True)
        groups.append({
            'id': f'version_{gid}',
            'files': gfiles,
            'primary_name': gfiles[0]['filename'],
            'status': 'version',
            'kind': 'version',
            'reasons': ['version_cluster'],
            'total_size': sum(f.get('size', 0) for f in gfiles),
            'threshold': threshold
        })
        gid += 1

    groups.sort(key=lambda g: g['total_size'], reverse=True)
    return groups

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

def _prepare_name_variants(files, aliases_map):
    """
    Attaches '_name_variants' to each file: the normalized filename plus all
    normalized aliases (from TMDB etc.). Two files are name-compatible when
    ANY pair of their variants matches, which is what makes cross-language
    matching ("英国病人" vs "The English Patient") work.
    """
    for f in files:
        variants = [f['norm_name']]
        aliases = aliases_map.get(f['filename']) if aliases_map else None
        if aliases:
            for alias in aliases:
                norm = normalize_filename(alias)
                if norm and norm not in variants:
                    variants.append(norm)
        f['_name_variants'] = variants

def _names_similar(file_a, file_b):
    """True if any name variant pair exceeds the similarity threshold."""
    for a in file_a['_name_variants']:
        for b in file_b['_name_variants']:
            if fuzz.token_set_ratio(a, b) > 85:
                return True
    return False

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
    _prepare_name_variants(files, aliases_map)

    # --- Strategy 0: Same Physical File (hardlink / same file twice) (O(N)) ---
    # Same volume + file index means one file with two paths (hardlink).
    # Deleting one path never frees space, so surface these separately.
    inode_map = {}
    for i, f in enumerate(files):
        inode = f.get('inode')
        volume = f.get('volume')
        if inode and volume is not None:
            inode_map.setdefault((volume, inode), []).append(i)

    for (volume, inode), indices in inode_map.items():
        if len(indices) > 1:
            current_group = []
            for idx in indices:
                if idx in processed_indices: continue
                processed_indices.add(idx)
                f = files[idx]
                f['match_reason'].append('same_file')
                current_group.append(f)

            if len(current_group) > 1:
                groups.append({
                    'id': f"group_inode_{volume}_{inode}",
                    'files': current_group,
                    'primary_name': current_group[0]['filename'],
                    'status': 'confirmed',
                    'reasons': ['same_file'],
                    'total_size': sum(f.get('size', 0) for f in current_group)
                })

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
            # Sub-group by extension
            ext_map = {}
            for idx in indices:
                ext = files[idx].get('extension', '')
                if ext not in ext_map: ext_map[ext] = []
                ext_map[ext].append(idx)

            for ext, ext_indices in ext_map.items():
                if len(ext_indices) > 1:
                    # Same size+ext is not proof: unrelated files can share a
                    # byte size. When both durations are known they must also
                    # agree (unknown duration = 0 stays a wildcard).
                    duration_runs = []
                    for idx in sorted(ext_indices, key=lambda i: files[i].get('duration', 0)):
                        d = files[idx].get('duration', 0)
                        if d > 0 and duration_runs:
                            last_d = max(files[j].get('duration', 0) for j in duration_runs[-1])
                            if last_d > 0 and d - last_d > 2:
                                duration_runs.append([])
                        elif not duration_runs:
                            duration_runs.append([])
                        duration_runs[-1].append(idx)

                    for run in duration_runs:
                        if len(run) < 2:
                            continue
                        current_group = []
                        for idx in run:
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
            # Fuzzy match (filename first, then aliases incl. cross-language)
            ratio = fuzz.token_set_ratio(current_file['norm_name'], candidate['norm_name'])

            is_match = False
            if ratio > 85:
                is_match = True
                candidate['match_reason'].append('name_fuzzy')
            elif _names_similar(current_file, candidate):
                is_match = True
                candidate['match_reason'].append('alias_match')
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

    # --- Strategy 3: Name Similarity Window for Non-Media/Short Files (O(N log N)) ---
    # For files skipped by Strategy 2 (duration < 120, typically non-media files)
    # We sort them by size, and only compare names within a +/- 5% size window
    remaining_indices = [i for i in range(len(files)) if i not in processed_indices]
    if remaining_indices:
        # Sort by size
        sorted_by_size = sorted(remaining_indices, key=lambda i: files[i].get('size', 0))
        
        i = 0
        while i < len(sorted_by_size):
            current_idx = sorted_by_size[i]
            if current_idx in processed_indices:
                i += 1
                continue
                
            current_file = files[current_idx]
            s1 = current_file.get('size', 0)
            
            current_group_indices = [current_idx]
            
            j = i + 1
            while j < len(sorted_by_size):
                candidate_idx = sorted_by_size[j]
                if candidate_idx in processed_indices:
                    j += 1
                    continue
                    
                candidate = files[candidate_idx]
                s2 = candidate.get('size', 0)
                
                # Window condition: size diff <= 5% of s1
                # If s1 is 0, exact match only
                if s1 == 0:
                    if s2 > 0:
                        break
                else:
                    if (s2 - s1) / s1 > 0.05:
                        break
                        
                # Check Name Similarity
                ratio = fuzz.token_set_ratio(current_file['norm_name'], candidate['norm_name'])

                is_match = False
                if ratio > 85:
                    is_match = True
                    candidate['match_reason'].append('name_fuzzy_size_window')
                elif _names_similar(current_file, candidate):
                    is_match = True
                    candidate['match_reason'].append('alias_match_size_window')
                elif current_file['id_code'] and candidate['id_code'] and current_file['id_code'] == candidate['id_code']:
                    is_match = True
                    candidate['match_reason'].append('id_match_size_window')
                    
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
                
                if not reasons: reasons.add('size_name_match')
                
                groups.append({
                    'id': f"group_size_fuzzy_{s1}_{i}",
                    'files': group_files,
                    'primary_name': group_files[0]['filename'],
                    'status': 'suspected',
                    'reasons': list(reasons),
                    'total_size': sum(f.get('size', 0) for f in group_files)
                })
                
            i += 1

    # --- Strategy 3.5: Image dHash Matching (O(N) exact + bounded window near-dup) ---
    # Only files that still have an image_hash (photos etc.) and were not
    # matched above. Exact same hash catches copies/re-encodes; a small
    # Hamming radius within a size-ratio window catches resized crops.
    remaining_indices = [i for i in range(len(files)) if i not in processed_indices
                         and files[i].get('image_hash')]
    if remaining_indices:
        # a) Exact hash buckets
        hash_map = {}
        for idx in remaining_indices:
            hash_map.setdefault(files[idx]['image_hash'], []).append(idx)

        for h, indices in hash_map.items():
            if len(indices) > 1:
                group_files = []
                for idx in indices:
                    processed_indices.add(idx)
                    f = files[idx]
                    f['match_reason'].append('image_hash')
                    group_files.append(f)
                groups.append({
                    'id': f"group_ihash_{h}",
                    'files': group_files,
                    'primary_name': group_files[0]['filename'],
                    'status': 'suspected',
                    'reasons': ['image_hash'],
                    'total_size': sum(f.get('size', 0) for f in group_files)
                })

        # b) Near-dup window: sorted by size, compare while size ratio <= 2x
        remaining_indices = [i for i in range(len(files)) if i not in processed_indices
                             and files[i].get('image_hash')]
        sorted_by_size = sorted(remaining_indices, key=lambda i: files[i].get('size', 0))
        i = 0
        while i < len(sorted_by_size):
            current_idx = sorted_by_size[i]
            if current_idx in processed_indices:
                i += 1
                continue

            current_file = files[current_idx]
            s1 = current_file.get('size', 0) or 1
            current_group_indices = [current_idx]
            comparisons = 0

            j = i + 1
            while j < len(sorted_by_size):
                candidate_idx = sorted_by_size[j]
                if candidate_idx in processed_indices:
                    j += 1
                    continue

                candidate = files[candidate_idx]
                s2 = candidate.get('size', 0)
                if s1 == 0 or s2 / s1 > 2:
                    break
                if s1 / s2 > 2:
                    j += 1
                    continue

                comparisons += 1
                if comparisons > 500:  # safety valve for huge same-size sets
                    break

                if _hamming_hex(current_file['image_hash'], candidate['image_hash']) <= 10:
                    current_group_indices.append(candidate_idx)

                j += 1

            if len(current_group_indices) > 1:
                group_files = []
                for idx in current_group_indices:
                    processed_indices.add(idx)
                    f = files[idx]
                    f['match_reason'].append('image_hash_near')
                    group_files.append(f)
                groups.append({
                    'id': f"group_ihash_near_{current_file['image_hash']}",
                    'files': group_files,
                    'primary_name': group_files[0]['filename'],
                    'status': 'suspected',
                    'reasons': ['image_hash_near'],
                    'total_size': sum(f.get('size', 0) for f in group_files)
                })

            i += 1

    # --- Strategy 4: File Type Grouping + Pure Name Fuzzy Match (O(K * N^2)) ---
    # Catch-all for files with the same/similar extension but vastly different sizes 
    # (e.g. 200KB .html vs 26MB .mhtml of the same page).
    remaining_indices = [i for i in range(len(files)) if i not in processed_indices]
    if remaining_indices:
        # 1. Group by logical file type
        type_map = {}
        for idx in remaining_indices:
            ext = files[idx].get('extension', '').lower()
            # Normalize web page extensions into a single category
            if ext in ['.html', '.htm', '.mhtml', '.mht']:
                group_key = 'web_page'
            else:
                group_key = ext
            
            type_map.setdefault(group_key, []).append(idx)
            
        # 2. Match within same type group ignoring size
        for group_key, indices in type_map.items():
            if len(indices) < 2:
                continue
                
            local_processed = set()
            for i in range(len(indices)):
                idx1 = indices[i]
                if idx1 in local_processed or idx1 in processed_indices:
                    continue
                    
                current_file = files[idx1]
                current_group_indices = [idx1]
                
                for j in range(i + 1, len(indices)):
                    idx2 = indices[j]
                    if idx2 in local_processed or idx2 in processed_indices:
                        continue
                        
                    candidate = files[idx2]

                    # Pure name match, no size or duration checks
                    ratio = fuzz.token_set_ratio(current_file['norm_name'], candidate['norm_name'])
                    if ratio > 85:
                        current_group_indices.append(idx2)
                        candidate['match_reason'].append('name_fuzzy_type_only')
                    elif _names_similar(current_file, candidate):
                        current_group_indices.append(idx2)
                        candidate['match_reason'].append('alias_match_type_only')
                        
                if len(current_group_indices) > 1:
                    group_files = []
                    reasons = set()
                    for idx in current_group_indices:
                        processed_indices.add(idx)
                        local_processed.add(idx)
                        f = files[idx]
                        group_files.append(f)
                        reasons.update(f['match_reason'])
                    
                    if not reasons: reasons.add('type_name_match')
                    
                    groups.append({
                        'id': f"group_type_fuzzy_{group_key}_{idx1}",
                        'files': group_files,
                        'primary_name': group_files[0]['filename'],
                        'status': 'suspected',
                        'reasons': list(reasons),
                        'total_size': sum(f.get('size', 0) for f in group_files)
                    })

    # Sort groups by total_size descending
    groups.sort(key=lambda x: x.get('total_size', 0), reverse=True)

    return groups

def verify_group_deep(group):
    if len(group['files']) < 2:
        for f in group['files']:
            if 'content_hash' not in f:
                f['content_hash'] = extract_first_frame_hash(f['path'])
        return [group]
    for f in group['files']:
        if 'content_hash' not in f:
            f['content_hash'] = extract_first_frame_hash(f['path'])
    buckets = {}
    for f in group['files']:
        h = f.get('content_hash')
        buckets.setdefault(h, []).append(f)
    new_groups = []
    for h, flist in buckets.items():
        status = 'confirmed' if (h is not None and len(flist) >= 2) else 'suspected'
        reasons = ['content_match'] if (h is not None and len(flist) >= 2) else (['hash_failed'] if h is None else ['content_mismatch'])
        new_groups.append({
            'id': f"{group['id']}_h_{h if h is not None else 'none'}",
            'files': flist,
            'primary_name': flist[0]['filename'],
            'status': status,
            'reasons': reasons,
            'total_size': sum(f.get('size', 0) for f in flist)
        })
    return new_groups
