import os
import sys
import logging

_APP_DIR = None

def _is_writable(path):
    try:
        probe = os.path.join(path, '.write_test')
        with open(probe, 'w') as f:
            f.write('ok')
        os.remove(probe)
        return True
    except Exception:
        return False

def get_app_dir():
    """
    Writable directory for runtime files (log, exclusions, report).

    Frozen exe: prefer the exe's own folder so portable zip layouts keep
    working. Dev: current working directory. If neither is writable
    (e.g. exe dropped into Program Files or run from a read-only share),
    fall back to %LOCALAPPDATA%\\DuplicateChecker instead of crashing.
    """
    global _APP_DIR
    if _APP_DIR:
        return _APP_DIR

    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.dirname(sys.executable))
    candidates.append(os.getcwd())

    local_app = os.environ.get('LOCALAPPDATA')
    if local_app:
        fallback = os.path.join(local_app, 'DuplicateChecker')
        try:
            os.makedirs(fallback, exist_ok=True)
            candidates.append(fallback)
        except Exception:
            pass

    for c in candidates:
        if c and os.path.isdir(c) and _is_writable(c):
            _APP_DIR = c
            return _APP_DIR

    import tempfile
    _APP_DIR = tempfile.gettempdir()
    logging.warning(f"No writable app dir found, using temp dir: {_APP_DIR}")
    return _APP_DIR
