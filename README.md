# GHARuns_Collector

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
[![Tests](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml)
[![Dependabot](https://badgen.net/badge/Dependabot/enabled/green?icon=dependabot)](https://dependabot.com/)
[![License: LGPL v3](https://img.shields.io/badge/License-LGPL%20v3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Last Commit](https://img.shields.io/github/last-commit/aref98/GHA_run_metadata)](https://github.com/aref98/GHA_run_metadata/commits/main)

A large-scale data extraction pipeline designed to collect massive datasets of GitHub Actions workflow runs, jobs, steps. This tool was developed for the **ICSME 2026 Data Track** to support empirical research on CI/CD reliability and maintainability.

```mermaid
flowchart LR
    %% Define the Color Palette with larger fonts and padding
    classDef core fill:#f3f4f6,stroke:#9e9e9e,stroke-width:3px,font-size:16px,padding:10px;
    classDef phaseA fill:#e3f2fd,stroke:#0288d1,stroke-width:3px,font-size:16px,padding:10px;
    classDef phaseB fill:#e8f5e9,stroke:#388e3c,stroke-width:3px,font-size:16px,padding:10px;
    classDef phaseC fill:#fff8e1,stroke:#f57c00,stroke-width:3px,font-size:16px,padding:10px;
    classDef decision fill:#fff9c4,stroke:#fbc02d,stroke-width:3px,font-size:16px,padding:10px;
    classDef endpoint fill:#374151,color:#fff,stroke:#374151,stroke-width:3px,font-size:18px,font-weight:bold,padding:15px;

    A(["Start"]) ::: endpoint --> B["Load Repository List"] ::: core
    B --> C["Resolve Time Window\nfrom state file"] ::: core
    C --> D{"Within grace\nperiod?"} ::: decision
    D -- Yes --> Z(["End"]) ::: endpoint
    D -- No --> E{"Phase A\nalready complete?"} ::: decision
    
    E -- No --> F["Phase A — REST Discovery"] ::: phaseA
    F --> G["Query REST API\nfor completed runs"] ::: phaseA
    G --> H["Filter & save runs\nto runs.jsonl"] ::: phaseA
    H --> I["Enqueue check suite IDs\ninto GraphQL buffer"] ::: phaseA
    I --> J{"GraphQL buffer\nfull?"} ::: decision
    
    J -- Yes --> K["Phase B — GraphQL Enrichment"] ::: phaseB
    J -- No, next repo --> F
    
    E -- Yes --> K
    K --> L["Batch-query jobs & steps\nvia GraphQL API"] ::: phaseB
    L --> M{"Run exceeds\nGraphQL page limits?"} ::: decision
    M -- No --> N["Write jobs & steps\nto details.jsonl"] ::: phaseB
    M -- Yes --> O["Add to massive\nruns buffer"] ::: phaseB
    
    N --> P{"More runs\nin GraphQL buffer?"} ::: decision
    O --> P
    P -- Yes --> L
    P -- No --> Q{"Any massive runs\nin buffer?"} ::: decision
    
    Q -- No --> U["Save report &\ncheckpoint state"] ::: core
    Q -- Yes --> R["Phase C — REST Cleanup Crew"] ::: phaseC
    R --> S["Fetch full jobs & steps\nvia REST API"] ::: phaseC
    S --> T["Write to details.jsonl"] ::: phaseC
    T --> U
    
    U --> V{"More windows\nto process?"} ::: decision
    V -- Yes --> C
    V -- No --> Z

```
