import json
import logging
import os
from app_paths import get_app_dir

SESSION_FILE = 'session.json'
SESSION_VERSION = 1

def save_session(files, groups, aliases_map):
    """
    Persists the current scan results so the app can restore them on the
    next start (stable uuids keep selections/exclusions meaningful) and
    reuse them as an incremental-scan cache.
    """
    try:
        path = os.path.join(get_app_dir(), SESSION_FILE)
        data = {
            'version': SESSION_VERSION,
            'files': files,
            'groups': groups,
            'aliases': {k: sorted(v) for k, v in aliases_map.items()} if aliases_map else {},
        }
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
        return True
    except Exception as e:
        logging.error(f"Failed to save session: {e}")
        return False

def load_session():
    """
    Returns (files, groups, aliases_map) or None when no valid session exists.
    Alias values come back as lists; consumers iterate them, so sets are
    not required.
    """
    path = os.path.join(get_app_dir(), SESSION_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('version') != SESSION_VERSION:
            return None
        return data['files'], data['groups'], data.get('aliases', {})
    except Exception as e:
        logging.error(f"Failed to load session: {e}")
        return None
