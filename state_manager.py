import json
# from logging import config
import os
import fcntl
from datetime import datetime, timedelta, timezone, date
import config


# =============================================================================
# STATE SCHEMA
# {
#   "status": "in_progress" | "completed",
#   "retention_day": "2026-01-16",   # the Phase B target date
#   "cursor": "2026-01-18",          # last window_end fully fetched in Phase A
#   "skip_phase_a": false,           # whether this run reuses existing runs.jsonl
#   "phase_a_done_repos": [...],     # repos that finished Phase A this session
#   "phase_b_done_runs": [...],      # integer databaseIds written to details.jsonl
#   "phase_b_massive_runs": [...],   # databaseIds identified as massive (need REST crew)
#   "run_url_lookup": {...}          # str(databaseId) -> jobs_url for massive runs
# }
# =============================================================================

def load_state():
    """Load pipeline state from disk. Returns None if no state file exists."""
    if not os.path.exists(config.STATE_FILE):  # USE config.STATE_FILE
        return None
    with open(config.STATE_FILE, 'r') as f:
        return json.load(f)


def save_state(state):
    """Persist pipeline state atomically (safe against mid-write crashes)."""
    tmp = config.STATE_FILE + ".tmp"  # USE config.STATE_FILE
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, config.STATE_FILE)


def _fresh_state(retention_day, window_end, skip_phase_a=False):
    return {
        "status": "in_progress",
        "retention_day": str(retention_day),
        "cursor": str(window_end),
        "skip_phase_a": skip_phase_a,
        "phase_a_done_repos": [],
        "phase_b_done_runs": [],
        "phase_b_massive_runs": [],
        "run_url_lookup": {}
    }


def resolve_startup(expected_repo_count=0):
    """
    Determines what the current run should do based on existing state.

    Returns:
        (state, retention_day_str, window_end_str, skip_phase_a)
        Returns (None, None, None, None) if already up to date — caller should exit.
    """
    today = datetime.now(timezone.utc).date()
    new_retention_day = today - timedelta(days=config.RETENTION_DAYS)
    new_window_end = new_retention_day + timedelta(days=config.WINDOW_DAYS - 1)

    state = load_state()

    # ── First ever run ────────────────────────────────────────────────────────
    if state is None:
        state = _fresh_state(new_retention_day, new_window_end)
        save_state(state)
        return state, str(new_retention_day), str(new_window_end), False

    # ── Resume interrupted run ────────────────────────────────────────────────
    if state['status'] == 'in_progress':
        retention_day = state['retention_day']
        cursor = state['cursor']
        skip_phase_a = state.get('skip_phase_a', False)

        # Guard: check if the saved retention_day has now fallen outside GraphQL's
        # 87-day retention window. This happens when e.g. the script was started on
        # day 87, crashed, and is resumed one or more days later.
        retention_day_date = date.fromisoformat(retention_day)
        days_old = (today - retention_day_date).days
        if days_old > config.RETENTION_DAYS:
            print(
                f"\n[!] ABORT: Cannot resume interrupted run.\n"
                f"    Saved retention_day={retention_day} is now {days_old} days old.\n"
                f"    GitHub's GraphQL API only retains CheckSuite data for {config.RETENTION_DAYS} days.\n"
                f"    The data for this window is no longer accessible.\n"
                f"    Please manually delete the state file and start a fresh run for today's window."
            )
            return None, None, None, None

        print(f"[*] Resuming interrupted run for retention_day={retention_day}, skip_phase_a={skip_phase_a}")
        return state, retention_day, cursor, skip_phase_a

    # ── Previous run completed ────────────────────────────────────────────────
    cursor = date.fromisoformat(state['cursor'])
    saved_retention_day = date.fromisoformat(state['retention_day'])

    if new_retention_day == saved_retention_day:
        # NEW LOGIC: Check if the dataset grew since we "completed" it!
        done_count = len(state.get('phase_a_done_repos', []))
        print(done_count, expected_repo_count)
        if done_count < expected_repo_count:
            print(f"[*] Dataset expanded ({done_count} done, {expected_repo_count} expected). Resuming today's window.")
            state['status'] = 'in_progress'
            save_state(state)
            return state, str(new_retention_day), state['cursor'], False
        # Dataset is complete — fall through to the cursor-advancement logic below


    if new_retention_day <= cursor:
        # The natural retention_day is still inside the completed window.
        # Don't wait — immediately advance to the next window.
        next_retention_day = cursor + timedelta(days=1)
        next_window_end = next_retention_day + timedelta(days=config.WINDOW_DAYS - 1)

        # Guard: don't fetch data if we are in grace period (last 7 days — runs may still be active)
        if next_retention_day > today - timedelta(days=config.GRACE_PERIOD_DAYS):
            print(f"[*] Next window ({next_retention_day}) is in the grace period. Waiting until it we pass the grace period...")
            return None, None, None, None

        print(f"[*] Previous window ({saved_retention_day} to {cursor}) complete. Advancing to next window: {next_retention_day} to {next_window_end}")
        state = _fresh_state(next_retention_day, next_window_end)
        save_state(state)
        return state, str(next_retention_day), str(next_window_end), False

    # New retention_day is beyond the cursor — full run with a fresh window.
    state = _fresh_state(new_retention_day, new_window_end, skip_phase_a=False)
    save_state(state)
    
    return state, str(new_retention_day), str(new_window_end), False


# =============================================================================
# PROGRESS TRACKING
# =============================================================================

def _summary_file():
    return os.path.join(config.STATE_DIR, "pipeline_summary.json")

def _summary_lock_file():
    return os.path.join(config.STATE_DIR, "pipeline_summary.lock")

def _read_summary():
    path = _summary_file()
    if not os.path.exists(path):
        return {}
    with open(path, 'r') as f:
        return json.load(f)

def _write_summary_locked(update_fn):
    """Read-modify-write the summary JSON under an exclusive flock."""
    lock_path = _summary_lock_file()
    with open(lock_path, 'a') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            summary = _read_summary()
            update_fn(summary)
            tmp = _summary_file() + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(summary, f, indent=2)
            os.replace(tmp, _summary_file())
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def record_window_start(shard_idx, retention_day_str, window_end_str):
    """
    Called once at the start of a fresh window (not on resume).
    - Appends an IN_PROGRESS line to the per-shard progress log.
    - Adds a placeholder entry to the cross-shard summary JSON.
    """
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    window_key = f"{retention_day_str}_{window_end_str}"
    shard_key = f"w{shard_idx}"

    # Per-shard append log (easy to tail)
    log_file = os.path.join(config.STATE_DIR, f"progress_shard_{shard_idx}.log")
    with open(log_file, 'a') as f:
        f.write(f"[{now}] {retention_day_str} --> {window_end_str} | IN_PROGRESS\n")

    # Cross-shard summary — only add placeholder if entry doesn't exist yet
    def update(summary):
        if window_key not in summary:
            summary[window_key] = {}
        if shard_key not in summary[window_key]:
            summary[window_key][shard_key] = {"status": "in_progress", "started_at": now}

    _write_summary_locked(update)


def record_window_complete(shard_idx, retention_day_str, window_end_str, stats):
    """
    Called when a window completes successfully.
    - Appends a COMPLETED line to the per-shard progress log.
    - Writes full stats to the cross-shard summary JSON.

    stats keys: duration_h, repos, runs_discovered, runs_graphql, runs_rest_crew,
                jobs, steps, rest_calls, gql_pts
    """
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    window_key = f"{retention_day_str}_{window_end_str}"
    shard_key = f"w{shard_idx}"

    # Per-shard append log
    log_file = os.path.join(config.STATE_DIR, f"progress_shard_{shard_idx}.log")
    with open(log_file, 'a') as f:
        f.write(
            f"[{now}] {retention_day_str} --> {window_end_str} | COMPLETED | "
            f"{stats['duration_h']:.2f}h | repos={stats['repos']:,} | "
            f"runs={stats['runs_discovered']:,} | jobs={stats['jobs']:,} | "
            f"steps={stats['steps']:,} | rest={stats['rest_calls']:,} | "
            f"gql_pts={stats['gql_pts']:,}\n"
        )

    # Cross-shard summary
    def update(summary):
        if window_key not in summary:
            summary[window_key] = {}
        started_at = summary.get(window_key, {}).get(shard_key, {}).get("started_at", now)
        summary[window_key][shard_key] = {
            "status": "completed",
            "started_at": started_at,
            "completed_at": now,
            "duration_h": round(stats['duration_h'], 2),
            "repos": stats['repos'],
            "runs_discovered": stats['runs_discovered'],
            "runs_graphql": stats['runs_graphql'],
            "runs_rest_crew": stats['runs_rest_crew'],
            "jobs": stats['jobs'],
            "steps": stats['steps'],
            "rest_calls": stats['rest_calls'],
            "gql_pts": stats['gql_pts'],
        }

    _write_summary_locked(update)
