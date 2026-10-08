"""
xml_builder.py - turns the database results from db.search() into XML.

Three steps:
  1. mongo_to_xml()  - the MongoDB data (catalog + stock)  -> one XML piece
  2. neo4j_to_xml()  - the Neo4j data (category + related) -> another XML piece
  3. merge_xml()     - join the two pieces by SKU into ONE document
Then validate() checks the merged document against merchandise.xsd.

This file does not talk to the databases itself; it only reads the dict that
db.search() returns. So it can be tested with made-up data too.

Run it on its own to test:
  python xml_builder.py LIP-001    -> search, then print the 3 XML documents + validation
  python xml_builder.py --check    -> small self-test
"""
import copy
import sys
from pathlib import Path

from lxml import etree

# Load the XSD once, when this file is first imported.
# XMLSchema(...) turns the rulebook into an object that can validate documents.
SCHEMA_PATH = Path(__file__).resolve().parent / "merchandise.xsd"
schema = etree.XMLSchema(etree.parse(str(SCHEMA_PATH)))


def as_text(value):
    """XML holds only text. Python's True prints as 'True', but XML wants 'true'."""
    if isinstance(value, bool):          # check bool first (in Python, True is also an int)
        return "true" if value else "false"
    return str(value)


def to_string(element):
    """Element -> readable (indented) text. encoding="unicode" keeps letters like 'é'."""
    return etree.tostring(element, pretty_print=True, encoding="unicode")


# =========================================================================
# STEP 1: MongoDB -> XML
# =========================================================================

def mongo_to_xml(products, stock):
    """
    products = list of product dicts from MongoDB "products"
    stock    = list of stock dicts from MongoDB "inventory" (with a status added)
    Returns <mongodb_result> holding one <product sku="..."> per product.
    """
    root = etree.Element("mongodb_result")

    # Group the stock rows by SKU, so each product can find its own rows.
    stock_by_sku = {}
    for row in stock:
        stock_by_sku.setdefault(row["sku"], []).append(row)

    for doc in products:
        product = etree.SubElement(root, "product", sku=doc["sku"])
        etree.SubElement(product, "name").text = doc["name"]
        etree.SubElement(product, "brand").text = doc["brand"]
        etree.SubElement(product, "price", currency="INR").text = as_text(doc["price"])

        tags = etree.SubElement(product, "tags")
        for tag in doc["tags"]:
            etree.SubElement(tags, "tag").text = tag

        # The attributes differ for every product, so the attribute's name
        # goes in an XML attribute (name="shade") instead of the tag name.
        attributes = etree.SubElement(product, "attributes")
        for name, value in doc["attributes"].items():
            etree.SubElement(attributes, "attribute", name=name).text = as_text(value)

        # Stock: always add <stock>. A product with no stock rows gets total="0"
        # and no <warehouse> children (e.g. OIL-001).
        rows = stock_by_sku.get(doc["sku"], [])
        stock_el = etree.SubElement(product, "stock",
                                    total=str(sum(row["quantity"] for row in rows)))
        for row in rows:
            etree.SubElement(stock_el, "warehouse",
                             code=row["warehouse"]["code"],
                             city=row["warehouse"]["city"],
                             quantity=str(row["quantity"]),
                             reorder_level=str(row["reorder_level"]),
                             status=row["status"])
    return root


# =========================================================================
# STEP 2: Neo4j -> XML
# =========================================================================

def neo4j_to_xml(graph):
    """
    graph = list of dicts from Neo4j: {"sku", "path": [...], "related": [...]}
    Returns <neo4j_result> holding one <product sku="..."> per row.
    """
    root = etree.Element("neo4j_result")
    for row in graph:
        product = etree.SubElement(root, "product", sku=row["sku"])

        path = etree.SubElement(product, "category_path")
        for name in row["path"]:                       # e.g. Makeup, Lips, Lipstick
            etree.SubElement(path, "category").text = name

        related = etree.SubElement(product, "related")
        for other in row["related"]:
            etree.SubElement(related, "item", sku=other["sku"], type=other["type"])
    return root


# =========================================================================
# STEP 3: merge the two pieces by SKU
# =========================================================================

def merge_xml(mongo_xml, neo4j_xml, query, mode):
    """
    Combine both pieces into <merchandise>. Products with the same SKU are
    joined: the MongoDB parts first, then the Neo4j parts (the order the XSD wants).
    If a SKU is in only one piece (the other database failed), it is kept
    with just the parts we have.
    """
    merged = {}                                   # sku -> the combined <product>
    for piece in (mongo_xml, neo4j_xml):          # MongoDB first, then Neo4j
        for product in piece:
            sku = product.get("sku")
            if sku not in merged:
                merged[sku] = etree.Element("product", sku=sku)
            for child in product:
                # deepcopy: an element can only live in one place in a tree,
                # so we copy it instead of moving it out of the original piece.
                merged[sku].append(copy.deepcopy(child))

    # Strip characters XML cannot hold (the query is whatever the user typed).
    query = "".join(ch for ch in query if ch.isprintable())

    root = etree.Element("merchandise", query=query, mode=mode, count=str(len(merged)))
    for sku in sorted(merged):                    # same order every time
        root.append(merged[sku])
    return root


# =========================================================================
# VALIDATION + THE WHOLE JOB
# =========================================================================

def validate(document):
    """Check a document against merchandise.xsd. Returns (is_valid, list of error messages)."""
    is_valid = schema.validate(document)
    errors = [f"line {e.line}: {e.message}" for e in schema.error_log]
    return is_valid, errors


def build(result):
    """
    Take the dict returned by db.search() and return the three XML documents
    (as text) plus the validation outcome.
    """
    mongo_xml = mongo_to_xml(result["products"], result["stock"])
    neo4j_xml = neo4j_to_xml(result["graph"])
    merged = merge_xml(mongo_xml, neo4j_xml, result["query"], result["mode"])
    is_valid, errors = validate(merged)
    return {"mongodb_xml": to_string(mongo_xml),
            "neo4j_xml": to_string(neo4j_xml),
            "merged_xml": to_string(merged),
            "valid": is_valid,
            "errors": errors}


def self_check():
    """Small test of the XML logic. The search tests need the data from seed.py."""
    from db import search                      # imported here so the rest works without databases

    # --- one SKU: both databases joined ---
    out = build(search("LIP-001"))
    assert out["valid"], out["errors"]
    doc = etree.fromstring(out["merged_xml"])
    assert doc.get("mode") == "sku" and doc.get("count") == "1"
    product = doc.find("product")
    assert product.findtext("brand") == "Maybelline New York"                      # from MongoDB
    assert [c.text for c in product.find("category_path")] == ["Makeup", "Lips", "Lipstick"]  # Neo4j
    assert product.find("stock").get("total") == "200"
    assert len(product.find("stock")) == 2                                         # 2 warehouses
    assert product.find("attributes/attribute[@name='shade']").text == "Pioneer"
    assert product.find("related/item[@sku='KAJ-001']").get("type") == "BOUGHT_TOGETHER"
    assert "Lakmé" in build(search("LIP-003"))["merged_xml"]                       # accents survive

    # --- a category: many products, every one valid ---
    out = build(search("Makeup"))
    assert out["valid"], out["errors"]
    assert etree.fromstring(out["merged_xml"]).get("count") == "8"

    # --- missing data is still valid ---
    oil = etree.fromstring(build(search("OIL-001"))["merged_xml"]).find("product")
    assert oil.find("stock").get("total") == "0" and len(oil.find("stock")) == 0   # no stock rows
    mas = etree.fromstring(build(search("MAS-001"))["merged_xml"]).find("product")
    assert mas.find("attributes/attribute[@name='waterproof']").text == "true"     # not "True"

    nothing = build(search("xyz"))
    assert nothing["valid"] and etree.fromstring(nothing["merged_xml"]).get("count") == "0"

    # Neo4j failed: pretend the graph came back empty. MongoDB data must still be valid.
    r = search("LIP-001")
    r["graph"] = []
    out = build(r)
    assert out["valid"], out["errors"]
    assert etree.fromstring(out["merged_xml"]).find("product/category_path") is None

    # --- the XSD must reject bad documents ---
    bad = etree.fromstring(build(search("LIP-001"))["merged_xml"])
    bad.find("product/stock/warehouse").set("status", "BROKEN")        # not OK/LOW/OUT
    assert not validate(bad)[0]
    bad = etree.fromstring(build(search("LIP-001"))["merged_xml"])
    bad.find("product").set("sku", "lip1")                              # wrong SKU pattern
    assert not validate(bad)[0]
    bad = etree.fromstring(build(search("Lipstick"))["merged_xml"])
    bad.findall("product")[1].set("sku", "LIP-001")                     # duplicate SKU
    assert not validate(bad)[0]
    print("All checks passed.")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] == "--check":
        self_check()
    else:
        from db import search, print_trace
        result = search(" ".join(sys.argv[1:]))
        out = build(result)
        print("=== MongoDB XML ===\n" + out["mongodb_xml"])
        print("=== Neo4j XML ===\n" + out["neo4j_xml"])
        print("=== Merged XML ===\n" + out["merged_xml"])
        print("Valid against merchandise.xsd:", out["valid"])
        for error in out["errors"]:
            print("  ", error)
        print()
        print_trace(result["trace"])
