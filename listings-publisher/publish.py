"""Publish ungraded sports trading card listings from a Google Sheet + local image folder."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# listings-publisher has a hyphen so it can't be imported as a package;
# add both the repo root (for shared.*) and this dir (for sibling modules)
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import ebay_api
import images
import sheets

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: missing environment variable {key}")
        sys.exit(1)
    return val


_SPORT_TO_LEAGUE = {
    "baseball": "Major League Baseball (MLB)",
    "football": "National Football League (NFL)",
    "basketball": "National Basketball Association (NBA)",
}


def _build_aspects(row: dict) -> dict:
    """Build eBay product.aspects dict from sheet row. All values must be string arrays."""
    aspects = {
        "Sport": [row["Sport"]],
        "Player/Athlete": [row["Player/Athlete"]],
        "Manufacturer": [row["Manufacturer"]],
        "Graded": ["No"],
    }
    league = _SPORT_TO_LEAGUE.get(row.get("Sport", "").lower())
    if league:
        aspects["League"] = [league]
    if row.get("Set"):
        aspects["Set"] = [str(row["Set"])]
    if row.get("Year"):
        aspects["Season"] = [str(row["Year"])]
    if row.get("Team"):
        aspects["Team"] = [str(row["Team"])]
    if row.get("Card Number"):
        aspects["Card Number"] = [str(row["Card Number"])]
    if row.get("Features"):
        # Comma-separated in sheet → array (e.g. "Rookie, Autographed" → ["Rookie", "Autographed"])
        features = [f.strip() for f in str(row["Features"]).split(",") if f.strip()]
        if features:
            aspects["Features"] = features
    if row.get("Parallel/Variety"):
        aspects["Parallel/Variety"] = [str(row["Parallel/Variety"])]
    return aspects


def _auto_title(row: dict) -> str:
    """Generate a listing title from card attributes when Title column is blank."""
    parts = []
    if row.get("Year"):
        parts.append(str(row["Year"]))
    if row.get("Manufacturer"):
        parts.append(str(row["Manufacturer"]))
    if row.get("Set"):
        parts.append(str(row["Set"]))
    if row.get("Player/Athlete"):
        parts.append(str(row["Player/Athlete"]))
    if row.get("Card Number"):
        parts.append(f"#{row['Card Number']}")
    if row.get("Sport"):
        parts.append(str(row["Sport"]))
    parts.append("Card")
    return " ".join(parts)


def main(image_folder: str | None = None):
    client_id = _require("EBAY_CLIENT_ID")
    client_secret = _require("EBAY_CLIENT_SECRET")
    refresh_token_write = _require("EBAY_REFRESH_TOKEN_WRITE")
    sheet_id = _require("LISTINGS_SHEET_ID")
    creds_path = os.environ.get("GOOGLE_CREDS_PATH", "pl/credentials/service_account.json")
    image_folder_str = image_folder or (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("IMAGE_FOLDER")

    default_return_policy = os.environ.get("EBAY_RETURN_POLICY_ID", "")
    default_payment_policy = os.environ.get("EBAY_PAYMENT_POLICY_ID", "")
    merchant_location_key = os.environ.get("EBAY_MERCHANT_LOCATION_KEY", "")

    print("Reading listings from Google Sheet...")
    all_pending = sheets.read_pending(sheet_id, creds_path)
    reprice_rows, ambiguous_rows = sheets.read_reprice(sheet_id, creds_path)

    # If no image folder was provided, skip new listings (reprice-only run)
    listings = all_pending if image_folder_str else []
    if all_pending and not image_folder_str:
        print(f"  NOTE: {len(all_pending)} pending listing(s) skipped — no photo folder provided.")

    if not listings and not reprice_rows:
        print("No pending listings and no REPRICE rows found.")
        if ambiguous_rows:
            for row in ambiguous_rows:
                status = str(row.get("Status", "")).strip()
                print(f"  NOTE: Row {row['_row_index']} has Listing ID but Status '{status}' — set to REPRICE to update price/quantity.")
        return

    for row in ambiguous_rows:
        status = str(row.get("Status", "")).strip()
        print(f"  WARNING: Row {row['_row_index']} has Listing ID but Status '{status}' — set to REPRICE to update price/quantity. Skipping.")

    # Validate required columns for new listings
    if listings:
        for row in listings:
            missing = [f for f in ("Sport", "Player/Athlete", "Manufacturer") if not row.get(f)]
            if missing:
                print(f"ERROR: Row {row['_row_index']} is missing required columns: {', '.join(missing)}")
                sys.exit(1)

    # Image scanning — only needed when publishing new listings
    groups: list = []
    titles: list = []
    per_listing: int | None = None
    all_images: list = []
    if listings:
        if not image_folder_str:
            print("ERROR: provide the image folder as an argument or set IMAGE_FOLDER in .env")
            print("  Usage: python listings-publisher/publish.py /path/to/photos")
            sys.exit(1)
        image_folder_path = Path(image_folder_str)

        try:
            per_listing = int(input("How many photos per listing for this batch? ").strip())
            if per_listing < 1:
                raise ValueError
        except ValueError:
            print("ERROR: enter a whole number (e.g. 2)")
            sys.exit(1)

        print(f"Scanning images in {image_folder_path}...")
        all_images = images.scan_and_sort(image_folder_path)
        groups = images.group(all_images, per_listing)

        if len(groups) != len(listings):
            expected = len(listings) * per_listing
            print(
                f"ERROR: {len(all_images)} images ÷ {per_listing} per listing = {len(groups)} groups, "
                f"but sheet has {len(listings)} pending listings.\n"
                f"Expected exactly {expected} images."
            )
            sys.exit(1)

        titles = [row.get("Title") or _auto_title(row) for row in listings]

        errors = []
        for i, (row, title) in enumerate(zip(listings, titles), 1):
            if len(title) > 80:
                errors.append(
                    f"  Row {row['_row_index']} title is {len(title)} chars (max 80):\n"
                    f"    \"{title}\""
                )
        if errors:
            print("ERROR: the following titles exceed 80 characters — shorten them in the sheet or\n"
                  "add a manual Title to override the auto-generated one:\n")
            print("\n".join(errors))
            sys.exit(1)

    # Preview
    print()
    print("─" * 60)
    if listings:
        print(f"  NEW LISTINGS — {len(listings)} listing(s), {per_listing} photo(s) each")
        print("─" * 60)
        for i, (row, photo_group, title) in enumerate(zip(listings, groups, titles), 1):
            photo_names = ", ".join(p.name for p in photo_group)
            print(f"\n  #{i}  {title}")
            print(f"       ${row['Price']}  ·  {row.get('Condition', '')}  ·  {row.get('Sport', '')}")
            print(f"       Photos: {photo_names}")
        print()
        print(f"  {len(all_images)} photos ÷ {per_listing} per listing = {len(listings)} listings ✓")

    if reprice_rows:
        if listings:
            print()
        print(f"  REPRICE UPDATES — {len(reprice_rows)} row(s)")
        print("─" * 60)
        for row in reprice_rows:
            price_str = str(row.get("Price", "")).strip()
            qty_str = str(row.get("Quantity", "")).strip()
            changes = []
            if price_str:
                changes.append(f"${price_str}")
            if qty_str:
                changes.append(f"qty {qty_str}")
            title_preview = (row.get("Title") or _auto_title(row))[:50]
            print(f"  Row {row['_row_index']}: \"{title_preview}\" → {', '.join(changes) or '(nothing to update)'}")

    print("─" * 60)
    print()

    confirm = input("Proceed? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    print("\nAuthenticating with eBay...")
    token = ebay_api.get_write_token(client_id, client_secret, refresh_token_write)

    # Publish new listings
    if listings:
        print()
        for i, (row, photo_group, title) in enumerate(zip(listings, groups, titles), 1):
            sku = row.get("SKU") or ebay_api.auto_sku(row["_row_index"], title)
            row_idx = row["_row_index"]

            print(f"[{i}/{len(listings)}] {title}")

            try:
                fulfillment_policy_id = str(row.get("Shipping Policy ID", "")).strip()
                return_policy_id = str(row.get("Return Policy ID", "")).strip() or default_return_policy
                payment_policy_id = str(row.get("Payment Policy ID", "")).strip() or default_payment_policy

                if not fulfillment_policy_id:
                    raise ValueError("Shipping Policy ID is required but missing from this row")

                def _float_or_none(key: str):
                    val = str(row.get(key, "")).strip()
                    return float(val) if val else None

                package_weight_oz = _float_or_none("Weight (oz)")
                package_length_in = _float_or_none("Length (in)")
                package_width_in = _float_or_none("Width (in)")
                package_height_in = _float_or_none("Height (in)")
                best_offer = str(row.get("Best Offer", "")).strip().lower() in ("y", "yes", "true", "1")

                print(f"         Uploading {len(photo_group)} photo(s)...")
                image_urls = [images.upload(path, token) for path in photo_group]

                print("         Publishing to eBay...")
                listing_id = ebay_api.create_and_publish(
                    token=token,
                    sku=sku,
                    title=title,
                    description=str(row.get("Description", "")),
                    aspects=_build_aspects(row),
                    condition=str(row.get("Condition", "near mint or better")),
                    price=float(row["Price"]),
                    qty=int(row.get("Quantity") or 1),
                    category_id=str(row.get("Category ID", "")),
                    image_urls=image_urls,
                    fulfillment_policy_id=fulfillment_policy_id,
                    payment_policy_id=payment_policy_id,
                    return_policy_id=return_policy_id,
                    merchant_location_key=merchant_location_key,
                    package_weight_oz=package_weight_oz,
                    package_length_in=package_length_in,
                    package_width_in=package_width_in,
                    package_height_in=package_height_in,
                    best_offer_enabled=best_offer,
                )

                sheets.write_result(sheet_id, creds_path, row_idx, listing_id, "published", sku=sku)
                print(f"         ✓ Listed — ID {listing_id}")

            except Exception as e:
                sheets.write_result(sheet_id, creds_path, row_idx, "", f"error: {e}")
                print(f"         ✗ FAILED — {e}")

    # Reprice existing listings
    if reprice_rows:
        # Rows with SKU already in sheet: one offer lookup each (fast)
        # Rows with blank SKU (legacy): build a full listing→offer map (slow, one-time)
        blank_sku_rows = [r for r in reprice_rows if not str(r.get("SKU", "")).strip()]
        offer_map: dict = {}
        if blank_sku_rows:
            blank_ids = {str(r.get("Listing ID", "")).strip() for r in blank_sku_rows}
            print(f"\nLooking up offer info for {len(blank_ids)} legacy listing(s) (no SKU stored)...")
            offer_map = ebay_api.build_listing_offer_map(token, blank_ids)

        updates = []
        for row in reprice_rows:
            listing_id = str(row.get("Listing ID", "")).strip()
            row_idx = row["_row_index"]
            sku = str(row.get("SKU", "")).strip()

            if sku:
                # Fast path: SKU in sheet, look up offer directly
                try:
                    lookup = ebay_api.get_offer_for_sku(token, sku)
                except Exception as e:
                    sheets.write_result(sheet_id, creds_path, row_idx, listing_id, f"error: {e}")
                    print(f"  ✗ Row {row_idx}: {e}")
                    continue
            else:
                lookup = offer_map.get(listing_id)
                if not lookup:
                    err = f"no offer found for Listing ID {listing_id}"
                    sheets.write_result(sheet_id, creds_path, row_idx, listing_id, f"error: {err}")
                    print(f"  ✗ Row {row_idx}: {err}")
                    continue

            price_str = str(row.get("Price", "")).strip()
            qty_str = str(row.get("Quantity", "")).strip()
            if not price_str and not qty_str:
                print(f"  ✗ Row {row_idx}: no Price or Quantity to update — skipping")
                continue
            updates.append({
                "sku": lookup["sku"],
                "offer_id": lookup["offer_id"],
                "price": float(price_str) if price_str else None,
                "quantity": int(qty_str) if qty_str else None,
                "_row_index": row_idx,
                "_listing_id": listing_id,
            })

        for batch_start in range(0, len(updates), 25):
            batch = updates[batch_start:batch_start + 25]
            results = ebay_api.bulk_update_price_quantity(token, batch)
            result_by_sku = {r["sku"]: r for r in results}
            for u in batch:
                result = result_by_sku.get(u["sku"])
                row_idx = u["_row_index"]
                if result and result["success"]:
                    sheets.write_result(sheet_id, creds_path, row_idx, u["_listing_id"], "published", sku=u["sku"])
                    print(f"  ✓ Row {row_idx} updated")
                else:
                    err = result["error"] if result else "no response"
                    sheets.write_result(sheet_id, creds_path, row_idx, u["_listing_id"], f"error: {err}", sku=u["sku"])
                    print(f"  ✗ Row {row_idx}: {err}")

    print("\nDone.")


if __name__ == "__main__":
    main()
