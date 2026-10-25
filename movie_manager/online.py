import logging
import requests
import urllib.parse

# matcher is importable as a top-level module in both entry paths
from matcher import normalize_filename, extract_id

class TMDBSearcher:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self.cache = {} # Simple in-memory cache to avoid hitting rate limits for same queries

    def search_aliases(self, filename):
        """
        Searches TMDB for the filename and returns a set of aliases (titles in different languages).
        """
        if not self.api_key:
            return set()
            
        if filename in self.cache:
            return self.cache[filename]

        # Strip extension and quality/release keywords so TMDB gets a clean title
        query = normalize_filename(filename)
        
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"{self.base_url}/search/movie?api_key={self.api_key}&query={encoded_query}&language=zh-CN"
            
            response = requests.get(url, timeout=5)
            data = response.json()
            
            aliases = set()
            
            if data.get('results'):
                # Take the top result
                top_result = data['results'][0]
                movie_id = top_result['id']
                
                # Add found title and original title
                aliases.add(top_result.get('title'))
                aliases.add(top_result.get('original_title'))
                
                # Get translations/aliases
                # Need another call for details/translations?
                # Actually, standard search result usually gives original_title.
                # Let's try to get English title specifically if we searched in Chinese, or vice versa.
                # But simple approach: just use title + original_title.
                
                pass
            
            # Clean None values
            aliases = {a for a in aliases if a}
            
            self.cache[filename] = aliases
            return aliases
            
        except Exception as e:
            logging.error(f"Error searching TMDB for {filename}: {e}")
            return set()

def get_aliases_for_files(files, api_key, progress_callback=None):
    """
    Batch processes files to find aliases.
    Returns a dict: {filename: set(aliases)}
    progress_callback: function(current, total, message)
    """
    if not api_key:
        return {}
        
    searcher = TMDBSearcher(api_key)
    aliases_map = {}
    
    total = len(files)
    logging.info("Starting online search for aliases...")
    
    for idx, f in enumerate(files):
        # Only search if it looks like a movie name (not just an ID).
        # id_code may not be precomputed when this runs before matching,
        # so derive it from the filename directly.
        if f.get('id_code') or extract_id(f['filename']):
            continue

        clean_name = normalize_filename(f['filename'])

        # Optimization: Don't search every single file if names are identical.
        # But here we iterate all files.
        
        results = searcher.search_aliases(clean_name)
        if results:
            aliases_map[f['filename']] = results
            
        if idx % 10 == 0:
            logging.info(f"Searched {idx}/{total}...")
            if progress_callback:
                progress_callback(idx, total, f"Searching aliases: {clean_name}")
                
    if progress_callback:
        progress_callback(total, total, "Online search complete.")
            
    return aliases_map
