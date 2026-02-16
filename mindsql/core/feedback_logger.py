import json
import os
from datetime import datetime
from .._utils import logger

log = logger.init_loggers("FeedbackLogger")

class FeedbackLogger:
    def __init__(self, log_path='feedback_scores.json', decay_factor=0.95, max_feedback=5, min_feedback=-5):
        self.log_path = log_path
        self.decay_factor = decay_factor
        self.max_feedback = max_feedback
        self.min_feedback = min_feedback
        self.scores = self._load_scores()

    def _load_scores(self):
        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, 'r') as f:
                    data = json.load(f)
                    # Handle migration from simple int scores to dict structure
                    standardized = {}
                    for table, val in data.items():
                        if isinstance(val, (int, float)):
                            standardized[table] = {
                                "score": float(val),
                                "last_updated": datetime.now().isoformat()
                            }
                        else:
                            standardized[table] = val
                    return standardized
            except Exception:
                return {}
        return {}

    def _save_scores(self):
        """Saves scores atomically using a temporary file to prevent corruption."""
        temp_path = self.log_path + ".tmp"
        try:
            with open(temp_path, 'w') as f:
                json.dump(self.scores, f, indent=4)
            os.replace(temp_path, self.log_path)
        except Exception as e:
            log.error(f"Failed to save feedback scores: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

    def _apply_decay(self, table_name):
        """Applies exponential decay based on days passed since last update."""
        if table_name not in self.scores:
            return 0.0
        
        info = self.scores[table_name]
        try:
            last_updated = datetime.fromisoformat(info["last_updated"])
            days_passed = (datetime.now() - last_updated).days
            if days_passed > 0:
                info["score"] = info["score"] * (self.decay_factor ** days_passed)
                info["last_updated"] = datetime.now().isoformat()
        except (ValueError, KeyError):
            info["last_updated"] = datetime.now().isoformat()
            
        return info["score"]

    def log_feedback(self, question, tables, status):
        """
        Logs feedback and updates scores with decay and clamping.
        Status: 'success', 'sql_error', 'column_error'
        """
        weight = 0
        if status == 'success':
            weight = 1
        elif status == 'sql_error' or status == 'failure':
            weight = -1
        elif status == 'column_error':
            weight = -2

        for table in tables:
            # Apply decay before updating
            current_score = self._apply_decay(table)
            new_score = current_score + weight
            
            # Clamping
            new_score = max(self.min_feedback, min(self.max_feedback, new_score))
            
            self.scores[table] = {
                "score": new_score,
                "last_updated": datetime.now().isoformat()
            }
        
        self._save_scores()

    def get_table_boost(self, table_name):
        """Returns decayed and clamped boost for a table."""
        return self._apply_decay(table_name)

    def rank_tables(self, tables):
        """Ranks tables based on historical scores."""
        return sorted(tables, key=lambda t: self.get_table_boost(t), reverse=True)
