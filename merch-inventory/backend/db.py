"""
db.py - connects Python to our two databases and asks them questions.

  MongoDB -> product catalog ("products") + stock ("inventory")
  Neo4j   -> category tree + product relationships

Every database call is recorded in a "trace" (which database, what query,
how many rows, how long it took) so we can show it in the frontend.

Run it on its own to test:
  python db.py              -> checks both connections
  python db.py LIP-001      -> runs a full search and prints the raw results + trace
  python db.py --check      -> small self-test of the search logic
"""
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase
from pymongo import MongoClient

# Read settings (URIs, user, password) from merch-inventory/.env.
# __file__ is this file (backend/db.py), so .parent.parent is merch-inventory/.
# load_dotenv copies each line of .env into os.environ.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------- MongoDB --
# MongoClient does not connect straight away; it connects on the first real
# command. serverSelectionTimeoutMS = give up after 3 seconds if Mongo is down
# (the default is 30 seconds, which feels like the program has frozen).
mongo_client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=3000)

# Our database is called "nykaa" (seed.py fills it).
mongo_db = mongo_client["nykaa"]

# ------------------------------------------------------------------ Neo4j --
# The driver talks to Neo4j over the "bolt" protocol (port 7687),
# logging in with the user and password from .env.
# By default the driver keeps retrying a failed query for 30 seconds, which
# would freeze the page if Neo4j is down. These two settings make it give up
# after ~3 seconds instead (like MongoDB above).
neo4j_driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    connection_timeout=3,
    max_transaction_retry_time=0,
)


def check_mongo():
    """Send MongoDB a 'ping'. Raises an error if it does not answer."""
    mongo_client.admin.command("ping")


def check_neo4j():
    """Open a connection and log in. Raises an error if Neo4j is down or the password is wrong."""
    neo4j_driver.verify_connectivity()


# =========================================================================
# THE TRACE
# =========================================================================

def traced(trace, database, purpose, query, params, run):
    """
    Run one database call and write down what happened in `trace` (a list).

      database - "mongodb" or "neo4j"
      purpose  - plain-English reason for the call (shown in the UI)
      query    - the query text, for display only
      params   - the values sent with the query (e.g. the SKUs)
      run      - a function that actually calls the database and returns a list

    If the database fails, we record the error and return an empty list,
    so one broken database does not crash the whole search.
    """
    entry = {"step": len(trace) + 1, "database": database, "purpose": purpose,
             "query": query, "params": params}
    start = time.perf_counter()                # start the stopwatch
    try:
        result = run()
        entry["rows"] = len(result)
    except Exception as error:
        result = []
        entry["rows"] = 0
        entry["error"] = str(error).splitlines()[0]   # first line is enough
    # stop the stopwatch; milliseconds, 2 decimal places
    entry["ms"] = round((time.perf_counter() - start) * 1000, 2)
    trace.append(entry)
    return result


# =========================================================================
# THE QUERIES (one function per database question)
# =========================================================================

def find_sku(text, trace):
    """MongoDB: is the search text a product SKU? Returns [sku] or []."""
    sku = text.upper()                         # so "lip-001" also works
    return traced(
        trace, "mongodb", "Is the search text a SKU?",
        "db.products.find({sku: $sku}, {sku: 1})", {"sku": sku},
        lambda: [doc["sku"] for doc in
                 mongo_db["products"].find({"sku": sku}, {"_id": 0, "sku": 1})],
    )


# Cypher for "all products in this category OR any category below it".
#   (c)<-[:SUBCATEGORY_OF*0..]-(sub)  means: start at c and walk DOWN the tree
#   0 or more steps. 0 steps = c itself, so products directly in c count too.
CATEGORY_QUERY = """
MATCH (c:Category) WHERE toLower(c.name) = toLower($name)
MATCH (c)<-[:SUBCATEGORY_OF*0..]-(sub:Category)<-[:IN_CATEGORY]-(p:Product)
RETURN DISTINCT p.sku AS sku ORDER BY sku
""".strip()


def skus_in_category(text, trace):
    """Neo4j: which product SKUs are under this category (including subcategories)?"""
    def run():
        records, _, _ = neo4j_driver.execute_query(CATEGORY_QUERY, name=text)
        return [record["sku"] for record in records]
    return traced(trace, "neo4j", "Which products are in this category?",
                  CATEGORY_QUERY, {"name": text}, run)


def get_products(skus, trace):
    """MongoDB "products": the catalog details for these SKUs."""
    return traced(
        trace, "mongodb", "Get product details (catalog)",
        "db.products.find({sku: {$in: $skus}}).sort({sku: 1})", {"skus": skus},
        # {"_id": 0} = leave out Mongo's internal id, we don't need it
        lambda: list(mongo_db["products"].find({"sku": {"$in": skus}}, {"_id": 0}).sort("sku", 1)),
    )


def stock_status(quantity, reorder_level):
    """OUT if none left, LOW if at or below the reorder level, otherwise OK."""
    if quantity == 0:
        return "OUT"
    if quantity <= reorder_level:
        return "LOW"
    return "OK"


def get_stock(skus, trace):
    """MongoDB "inventory": stock in each warehouse for these SKUs (+ a status)."""
    def run():
        docs = list(mongo_db["inventory"]
                    .find({"sku": {"$in": skus}}, {"_id": 0})
                    .sort([("sku", 1), ("warehouse.code", 1)]))
        for doc in docs:
            doc["status"] = stock_status(doc["quantity"], doc["reorder_level"])
        return docs
    return traced(
        trace, "mongodb", "Get stock per warehouse (inventory)",
        "db.inventory.find({sku: {$in: $skus}}).sort({sku: 1, 'warehouse.code': 1})",
        {"skus": skus}, run)


# Cypher for "category path + related products" for each SKU.
#   1. UNWIND: one row per SKU in the list.
#   2. Find the product and the category it is in.
#   3. Walk UP the tree to the top (root = a category with no parent).
#   4. OPTIONAL MATCH: related products, if there are any (OPTIONAL = don't
#      drop the product when it has none).
#   5. reverse(nodes(path)) turns [Lipstick, Lips, Makeup] into [Makeup, Lips, Lipstick].
#      The WHERE inside the list removes the empty entry made when there are no related products.
GRAPH_QUERY = """
UNWIND $skus AS sku
MATCH (p:Product {sku: sku})-[:IN_CATEGORY]->(c:Category)
MATCH path = (c)-[:SUBCATEGORY_OF*0..]->(root:Category)
WHERE NOT (root)-[:SUBCATEGORY_OF]->()
OPTIONAL MATCH (p)-[r:BOUGHT_TOGETHER|SIMILAR_TO]-(other:Product)
WITH sku, path, other, r ORDER BY type(r), other.sku
RETURN sku,
       [n IN reverse(nodes(path)) | n.name] AS path,
       [x IN collect({sku: other.sku, type: type(r)}) WHERE x.sku IS NOT NULL] AS related
ORDER BY sku
""".strip()


def get_graph_info(skus, trace):
    """Neo4j: category path and related products for these SKUs."""
    def run():
        records, _, _ = neo4j_driver.execute_query(GRAPH_QUERY, skus=skus)
        return [record.data() for record in records]   # .data() turns a record into a dict
    return traced(trace, "neo4j", "Get category path + related products",
                  GRAPH_QUERY, {"skus": skus}, run)


# =========================================================================
# THE WHOLE SEARCH (what one query from the frontend does)
# =========================================================================

def search(text):
    """
    Take what the user typed ("LIP-001" or "Makeup") and fetch everything
    about the matching products from both databases.

    Returns a dict with the raw results from each database and the trace.
    (Stage 4 turns this into XML.)
    """
    text = text.strip()
    trace = []

    # Step 1: is it a SKU? (MongoDB)
    skus = find_sku(text, trace) if text else []
    if skus:
        mode = "sku"
    else:
        # Step 1b: not a SKU, so try it as a category name (Neo4j)
        skus = skus_in_category(text, trace) if text else []
        mode = "category" if skus else "none"

    # Steps 2-4: fetch the data for those SKUs (only if we found any)
    products = get_products(skus, trace) if skus else []
    stock = get_stock(skus, trace) if skus else []
    graph = get_graph_info(skus, trace) if skus else []

    return {"query": text, "mode": mode, "skus": skus,
            "products": products, "stock": stock, "graph": graph, "trace": trace}


def print_trace(trace):
    """Print the trace as a small table in the terminal."""
    print(f"{'#':<3}{'database':<10}{'rows':>5}{'ms':>9}  purpose")
    for t in trace:
        print(f"{t['step']:<3}{t['database']:<10}{t['rows']:>5}{t['ms']:>9}  {t['purpose']}"
              + (f"  ERROR: {t['error']}" if "error" in t else ""))


def self_check():
    """Small test of the search logic. Needs the data from seed.py."""
    r = search("LIP-001")
    assert r["mode"] == "sku" and r["skus"] == ["LIP-001"]
    assert r["products"][0]["brand"] == "Maybelline New York"            # from MongoDB
    assert r["graph"][0]["path"] == ["Makeup", "Lips", "Lipstick"]       # from Neo4j
    assert {"sku": "KAJ-001", "type": "BOUGHT_TOGETHER"} in r["graph"][0]["related"]
    assert sum(s["quantity"] for s in r["stock"]) == 200                 # 140 + 60

    assert search("lip-001")["skus"] == ["LIP-001"]                      # lowercase works
    assert len(search("makeup")["skus"]) == 8                            # subcategories included
    assert search("Lipstick")["skus"] == ["LIP-001", "LIP-002", "LIP-003"]
    assert search("OIL-001")["stock"] == []                              # no stock on purpose
    assert [s["status"] for s in search("FND-001")["stock"]] == ["LOW", "LOW"]
    assert search("CMP-001")["stock"][0]["status"] == "OUT"

    nothing = search("xyz")
    assert nothing["mode"] == "none" and nothing["skus"] == []
    assert len(nothing["trace"]) == 2          # asked MongoDB, then Neo4j, then stopped
    assert search("   ")["mode"] == "none"     # empty input: no database calls
    print("All checks passed.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # python db.py -> check each database separately
        for name, check in [("MongoDB", check_mongo), ("Neo4j", check_neo4j)]:
            try:
                check()
                print(f"{name} OK")
            except Exception as error:
                print(f"{name} FAILED: {error}")
    elif sys.argv[1] == "--check":
        self_check()
    else:
        # python db.py LIP-001 -> run a search and show everything
        result = search(" ".join(sys.argv[1:]))
        trace = result.pop("trace")
        # ensure_ascii=False so "Lakmé" prints properly
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print()
        print_trace(trace)
