"""
inventory_sync.py - append posted eBay listings to the team's Google Sheet.

Called by ebay_lister.py after --mode live. Only listings that actually
posted (have an eBay ItemID) are written.

Setup (one time):
  pip install gspread
  1. Google Cloud console -> new project -> enable "Google Sheets API".
  2. IAM -> Service accounts -> create one -> Keys -> Add key -> JSON.
     Save it next to this file as inventory_key.json (keep it PRIVATE).
  3. Share the inventory sheet with the service account's email as Editor.
  4. Put the sheet id (the long id in the sheet URL between /d/ and /edit)
     in config.json as "sheet_id".

Manual use:
  python inventory_sync.py --test      # check the connection (writes nothing)
  python inventory_sync.py --retry     # push rows that failed to write earlier
"""
import json
import os
import sys
import time

# ---------------- config ----------------
from config import CFG
SHEET_ID = CFG["sheet_id"]           # the id between /d/ and /edit in the sheet link (set in config.json)
TAB_NAME = CFG["sheet_tab"]          # the tab name at the bottom of the sheet
if "/d/" in SHEET_ID:                # a full link was pasted: keep only the id
    SHEET_ID = SHEET_ID.split("/d/", 1)[1].split("/", 1)[0]
KEY_FILE = os.environ.get("INVENTORY_KEY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory_key.json"))
PENDING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory_pending.jsonl")
# Column order A..P, matching the sheet header row.
COLUMNS = ["Seller SKU", "UPC", "Brand", "Item Name", "Model Name", "Style Code",
           "Department", "Size", "Size System", "Color", "Manufacturer Colorway",
           "Upper Material", "Closure", "Quantity", "List Price", "eBay Ref / Shelf"]
LAST_COL = chr(ord("A") + len(COLUMNS) - 1)  # "P"
# ----------------------------------------


def _draft_key(custom_label):
    """'#101 2-A-1 AB1234-100 Size 9 J.D.' -> '#101 AB1234-100' (draft number + style code).
    Using both means another lister's row with the same draft number is never overwritten."""
    parts = custom_label.split() if custom_label else []
    if len(parts) >= 3 and parts[0].startswith("#"):
        return f"{parts[0]} {parts[2]}"
    return ""


def _shelf(custom_label):
    parts = custom_label.split()
    return parts[1] if len(parts) > 1 else ""


def _size(custom_label, listing):
    # Keep the human size from the label ("7Y", "6.5M/8W") rather than the bare eBay value.
    if " Size " in custom_label:
        rest = custom_label.split(" Size ", 1)[1].split()
        if rest:
            return rest[0]
    return listing.get("us_size", "")


def _model(listing):
    """'ASICS Gel-1130' -> 'Gel-1130' (sheet stores the model without the brand)."""
    model, brand = str(listing.get("model", "")), listing.get("brand", "")
    if brand and model.lower().startswith(brand.lower() + " "):
        return model[len(brand) + 1:]
    return model


def _retail(listing):
    """Column O = brand RETAIL price (not our eBay price). Uses the JSON 'retail' field.
    Old JSON files without it: eBay price + 20 when above the $74.99 floor, else blank
    (at the floor the real retail can't be worked out, so it's filled in by hand)."""
    r = listing.get("retail")
    if r not in (None, ""):
        return float(r)
    price = float(listing.get("price", 0) or 0)
    return round(price + 20, 2) if price > 74.99 else ""


def build_row(listing, item_id):
    label = listing["custom_label"]
    return [
        label,
        str(listing.get("upc", "")),            # text, keeps leading zeros
        listing.get("brand", ""),
        listing.get("title", ""),
        _model(listing),                        # text, so "480"/"9060" stay text
        listing.get("style_code", ""),
        listing.get("department", ""),
        _size(label, listing),
        "US",
        listing.get("color", ""),
        listing.get("manufacturer_colorway", ""),
        listing.get("upper_material", ""),
        listing.get("closure") or ("Slip On" if "slide" in str(listing.get("model", "")).lower() else "Lace Up"),
        int(listing.get("quantity", 1)),
        _retail(listing),
        f"{item_id} / {_shelf(label)}" if item_id else _shelf(label),
    ]


def _open_sheet():
    if not SHEET_ID:
        raise RuntimeError("sheet_id not set in config.json")
    import gspread
    gc = gspread.service_account(filename=KEY_FILE)
    return gc.open_by_key(SHEET_ID).worksheet(TAB_NAME)


def _write(ws, rows):
    """Append new rows; if a draft number is already in column A, update that row
    instead (covers cross-batch merges where quantity and shelves changed)."""
    col_a = ws.col_values(1)
    existing = {}
    for i, v in enumerate(col_a, start=1):
        k = _draft_key(v)
        if k:
            existing[k] = i
    new_rows, updated = [], 0
    for row in rows:
        r = existing.get(_draft_key(row[0]))
        if r:
            ws.update(range_name=f"A{r}:{LAST_COL}{r}", values=[row], value_input_option="RAW")
            updated += 1
        else:
            new_rows.append(row)
    if new_rows:
        # Write right under the LAST filled cell in column A. (Google's "append" stops at the
        # first blank row in the sheet, which put rows in the middle of the data.)
        start = len(col_a) + 1
        end = start + len(new_rows) - 1
        if end > ws.row_count:
            ws.add_rows(end - ws.row_count)
        ws.update(range_name=f"A{start}:{LAST_COL}{end}", values=new_rows, value_input_option="RAW")
        print(f"[inventory] new rows written to sheet rows {start}-{end}")
    return len(new_rows), updated


def _save_pending(rows):
    with open(PENDING, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _load_pending():
    if not os.path.exists(PENDING):
        return []
    with open(PENDING, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sync(posted):
    """posted: list of (listing_dict, ebay_item_id). Never raises: if Google is
    unreachable, rows are saved to inventory_pending.jsonl and retried next run."""
    rows = _load_pending() + [build_row(l, iid) for l, iid in posted if iid]
    if not rows:
        return
    for attempt in range(3):
        try:
            ws = _open_sheet()
            added, updated = _write(ws, rows)
            if os.path.exists(PENDING):
                os.remove(PENDING)
            print(f"[inventory] sheet updated: {added} added, {updated} updated")
            return
        except Exception as e:  # network, quota, auth
            err = e
            time.sleep(2 * (attempt + 1))
    # keep only the new rows in pending (old pending rows are already in the file)
    new_only = rows[len(_load_pending()):]
    _save_pending(new_only)
    print(f"[inventory] could not write to sheet ({err}); {len(rows)} row(s) saved to "
          f"{os.path.basename(PENDING)}, run 'python inventory_sync.py --retry'")


def test():
    try:
        ws = _open_sheet()
        head = ws.row_values(1)
        print(f"Connected to tab '{ws.title}' ({ws.row_count} rows).")
        print("Header row A..P:", head[:len(COLUMNS)])
        print("Column A last entry:", (ws.col_values(1) or ["(empty)"])[-1])
        if head[:len(COLUMNS)] != COLUMNS:
            print("NOTE: header names differ from the script's COLUMNS list. Writing still goes by "
                  "column position A..P, so check the order matches.")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        test()
    elif "--retry" in sys.argv:
        sync([])
    else:
        print(__doc__)
