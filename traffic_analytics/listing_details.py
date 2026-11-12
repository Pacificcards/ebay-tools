"""Per-listing details that GetMyeBaySelling does not return, fetched once per listing via GetItem.

- category (PrimaryCategory)
- sport_or_game: item specific "Sport" (sports cards) or "Game" (TCG)
- grader / grade: for graded cards (ConditionID 2750) ONLY from eBay's condition
  descriptors (27501 = grader, 27502 = grade), mapped to short names ("PSA", "10").
  Item specifics are deliberately ignored here: sellers fill them with free/long text
  (e.g. "Professional Sports Authenticator (PSA)"). Ungraded cards get grade "Raw".

All of this is shown on the public eBay listing page.

Probe (read-only, prints aggregates only, writes nothing):
  python -m traffic_analytics.listing_details --probe
"""
import argparse
import os
import xml.etree.ElementTree as ET
from collections import Counter

import requests

TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}

# Bump when parse_details changes what it stores; sync_listings refetches every listing
# whose stored details_version differs (one GetItem each, ~10 min for ~750 listings).
DETAILS_VERSION = 2

GRADED_CONDITION_ID = "2750"
DESCRIPTOR_GRADER = "27501"
DESCRIPTOR_GRADE = "27502"

# eBay condition-descriptor value IDs for trading cards. Unknown IDs are left blank
# (and reported by --probe) rather than guessed.
GRADER_IDS = {
    "275010": "PSA", "275011": "BCCG", "275012": "BVG", "275013": "BGS", "275014": "CSG",
    "275015": "CGC", "275016": "SGC", "275017": "KSA", "275018": "GMA", "275019": "HGA",
    "2750110": "ISA", "2750111": "PCA", "2750112": "GSG", "2750113": "PGS", "2750114": "MNT",
    "2750115": "TAG", "2750116": "Rare Edition", "2750117": "RCG", "2750118": "PCG",
    "2750119": "Ace Grading", "2750120": "CGA", "2750121": "TCG", "2750122": "ARK",
    "2750123": "Other",
}
GRADE_IDS = {
    "275020": "10", "275021": "9.5", "275022": "9", "275023": "8.5", "275024": "8",
    "275025": "7.5", "275026": "7", "275027": "6.5", "275028": "6", "275029": "5.5",
    "2750210": "5", "2750211": "4.5", "2750212": "4", "2750213": "3.5", "2750214": "3",
    "2750215": "2.5", "2750216": "2", "2750217": "1.5", "2750218": "1",
    "2750219": "Authentic", "2750220": "Authentic Altered", "2750221": "Authentic - Trimmed",
    "2750222": "Authentic - Coloured",
}


def fetch_item_xml(token: str, listing_id: str) -> str | None:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{listing_id}</ItemID>
  <IncludeItemSpecifics>true</IncludeItemSpecifics>
  <OutputSelector>Item.PrimaryCategory</OutputSelector>
  <OutputSelector>Item.ConditionID</OutputSelector>
  <OutputSelector>Item.ConditionDescriptors</OutputSelector>
  <OutputSelector>Item.ItemSpecifics</OutputSelector>
</GetItemRequest>"""
    headers = {
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "1349",
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }
    response = requests.post(TRADING_API_URL, data=body.encode("utf-8"), headers=headers, timeout=60)
    if not response.ok:
        print(f"[listing_details] GetItem HTTP {response.status_code} for {listing_id}: {response.text[:200]}")
        return None
    return response.text


def _clean_grade(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        number = float(value)
        return f"{number:g}"  # "10.0" -> "10", "9.5" stays
    except ValueError:
        return value


def parse_details(xml_text: str) -> dict | None:
    """Return the detail fields, or None if the response has no category (bad/empty response)."""
    root = ET.fromstring(xml_text)
    cat_id = root.findtext(".//e:PrimaryCategory/e:CategoryID", namespaces=NS)
    if not cat_id:
        return None

    specifics: dict[str, str] = {}
    for nvl in root.findall(".//e:ItemSpecifics/e:NameValueList", namespaces=NS):
        name = (nvl.findtext("e:Name", namespaces=NS) or "").strip()
        values = [v.text.strip() for v in nvl.findall("e:Value", namespaces=NS) if v.text]
        if name and values:
            specifics[name.lower()] = ", ".join(values)

    descriptors: dict[str, str] = {}
    for d in root.findall(".//e:ConditionDescriptors/e:ConditionDescriptor", namespaces=NS):
        name = (d.findtext("e:Name", namespaces=NS) or "").strip()
        value = (d.findtext("e:Value", namespaces=NS) or "").strip()
        if name:
            descriptors[name] = value

    condition_id = root.findtext(".//e:Item/e:ConditionID", namespaces=NS)

    grader = grade = None
    if condition_id == GRADED_CONDITION_ID:
        grader = GRADER_IDS.get(descriptors.get(DESCRIPTOR_GRADER, ""))
        grade = _clean_grade(GRADE_IDS.get(descriptors.get(DESCRIPTOR_GRADE, "")))
    elif condition_id:
        grade = "Raw"

    return {
        "category_id": cat_id,
        "category_name": root.findtext(".//e:PrimaryCategory/e:CategoryName", namespaces=NS),
        "condition_id": condition_id,
        "sport_or_game": specifics.get("sport") or specifics.get("game"),
        "grader": grader,
        "grade": grade,
        "_descriptors": descriptors,
    }


def _probe() -> None:
    from shared.db import get_connection
    from shared.ebay_auth import get_access_token

    token = get_access_token(
        os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], os.environ["EBAY_REFRESH_TOKEN"]
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT listing_id FROM listing_metadata WHERE status != 'ended' ORDER BY listing_id")
            ids = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    combos, sports, unmapped, failed = Counter(), Counter(), Counter(), 0
    graded_examples = []
    for listing_id in ids:
        xml_text = fetch_item_xml(token, listing_id)
        d = parse_details(xml_text) if xml_text else None
        if not d:
            failed += 1
            continue
        cat = d["category_id"]
        combos[(cat, d["condition_id"], d["grader"], d["grade"])] += 1
        sports[(cat, d["sport_or_game"])] += 1
        if d["condition_id"] == GRADED_CONDITION_ID:
            for k, v in d["_descriptors"].items():
                known = (k == DESCRIPTOR_GRADER and v in GRADER_IDS) or (k == DESCRIPTOR_GRADE and v in GRADE_IDS)
                if not known:
                    unmapped[(k, v)] += 1
            if len(graded_examples) < 10:
                graded_examples.append((listing_id, d["grader"], d["grade"], d["_descriptors"]))

    print(f"[probe] {len(ids)} listings, {failed} failed")
    print("[probe] category | condition | grader | grade -> count")
    for k, n in combos.most_common():
        print(f"   {k} -> {n}")
    print("[probe] category | sport/game -> count")
    for k, n in sports.most_common():
        print(f"   {k} -> {n}")
    print(f"[probe] unmapped descriptors on graded listings: {dict(unmapped) or 'none'}")
    for ex in graded_examples:
        print(f"   graded example {ex}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    if parser.parse_args().probe:
        _probe()
