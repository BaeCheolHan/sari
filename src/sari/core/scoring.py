from typing import Dict, Any, List
import time

class ScoringPolicy:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        
        # Symbol weights
        self.symbol_weights = self.config.get("symbol_weights", {
            "class": 600.0,
            "function": 500.0,
            "method": 350.0,
            "interface": 450.0,
            "exact_match_bonus": 800.0,
            "symbol_priority_base": 1000.0
        })
        
        # File/Path weights
        self.path_weights = self.config.get("path_weights", {
            "exact_filename": 2.0,
            "filename_stem": 1.2,
            "path_suffix": 1.0,
            "core_file_bonus": 0.2
        })
        
        # SQL based priors (from search_engine.py)
        self.sql_priors = self.config.get("sql_priors", {
            "src_path": 0.6,
            "config_path": 0.4,
            "test_path": -0.7,
            "code_ext": 0.3,
            "config_ext": 0.15,
            "noise_ext": -0.8
        })

    def get_symbol_boost(self, kind: str, is_exact: bool = False) -> float:
        boost = self.symbol_weights.get(kind.lower(), 0.0)
        if is_exact:
            boost += self.symbol_weights.get("exact_match_bonus", 0.0)
        return self.symbol_weights.get("symbol_priority_base", 1000.0) + boost

    def calculate_recency_boost(self, mtime: int, base_score: float) -> float:
        now = time.time()
        age_days = (now - mtime) / 86400
        if age_days < 1:
            boost = 1.5
        elif age_days < 7:
            boost = 1.3
        elif age_days < 30:
            boost = 1.1
        else:
            boost = 1.0
        return (base_score + 0.1) * boost

    def get_path_prior_sql(self) -> str:
        p = self.sql_priors
        return f"""
        CASE
            WHEN f.path LIKE 'src/%' OR f.path LIKE '%/src/%' OR f.path LIKE 'app/%' OR f.path LIKE '%/app/%' OR f.path LIKE 'core/%' OR f.path LIKE '%/core/%' THEN {p['src_path']}
            WHEN f.path LIKE 'config/%' OR f.path LIKE '%/config/%' OR f.path LIKE 'domain/%' OR f.path LIKE '%/domain/%' OR f.path LIKE 'service/%' OR f.path LIKE '%/service/%' THEN {p['config_path']}
            WHEN f.path LIKE 'test/%' OR f.path LIKE '%/test/%' OR f.path LIKE 'tests/%' OR f.path LIKE '%/tests/%' OR f.path LIKE 'example/%' OR f.path LIKE '%/example/%' OR f.path LIKE 'dist/%' OR f.path LIKE '%/dist/%' OR f.path LIKE 'build/%' OR f.path LIKE '%/build/%' THEN {p['test_path']}
            ELSE 0.0
        END
        """

    def get_filetype_prior_sql(self) -> str:
        p = self.sql_priors
        return f"""
        CASE
            WHEN f.path LIKE '%.py' OR f.path LIKE '%.ts' OR f.path LIKE '%.go' OR f.path LIKE '%.java' OR f.path LIKE '%.kt' THEN {p['code_ext']}
            WHEN f.path LIKE '%.yaml' OR f.path LIKE '%.yml' OR f.path LIKE '%.json' THEN {p['config_ext']}
            WHEN f.path LIKE '%.lock' OR f.path LIKE '%.min.js' OR f.path LIKE '%.map' THEN {p['noise_ext']}
            ELSE 0.0
        END
        """
