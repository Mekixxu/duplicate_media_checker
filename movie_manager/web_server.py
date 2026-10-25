import os
import subprocess
import platform
import logging
import time
import json
import sys
from urllib.parse import urlparse
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from scan_manager import ScanManager
from app_paths import get_app_dir

# App Version
__version__ = "0.0.9"

# Configure logging to file (writable dir, see app_paths)
log_file = os.path.join(get_app_dir(), 'duplicate_checker.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logging.info(f"Starting Duplicate Media Checker v{__version__}")

# PyInstaller resource path helper
if getattr(sys, 'frozen', False):
    # Running in PyInstaller bundle
    # Templates are at the root of the bundle (see build.py)
    template_dir = os.path.join(sys._MEIPASS, 'templates')
else:
    # Running in dev environment
    # Templates are in the same directory as this script (movie_manager/templates)
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')

app = Flask(__name__, template_folder=template_dir)
manager = ScanManager()

# Local-only guard: the server binds 127.0.0.1, but a webpage in the
# browser (CSRF) or DNS rebinding can still send requests to localhost.
# Reject foreign Host headers and cross-site Origins outright.
_ALLOWED_HOSTNAMES = {'127.0.0.1', 'localhost', '::1'}

@app.before_request
def guard_local_only():
    host = (request.host.split(':')[0] or '').lower()
    if host not in _ALLOWED_HOSTNAMES:
        return jsonify({'success': False, 'message': 'Forbidden host'}), 403

    origin = request.headers.get('Origin')
    if origin:
        parsed = urlparse(origin)
        origin_host = (parsed.hostname or '').lower()
        server_port = request.environ.get('SERVER_PORT', '')
        if origin_host not in _ALLOWED_HOSTNAMES:
            return jsonify({'success': False, 'message': 'Forbidden origin'}), 403
        if parsed.port and server_port and str(parsed.port) != server_port:
            return jsonify({'success': False, 'message': 'Forbidden origin'}), 403

@app.route('/assets/<path:filename>')
def serve_asset(filename):
    return send_from_directory(os.path.join(template_dir, 'assets'), filename)

@app.route('/')
def index():
    return send_from_directory(template_dir, 'report.html')

@app.route('/api/scan/start', methods=['POST'])
def start_scan():
    data = request.json
    paths = data.get('paths', [])
    extensions = data.get('extensions', None)
    ignore_hidden = data.get('ignore_hidden', True)
    ignore_system = data.get('ignore_system', True)
    
    if not paths:
        return jsonify({'success': False, 'message': 'No paths provided'}), 400
        
    success, msg = manager.start_scan(paths, extensions, ignore_hidden, ignore_system)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/tmdb/start', methods=['POST'])
def start_tmdb():
    data = request.json
    api_key = data.get('api_key', '')
    uuids = data.get('uuids', None) # If None, process all
    
    if not api_key:
        return jsonify({'success': False, 'message': 'API Key required'}), 400
        
    success, msg = manager.start_tmdb_search(api_key, uuids)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/match/start', methods=['POST'])
def start_match():
    data = request.json
    uuids = data.get('uuids', None) # If None, process all
    
    success, msg = manager.start_matching(uuids)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/match/deep', methods=['POST'])
def start_deep_match():
    data = request.json
    group_ids = data.get('group_ids', None) # If None, process all

    success, msg = manager.start_deep_search(group_ids)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/version/cluster', methods=['POST'])
def version_cluster():
    data = request.json or {}
    try:
        threshold = float(data.get('threshold', 85))
    except (TypeError, ValueError):
        threshold = 85.0
    threshold = min(max(threshold, 50), 100)

    success, msg = manager.start_version_clustering(threshold)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/compare/start', methods=['POST'])
def start_compare():
    data = request.json or {}
    uuids = data.get('uuids') or [data.get('uuid_a'), data.get('uuid_b')]
    uuids = [u for u in uuids if u]
    if len(uuids) != 2:
        return jsonify({'success': False, 'message': 'Select exactly two files'}), 400

    success, msg = manager.start_compare(uuids[0], uuids[1])
    return jsonify({'success': success, 'message': msg})

@app.route('/api/compare/result')
def compare_result():
    return jsonify(manager.compare_result or {'error': 'No comparison has been run yet.'})

@app.route('/api/scan/stop', methods=['POST'])
def stop_scan():
    manager.stop_scan()
    return jsonify({'success': True, 'message': 'Stop signal sent'})

# Move imports to top to avoid shadowing or re-import
import math
import json

@app.route('/api/scan/status')
def get_status():
    status_data = manager.get_status()
    # Ensure status_data is clean
    return jsonify(clean_data(status_data))

def clean_data(data):
    if isinstance(data, dict):
        return {k: clean_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_data(v) for v in data]
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return 0.0
    elif isinstance(data, str):
        # Remove null bytes or other control chars that might break things
        # But keep newlines/tabs. 
        # Actually jsonify handles most things, but let's be safe against weird binary garbage
        return data.replace('\x00', '')
    return data

@app.route('/api/data')
def get_data():
    results = manager.get_results()
    # Clean data to ensure no NaN/Infinity which breaks JSON.parse in JS
    cleaned_results = clean_data(results)
    return jsonify(cleaned_results)

@app.route('/api/file/delete', methods=['POST'])
def delete_file():
    data = request.json
    uuid = data.get('uuid')
    if not uuid:
        return jsonify({'success': False, 'message': 'UUID required'}), 400
        
    success, msg = manager.delete_file(uuid)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/group/delete_others', methods=['POST'])
def delete_group_others():
    data = request.json
    group_id = data.get('group_id')
    keep_uuid = data.get('keep_uuid')
    
    if not group_id or not keep_uuid:
        return jsonify({'success': False, 'message': 'Group ID and Keep UUID required'}), 400
        
    success, msg = manager.delete_group_others(group_id, keep_uuid)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/files/batch_delete', methods=['POST'])
def batch_delete():
    data = request.json
    uuids = data.get('uuids', [])
    if not uuids:
        return jsonify({'success': False, 'message': 'No UUIDs provided'}), 400
        
    success, msg = manager.delete_files_batch(uuids)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/files/batch_exclude', methods=['POST'])
def batch_exclude():
    data = request.json
    uuids = data.get('uuids', [])
    if not uuids:
        return jsonify({'success': False, 'message': 'No UUIDs provided'}), 400

    success, msg = manager.exclude_files_batch(uuids)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/cleanup/confirmed', methods=['POST'])
def cleanup_confirmed():
    data = request.json or {}
    policy = data.get('policy', 'keep_largest')

    success, msg = manager.cleanup_confirmed(policy)
    return jsonify({'success': success, 'message': msg})

@app.route('/api/thumb/<path:uuid>')
def get_thumb(uuid):
    """
    Thumbnail for a scanned file: Pillow for images, ffmpeg for videos.
    Results are cached in <app dir>/thumbs. Video thumbnails require a full
    ffmpeg build (the stripped OBS build cannot encode JPEG).
    """
    file_obj = manager.get_file(uuid)
    if not file_obj or not os.path.exists(file_obj['path']):
        return jsonify({'success': False, 'message': 'File not found'}), 404

    thumbs_dir = os.path.join(get_app_dir(), 'thumbs')
    os.makedirs(thumbs_dir, exist_ok=True)
    thumb_path = os.path.join(thumbs_dir, uuid + '.jpg')

    if not os.path.exists(thumb_path):
        if not _generate_thumb(file_obj, thumb_path):
            return jsonify({'success': False, 'message': 'Thumbnail unavailable'}), 404

    return send_file(thumb_path, mimetype='image/jpeg')

def _generate_thumb(file_obj, out_path):
    from metadata import IMAGE_EXTENSIONS
    path = file_obj['path']

    if file_obj.get('extension') in IMAGE_EXTENSIONS:
        try:
            from PIL import Image
            with Image.open(path) as img:
                img = img.convert('RGB')
                img.thumbnail((320, 320))
                img.save(out_path, 'JPEG', quality=80)
            return True
        except Exception as e:
            logging.debug(f"Image thumb failed for {path}: {e}")
            return False

    # Video: grab one frame near the start
    try:
        from matcher import get_ffmpeg_path
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            return False
        dur = file_obj.get('duration') or 0
        seek = min(3, dur / 2) if dur else 0
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            [ffmpeg, '-v', 'error', '-ss', f'{seek:.2f}', '-i', path,
             '-frames:v', '1', '-vf', 'scale=320:-2', '-q:v', '4',
             '-f', 'image2', '-y', out_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            startupinfo=startupinfo
        )
        return result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        logging.debug(f"Video thumb failed for {path}: {e}")
        return False

# POST-only so a plain <img>/<form> on some webpage can't trigger these
# while the server is running (combined with the Origin guard above).
@app.route('/api/open', methods=['POST'])
def open_file():
    path = (request.json or {}).get('path')
    if not path or not os.path.exists(path):
        return jsonify({'success': False, 'message': 'File not found'}), 404

    try:
        os.startfile(path) # Windows only
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/reveal', methods=['POST'])
def reveal_file():
    path = (request.json or {}).get('path')
    if not path or not os.path.exists(path):
        return jsonify({'success': False, 'message': 'File not found'}), 404

    try:
        # Use shell command to handle arguments and explorer quirks better
        # Normalizing path is crucial for Windows
        norm_path = os.path.normpath(path)
        subprocess.run(f'explorer /select,"{norm_path}"', shell=True)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

import webbrowser
import threading

def start_server(report_file=None, port=5000):
    url = f"http://127.0.0.1:{port}"
    print(f"Starting server at {url}")
    
    # Open browser after a short delay
    def open_browser():
        time.sleep(1)
        webbrowser.open(url)
        
    threading.Thread(target=open_browser, daemon=True).start()
    
    # Only allow local connections (Single User Mode)
    app.run(host='127.0.0.1', port=port)

if __name__ == '__main__':
    start_server()
