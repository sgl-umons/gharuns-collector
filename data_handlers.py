import os
import sys
import pandas as pd
import config

WEIGHTS_FILE = "repo_weights.csv"

def _clean_repo_name(repo_str):
    """Extracts 'owner/repo' from URLs or raw strings and validates the format."""
    if not isinstance(repo_str, str):
        return None
    
    repo_str = repo_str.strip()
    if not repo_str:
        return None
        
    # Strip trailing .git if present
    if repo_str.endswith('.git'):
        repo_str = repo_str[:-4]
        
    # Handle full URLs (e.g., https://github.com/aref98/nodejs-rest-api)
    if 'github.com/' in repo_str:
        parts = repo_str.split('github.com/')[-1].split('/')
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
            
    # Validate it is in standard owner/repo format (must contain exactly one slash)
    if repo_str.count('/') == 1:
        return repo_str
        
    return None

def load_test_repos(input_file=None, limit=None, worker_index=0, total_workers=1):
    repos = []
    
    # 1. Custom Input File Provided
    if input_file:
        if os.path.exists(input_file):
            print(f"[*] Loading custom repositories from {input_file}")
            raw_repos = []
            
            if input_file.endswith('.csv'):
                df = pd.read_csv(input_file)
                col_name = 'repository' if 'repository' in df.columns else df.columns[0]
                raw_repos = df[col_name].dropna().tolist()
                
            elif input_file.endswith('.txt'):
                with open(input_file, 'r') as f:
                    content = f.read()
                # Replace commas with newlines to gracefully handle both comma-separated 
                # and line-by-line formats (or even a mix of both)
                content = content.replace(',', '\n')
                raw_repos = content.splitlines()
            else:
                print(f"[!] Error: Unsupported file extension for '{input_file}'. Please provide a .csv or .txt file.")
                sys.exit(1)

            # Clean and validate all extracted strings
            for r in raw_repos:
                cleaned = _clean_repo_name(r)
                if cleaned:
                    repos.append(cleaned)
            
            # Remove duplicates while preserving order (dict keys maintain insertion order in modern Python)
            repos = list(dict.fromkeys(repos))
            
            if not repos:
                print("[!] Error: No valid repositories found in the input file.")
                print("[*] Acceptable formats:")
                print("    - CSV file with a 'repository' column (or as the first column)")
                print("    - TXT file with one repository per line")
                print("    - TXT file with comma-separated repositories on a single line")
                print("[*] Entries must be in 'owner/repo' format or full GitHub URLs (e.g., https://github.com/owner/repo).")
                sys.exit(1)
                
        else:
            print(f"[!] Error: The provided input file '{input_file}' does not exist.")
            sys.exit(1)
            
    # 2. Default Logic (Original Behavior)
    else:
        if os.path.exists(WEIGHTS_FILE):
            # Load pre-computed weights: repos sorted heavy→light for balanced round-robin
            df = pd.read_csv(WEIGHTS_FILE)
            repos = df['repository'].tolist()  # already sorted by run_count desc
            print(f"[*] Using repo_weights.csv for balanced assignment.")
            
        elif os.path.exists(config.INPUT_FILE):
            # Fallback: random shuffle (no weight file yet)
            df = pd.read_csv(config.INPUT_FILE)
            repos = df['repository'].drop_duplicates().sample(frac=1.0, random_state=42).tolist()
            print(f"[*] No repo_weights.csv found — using random assignment.")
            
        # 3. Missing Data - Friendly Error Message
        else:
            print("[!] Error: No repository data found.")
            print("[*] Please provide your list of repos as input using the --input flag.")
            print("[*] Example: python main.py --token YOUR_TOKEN --input my_repos.txt")
            print("[*] Example: python main.py --token YOUR_TOKEN --input my_repos.csv")
            sys.exit(1)

    # Apply the optional limit for testing
    if limit is not None:
        repos = repos[:limit]

    print(f"[*] Loaded {len(repos)} unique valid repositories for processing.")
    
    # Deal repos like a deck of cards: heavy repos spread evenly across workers
    return repos[worker_index::total_workers]