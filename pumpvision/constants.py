NOZZLE_LABEL_MAP = {
    "HSD 1": {"nozzle_no": 7,    "product": "HSD"},
    "HSD 2": {"nozzle_no": 16,   "product": "HSD"},
    "MS 1":  {"nozzle_no": 18,   "product": "MS"},
    "MS 2":  {"nozzle_no": 15,   "product": "MS"},
    "XP":    {"nozzle_no": 17,   "product": "XP"},
    "XG":    {"nozzle_no": 11,   "product": "XG"},
    "CNG":   {"nozzle_no": None, "product": "CNG"},
}

PRODUCT_LABELS = {
    "HSD": ["HSD 1", "HSD 2"],
    "MS":  ["MS 1",  "MS 2"],
    "XP":  ["XP"],
    "XG":  ["XG"],
    "CNG": ["CNG"],
}

ALL_PRODUCTS = ["HSD", "MS", "XP", "XG", "CNG"]
ALL_LABELS   = list(NOZZLE_LABEL_MAP.keys())

PUMP_TEST_NOZZLES = {
    7:  "HSD 1 (Nozzle 7)",
    11: "XG (Nozzle 11)",
    15: "MS 2 (Nozzle 15)",
    16: "HSD 2 (Nozzle 16)",
    17: "XP (Nozzle 17)",
    18: "MS 1 (Nozzle 18)",
}
