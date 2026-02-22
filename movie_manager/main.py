import os
import argparse
import logging
from scanner import scan_directories
from metadata import enrich_metadata
from matcher import find_duplicates
from report import generate_report
from online import get_aliases_for_files

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    parser = argparse.ArgumentParser(description="Movie Manager & Duplicate Finder")
    parser.add_argument("--paths", nargs='+', required=True, help="List of paths to scan (local or SMB)")
    parser.add_argument("--output", default="movie_report.html", help="Output HTML report file")
    parser.add_argument("--serve", action='store_true', help="Start a local web server to view the report with full interactivity")
    parser.add_argument("--tmdb-key", help="Optional TMDB API Key for online alias search")
    
    args = parser.parse_args()
    
    paths = args.paths
    output_file = args.output
    tmdb_key = args.tmdb_key
    
    logging.info(f"Starting scan on: {paths}")
    
    # 1. Scan files
    files = scan_directories(paths)
    if not files:
        logging.warning("No video files found!")
        return

    logging.info(f"Found {len(files)} potential video files.")
    
    # 2. Enrich with metadata (duration, size, etc.)
    files = enrich_metadata(files)
    logging.info("Metadata extraction complete.")
    
    # 3. Online Search (Optional)
    aliases_map = {}
    if tmdb_key:
        logging.info("TMDB Key provided. Starting online alias search...")
        aliases_map = get_aliases_for_files(files, tmdb_key)
        logging.info(f"Found aliases for {len(aliases_map)} files.")
    
    # 4. Find duplicates and groups
    groups = find_duplicates(files, aliases_map)
    logging.info(f"Found {len(groups)} groups of potential duplicates.")
    
    # 5. Generate Report
    generate_report(files, groups, output_file)
    logging.info(f"Report generated: {output_file}")

    # 6. Serve if requested
    if args.serve:
        from web_server import start_server
        start_server(output_file)


if __name__ == "__main__":
    main()
