import json
import os
import fcntl
from datetime import datetime, timedelta, timezone, date
import config


# This is the single source of truth for the pipeline's state machine. It tracks what has been completed, what still needs to be done, and whether we're resuming an interrupted run or starting fresh. The state is persisted to disk after every update, so the pipeline can be safely stopped and resumed without losing progress. The state file also serves as a checkpoint for the current retention window, ensuring that we don't accidentally start a new window before the old one is fully completed and its data is safely stored in the output files.


def load_state():
    if not os.path.exists(config.STATE_FILE):  
        return None
    with open(config.STATE_FILE, 'r') as f:
        return json.load(f)


def save_state(state):
    tmp = config.STATE_FILE + ".tmp"  
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, config.STATE_FILE)


def _fresh_state(retention_day, window_end, skip_phase_a=False, total_shards=1):
    return {
        "status": "in_progress",
        "retention_day": str(retention_day),
        "cursor": str(window_end),
        "skip_phase_a": skip_phase_a,
        "phase_a_done_repos": [],
        "phase_b_done_runs": [],
        "phase_b_massive_runs": [],
        "run_url_lookup": {},
        "total_shards": total_shards  
    }


def resolve_startup(expected_repo_count=0, current_total_shards=1):

    today = datetime.now(timezone.utc).date()
    new_retention_day = today - timedelta(days=config.RETENTION_DAYS)
    new_window_end = new_retention_day + timedelta(days=config.WINDOW_DAYS - 1)

    state = load_state()

    #  First ever run 
    if state is None:
        state = _fresh_state(new_retention_day, new_window_end, total_shards=current_total_shards)
        save_state(state)
        return state, str(new_retention_day), str(new_window_end), False

    #  Resume interrupted run 
    if state['status'] == 'in_progress':
        retention_day = state['retention_day']
        cursor = state['cursor']
        skip_phase_a = state.get('skip_phase_a', False)

        # check if the saved retention_day has now fallen outside GraphQL's
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

    #  Previous run completed 
    cursor = date.fromisoformat(state['cursor'])
    saved_retention_day = date.fromisoformat(state['retention_day'])

    if new_retention_day == saved_retention_day:
        
        # Check if the user changed the shard count!
        saved_shards = state.get('total_shards', current_total_shards)
        
        if saved_shards == current_total_shards:
            # Shard count is the same, so it's the dataset expansion scenario.
            done_count = len(state.get('phase_a_done_repos', []))
            if done_count < expected_repo_count:
                print(f"[*] Dataset expanded ({done_count} done, {expected_repo_count} expected). Resuming today's window.")
                state['status'] = 'in_progress'
                save_state(state)
                return state, str(new_retention_day), state['cursor'], False
        else:
            # Shard count changed! Bypass the expansion check safely.
            print(f"[*] Shard count changed from {saved_shards} to {current_total_shards}. Bypassing dataset expansion check.")
            
    if new_retention_day <= cursor:
        next_retention_day = cursor + timedelta(days=1)
        next_window_end = next_retention_day + timedelta(days=config.WINDOW_DAYS - 1)

        if next_retention_day > today - timedelta(days=config.GRACE_PERIOD_DAYS):
            print(f"[*] Next window ({next_retention_day}) is in the grace period...")
            return None, None, None, None

        state = _fresh_state(next_retention_day, next_window_end, total_shards=current_total_shards)
        save_state(state)
        return state, str(next_retention_day), str(next_window_end), False
        
    state = _fresh_state(new_retention_day, new_window_end, skip_phase_a=False, total_shards=current_total_shards)
    save_state(state)
    return state, str(new_retention_day), str(new_window_end), False



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

    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    window_key = f"{retention_day_str}_{window_end_str}"
    shard_key = f"w{shard_idx}"

    # Per-shard append log 
    log_file = os.path.join(config.STATE_DIR, f"progress_shard_{shard_idx}.log")
    with open(log_file, 'a') as f:
        f.write(f"[{now}] {retention_day_str} --> {window_end_str} | IN_PROGRESS\n")

    def update(summary):
        if window_key not in summary:
            summary[window_key] = {}
        if shard_key not in summary[window_key]:
            summary[window_key][shard_key] = {"status": "in_progress", "started_at": now}

    _write_summary_locked(update)


def record_window_complete(shard_idx, retention_day_str, window_end_str, stats):

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
