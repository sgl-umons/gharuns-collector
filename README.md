# GHARuns Collector

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
[![Tests](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml)
[![Dependabot](https://badgen.net/badge/Dependabot/enabled/green?icon=dependabot)](https://dependabot.com/)
[![License: LGPL v3](https://img.shields.io/badge/License-LGPL%20v3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Last Commit](https://img.shields.io/github/last-commit/aref98/GHA_run_metadata)](https://github.com/aref98/GHA_run_metadata/commits/main)

[![archived repository](https://img.shields.io/badge/archived-repository-orange)](https://archive.softwareheritage.org/swh:1:dir:5eea687b596faf11a1f199260dd2e3d3d336434c;origin=https://github.com/sgl-umons/gharuns-collector)

A large-scale data extraction pipeline designed to collect massive datasets (GHARuns) of GitHub Actions workflow runs, jobs, steps. This tool was developed for the **ICSME 2026 Data Track** to support empirical research on CI/CD reliability and maintainability.

## Overview and Architecture
Mining GitHub Actions workflows run metadata at scale is severely constrained by GitHub's strict rate limits and rolling log retention policies. To overcome this, this tool utilizes an optimized **two-phase extraction architecture** with a REST fallback cleanup phase for edge cases. 

<p align="center">
  <img src="docs/activity_diagram6.svg" alt="Activity diagram of GHARuns Collector" width="1200">
</p>

<p align="center">
  <em>Figure 1: High-level activity diagram of the GHARuns Collector pipeline</em>
</p>

The pipeline (illustrated in Figure 1) operates in three sequential phases per time window:

**Phase A — REST Discovery:** For each repository in the assigned shard, the tool queries the GitHub REST API (`/actions/runs`) to discover all workflow runs that completed within the current 7-day retention window. Discovered runs are immediately written to a `runs.jsonl` file and their check suite IDs are enqueued into a GraphQL buffer. This phase is skipped on days where runs were already collected within the same window.

**Phase B — GraphQL Enrichment:** Once the GraphQL buffer reaches a batch threshold (or Phase A completes), batches of check suite IDs are dispatched to the GitHub GraphQL API to retrieve detailed job and step-level metadata. If a batch exceeds GitHub's complexity limits, it is automatically split and retried. Results are written to a `details.jsonl` file. Runs whose job or step counts overflow GraphQL's pagination limits are flagged and forwarded to Phase C.

**Phase C — REST Cleanup Crew:** A small fraction of workflow runs (~1%) contain so many jobs or steps that they cannot be fully retrieved through GraphQL. These "massive runs" are fetched individually via the REST API (`/actions/jobs`), using a two-attempt dynamic resizing strategy (100 jobs/page, falling back to 10 jobs/page on payload errors). Their output is normalized into the same structure as Phase B results and written to the same `details.jsonl` file, ensuring complete coverage.

The pipeline is designed to be fault-tolerant. Progress is checkpointed to a state file after every repository and every GraphQL batch, so execution can be safely interrupted and resumed at any point without data loss or duplicate API calls.

## Installation
Clone the repository and install the required dependencies:

```bash
git clone https://github.com/sgl-umons/gharuns-collector.git
cd gharuns-collector
pip install -r requirements.txt
```

The tool requires **Python 3.9+**. All dependencies are listed in `requirements.txt`:

| Package | Purpose |
|---|---|
| `requests` | REST API calls to GitHub |
| `pandas` | Loading and sharding repository lists |
| `tqdm` | Progress bars for long-running collection |
| `python-dotenv` | Optional `.env` file support for token loading |

A GitHub Personal Access Token with `repo` and `workflow` read scopes is required. Pass it via `--token` or set it as `GITHUB_TOKEN` in a `.env` file in the project root.

## Usage

Run the pipeline by pointing it at a repository list and providing a GitHub token:

```bash
python main.py --token ghp_YOUR_TOKEN_HERE --input repos.txt
```

The pipeline will automatically determine the correct 7-day collection window, resume from any previous checkpoint, and advance through all outstanding windows until it reaches the grace period boundary.

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--token` | Yes | — | GitHub Personal Access Token (PAT) with `repo` and `workflow` read scopes |
| `--input` | Yes | — | Path to a `.csv` or `.txt` file containing the target repositories |
| `--worker` | No | `0/1` | Shard assignment in `index/total` format (e.g. `1/3` for the second of three workers) |
| `--test` | No | `None` | Limit the number of repositories processed — useful for smoke-testing |

**Input file formats accepted by `--input`:**
- `.csv` — must have a `repository` column (or repository names as the first column)
- `.txt` — one `owner/repo` per line, or comma-separated on a single line
- Full GitHub URLs (e.g. `https://github.com/owner/repo`) are also accepted and normalized automatically

## Examples

**1. Basic Single-Worker Run**
Collect data for a custom repository list on a single machine:
```bash
python main.py --token ghp_YOUR_TOKEN_HERE --input my_repos.txt
```

**2. CSV Repository List**
Use a CSV file with a `repository` column:
```bash
python main.py --token ghp_YOUR_TOKEN_HERE --input my_repos.csv
```

**3. Smoke Test (Limit to 10 Repos)**
Quickly verify the setup is working before a full run:
```bash
python main.py --token ghp_YOUR_TOKEN_HERE --input my_repos.txt --test 10
```

**4. Distributed Sharding (e.g., 3 Workers)**
If running on multiple servers to speed up collection, assign each instance a specific shard.
*(Note: Worker counts must remain constant for the duration of a collection window.) - You can only change the number of workers after all cursors are pointing to a same retention day. For example all workers finish the 2026-05-01 -- 2026-05-07 window, so in this example you can safely reduce or increase the number of workers*
```bash
# On Server A
python main.py --token ghp_TOKEN_A --input repos.txt --worker 0/3

# On Server B
python main.py --token ghp_TOKEN_B --input repos.txt --worker 1/3

# On Server C
python main.py --token ghp_TOKEN_C --input repos.txt --worker 2/3
```

Each worker processes a disjoint subset of the repository list and writes its output to separate shard-stamped files (e.g. `runs_shard_0_DATE.jsonl`, `runs_shard_1_DATE.jsonl`).

## Citation

If you use this tool or the accompanying dataset in your research, please cite our ICSME 2026 paper:

```bibtex

@misc{Talebzadeh2026-ICSME,
      title={On the GitHub Actions Language: Usage, Evolution, and Workflow Reliability}, 
      author={Aref Talebzadeh Bardsiri and Alexandre Decan and Tom Mens},
      year={2026},
      eprint={2605.26825},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2605.26825}, 
}

```

## License

This project is licensed under GNU Lesser General Public License v3.
