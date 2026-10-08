"""
main.py - the web server (the "mediator").

The browser sends one request: /api/search?q=LIP-001
The mediator then:
  1. asks both databases (db.search)
  2. turns the answers into XML, merges and validates them (xml_builder.build)
  3. sends everything back as JSON, including the trace of every database call

Run it:
  uvicorn main:app --reload        then open http://127.0.0.1:8000/  (API docs: /docs)
"""
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

import db
import xml_builder

app = FastAPI(title="Merchandise Categorization and Inventory (mediator)")

# The single HTML page lives in merch-inventory/frontend/ (we are in backend/).
FRONTEND_PAGE = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.get("/", include_in_schema=False)
def home():
    """Open http://127.0.0.1:8000/ to get the search page."""
    return FileResponse(FRONTEND_PAGE)


# A plain `def` (not `async def`) on purpose: our database calls block while they
# wait, and FastAPI runs plain `def` endpoints in a worker thread so one slow
# search does not freeze the whole server.
@app.get("/api/search")
def search(q: str = Query("", max_length=100, description="A SKU (LIP-001) or a category (Makeup)")):
    result = db.search(q)                  # step 1: ask both databases
    xml = xml_builder.build(result)        # step 2: XML pieces -> merge -> validate
    return {
        "query": result["query"],
        "mode": result["mode"],            # "sku", "category" or "none"
        "skus": result["skus"],
        "valid": xml["valid"],             # did the merged XML pass merchandise.xsd?
        "errors": xml["errors"],
        "mongodb_xml": xml["mongodb_xml"],
        "neo4j_xml": xml["neo4j_xml"],
        "merged_xml": xml["merged_xml"],
        "trace": result["trace"],          # every database call, with timings
    }


@app.get("/api/health")
def health():
    """Is each database reachable right now? (Used to show a status light later.)"""
    status = {}
    for name, check in [("mongodb", db.check_mongo), ("neo4j", db.check_neo4j)]:
        try:
            check()
            status[name] = "up"
        except Exception as error:
            status[name] = "down: " + str(error).splitlines()[0]
    return status
