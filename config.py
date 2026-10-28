"""
config.py - loads store settings from config.json (never committed).

Copy config.example.json to config.json and fill in your own values.
Set LISTER_CONFIG to use a config file somewhere else.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("LISTER_CONFIG", os.path.join(HERE, "config.json"))

DEFAULTS = {
    "environment": "sandbox",          # "sandbox" for testing, "production" for a real store
    "postal_code": "",
    "location": "",
    "country": "US",
    "policy_names": {"PAYMENT": "", "SHIPPING": "", "RETURN_POLICY": ""},
    "sheet_id": "",
    "sheet_tab": "Inventory",
}

API_URLS = {
    "sandbox": "https://api.sandbox.ebay.com/ws/api.dll",
    "production": "https://api.ebay.com/ws/api.dll",
}


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    if cfg["environment"] not in API_URLS:
        raise ValueError(f"config.json: environment must be one of {list(API_URLS)}")
    return cfg


CFG = load()


def missing_store_settings():
    """Names of settings needed to talk to eBay that are still blank."""
    gaps = [k for k in ("postal_code", "location") if not CFG.get(k)]
    gaps += [f"policy_names.{k}" for k, v in CFG["policy_names"].items() if not v]
    return gaps
