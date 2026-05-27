# GHARuns_Collector

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
[![Tests](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml)
[![Dependabot](https://badgen.net/badge/Dependabot/enabled/green?icon=dependabot)](https://dependabot.com/)
[![License: LGPL v3](https://img.shields.io/badge/License-LGPL%20v3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Last Commit](https://img.shields.io/github/last-commit/aref98/GHA_run_metadata)](https://github.com/aref98/GHA_run_metadata/commits/main)

A large-scale data extraction pipeline designed to collect massive datasets of GitHub Actions workflow runs, jobs, steps. This tool was developed for the **ICSME 2026 Data Track** to support empirical research on CI/CD reliability and maintainability.

```mermaid
flowchart LR
    A(["Start"]) --> B["Load Repository List"]
    B --> C["Resolve Time Window\nfrom state file"]
    C --> D{"Within grace\nperiod?"}
    D -- Yes --> Z(["End"])
    D -- No --> E{"Phase A\nalready complete?"}
    E -- No --> F["Phase A — REST Discovery"]
    F --> G["Query REST API\nfor completed runs"]
    G --> H["Filter & save runs\nto runs.jsonl"]
    H --> I["Enqueue check suite IDs\ninto GraphQL buffer"]
    I --> J{"GraphQL buffer\nfull?"}
    J -- Yes --> K["Phase B — GraphQL Enrichment"]
    J -- No, next repo --> F
    E -- Yes --> K
    K --> L["Batch-query jobs & steps\nvia GraphQL API"]
    L --> M{"Run exceeds\nGraphQL page limits?"}
    M -- No --> N["Write jobs & steps\nto details.jsonl"]
    M -- Yes --> O["Add to massive\nruns buffer"]
    N --> P{"More runs\nin GraphQL buffer?"}
    O --> P
    P -- Yes --> L
    P -- No --> Q{"Any massive runs\nin buffer?"}
    Q -- No --> U["Save report &\ncheckpoint state"]
    Q -- Yes --> R["Phase C — REST Cleanup Crew"]
    R --> S["Fetch full jobs & steps\nvia REST API"]
    S --> T["Write to details.jsonl"]
    T --> U
    U --> V{"More windows\nto process?"}
    V -- Yes --> C
    V -- No --> Z

     A:::endpoint
     B:::core
     C:::core
     C:::core
     D:::decision
     Z:::endpoint
     Z:::endpoint
     E:::decision
     F:::phaseA
     F:::phaseA
     G:::phaseA
     H:::phaseA
     I:::phaseA
     J:::decision
     K:::phaseB
     K:::phaseB
     L:::phaseB
     M:::decision
     N:::phaseB
     O:::phaseB
     P:::decision
     Q:::decision
     U:::core
     U:::core
     R:::phaseC
     S:::phaseC
     T:::phaseC
     V:::decision
    classDef core fill:#f3f4f6,stroke:#9e9e9e,stroke-width:2px
    classDef phaseA fill:#e3f2fd,stroke:#0288d1,stroke-width:2px
    classDef phaseB fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    classDef phaseC fill:#fff8e1,stroke:#f57c00,stroke-width:2px
    classDef decision fill:#fff9c4,stroke:#fbc02d,stroke-width:2px
    classDef endpoint fill:#374151,color:#fff,stroke:#374151,stroke-width:2px
    style D fill:#fff8e1
    style E fill:#fff8e1
    style J fill:#fff8e1
    style M fill:#fff8e1
    style P fill:#fff8e1
    style Q fill:#fff8e1
    style R fill:#f3f4f6
    style S fill:#f3f4f6
    style T fill:#f3f4f6
    style V fill:#fff8e1

```
