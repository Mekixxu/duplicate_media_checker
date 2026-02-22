import logging
import requests
import urllib.parse

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

        # Clean filename for search (remove extension, dots, year)
        # We rely on the simple normalization from matcher, but we might need a cleaner one here.
        # For search, we just want the main title part.
        query = filename.replace('.', ' ').replace('_', ' ')
        
        # Simple heuristic: take first few words? Or just search as is?
        # Let's try to search as is first.
        
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
        # Only search if it looks like a movie name (not just an ID)
        if f.get('id_code'): 
            continue
            
        # Use the normalized name or original filename?
        # Original filename usually has years and quality info, which confuses search.
        # Normalized name (from matcher) is cleaner.
        # But we don't have normalized name here yet? We do if we run this after scanning.
        
        # Let's use a simple clean here
        clean_name = f['filename'].rsplit('.', 1)[0]
        
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
