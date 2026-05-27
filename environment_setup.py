
import os
import csv
from datetime import datetime, timezone
import config


def setup_environment():

    print("[*] Setting up output directories...")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.REPORT_DIR, exist_ok=True)


def diag_start_window(filepath, headers, retention_day_str, window_end_str):
    """Append a window-start separator + column headers to a diagnostic CSV."""
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with open(filepath, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([f"# START WINDOW {retention_day_str} --> {window_end_str}", now])
        writer.writerow(headers)


def diag_end_window(filepath, retention_day_str, window_end_str):
    """Append a window-end separator to a diagnostic CSV."""
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with open(filepath, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([f"# END WINDOW {retention_day_str} --> {window_end_str}", now])

