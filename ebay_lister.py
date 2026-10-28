"""
ebay_lister.py  -  posts shoe listings to eBay from a batch JSON file + photo folder.

Modes (always run them in this order for a new batch):
  preview : makes preview.html showing each draft # with its photos. Nothing sent to eBay.
  check   : asks eBay to VALIDATE every listing (VerifyAddFixedPriceItem). Nothing is listed, no fees.
  live    : actually lists the items (AddFixedPriceItem). Asks you to type YES first.

Examples (run inside the folder that holds this script):
  python ebay_lister.py examples/sample_batch.json --photos "path/to/photos" --mode preview
  python ebay_lister.py examples/sample_batch.json --photos "path/to/photos" --mode check
  python ebay_lister.py examples/sample_batch.json --photos "path/to/photos" --mode live --only 101

Photos: group 1 starts at the JSON's "first_photo" (e.g. IMG_0002), wherever it sits in the
folder; earlier photos are ignored. Use --first IMG_xxxx to override it.

Store settings (location, business policy names, sandbox vs production) live in config.json.
eBay credentials live in ebay_keys.txt. Neither file is committed.
"""
import argparse, csv, hashlib, json, os, sys, html
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
import requests

from config import CFG, API_URLS, missing_store_settings

try:
    import inventory_sync          # writes posted listings to a Google Sheet
except Exception as _e:            # missing file / gspread: listing still works
    inventory_sync = None
    _SYNC_ERR = _e

API_URL = API_URLS[CFG["environment"]]
COMPAT = "1193"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}

# ---- listing settings ----
POLICY_NAMES = CFG["policy_names"]
POSTAL_CODE, LOCATION, COUNTRY = CFG["postal_code"], CFG["location"], CFG["country"]
CONDITION_ID = "1500"          # New without box
PHOTOS_PER_PAIR = 5
PHOTO_EXT = (".jpg", ".jpeg", ".png")
# --------------------------

HERE = os.path.dirname(os.path.abspath(__file__))


def load_keys(path):
    missing = missing_store_settings()
    if missing:
        sys.exit("config.json is missing: " + ", ".join(missing) + " (copy config.example.json to config.json)")
    keys = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    for k in ("APP_ID", "DEV_ID", "CERT_ID", "USER_TOKEN"):
        if not keys.get(k):
            sys.exit(f"{os.path.basename(path)} is missing {k}")
    return keys


def call(keys, name, body, files=None):
    headers = {"X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT, "X-EBAY-API-CALL-NAME": name,
               "X-EBAY-API-SITEID": "0", "X-EBAY-API-APP-NAME": keys["APP_ID"],
               "X-EBAY-API-DEV-NAME": keys["DEV_ID"], "X-EBAY-API-CERT-NAME": keys["CERT_ID"]}
    xml = (f'<?xml version="1.0" encoding="utf-8"?><{name}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
           f'<RequesterCredentials><eBayAuthToken>{keys["USER_TOKEN"]}</eBayAuthToken></RequesterCredentials>'
           f'{body}</{name}Request>')
    if files:
        r = requests.post(API_URL, headers=headers, timeout=120,
                          files={"XML Payload": (None, xml, "text/xml"), "image": files})
    else:
        headers["Content-Type"] = "text/xml"
        r = requests.post(API_URL, headers=headers, data=xml.encode("utf-8"), timeout=120)
    root = ET.fromstring(r.content)
    ack = root.findtext("e:Ack", "", NS)
    msgs = [f'{e.findtext("e:SeverityCode", "", NS)}: {e.findtext("e:LongMessage", "", NS)}'
            for e in root.findall("e:Errors", NS)]
    return ack, root, msgs


def get_policy_ids(keys):
    ack, root, msgs = call(keys, "GetUserPreferences", "<ShowSellerProfilePreferences>true</ShowSellerProfilePreferences>")
    if ack not in ("Success", "Warning"):
        sys.exit("Could not read business policies:\n" + "\n".join(msgs))
    found, ids = [], {}
    for p in root.iter("{urn:ebay:apis:eBLBaseComponents}SupportedSellerProfile"):
        ptype, pname, pid = (p.findtext(f"e:{t}", "", NS) for t in ("ProfileType", "ProfileName", "ProfileID"))
        found.append(f"  {ptype:14} {pname}")
        want = POLICY_NAMES.get(ptype)
        if want and pname.strip().lower() == want.lower():
            ids[ptype] = pid
    if len(ids) != 3:
        sys.exit("Could not match all 3 policies. Policies on the account:\n" + "\n".join(found) +
                 "\nCopy the exact names into policy_names in config.json.")
    return ids


def photo_groups(folder, needed_groups, first_photo=None):
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(PHOTO_EXT))
    if first_photo:
        want = os.path.splitext(first_photo)[0].lower()
        stems = [os.path.splitext(f)[0].lower() for f in files]
        if want not in stems:
            sys.exit(f"first_photo {first_photo} not found in {folder}.")
        start = stems.index(want)
        files = files[start:]
        print(f"Counting photo groups from {files[0]} (skipping {start} earlier photo(s) in the folder).")
    if len(files) < needed_groups * PHOTOS_PER_PAIR:
        sys.exit(f"Folder has {len(files)} photos from the start point but this batch needs {needed_groups * PHOTOS_PER_PAIR}.")
    return [[os.path.join(folder, f) for f in files[i:i + PHOTOS_PER_PAIR]]
            for i in range(0, len(files), PHOTOS_PER_PAIR)]


def make_preview(listings, groups, out):
    rows = []
    for L in listings:
        pics = groups[L["photo_group"] - 1]
        imgs = "".join(f'<img src="file:///{html.escape(p.replace(os.sep, "/"))}" title="{html.escape(os.path.basename(p))}">' for p in pics)
        names = ", ".join(os.path.basename(p) for p in pics)
        rows.append(f'<div class="r"><b>#{L["draft"]}</b> {html.escape(L["title"])}<br><small>{names}</small><br>{imgs}</div>')
    with open(out, "w", encoding="utf-8") as f:
        f.write("<html><head><meta charset='utf-8'><style>body{font-family:Arial}.r{margin:10px 0;padding:8px;"
                "border-bottom:1px solid #ccc}img{height:120px;margin:4px}</style></head><body>"
                "<h2>Check: does each draft # show the right shoe?</h2>" + "".join(rows) + "</body></html>")


def upload_photo(keys, path):
    with open(path, "rb") as f:
        data = f.read()
    body = f"<PictureName>{escape(os.path.basename(path))}</PictureName><PictureSet>Supersize</PictureSet>"
    ack, root, msgs = call(keys, "UploadSiteHostedPictures", body, files=(os.path.basename(path), data, "image/jpeg"))
    url = root.findtext("e:SiteHostedPictureDetails/e:FullURL", "", NS)
    if not url:
        raise RuntimeError(f"Photo upload failed for {path}: " + " | ".join(msgs))
    return url


def specifics(L):
    pairs = [("Brand", L["brand"]), ("Model", L["model"]), ("Style Code", L["style_code"]),
             ("Department", L["department"]), ("Type", L.get("type", "Athletic")), ("Style", L.get("style", "Sneaker")),
             ("Color", L["color"]), ("Upper Material", L["upper_material"]), ("Closure", L.get("closure") or "Lace Up"),
             ("Shoe Shaft Style", L["shaft_style"]), ("US Shoe Size", L["us_size"]), ("Shoe Width", L.get("width") or "M"),
             ("UK Shoe Size", L["uk_size"]), ("EU Shoe Size", L["eu_size"]),
             ("Features", ["Cushioned", "Padded Collar", "Lightweight"]), ("Performance/Activity", "Casual")]
    if L.get("manufacturer_colorway"):
        pairs.append(("Manufacturer Color", L["manufacturer_colorway"]))
    out = ""
    for name, val in pairs:
        vals = val if isinstance(val, list) else [val]
        out += f"<NameValueList><Name>{escape(name)}</Name>" + "".join(f"<Value>{escape(str(v))}</Value>" for v in vals) + "</NameValueList>"
    return out


def listing_uuid(L):
    """Stable 32-hex id per listing. eBay refuses a second AddFixedPriceItem with the same
    UUID, so retrying after a dropped connection can't create a duplicate listing."""
    return hashlib.md5(f"{L['custom_label']}|{L.get('upc', '')}".encode("utf-8")).hexdigest().upper()


def item_xml(L, pics, pol):
    length, width, depth = L["package_in"]
    upc = str(L.get("upc", "")).strip()
    upc_xml = f"<ProductListingDetails><UPC>{escape(upc)}</UPC></ProductListingDetails>" if upc else ""
    return f"""<Item>
<UUID>{listing_uuid(L)}</UUID>
<Title>{escape(L['title'])}</Title>
<SKU>{escape(L['custom_label'])}</SKU>
<Description><![CDATA[{L['description']}]]></Description>
<PrimaryCategory><CategoryID>{L['category_id']}</CategoryID></PrimaryCategory>
<StartPrice currencyID="USD">{L['price']}</StartPrice>
<Quantity>{L['quantity']}</Quantity>
<ConditionID>{CONDITION_ID}</ConditionID>
<Country>{COUNTRY}</Country><Currency>USD</Currency><Site>US</Site>
<Location>{escape(LOCATION)}</Location><PostalCode>{escape(POSTAL_CODE)}</PostalCode>
<ListingDuration>GTC</ListingDuration><ListingType>FixedPriceItem</ListingType>
{upc_xml}
<ItemSpecifics>{specifics(L)}</ItemSpecifics>
<PictureDetails>{''.join(f'<PictureURL>{escape(u)}</PictureURL>' for u in pics)}</PictureDetails>
<SellerProfiles>
 <SellerPaymentProfile><PaymentProfileID>{pol['PAYMENT']}</PaymentProfileID></SellerPaymentProfile>
 <SellerReturnProfile><ReturnProfileID>{pol['RETURN_POLICY']}</ReturnProfileID></SellerReturnProfile>
 <SellerShippingProfile><ShippingProfileID>{pol['SHIPPING']}</ShippingProfileID></SellerShippingProfile>
</SellerProfiles>
<ShippingPackageDetails><MeasurementUnit>English</MeasurementUnit>
 <PackageDepth>{depth}</PackageDepth><PackageLength>{length}</PackageLength><PackageWidth>{width}</PackageWidth>
 <WeightMajor>{L['package_lb']}</WeightMajor><WeightMinor>0</WeightMinor></ShippingPackageDetails>
</Item>"""


def already_listed(log):
    done = set()
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["mode"] == "live" and r["result"] in ("Success", "Warning"):
                    done.add(int(r["draft"]))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--photos", required=True)
    ap.add_argument("--mode", choices=["preview", "check", "live"], default="preview")
    ap.add_argument("--only", help="comma-separated draft numbers, e.g. 101,102")
    ap.add_argument("--first", help="override first_photo from the JSON, e.g. IMG_0002")
    ap.add_argument("--keys", default=os.path.join(HERE, "ebay_keys.txt"))
    a = ap.parse_args()

    with open(a.batch, encoding="utf-8") as f:
        data = json.load(f)
    listings = data["listings"]
    if a.only:
        want = {int(x) for x in a.only.split(",")}
        listings = [L for L in listings if L["draft"] in want]
    first = a.first or data.get("first_photo")
    groups = photo_groups(a.photos, max(L["photo_group"] for L in listings), first)

    if a.mode == "preview":
        out = os.path.join(HERE, "preview.html")
        make_preview(listings, groups, out)
        print(f"Preview written: {out}\nOpen it, check every draft shows the right shoe, then run --mode check.")
        return

    keys = load_keys(a.keys)
    print(f"eBay environment: {CFG['environment']}")
    pol = get_policy_ids(keys)
    log = os.path.join(HERE, "results_log.csv")
    done = already_listed(log)
    if a.mode == "live":
        todo = [L for L in listings if L["draft"] not in done]
        if len(todo) < len(listings):
            print(f"Skipping {len(listings) - len(todo)} draft(s) already listed (see results_log.csv).")
        listings = todo
        if input(f"About to LIST {len(listings)} item(s) on eBay ({CFG['environment']}). Type YES to continue: ").strip() != "YES":
            sys.exit("Cancelled.")

    posted = []   # (listing, eBay item id) for every live listing that succeeded
    new_log = not os.path.exists(log)
    with open(log, "a", newline="", encoding="utf-8") as lf:
        w = csv.writer(lf)
        if new_log:
            w.writerow(["draft", "mode", "result", "item_id", "messages"])
        call_name = "AddFixedPriceItem" if a.mode == "live" else "VerifyAddFixedPriceItem"
        for L in listings:
            paths = groups[L["photo_group"] - 1]
            if a.mode == "check":
                paths = paths[:1]   # one photo is enough to validate; saves time
            try:
                pics = [upload_photo(keys, p) for p in paths]
                ack, root, msgs = call(keys, call_name, item_xml(L, pics, pol))
                item_id = root.findtext("e:ItemID", "", NS)
            except Exception as ex:
                ack, item_id, msgs = "Failure", "", [str(ex)]
            print(f"#{L['draft']}: {ack} {item_id}")
            for m in msgs:
                print("    " + m)
            w.writerow([L["draft"], a.mode, ack, item_id, " | ".join(msgs)])
            lf.flush()
            if a.mode == "live" and ack in ("Success", "Warning") and item_id:
                posted.append((L, item_id))
    print(f"Done. Results saved to {log}")

    # ---- inventory sheet (live mode only; never stops the run) ----
    if a.mode == "live":
        if inventory_sync is None:
            print(f"[inventory] sheet NOT updated ({_SYNC_ERR}). Fix, then rows can be added later.")
        else:
            try:
                inventory_sync.sync(posted)
            except Exception as ex:
                print(f"[inventory] sheet update failed: {ex}")


if __name__ == "__main__":
    main()
