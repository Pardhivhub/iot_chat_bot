import json
import os
import re

class GoldenCache:
    def __init__(self, cache_path='golden_cache.json'):
        self.cache_path = cache_path
        self.cache = self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_path):
            with open(self.cache_path, 'r') as f:
                return json.load(f)
        return {}

    def _save_cache(self):
        with open(self.cache_path, 'w') as f:
            json.dump(self.cache, f, indent=4)

    def _normalize_pattern(self, text):
        """Normalizes text for pattern matching (lower, no extra spaces, strip)."""
        if not text:
            return ""
        text = text.lower().strip()
        text = re.sub(r'\s+', ' ', text)
        return text

    def get_cached_sql(self, question):
        pattern = self._normalize_pattern(question)
        return self.cache.get(pattern)

    def store_query(self, question, sql):
        pattern = self._normalize_pattern(question)
        self.cache[pattern] = sql
        self._save_cache()
