# GHARuns_Collector

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
[![Tests](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml)
[![Dependabot](https://badgen.net/badge/Dependabot/enabled/green?icon=dependabot)](https://dependabot.com/)
[![License: LGPL v3](https://img.shields.io/badge/License-LGPL%20v3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Last Commit](https://img.shields.io/github/last-commit/aref98/GHA_run_metadata)](https://github.com/aref98/GHA_run_metadata/commits/main)

A fault-tolerant, large-scale data extraction pipeline designed to collect massive datasets of GitHub Actions workflow runs, jobs, steps, and annotations. This tool was developed for the **ICSME 2026 Data Track** to support empirical research on CI/CD reliability and maintainability.




```mermaid
flowchart TD
    %% Define the Color Palette (Adjusted font to 20px for better boxing)
    classDef core fill:#f3f4f6,stroke:#9e9e9e,stroke-width:3px,font-size:10px,padding:10px;
    classDef phaseA fill:#e3f2fd,stroke:#0288d1,stroke-width:3px,font-size:10px,padding:10px;
    classDef phaseB fill:#e8f5e9,stroke:#388e3c,stroke-width:3px,font-size:10px,padding:10px;
    classDef phaseC fill:#fff8e1,stroke:#f57c00,stroke-width:3px,font-size:10px,padding:10px;
    classDef decision fill:#fff9c4,stroke:#fbc02d,stroke-width:3px,font-size:10px,padding:10px;
    classDef endpoint fill:#374151,color:#fff,stroke:#374151,stroke-width:3px,font-size:8px,font-weight:bold,padding:15px;

    %% Initial Steps
    A(["Start"]) --> B["Load Repository List"]
    B --> C["Resolve Time Window\nfrom state file"]
    C --> D{"Within grace\nperiod?"}
    D -- Yes --> Z(["End"])
    D -- No --> E{"Phase A\nalready complete?"}

    %% PHASE A: REST Discovery
    subgraph SubA [Phase A — REST Discovery]
        F["Query REST API\nfor completed runs"]
        G["Filter & save runs\nto runs.jsonl"]
        H["Enqueue check suite IDs\ninto GraphQL buffer"]
        I{"GraphQL buffer\nfull?"}
        
        F --> G --> H --> I
    end

    E -- No --> F
    I -- No, next repo --> F

    %% PHASE B: GraphQL Enrichment
    subgraph SubB [Phase B — GraphQL Enrichment]
        K["Batch-query jobs & steps\nvia GraphQL API"]
        L{"Run exceeds\nGraphQL page limits?"}
        M["Write jobs & steps\nto details.jsonl"]
        N["Add to massive\nruns buffer"]
        O{"More runs\nin GraphQL buffer?"}
        
        K --> L
        L -- No --> M --> O
        L -- Yes --> N --> O
        O -- Yes --> K
    end

    I -- Yes --> K
    E -- Yes --> K

    %% PHASE C & FINALIZATION
    subgraph SubC [Phase C — REST Cleanup & State Save]
        P{"Any massive runs\nin buffer?"}
        Q["Fetch full jobs & steps\nvia REST API"]
        R["Write to details.jsonl"]
        S["Save report &\ncheckpoint state"]
        T{"More windows\nto process?"}

        P -- Yes --> Q --> R --> S
        P -- No --> S
        S --> T
    end

    O -- No --> P
    T -- Yes --> C
    T -- No --> Z

    %% Apply Classes Safely
    class A,Z endpoint;
    class B,C,S core;
    class D,E,I,L,O,P,T decision;
    class F,G,H phaseA;
    class K,M,N phaseB;
    class Q,R phaseC;

```
