"""Constants for Database Statistics."""

from datetime import timedelta

DOMAIN = "database_stats"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60
