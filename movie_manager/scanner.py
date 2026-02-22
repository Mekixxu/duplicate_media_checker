import os
import logging
import stat
from pathlib import Path

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mts', '.m2ts', '.rmvb'}

def is_hidden(path):
    """
    Checks if a file or directory is hidden.
    Supports Windows hidden attribute and Unix dot-prefix.
    """
    name = os.path.basename(path)
    
    # Common check for dot-files (Unix convention, also used by some tools on Windows)
    if name.startswith('.'):
        return True
        
    # Windows specific check
    if os.name == 'nt':
        try:
            # os.stat might fail on some paths
            attrs = os.stat(path).st_file_attributes
            return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)
        except Exception:
            return False
            
    return False

def scan_directories(paths, extensions=None, ignore_hidden=True, ignore_system=True):
    """
    Scans the given paths for video files recursively.
    extensions: list of extensions to scan (e.g. ['.mkv', '.mp4']). If None, use default.
    ignore_hidden: whether to ignore hidden files and directories.
    ignore_system: whether to ignore system directories ($RECYCLE.BIN, etc).
    """
    target_extensions = set(ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in extensions) if extensions else VIDEO_EXTENSIONS
    
    # System directories to always ignore
    IGNORED_DIRS = {'$RECYCLE.BIN', 'System Volume Information', '.git', '.Trash-1000'}
    
    video_files = []
    
    for path_str in paths:
        path = Path(path_str)
        if not path.exists():
            logging.warning(f"Path does not exist: {path_str}")
            continue
            
        logging.info(f"Scanning directory: {path_str}")
        
        # Walk through the directory
        for root, dirs, files in os.walk(path):
            # Modify dirs in-place to skip ignored directories
            # We filter based on both flags
            dirs[:] = [d for d in dirs if 
                       (not ignore_system or d not in IGNORED_DIRS) and 
                       (not ignore_hidden or not is_hidden(os.path.join(root, d)))]
            
            for file in files:
                if ignore_hidden and is_hidden(os.path.join(root, file)):
                    continue
                    
                file_path = Path(root) / file
                if file_path.suffix.lower() in target_extensions:
                    try:
                        # os.path.getsize is sometimes more reliable/direct than Path.stat() on Windows for network drives
                        # but Path.stat() is generally fine.
                        # Let's ensure we get the size correctly.
                        
                        abs_path = str(file_path.absolute())
                        
                        # Use os.stat for potentially better compatibility
                        file_stat = os.stat(abs_path)
                        size = file_stat.st_size
                        
                        video_files.append({
                            'path': abs_path,
                            'filename': file,
                            'size': size,
                            'ctime': file_stat.st_ctime,
                            'mtime': file_stat.st_mtime,
                            'extension': file_path.suffix.lower()
                        })
                    except Exception as e:
                        logging.error(f"Error accessing file {file_path}: {e}")
                        
    return video_files
