import csv
import time
from datetime import datetime, timedelta, timezone
import requests
from tqdm import tqdm

import config 


def _log_rate_limit_event(event_type, response, sleep_sec, token):
    """Appends a single rate-limit event row to the rate limit log CSV."""
    token_hint = f"****{token[-4:]}" if token and len(token) >= 4 else "****"
    reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))
    reset_at = datetime.fromtimestamp(reset_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if reset_ts else "unknown"
    row = [
        datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),   
        token_hint,                                       
        event_type,                                       # HARD_403 / HARD_429 / NEAR_LIMIT
        response.status_code,                             # raw HTTP status
        response.headers.get("X-RateLimit-Resource", "unknown"),  # core / graphql / search
        response.headers.get("X-RateLimit-Remaining", "?"),       # quota left
        response.headers.get("X-RateLimit-Used", "?"),            # quota consumed
        reset_at,                                         # when quota refills
        sleep_sec,                                        # how long we slept
        response.url,                                     # endpoint that triggered it
    ]
    with open(config.RATE_LIMIT_LOG_FILE, 'a', newline='') as f:
        csv.writer(f).writerow(row)


def handle_rate_limit(response, token=None, retry_count=0):
    
    if response.status_code == 403 or response.status_code == 429:
        
        # 1. Check if this is an Access Block (ToS), NOT a rate limit!
        try:
            error_data = response.json()
            if error_data.get("message") == "Repository access blocked" or "block" in error_data:
                reason = error_data.get("block", {}).get("reason", "unknown")
                tqdm.write(f"       [!] Skipping blocked repo (Reason: {reason}). Not a rate limit.")
                return False  # Break the loop, move to next repo
        except Exception:
            pass 

        remaining = int(response.headers.get("X-RateLimit-Remaining", 0))
        
        # 2. Primary Limit Exhausted
        if remaining == 0:
            reset_time = int(response.headers.get("X-RateLimit-Reset", time.time() + 3600))
            sleep_time = max(0, reset_time - int(time.time())) + 5
            tqdm.write(f"       [!] PRIMARY RATE LIMIT HIT! Sleeping for {sleep_time} seconds...")
            
            event_type = f"HARD_{response.status_code}"
            _log_rate_limit_event(event_type, response, sleep_time, token)
            time.sleep(sleep_time)
            return True
            
        # 3. Secondary Limit Hit (CPU / Burst throttled)
        else:
            # If we've retried 3 times and still getting 403 Secondary Limits, give up on this repo/page!
            if retry_count >= 3:
                tqdm.write("[!] Secondary limit persists after 3 retries. Skipping to avoid token ban.")
                with open('incomplete_data.log', 'a') as f:
                    f.write(f"{datetime.now().isoformat()},SECONDARY_LIMIT_ABORT,{response.url}\n")
                return False # Break the loop, move on!
            
            # Base sleep is either Retry-After or 60 seconds
            base_sleep = int(response.headers.get("Retry-After", 60))
            
            # Exponential backoff: 60s -> 120s -> 240s -> 480s
            sleep_time = base_sleep * (2 ** retry_count) 
            
            tqdm.write(f"       [!] SECONDARY RATE LIMIT HIT! (Retry {retry_count}). Sleeping for {sleep_time} seconds...")
            
            event_type = f"HARD_{response.status_code}_SEC"
            _log_rate_limit_event(event_type, response, sleep_time, token)
            time.sleep(sleep_time)
            return True

    # 4. Pre-emptive Safety Net
    remaining = int(response.headers.get("X-RateLimit-Remaining", 5000))
    if remaining < 10:
        reset_time = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
        sleep_time = max(0, reset_time - int(time.time())) + 5
        tqdm.write(f"       [!] ALMOST OUT OF QUOTA ({remaining} left). Sleeping for {sleep_time} seconds...")
        _log_rate_limit_event("NEAR_LIMIT", response, sleep_time, token)
        time.sleep(sleep_time)
        return True
        
    return False



def fetch_runs_rest(repo, window_start, window_end,token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    all_runs = []
    seen_ids = set()
    current_window_end = f"{window_end}T23:59:59Z"
    
    # fail-safe
    max_sliding_loops = 50 
    loop_count = 0

    while loop_count < max_sliding_loops:
        loop_count += 1
        date_filter = f"{window_start}..{current_window_end}"
        base_url = (f"https://api.github.com/repos/{repo}/actions/runs"
                    f"?per_page=100&status=completed&created={date_filter}")
        
        page = 1
        runs_in_this_window = 0
        oldest_timestamp_found = None
        
        while page <= 10:
            url = f"{base_url}&page={page}"

            net_errors = 0
            rl_retries = 0
            response = None
            while True:
                try:
                    response = requests.get(url, headers=headers, timeout=20)
                except requests.exceptions.RequestException as e:
                    net_errors += 1
                    if net_errors >= 3:
                        tqdm.write(f"  [!] Network Exception in fetch_runs_rest: {type(e).__name__}. Skipping page.")
                        break
                    tqdm.write(f" [!] Network Exception in fetch_runs_rest: {type(e).__name__}. Retrying in 5s...")
                    time.sleep(5)
                    continue
                
                if handle_rate_limit(response, token, retry_count=rl_retries):
                    rl_retries += 1 
                    continue 
                    
                break 

            if response is None:
                tqdm.write(f"      *** WARNING: fetch_runs_rest aborted for {repo} on page {page}. Run list is INCOMPLETE. ***")
                with open('incomplete_data.log', 'a') as f:
                    f.write(f"{datetime.now().isoformat()},DISCOVERY,{repo},{window_start}_to_{current_window_end},failed_on_page_{page}\n")
                break

            if response.status_code != 200: 
                tqdm.write(f"       [!] API 1 Error: {response.status_code} for {repo}")
                with open('incomplete_data.log', 'a') as f:
                    f.write(f"{datetime.now().isoformat()},HTTP_ERROR_{response.status_code},DISCOVERY,{repo},page_{page}\n")
                break
            
            data = response.json()
            raw_runs = data.get('workflow_runs', [])

            # Detect silent 301 redirects (repo renamed or org-transferred)
            # requests follows them automatically; capture old→new name for provenance.
            if page == 1 and response.history and response.history[0].status_code == 301:
                new_name = raw_runs[0]['repository']['full_name'] if raw_runs else None
                if new_name and new_name.lower() != repo.lower():
                    with open(config.REDIRECT_LOG_FILE, 'a', newline='') as _rlog:
                        csv.writer(_rlog).writerow([
                            datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                            repo,      
                            new_name,  
                        ])

            if not raw_runs: 
                break
                
            for r in raw_runs:
                if r.get('status') == 'completed' and r['id'] not in seen_ids:
                    seen_ids.add(r['id'])
                    all_runs.append(r)
                    runs_in_this_window += 1
                    if not oldest_timestamp_found or r['created_at'] < oldest_timestamp_found:
                        oldest_timestamp_found = r['created_at']

            if len(raw_runs) < 100: 
                break

            page += 1

        if runs_in_this_window >= 1000 and oldest_timestamp_found:
            dt = datetime.strptime(oldest_timestamp_found, "%Y-%m-%dT%H:%M:%SZ")
            new_dt = dt - timedelta(seconds=1)
            current_window_end = new_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            tqdm.write(f"        Sliding window for {repo} -> {current_window_end}")
        else:
            break

    if loop_count >= max_sliding_loops:
        tqdm.write(f"      WARNING: Repo {repo} hit the 50-loop infinite failsafe! Data truncated.")
        with open('incomplete_data.log', 'a') as f:
            f.write(f"{datetime.now().isoformat()},SLIDING_WINDOW_FAILSAFE,{repo},collected_{len(all_runs)}_runs_truncated\n")

    return all_runs

def fetch_jobs_and_steps_graphql(check_suite_node_ids, token):
    if not check_suite_node_ids: 
        return [], 0, 5000, True
        
    query = f"""
    query($runIds: [ID!]!) {{
      rateLimit {{ cost remaining }}
      nodes(ids: $runIds) {{
        ... on CheckSuite {{
          databaseId
          checkRuns(first: {config.GQL_JOBS_LIMIT}) {{
            pageInfo {{ hasNextPage }}
            nodes {{
              databaseId name status conclusion startedAt completedAt
              steps(first: {config.GQL_STEPS_LIMIT}) {{
                pageInfo {{ hasNextPage }}
                nodes {{ number name status conclusion startedAt completedAt }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    
    headers = {"Authorization": f"Bearer {token}"}

    connection_errors = 0
    max_connection_retries = 3
    rl_retries = 0 
    while connection_errors < max_connection_retries: 
        batch_start_time = time.time()
        try:
            response = requests.post(
                "https://api.github.com/graphql", 
                json={"query": query, "variables": {"runIds": check_suite_node_ids}},
                headers=headers, 
                timeout=20
            )
            
            # Handle HTTP-level rate limits (sleep, then retry without burning a retry)
            if handle_rate_limit(response, token, retry_count=rl_retries):
                rl_retries += 1
                continue
                
            fetch_duration = time.time() - batch_start_time
            
            if response.status_code == 200:
                json_data = response.json()
                
                # Handle GraphQL-specific rate limit errors disguised as 200 OK
                if "errors" in json_data and any(e.get("type") == "RATE_LIMITED" for e in json_data["errors"]):
                    tqdm.write("       [!] GraphQL RATE LIMIT HIT (inside 200 OK). Sleeping 60s...")
                    time.sleep(60)
                    continue  

                data = json_data.get('data') or {}
                cost = data.get('rateLimit', {}).get('cost', 0)
                remaining = data.get('rateLimit', {}).get('remaining', 5000)
                
                with open(config.BATCH_LOG_FILE, 'a', newline='') as f:
                    csv.writer(f).writerow([datetime.now().strftime("%H:%M:%S"), len(check_suite_node_ids), f"{fetch_duration:.2f}", "SUCCESS", cost])
                
                # If remaining is critically low according to the payload, pause.
                if remaining < 10:
                    tqdm.write(f"       [!] GQL ALMOST OUT OF QUOTA ({remaining} left). Sleeping 60s...")
                    time.sleep(60)
                    
                return data.get('nodes', []), cost, remaining, True
            else:
                # This handles the 502/504 errors! It returns instantly to trigger the split.
                tqdm.write(f"       [!] API 2 Error: {response.status_code}")
                with open(config.BATCH_LOG_FILE, 'a', newline='') as f:
                    csv.writer(f).writerow([datetime.now().strftime("%H:%M:%S"), len(check_suite_node_ids), f"{fetch_duration:.2f}", f"FAILED {response.status_code}", 0])
                return [], 0, 5000, False
                
        except requests.exceptions.ReadTimeout:
            # Fail fast on timeout! Do not retry. Trigger split immediately.
            fetch_duration = time.time() - batch_start_time
            tqdm.write("       [!] Network Exception: ReadTimeout (Payload too big). Triggering split...")
            with open(config.BATCH_LOG_FILE, 'a', newline='') as f:
                csv.writer(f).writerow([datetime.now().strftime("%H:%M:%S"), len(check_suite_node_ids), f"{fetch_duration:.2f}", "TIMEOUT", 0])
            return [], 0, 5000, False
            
        except requests.exceptions.RequestException as e:
            # Only genuine connection errors consume the retry budget
            connection_errors += 1
            fetch_duration = time.time() - batch_start_time
            tqdm.write(f"       [!] Network Exception: {type(e).__name__}. Retrying in 5s... ({connection_errors}/{max_connection_retries})")
            time.sleep(5)
            continue 

    # Failsafe return if all connection retries fail. Prevents a hard crash.
    # Return False to let fetch_with_dynamic_resizing split the batch further —
    # connection errors at larger batch sizes are often ChunkedEncodingError (payload too big),
    # so splitting can recover most items. Only truly un-fetchable single items end up skipped.
    tqdm.write(f"       [!] GQL connection retries exhausted for batch of {len(check_suite_node_ids)} IDs. Batch aborted.")
    with open('incomplete_data.log', 'a') as f:
        f.write(f"{datetime.now().isoformat()},GQL_CONNECTION_RETRIES_EXHAUSTED,batch_size_{len(check_suite_node_ids)}\n")
    return [], 0, 5000, False

def fetch_with_dynamic_resizing(batch_ids, token):
    nodes, cost, remaining, success = fetch_jobs_and_steps_graphql(batch_ids, token)
    
    # If it succeeded (even with zero nodes), return immediately
    if success: 
        return nodes, cost, remaining
        
    # If it failed AND we can still split it
    if len(batch_ids) > 1:
        mid = len(batch_ids) // 2
        tqdm.write(f"       Split triggered for {len(batch_ids)} runs...")
        nodes1, cost1, rem1 = fetch_with_dynamic_resizing(batch_ids[:mid], token)
        nodes2, cost2, rem2 = fetch_with_dynamic_resizing(batch_ids[mid:], token)
        combined = nodes1 + nodes2
        recovered = len([n for n in combined if n])
        tqdm.write(f"       Split result for {len(batch_ids)} runs: recovered {recovered}/{len(batch_ids)} nodes.")
        return combined, cost1 + cost2, min(rem1, rem2)
        
    # If it failed and length is exactly 1, SKIP IT to prevent infinite loop.
    else:
        tqdm.write(f"   [!] PERMANENT FAILURE: Skipping un-fetchable run ID {batch_ids[0]}")
        # Log this skipped ID to a file so you can investigate it later!
        with open('skipped_runs.txt', 'a') as f:
            f.write(f"{batch_ids[0]}\n")
        return [], 0, remaining
    
def fetch_massive_run_rest(jobs_url, token):

    """
    Uses the REST API to fetch all of the jobs and steps for massive runs.
       This is our cleaning crew for runs that exceed GraphQL's complexity limits and cause timeouts or 502/504 errors.
       It implements a two-pass dynamic resizing strategy:
       1. First attempt: per_page=100 to fetch quickly if the payload is manageable
       2. If it fails on page 1 or 2 with a timeout or 502/504, we assume it's a payload size issue and restart with per_page=10 to reduce the load per request and increase chances of success.
       3. If it fails deep into pagination (e.g., page 15), we assume it's a different issue and do not restart, just log and return whatever we got to avoid infinite loops.
       All failures and retries are logged for post-hoc analysis, and we keep track of how many jobs we managed to fetch before failure to understand the severity of the issue.
       
        ** For anyone wondering why we even have this function: some runs are so massive that they cause GraphQL timeouts or 502/504 errors, and we can't fetch their jobs/steps at all through GraphQL. This REST-based "cleaning crew" function is our workaround for those edge cases, allowing us to still get partial data instead of losing the entire run. **
    """
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    
    # We define a helper so we can easily restart with a smaller per_page if needed.
    def attempt_fetch(per_page):
        all_jobs = []
        page = 1
        # Set max pages to safely cover 2,500 jobs regardless of per_page size
        max_pages = (2500 // per_page) + 1  

        while page <= max_pages:
            retries = 0
            rl_retries = 0
            max_retries = 5
            res = None
            
            while retries < max_retries:
                try:
                    res = requests.get(
                        f"{jobs_url}?per_page={per_page}&page={page}",
                        headers=headers,
                        timeout=30
                    )
                except requests.exceptions.RequestException:
                    retries += 1
                    tqdm.write(f"       [!] Network error on page {page}, retry {retries}/{max_retries}")
                    if retries >= max_retries:
                        break
                    time.sleep(5 * retries)
                    continue
                
                if handle_rate_limit(res, token, retry_count=rl_retries):
                    rl_retries += 1  # Rate-limit sleeps do not burn the error-retry budget
                    continue
                
                if res.status_code in [502, 503, 504]:
                    retries += 1
                    tqdm.write(f"       [!] REST API {res.status_code} on page {page}, retry {retries}/{max_retries}")
                    if retries >= max_retries:
                        break
                    time.sleep(5 * retries)
                    continue
                
                break  # Success! 
            
            # If we hit a hard failure
            if res is None or res.status_code != 200:
                error_type = res.status_code if res else 'network_timeout'
                return False, all_jobs, page, error_type
            
            jobs_data = res.json().get('jobs', [])
            if not jobs_data:
                break
            
            all_jobs.extend(jobs_data)
            
            if len(jobs_data) < per_page:
                break
            
            page += 1
        
        return True, all_jobs, page, None

    
    # Attempt 1: Fast lane (100 jobs per page)
    success, jobs, failed_page, err = attempt_fetch(100)
    if success:
        return jobs, False
    
    with open('massive_run_handled.log', 'a') as f:
        f.write(f"{datetime.now().isoformat()},REST_FAIL_FAST,{jobs_url},failed_on_page_{failed_page},error_{err}\n")
        
    # If it failed on Page 1 or 2, the payload is likely too huge for GitHub.
    # Attempt 2: Slow lane (10 jobs per page). We start completely over to avoid pagination bugs.
    if failed_page <= 2 and err in ['network_timeout', 502, 504]:
        tqdm.write(f"       [!] {err} on {jobs_url}. Payload too heavy. Restarting with per_page=10...")
        success_small, jobs_small, failed_page_small, err_small = attempt_fetch(10)
        
        if success_small:
            with open('massive_run_handled.log', 'a') as f:
                f.write(f"{datetime.now().isoformat()},Successful recovery with slow lane for {jobs_url}. Total jobs fetched: {len(jobs_small)}\n")
            return jobs_small, False
        else:
            # Even the slow lane failed. Log it and give up.
            tqdm.write(f"      *** Aborting {jobs_url}. Failed even at per_page=10. ***")
            with open('incomplete_data.log', 'a') as f:
                f.write(f"{datetime.now().isoformat()},REST_FAIL_BOTH,{jobs_url},failed_on_page_{failed_page_small},error_{err_small}\n")
            return jobs_small, True  # Truncated: slow lane also failed

    # If it failed deep into the pagination (e.g., page 15), don't restart. Just log and return partial.
    tqdm.write(f"      *** Aborting {jobs_url} at page {failed_page}. Got {len(jobs)} jobs. ***")
    with open('incomplete_data.log', 'a') as f:
        f.write(f"{datetime.now().isoformat()},REST_FAIL,{jobs_url},failed_on_page_{failed_page},error_{err}\n")
        
    return jobs, True  # Truncated: failed mid-pagination