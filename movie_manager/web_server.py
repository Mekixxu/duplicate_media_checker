import os
import subprocess
import platform
import logging
import time
import json
import sys
from flask import Flask, request, jsonify, render_template, send_from_directory
from scan_manager import ScanManager

# App Version
__version__ = "0.0.6"

# Configure logging to file
log_file = os.path.join(os.getcwd(), 'duplicate_checker.log')
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

class SafeJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
        return super().default(obj)
    
    def encode(self, o):
        # Override encode to handle top-level floats or floats inside structures recursively?
        # Standard json.dumps with allow_nan=False raises ValueError.
        # We want to replace them.
        # The easiest way is to use default json.dumps but monkeypatch or use a recursive cleaner function.
        # However, Flask's jsonify uses its own provider.
        return super().encode(o)

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

@app.route('/api/open')
def open_file():
    path = request.args.get('path')
    if not path or not os.path.exists(path):
        return jsonify({'success': False, 'message': 'File not found'}), 404
        
    try:
        os.startfile(path) # Windows only
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/reveal')
def reveal_file():
    path = request.args.get('path')
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
