# Agentic Jobs Platform — MVPart 1

This repository contains the FastAPI + SQLAlchemy scaffold for the Agentic Jobs Platform. MVPart 1 covers the initial project boot, core data contracts, and stubbed HTTP interfaces that later milestones will extend.

## How to run MVPart 1

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Start the FastAPI development server:

   ```bash
   uvicorn agentic_jobs.main:app --reload
   ```

3. Run the test suite:

   ```bash
   pytest -q
   ```
