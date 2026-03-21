"""
logger_setup.py
───────────────
Configures dual logging: coloured console output + rotating file log.
"""

import os
import logging
from logging.handlers import RotatingFileHandler


def setup_logger(name: str = "polybot") -> logging.Logger:
    """
    Set up and return the named logger.
    Outputs to both console (coloured) and a rotating file.
    """
    log_level   = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file    = os.getenv("LOG_FILE", "logs/bot.log")

    # Create logs directory if needed
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    if logger.handlers:
        return logger  # Already configured (avoid duplicate handlers)

    formatter = logging.Formatter(
        fmt   = "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler (10MB per file, keep last 5)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes    = 10 * 1024 * 1024,
        backupCount = 5,
        encoding    = "utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
