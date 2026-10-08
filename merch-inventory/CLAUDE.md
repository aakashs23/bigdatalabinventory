# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Student case study: "Merchandise Categorization and Inventory", modelled on Nykaa (illustrative data, not affiliated). A FastAPI mediator queries two databases, converts each result to XML (lxml), merges them by SKU into one XML document, validates it against an XSD, and returns it with a per-query trace to a single HTML page.

- **MongoDB 7** (`localhost:27017`, no auth, db `nykaa`): `products` (flexible attributes) and `inventory` (stock per warehouse)
- **Neo4j 5** (browser `:7474`, bolt `:7687`, user `neo4j`, password from `.env`): category tree + BOUGHT_TOGETHER / SIMILAR_TO

The full plan and staged build order live in the user's approved plan; the user wants one stage at a time, plain-language explanations, and to say "next" before moving on.

## Layout

The git root is the parent folder `bigdatalabinventory/` (remote `origin` = github.com/aakashs23/bigdatalabinventory, branch `main`). This folder has no `.git` of its own.

## Commands

```bash
cp .env.example .env                              # once; .env is gitignored
docker compose up -d                              # start mongo + neo4j (no volumes: data is lost on `down`)
cd backend && source venv/bin/activate            # Python 3.9 venv (gitignored)
pip install -r requirements.txt
python seed.py                                    # wipe + reload both databases
uvicorn main:app --reload                         # then open http://127.0.0.1:8000/
python db.py --check; python xml_builder.py --check   # self-tests (need seeded databases)
```

On this Windows machine the venv comes from Anaconda's Python 3.9, which needs its DLLs on PATH first:
`$py="C:\Users\0rayc\anaconda3"; $env:PATH="$py;$py\Library\bin;$py\Scripts;$env:PATH"`, then use `venv\Scripts\python.exe`.
`.env` uses `127.0.0.1` (not `localhost`) for Neo4j: it halves the time a search waits when Neo4j is down.

## Gotchas

- The venv is Python 3.9, so avoid 3.10+ syntax (`X | Y` unions, `match`).
- Credentials come from `.env` (compose reads it automatically; Python reads it via python-dotenv). Never hardcode them.
- Keep code simple and heavily commented: the user must explain it line by line in a viva.
