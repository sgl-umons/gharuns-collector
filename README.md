# GHARuns_Collector

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
[![Tests](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/aref98/GHA_run_metadata/actions/workflows/ci.yml)
[![Dependabot](https://badgen.net/badge/Dependabot/enabled/green?icon=dependabot)](https://dependabot.com/)
[![License: LGPL v3](https://img.shields.io/badge/License-LGPL%20v3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Last Commit](https://img.shields.io/github/last-commit/aref98/GHA_run_metadata)](https://github.com/aref98/GHA_run_metadata/commits/main)

A large-scale data extraction pipeline designed to collect massive datasets of GitHub Actions workflow runs, jobs, steps. This tool was developed for the **ICSME 2026 Data Track** to support empirical research on CI/CD reliability and maintainability.

## Overview and Architecture
Mining GitHub Actions workflows run metadata at scale is severely constrained by GitHub's strict rate limits and rolling log retention policies. To overcome this, this tool utilizes an optimized **Two-Phase Extraction Architecture** combined with a forward-progressing sliding window.

<p align="center">
  <img src="docs/activity_diagram6.svg" alt="Activity diagram of GHARuns_Collector" width="1200">
</p>

<p align="center">
  <em>Figure 1: High-level activity diagram of the GHARuns_Collector pipeline</em>
</p>

/// explanations of the diagram ///




## Installation
Clone the repository and install the required dependencies:

```bash
git clone https://github.com/sgl-umons/GHA_run_metadata.git
cd GHA_run_metadata
pip install -r requirements.txt
```

## Usage
