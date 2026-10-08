"""
seed.py - loads the Nykaa case-study sample data into both databases.

  MongoDB  "products"  collection -> what each product IS (name, brand, price, attributes)
  MongoDB  "inventory" collection -> how many we HAVE in each warehouse
  Neo4j    graph                  -> WHERE each product sits (category tree) and how products CONNECT

Every run WIPES both databases and loads everything again, so you can run it
as often as you like (handy because docker-compose has no volumes).

NOTE: academic case study, not affiliated with Nykaa.
Brands/products are real; prices, stock and warehouses are made up.

Run:   python seed.py
"""
from neo4j.exceptions import Neo4jError
from pymongo.errors import WriteError

from db import mongo_db, neo4j_driver

# =========================================================================
# 1. DATA FOR MONGODB
# =========================================================================

# Each product has the same basic fields, but "attributes" are DIFFERENT for
# every kind of product (a lipstick has a shade, a sunscreen has an SPF).
# That is the main reason the catalog lives in MongoDB.
PRODUCTS = [
    {"sku": "LIP-001", "name": "SuperStay Matte Ink Liquid Lipstick", "brand": "Maybelline New York",
     "price": 699, "tags": ["long-lasting", "transfer-proof", "bestseller"],
     "attributes": {"shade": "Pioneer", "finish": "Matte", "volume_ml": 5}},
    {"sku": "LIP-002", "name": "So Matte Lipstick", "brand": "Nykaa Cosmetics",
     "price": 299, "tags": ["matte", "budget"],
     "attributes": {"shade": "Brick Red", "finish": "Matte", "weight_g": 4.2}},
    {"sku": "LIP-003", "name": "9to5 Primer + Matte Lip Color", "brand": "Lakmé",
     "price": 550, "tags": ["primer-infused", "matte"],
     "attributes": {"shade": "Plum Passion", "finish": "Matte", "weight_g": 3.6}},
    {"sku": "FND-001", "name": "Fit Me Matte + Poreless Foundation", "brand": "Maybelline New York",
     "price": 599, "tags": ["oil-control", "bestseller"],
     "attributes": {"shade": "128 Warm Nude", "skin_type": "Normal to Oily", "volume_ml": 30, "spf": 22}},
    {"sku": "FND-002", "name": "9to5 Weightless Mousse Foundation", "brand": "Lakmé",
     "price": 675, "tags": ["lightweight", "matte"],
     "attributes": {"shade": "Beige Vanilla", "finish": "Matte", "weight_g": 25}},
    {"sku": "CMP-001", "name": "9to5 Flawless Matte Complexion Compact", "brand": "Lakmé",
     "price": 500, "tags": ["touch-up", "matte"],
     "attributes": {"shade": "Melon", "weight_g": 8, "spf": 20}},
    {"sku": "KAJ-001", "name": "Eyeconic Kajal", "brand": "Lakmé",
     "price": 240, "tags": ["smudge-proof", "bestseller"],
     "attributes": {"color": "Deep Black", "smudge_proof_hours": 22, "weight_g": 0.35}},
    {"sku": "MAS-001", "name": "Lash Sensational Mascara", "brand": "Maybelline New York",
     "price": 599, "tags": ["volumising", "waterproof"],
     "attributes": {"color": "Very Black", "waterproof": True, "volume_ml": 10}},
    {"sku": "MOI-001", "name": "Moisturising Cream", "brand": "Cetaphil",
     "price": 849, "tags": ["dry skin", "dermatologist-recommended"],
     "attributes": {"skin_type": "Dry to Very Dry", "weight_g": 80, "fragrance_free": True}},
    {"sku": "SER-001", "name": "10% Niacinamide Face Serum", "brand": "Minimalist",
     "price": 599, "tags": ["oily skin", "acne", "bestseller"],
     "attributes": {"key_ingredient": "Niacinamide 10% + Zinc 1%", "skin_type": "Oily, Acne-prone", "volume_ml": 30}},
    {"sku": "SER-002", "name": "10% Vitamin C Face Serum", "brand": "The Derma Co",
     "price": 699, "tags": ["brightening", "dull skin"],
     "attributes": {"key_ingredient": "Vitamin C 10%", "skin_type": "All", "volume_ml": 30}},
    {"sku": "SUN-001", "name": "Ultra Sheer Sunscreen SPF 50+", "brand": "Neutrogena",
     "price": 699, "tags": ["sun protection", "non-greasy"],
     "attributes": {"spf": 50, "pa_rating": "PA+++", "volume_ml": 88}},
    {"sku": "SUN-002", "name": "SPF 50 Sunscreen", "brand": "Minimalist",
     "price": 399, "tags": ["sun protection", "no white cast"],
     "attributes": {"spf": 50, "pa_rating": "PA++++", "volume_ml": 50}},
    {"sku": "SHA-001", "name": "Total Repair 5 Shampoo", "brand": "L'Oréal Paris",
     "price": 399, "tags": ["damaged hair", "repair"],
     "attributes": {"hair_type": "Damaged", "volume_ml": 340}},
    {"sku": "SHA-002", "name": "Onion Shampoo", "brand": "Mamaearth",
     "price": 349, "tags": ["hair fall", "natural"],
     "attributes": {"hair_type": "Hair Fall", "key_ingredient": "Onion Oil", "volume_ml": 250}},
    {"sku": "OIL-001", "name": "Bringadi Intensive Hair Treatment Oil", "brand": "Kama Ayurveda",
     "price": 1195, "tags": ["ayurvedic", "hair fall"],
     "attributes": {"key_ingredient": "Indigo, Gotu Kola", "volume_ml": 100}},
    {"sku": "PER-001", "name": "Celeste Eau de Parfum", "brand": "Skinn by Titan",
     "price": 2195, "tags": ["floral", "gift"],
     "attributes": {"notes": "Floral, Fruity", "concentration": "EDP", "volume_ml": 50}},
    {"sku": "PER-002", "name": "Date Woman Eau de Parfum", "brand": "Bella Vita Organic",
     "price": 599, "tags": ["fruity", "budget"],
     "attributes": {"notes": "Fruity, Floral", "concentration": "EDP", "volume_ml": 100}},
]

# Fulfilment centres (made up for the case study): code -> city
WAREHOUSES = {"WH-MUM": "Mumbai", "WH-DEL": "Delhi NCR", "WH-BLR": "Bengaluru"}

# Stock: (sku, warehouse code, quantity, reorder level)
# Stock is LOW when quantity <= reorder level, and OUT when quantity is 0.
# OIL-001 is missing on purpose: it shows how the app handles missing data.
STOCK = [
    ("LIP-001", "WH-MUM", 140, 20), ("LIP-001", "WH-DEL", 60, 20),
    ("LIP-002", "WH-MUM", 220, 20), ("LIP-002", "WH-BLR", 90, 20),
    ("LIP-003", "WH-DEL", 9, 20),                                     # LOW
    ("FND-001", "WH-MUM", 10, 20), ("FND-001", "WH-BLR", 6, 20),      # LOW
    ("FND-002", "WH-DEL", 45, 20),
    ("CMP-001", "WH-BLR", 0, 15),                                     # OUT
    ("KAJ-001", "WH-MUM", 300, 50), ("KAJ-001", "WH-DEL", 180, 50),
    ("MAS-001", "WH-BLR", 70, 20),
    ("MOI-001", "WH-MUM", 85, 20), ("MOI-001", "WH-BLR", 40, 20),
    ("SER-001", "WH-MUM", 200, 30), ("SER-001", "WH-DEL", 150, 30),
    ("SER-002", "WH-BLR", 25, 20),
    ("SUN-001", "WH-DEL", 12, 20),                                    # LOW
    ("SUN-002", "WH-MUM", 160, 30), ("SUN-002", "WH-BLR", 110, 30),
    ("SHA-001", "WH-MUM", 90, 25),
    ("SHA-002", "WH-DEL", 130, 25), ("SHA-002", "WH-BLR", 75, 25),
    ("PER-001", "WH-MUM", 30, 10),
    ("PER-002", "WH-BLR", 4, 10),                                     # LOW
]

# =========================================================================
# 2. DATA FOR NEO4J
# =========================================================================

# The category tree as (category, parent). Top-level categories have parent None.
CATEGORIES = [
    ("Makeup", None), ("Lips", "Makeup"), ("Lipstick", "Lips"),
    ("Face", "Makeup"), ("Foundation", "Face"), ("Compact", "Face"),
    ("Eyes", "Makeup"), ("Kajal", "Eyes"), ("Mascara", "Eyes"),
    ("Skin", None), ("Face Care", "Skin"),
    ("Moisturizer", "Face Care"), ("Serum", "Face Care"), ("Sunscreen", "Face Care"),
    ("Hair", None), ("Hair Care", "Hair"), ("Shampoo", "Hair Care"), ("Hair Oil", "Hair Care"),
    ("Fragrance", None), ("Women's Fragrance", "Fragrance"), ("Perfume", "Women's Fragrance"),
]

# Which (lowest-level) category each product belongs to.
# Kept here and NOT in MongoDB: categorisation is Neo4j's job.
PRODUCT_CATEGORY = {
    "LIP-001": "Lipstick", "LIP-002": "Lipstick", "LIP-003": "Lipstick",
    "FND-001": "Foundation", "FND-002": "Foundation", "CMP-001": "Compact",
    "KAJ-001": "Kajal", "MAS-001": "Mascara",
    "MOI-001": "Moisturizer", "SER-001": "Serum", "SER-002": "Serum",
    "SUN-001": "Sunscreen", "SUN-002": "Sunscreen",
    "SHA-001": "Shampoo", "SHA-002": "Shampoo", "OIL-001": "Hair Oil",
    "PER-001": "Perfume", "PER-002": "Perfume",
}

# Product-to-product links. Stored with one direction, but our queries ignore direction.
BOUGHT_TOGETHER = [("FND-001", "CMP-001"), ("LIP-001", "KAJ-001"), ("SER-001", "SUN-002"),
                   ("MOI-001", "SUN-001"), ("SHA-001", "OIL-001")]
SIMILAR_TO = [("LIP-001", "LIP-002"), ("LIP-002", "LIP-003"), ("FND-001", "FND-002"),
              ("SER-001", "SER-002"), ("SUN-001", "SUN-002"), ("SHA-001", "SHA-002"),
              ("PER-001", "PER-002")]


# =========================================================================
# 3. LOADING FUNCTIONS
# =========================================================================

def seed_mongo():
    # Start fresh: delete both collections (no error if they don't exist yet).
    mongo_db.drop_collection("products")
    mongo_db.drop_collection("inventory")

    # --- products ---
    products = mongo_db["products"]
    products.create_index("sku", unique=True)   # no two products with the same SKU
    products.insert_many(PRODUCTS)

    # --- inventory ---
    # Create the collection WITH a validator: MongoDB itself will reject any
    # stock document whose quantity is missing, not a whole number, or below 0.
    mongo_db.create_collection("inventory", validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["sku", "warehouse", "quantity", "reorder_level"],
            "properties": {
                "sku": {"bsonType": "string"},
                "warehouse": {"bsonType": "object", "required": ["code", "city"]},
                "quantity": {"bsonType": "int", "minimum": 0},
                "reorder_level": {"bsonType": "int", "minimum": 0},
            },
        }
    })
    inventory = mongo_db["inventory"]
    # The same product can only appear once per warehouse.
    inventory.create_index([("sku", 1), ("warehouse.code", 1)], unique=True)
    inventory.insert_many([
        {"sku": sku, "warehouse": {"code": code, "city": WAREHOUSES[code]},
         "quantity": qty, "reorder_level": reorder}
        for sku, code, qty, reorder in STOCK
    ])

    print(f"MongoDB: {products.count_documents({})} products, "
          f"{inventory.count_documents({})} inventory documents")


def seed_neo4j():
    # execute_query runs one Cypher statement and returns (records, summary, keys).

    # Start fresh: delete every node and its relationships.
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")

    # Constraints: category names and product SKUs must be unique.
    # (IF NOT EXISTS = fine to run again; constraints survive the delete above.)
    neo4j_driver.execute_query(
        "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE")
    neo4j_driver.execute_query(
        "CREATE CONSTRAINT product_sku IF NOT EXISTS FOR (p:Product) REQUIRE p.sku IS UNIQUE")

    # Categories + tree. UNWIND turns the list into one row per category.
    # MERGE = "create it unless it already exists".
    neo4j_driver.execute_query("""
        UNWIND $rows AS row
        MERGE (c:Category {name: row.name})
        WITH c, row WHERE row.parent IS NOT NULL
        MERGE (parent:Category {name: row.parent})
        MERGE (c)-[:SUBCATEGORY_OF]->(parent)
    """, rows=[{"name": name, "parent": parent} for name, parent in CATEGORIES])

    # Products (only the SKU - details live in MongoDB) linked to their category.
    neo4j_driver.execute_query("""
        UNWIND $rows AS row
        MATCH (c:Category {name: row.category})
        MERGE (p:Product {sku: row.sku})
        MERGE (p)-[:IN_CATEGORY]->(c)
    """, rows=[{"sku": sku, "category": cat} for sku, cat in PRODUCT_CATEGORY.items()])

    # Product-to-product links. Cypher can't take a relationship TYPE as a
    # parameter, so there is one query per type.
    for rel_type, pairs in [("BOUGHT_TOGETHER", BOUGHT_TOGETHER), ("SIMILAR_TO", SIMILAR_TO)]:
        neo4j_driver.execute_query(f"""
            UNWIND $pairs AS pair
            MATCH (a:Product {{sku: pair[0]}}), (b:Product {{sku: pair[1]}})
            MERGE (a)-[:{rel_type}]->(b)
        """, pairs=[list(p) for p in pairs])

    # Count what we created, so we can see it worked.
    records, _, _ = neo4j_driver.execute_query("""
        RETURN COUNT { (:Category) } AS categories,
               COUNT { (:Product) } AS products,
               COUNT { ()-[:SUBCATEGORY_OF]->() } AS tree_links,
               COUNT { ()-[:IN_CATEGORY]->() } AS in_category,
               COUNT { ()-[:BOUGHT_TOGETHER]->() } AS bought_together,
               COUNT { ()-[:SIMILAR_TO]->() } AS similar_to
    """)
    counts = records[0].data()
    print("Neo4j:  ", ", ".join(f"{v} {k}" for k, v in counts.items()))
    return counts


def self_check(counts):
    """Small check that fails loudly if the data did not load as expected."""
    assert mongo_db["products"].count_documents({}) == len(PRODUCTS) == 18
    assert mongo_db["inventory"].count_documents({}) == len(STOCK)
    assert mongo_db["inventory"].count_documents({"sku": "OIL-001"}) == 0   # missing on purpose
    assert counts["categories"] == len(CATEGORIES)
    assert counts["products"] == 18 and counts["in_category"] == 18
    assert counts["bought_together"] == len(BOUGHT_TOGETHER)
    assert counts["similar_to"] == len(SIMILAR_TO)

    # The validator must reject negative stock.
    try:
        mongo_db["inventory"].insert_one(
            {"sku": "TEST", "warehouse": {"code": "WH-MUM", "city": "Mumbai"},
             "quantity": -5, "reorder_level": 10})
        raise AssertionError("negative quantity was accepted - validator not working!")
    except WriteError:
        print("Check:   MongoDB rejected a negative quantity (validator works)")

    # The unique constraint must reject a second category with the same name.
    try:
        neo4j_driver.execute_query("CREATE (:Category {name: 'Makeup'})")
        raise AssertionError("duplicate category was accepted - constraint not working!")
    except Neo4jError:
        print("Check:   Neo4j rejected a duplicate category (constraint works)")

    print("All checks passed.")


if __name__ == "__main__":
    seed_mongo()
    counts = seed_neo4j()
    self_check(counts)
