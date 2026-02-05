import logging
import os
from pathlib import Path
from typing import Optional

from sari.core.settings import settings

def get_logger(name: str, log_file: Optional[str] = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Stream Handler
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
        
        # File Handler if path provided
        if log_file:
            try:
                from logging.handlers import RotatingFileHandler
                log_path = Path(log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                # Max 10MB per file, keep 5 backups
                fh = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
                fh.setFormatter(formatter)
                logger.addHandler(fh)
            except Exception:
                pass
                
    return logger

def setup_global_logging():
    log_level_str = settings.LOG_LEVEL
    level = getattr(logging, log_level_str, logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
