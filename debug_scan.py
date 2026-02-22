import sys
import os

# Add movie_manager to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'movie_manager'))

import time
import json
import logging
from scan_manager import ScanManager

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_scan_d():
    print("Initializing ScanManager...")
    manager = ScanManager()
    
    paths = ['D:\\']
    print(f"Starting scan on: {paths}")
    
    # Start scan
    success, msg = manager.start_scan(paths, extensions=None, ignore_hidden=True, ignore_system=True)
    if not success:
        print(f"Failed to start scan: {msg}")
        return

    print("Scan started. Polling status...")
    
    # Poll status
    while True:
        status = manager.get_status()
        print(f"Status: {status['status']} | Progress: {status['progress']}% | Msg: {status['message']}")
        
        # Check for weird characters in status that might break JSON
        try:
            json_output = json.dumps(status)
        except Exception as e:
            print(f"CRITICAL: Failed to dump status to JSON: {e}")
            print(f"Status object: {status}")
            
        if status['status'] in ['completed', 'scanned', 'error', 'cancelled']:
            break
            
        time.sleep(1)
        
    print("Scan finished.")
    results = manager.get_results()
    print(f"Files found: {len(results['files'])}")
    print(f"Groups found: {len(results['groups'])}")

if __name__ == "__main__":
    run_scan_d()
