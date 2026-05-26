import argparse
import time
import os
import json
import csv
from tqdm import tqdm

from environment_setup import setup_environment, diag_start_window, diag_end_window
import config
from data_handlers import load_test_repos
from api_handlers import fetch_massive_run_rest, fetch_runs_rest, fetch_with_dynamic_resizing
from state_manager import resolve_startup, save_state, record_window_start, record_window_complete

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=str, default="0/1", help="e.g. 0/3, 1/3, 2/3")
    parser.add_argument('--token', type=str, required=True, help="GitHub PAT")
    parser.add_argument('--test', type=int, default=None, help="Limit number of repos for testing")
    parser.add_argument('--input', type=str,required=True, help="Path to a custom CSV or TXT file containing repositories")
    return parser.parse_args()
    

def main():

    args = parse_args()
    
    # Parse the shard info
    shard_idx, total_shards = map(int, args.worker.split('/'))
    
    print(f"[*] Starting worker {shard_idx} out of {total_shards} total shards. Test limit: {args.test if args.test else 'None'}")
    # --- ISOLATION SETUP ---
    # 1. Override the global token
    config.GITHUB_TOKEN = args.token 
    
    # Load the repos FIRST so we know how many there are
    repos = load_test_repos(args.input, args.test, shard_idx, total_shards)

    # Create the states folder and assign the file path
    os.makedirs(config.STATE_DIR, exist_ok=True)
    config.STATE_FILE = os.path.join(config.STATE_DIR, f"pipeline_state_shard_{shard_idx}.json")



    while True:

        state, retention_day_str, window_end_str, skip_phase_a = resolve_startup(len(repos))
        if state is None:
            print("[*] All windows up to date (grace period reached). Exiting.")

            break  # already up to date
        

        # date-stamped output files — each window/day gets its own file
        runs_file = os.path.join(config.OUTPUT_DIR, f"runs_shard_{shard_idx}_{retention_day_str}_{window_end_str}.jsonl")
        details_file = os.path.join(config.OUTPUT_DIR, f"details_shard_{shard_idx}_{retention_day_str}_{window_end_str}.jsonl")
        report_file = os.path.join(config.REPORT_DIR, f"report_shard_{shard_idx}_{retention_day_str}_{window_end_str}.txt")

        # Single per-shard diagnostic files — appended across all windows
        config.BATCH_LOG_FILE = os.path.join(config.STATE_DIR, f"batch_diagnostics_shard_{shard_idx}.csv")
        config.REPO_LOG_FILE = os.path.join(config.STATE_DIR, f"repo_diagnostics_shard_{shard_idx}.csv")

        # Only wipe output files on a fresh window (not on resume or within-window runs)
        if not state['phase_a_done_repos'] and not state['phase_b_done_runs']:
            setup_environment()
            record_window_start(shard_idx, retention_day_str, window_end_str)
            diag_start_window(config.REPO_LOG_FILE, ["Repository", "Runs_Found", "Discovery_Time_Sec"], retention_day_str, window_end_str)
            diag_start_window(config.BATCH_LOG_FILE, ["Timestamp", "Batch_Size", "Fetch_Time_Sec", "Status", "GraphQL_Cost"], retention_day_str, window_end_str)
        
        start_time = time.time()
        

        total_runs_discovered = 0
        total_runs_sent_to_graphql = 0
        total_jobs_fetched = 0
        total_steps_fetched = 0
        total_graphql_cost = 0
        total_rest_discovery_calls = 0  
        total_rest_crew_calls = 0 
        
        graphql_buffer = []  
        massive_runs_buffer = []  
        run_url_lookup = {}
        massive_runs_restored = 0   # restored from state (previous session)
        massive_new_count = 0       # identified as massive in this session

        phase_a_done = set(state['phase_a_done_repos'])
        phase_b_done = set(state['phase_b_done_runs'])

        print(f"\n[*] Process started... retention_day={retention_day_str}, skip_phase_a={skip_phase_a}")

        # Rebuild Phase B buffer from disk when Phase A is fully complete (covers both
        # skip_phase_a days AND crash-during-cleanup-crew resumes)
        need_rebuild_phase_b = skip_phase_a or (len(repos) > 0 and len(phase_a_done) == len(repos))

        # Restore massive runs that were identified before a mid-Phase-A crash
        if not need_rebuild_phase_b and state.get('phase_b_massive_runs'):
            massive_runs_buffer = list(state['phase_b_massive_runs'])
            run_url_lookup.update(state.get('run_url_lookup', {}))
            massive_runs_restored = len(massive_runs_buffer)
            print(f"[*] Restored {massive_runs_restored} massive runs from previous session state.")

        if need_rebuild_phase_b:
            print("[*] Loading Phase B targets from runs file...")
            # Restore known massive runs directly — do NOT re-query them through GraphQL.
            # Without this, they'd be re-added to graphql_buffer and lost if GitHub returns null.
            if state.get('phase_b_massive_runs'):
                saved_massive_ids = set(state['phase_b_massive_runs'])
                for mid in saved_massive_ids:
                    if mid not in phase_b_done:
                        massive_runs_buffer.append(mid)
                run_url_lookup.update(state.get('run_url_lookup', {}))
                massive_runs_restored = len(massive_runs_buffer)
                print(f"[*] Restored {massive_runs_restored} massive runs directly from state (rebuild path).")
            else:
                saved_massive_ids = set()

            seen_run_ids = set(phase_b_done) | saved_massive_ids  # exclude known massive from GraphQL re-query

            with open(runs_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    run = json.loads(line)
                    run_date = run.get('created_at', '')[:10]
                    event = run.get('event', '')
                    node_id = run.get('check_suite_node_id')
                    db_id = run.get('check_suite_id')

                    if (
                        run_date == retention_day_str
                        and event not in ['pull_request', 'pull_request_target']
                        and node_id
                        and db_id
                        and db_id not in seen_run_ids
                    ):
                        graphql_buffer.append(node_id)
                        run_url_lookup[str(db_id)] = run.get('jobs_url')
                        seen_run_ids.add(db_id)

            print(f"[*] Loaded {len(graphql_buffer)} runs into Phase B buffer.")
        
        with open(runs_file, "a", encoding="utf-8") as runs_out, open(details_file, "a", encoding="utf-8") as details_out:
            if not need_rebuild_phase_b:
                for repo in tqdm(repos, desc="Phase A: Discovery", unit="repo"):
                    # RESUME: skip repos already completed in a previous interrupted run
                    if repo in phase_a_done:
                        continue

                    repo_start_time = time.time()
                    
                    # 1. DISCOVERY
                    runs = fetch_runs_rest(repo, retention_day_str, window_end_str, args.token)
                    total_runs_discovered += len(runs)
                    runs_count = len(runs)
                    total_rest_discovery_calls += 1 + (runs_count // 100) + (runs_count // 1000)
                    repo_discovery_time = time.time() - repo_start_time
                    
                    with open(config.REPO_LOG_FILE, 'a', newline='') as f:
                        csv.writer(f).writerow([repo, len(runs), f"{repo_discovery_time:.2f}"])
                    
                    tqdm.write(f" Repo Heartbeat: {repo} finished in {repo_discovery_time:.1f}s. Discovered {len(runs)} total runs.")
                    
                    for run in runs:
                        runs_out.write(json.dumps(run) + "\n")
                        run_date = run.get('created_at', '')[:10]
                        event = run.get('event', '')
                        node_id = run.get('check_suite_node_id')
                        
                        # if run_date == retention_day_str and event not in ['pull_request', 'pull_request_target'] and node_id:
                        if event not in ['pull_request', 'pull_request_target'] and node_id:
                            db_id = run.get('check_suite_id')
                            if db_id and db_id not in phase_b_done:
                                graphql_buffer.append(node_id)
                                run_url_lookup[str(db_id)] = run.get('jobs_url')
                    
                    # 2. DETAILS (interleaved)
                    while len(graphql_buffer) >= config.GRAPHQL_BATCH_SIZE:
                        batch_to_send = graphql_buffer[:config.GRAPHQL_BATCH_SIZE]
                        graphql_buffer = graphql_buffer[config.GRAPHQL_BATCH_SIZE:]
                        
                        total_runs_sent_to_graphql += len(batch_to_send)
                        nodes, cost, remaining = fetch_with_dynamic_resizing(batch_to_send, args.token)
                        total_graphql_cost += cost
                        
                        newly_done = []
                        for node in nodes:
                            if not node: continue
                            db_id = node.get('databaseId')
                            if db_id in phase_b_done: continue
                            check_runs_data = node.get('checkRuns', {})
                            jobs = check_runs_data.get('nodes', [])
                            
                            has_more_jobs = check_runs_data.get('pageInfo', {}).get('hasNextPage', False)
                            has_more_steps = any(job.get('steps', {}).get('pageInfo', {}).get('hasNextPage', False) for job in jobs)

                            if has_more_jobs or has_more_steps:
                                db_id = node.get('databaseId')
                                massive_runs_buffer.append(db_id)
                                massive_new_count += 1
                                # Save to state so it survives a crash!
                                state.setdefault('phase_b_massive_runs', []).append(db_id)
                                state.setdefault('run_url_lookup', {})[str(db_id)] = run_url_lookup.get(str(db_id))
                                save_state(state)
                            else:
                                details_out.write(json.dumps(node) + "\n")
                                newly_done.append(node.get('databaseId'))
                                total_jobs_fetched += len(jobs)
                                for job in jobs:
                                    total_steps_fetched += len(job.get('steps', {}).get('nodes', []))

                        # CHECKPOINT: save Phase B progress after each batch
                        if newly_done:
                            phase_b_done.update(newly_done)
                            state['phase_b_done_runs'].extend(newly_done)
                            save_state(state)

                    # CHECKPOINT: save Phase A progress after each repo
                    state['phase_a_done_repos'].append(repo)
                    save_state(state)
                            
            # 3. FINAL FLUSH (handles skip_phase_a buffer too)
            if len(graphql_buffer) > 0:
                total_runs_sent_to_graphql += len(graphql_buffer)
                nodes, cost, remaining = fetch_with_dynamic_resizing(graphql_buffer, args.token)
                total_graphql_cost += cost
                
                newly_done = []
                for node in nodes:
                    if not node: continue
                    db_id = node.get('databaseId')
                    if db_id in phase_b_done: continue  # optional dedup guard
                    check_runs_data = node.get('checkRuns', {})
                    jobs = check_runs_data.get('nodes', [])
                    
                    has_more_jobs = check_runs_data.get('pageInfo', {}).get('hasNextPage', False)
                    has_more_steps = any(job.get('steps', {}).get('pageInfo', {}).get('hasNextPage', False) for job in jobs)

                    if has_more_jobs or has_more_steps:
                        db_id = node.get('databaseId')
                        massive_runs_buffer.append(db_id)
                        massive_new_count += 1
                        # Persist to state so it survives a crash between flush and cleanup crew
                        state.setdefault('phase_b_massive_runs', []).append(db_id)
                        state.setdefault('run_url_lookup', {})[str(db_id)] = run_url_lookup.get(str(db_id))
                    else:
                        details_out.write(json.dumps(node) + "\n")
                        newly_done.append(node.get('databaseId'))
                        total_jobs_fetched += len(jobs)
                        for job in jobs:
                            total_steps_fetched += len(job.get('steps', {}).get('nodes', []))
                if newly_done:
                    phase_b_done.update(newly_done)
                    state['phase_b_done_runs'].extend(newly_done)
                save_state(state)  # one save covers both newly_done and any new massive runs

            # =========================================================================
            # 4. THE CLEANUP CREW (REST API for 1% Massive Runs)
            # =========================================================================
            if massive_runs_buffer:
                massive_runs_buffer = list(set(massive_runs_buffer)) # Dedup the buffer!

                print(f"\n[*] CLEANUP CREW: Fetching {len(massive_runs_buffer)} massive runs via REST...")
                for node_id in tqdm(massive_runs_buffer, desc="Fetching Massive Runs"):
                    
                    if node_id in phase_b_done: 
                        continue
                    url = run_url_lookup.get(str(node_id))

                    if not url:
                        print(f" [!] Error: No jobs URL found for run with node_id {node_id}. Skipping REST fetch for this run - **saved** in skipped_runs.txt for manual follow-up.")
                        with open("skipped_runs.txt", "a") as skip_out:
                            skip_out.write(f"{node_id} - No jobs URL found\n")

                    if url:
                        rest_jobs, jobs_truncated = fetch_massive_run_rest(url, args.token)
                        total_rest_crew_calls += 1 + (len(rest_jobs) // 100)

                        gql_mimic = {"databaseId": node_id, "jobs_truncated": jobs_truncated, "checkRuns": {"nodes": []}}
                        
                        for job in rest_jobs:
                            gql_job = {
                                "databaseId": job.get('id'), "name": job.get('name'), 
                                "status": job.get('status').upper() if job.get('status') else None,
                                "conclusion": job.get('conclusion').upper() if job.get('conclusion') else None,
                                "startedAt": job.get('started_at'), "completedAt": job.get('completed_at'),
                                "steps": {"nodes": []}
                            }
                            for step in job.get('steps', []):
                                gql_job['steps']['nodes'].append({
                                    "number": step.get('number'), "name": step.get('name'),
                                    "status": step.get('status').upper() if step.get('status') else None,
                                    "conclusion": step.get('conclusion').upper() if step.get('conclusion') else None,
                                    "startedAt": step.get('started_at'), "completedAt": step.get('completed_at')
                                })
                            gql_mimic['checkRuns']['nodes'].append(gql_job)
                            
                        details_out.write(json.dumps(gql_mimic) + "\n")

                        # Bug 2: checkpoint cleanup crew progress so resume skips these
                        state['phase_b_done_runs'].append(node_id)
                        phase_b_done.add(node_id)
                        save_state(state)
                        
                        # Count the massive runs too!
                        total_jobs_fetched += len(gql_mimic['checkRuns']['nodes'])
                        for j in gql_mimic['checkRuns']['nodes']:
                            total_steps_fetched += len(j['steps']['nodes'])

        duration = time.time() - start_time
        total_nodes_fetched = total_jobs_fetched + total_steps_fetched


        # Calculate combined REST calls
        total_rest_calls = total_rest_discovery_calls + total_rest_crew_calls

        report_text = f"""
    DAILY REPORT: {retention_day_str}
    Data Window: {retention_day_str} to {window_end_str}
    {'='*30}

    - Total Execution Time:         {duration:.2f} seconds - {'{:.2f}'.format(duration/3600)} hours
    - Repositories Scanned:         {len(repos):,}

    Amounts of runs discovered by two phases:
    - Total Runs Discovered:              {total_runs_discovered:,}
    - Total Runs Sent to GraphQL:     {total_runs_sent_to_graphql - massive_new_count:,}
    - Total Runs Processed by Cleanup Crew (REST):   {len(massive_runs_buffer):,} (restored from prev session: {massive_runs_restored:,})
    - Total Jobs Fetched:           {total_jobs_fetched:,}
    - Total Steps Fetched:          {total_steps_fetched:,}
    - Total Data Nodes:             {total_nodes_fetched:,}

    API metrics:
    - Total GraphQL Points Spent:   {total_graphql_cost:,} pts
    - Total REST API Calls Used:    {total_rest_calls:,} calls
        - Discovery Phase:            {total_rest_discovery_calls:,} calls
        - Cleanup Crew Phase:         {total_rest_crew_calls:,} calls
    {'='*30}
    """

        with open(report_file, "w", encoding="utf-8") as rf:
            rf.write(report_text)
        
        print(report_text)

        # Write end-of-window separator to diagnostic logs
        diag_end_window(config.REPO_LOG_FILE, retention_day_str, window_end_str)
        diag_end_window(config.BATCH_LOG_FILE, retention_day_str, window_end_str)

        # Record completion in progress logs
        record_window_complete(shard_idx, retention_day_str, window_end_str, {
            'duration_h': duration / 3600,
            'repos': len(repos),
            'runs_discovered': total_runs_discovered,
            'runs_graphql': total_runs_sent_to_graphql - massive_new_count,
            'runs_rest_crew': len(massive_runs_buffer),
            'jobs': total_jobs_fetched,
            'steps': total_steps_fetched,
            'rest_calls': total_rest_calls,
            'gql_pts': total_graphql_cost,
        })

        # Mark this run as fully completed
        state['status'] = 'completed'
        save_state(state)

if __name__ == "__main__":
    main()