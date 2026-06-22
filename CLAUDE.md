# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HR Document Assistant — an open-source tool that uses the Anthropic Claude API to automate HR document workflows and assist with Korean labor law (한국 노동법) compliance.

### Core Features

- Employment contract (근로계약서) draft generation
- Salary/attendance review against Korean labor law
- HR announcements and internal notices
- Exit interview report automation

### Tech Stack

- Anthropic Claude API
- Python / JavaScript (specific framework TBD)

### Target Users

Small-to-medium business HR teams needing document automation.

## Development Commands

```bash
pip install -e ".[dev]"       # Install with dev dependencies
python -m pytest tests/ -v    # Run all tests
python -m pytest tests/test_contract.py::test_generate_contract_draft  # Run single test
```

## Project Structure

- `src/hr_assistant/` — main application package
- `tests/` — pytest test suite
- `pyproject.toml` — project config, dependencies, pytest settings
