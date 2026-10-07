"""Manager app text, Hindi (default) and English.

The manager picks a language with the switch in the app's top bar; the choice is a
cookie on that phone (`mg_lang`), like the attendant's large-text option, so no
database change is needed. Hindi is the default.

    STRINGS[key] = (hindi, english)

Templates call t('key', name=value) and routes call tr('key', ...). Placeholders use
{name}. What stays English on purpose: digits, ₹, units (L, kg), product codes
(HS, MS, X2, XG, CNG), customer and lube-product names, and everything the owner
sees (alerts). Keep the wording plain: the manager reads Hindi at least as easily
as English.
"""
from flask import request

COOKIE = "mg_lang"
MAX_AGE = 365 * 24 * 60 * 60

STRINGS = {
    # ── navigation / common ────────────────────────────────────────────────
    "nav_home": ("होम", "Home"),
    "nav_credit": ("उधार", "Credit"),
    "nav_lube": ("ल्यूब", "Lube"),
    "nav_expense": ("खर्च", "Expense"),
    "nav_payment": ("भुगतान", "Payment"),
    "back": ("वापस", "Back"),
    "sign_out": ("बाहर निकलें", "Sign out"),
    "switch_to": ("English", "हिंदी"),
    "switch_aria": ("Switch to English", "हिंदी में बदलें"),
    "optional": ("ज़रूरी नहीं", "optional"),

    # ── home ───────────────────────────────────────────────────────────────
    "greet_morning": ("सुप्रभात", "Good morning"),
    "greet_afternoon": ("नमस्ते", "Good afternoon"),
    "greet_evening": ("शुभ संध्या", "Good evening"),
    "greet_night": ("शुभ रात्रि", "Good night"),
    "today_tasks": ("आज के काम", "Today's tasks"),
    "task_shift": ("पिछली शिफ्ट की रीडिंग भरी गई", "Previous shift readings entered"),
    "task_shift_sub": ("{date} · 6 नोज़ल", "{date} · 6 nozzles"),
    "task_expense": ("आज का खर्च दर्ज हुआ", "Expenses logged today"),
    "task_paytm": ("Paytm रिपोर्ट अपलोड हुई", "Paytm report uploaded"),
    "task_paytm_sub": ("{date} की रिपोर्ट", "{date} report"),
    "pending_action": ("पक्का होना बाकी", "Pending action"),
    "bank_transfer_on": ("बैंक ट्रांसफर · {date}", "Bank transfer · {date}"),
    "verify": ("जाँच →", "Verify →"),
    "quick_log": ("जल्दी दर्ज करें", "Quick log"),
    "btn_credit_sale": ("उधार बिक्री", "Credit Sale"),
    "btn_lube_sale": ("ल्यूब बिक्री", "Lube Sale"),
    "btn_expense": ("खर्च", "Expense"),
    "btn_payment": ("भुगतान", "Payment"),

    # ── credit sale: pick the customer ─────────────────────────────────────
    "title_credit": ("उधार बिक्री", "Credit Sale"),
    "credit_pick_help": ("जिस ग्राहक ने उधार पर तेल लिया है, उसे चुनें।",
                         "Choose the customer who is taking fuel on credit."),
    "search_ph": ("नाम या गाड़ी नंबर खोजें", "Search name or vehicle number"),
    "search_aria": ("ग्राहक खोजें", "Search customers"),
    "owes": ("बकाया ₹{amount}", "Owes ₹{amount}"),
    "in_credit": ("जमा ₹{amount}", "In credit ₹{amount}"),
    "pct_of_limit": ("सीमा का {pct}%", "{pct}% of limit"),
    "limit_is": ("सीमा ₹{amount}", "limit ₹{amount}"),
    "no_customers": ("कोई चालू उधार ग्राहक नहीं है।", "No active credit customers."),
    "no_match": ("कोई ग्राहक नहीं मिला।", "No customer matches."),

    # ── credit sale: the form ──────────────────────────────────────────────
    "vehicle": ("गाड़ी", "Vehicle"),
    "select_vehicle": ("गाड़ी चुनें", "Select vehicle"),
    "no_number": ("बिना नंबर / कंटेनर", "No number / container"),
    "fuel": ("तेल", "Fuel"),
    "fuel_HS": ("HS डीज़ल", "HS Diesel"),
    "fuel_MS": ("MS पेट्रोल", "MS Petrol"),
    "fuel_X2": ("X2 प्रीमियम", "X2 Premium"),
    "fuel_XG": ("XG ग्रीन", "XG Green"),
    "fuel_CNG": ("CNG (किलो)", "CNG (kg)"),
    "entered_as": ("कैसे भरेंगे", "Entered as"),
    "amount_rs": ("रकम (₹)", "Amount (₹)"),
    "litres": ("लीटर", "Litres"),
    "kilograms": ("किलो (kg)", "Kilograms (kg)"),
    "amount_or_qty": ("रकम या मात्रा", "Amount or quantity"),
    "preview_choose": ("रेट देखने के लिए तेल चुनें।", "Choose the fuel to see the rate."),
    "preview_no_rate": ("{product} का अभी कोई रेट नहीं है। मालिक से पूछें।",
                        "{product} has no current rate. Ask the owner."),
    "rate_word": ("रेट", "Rate"),
    "record_credit": ("उधार बिक्री दर्ज करें", "Record credit sale"),

    # ── credit sale: done ──────────────────────────────────────────────────
    "credit_recorded": ("उधार बिक्री दर्ज हो गई", "Credit sale recorded"),
    "get_slip": ("ग्राहक से साइन की हुई पर्ची लें।", "Get the customer's signed slip."),
    "total": ("कुल", "Total"),
    "k_customer": ("ग्राहक", "Customer"),
    "k_vehicle": ("गाड़ी", "Vehicle"),
    "k_fuel": ("तेल", "Fuel"),
    "k_quantity": ("मात्रा", "Quantity"),
    "k_rate": ("रेट", "Rate"),
    "k_time": ("समय", "Time"),
    "k_entered_by": ("दर्ज करने वाला", "Entered by"),
    "record_another": ("एक और दर्ज करें", "Record another"),
    "back_home": ("होम पर जाएँ", "Back to home"),

    # ── payment ────────────────────────────────────────────────────────────
    "title_payment": ("भुगतान दर्ज करें", "Record Payment"),
    "customer": ("ग्राहक", "Customer"),
    "select_customer": ("ग्राहक चुनें", "Select customer"),
    "due": ("बकाया", "due"),
    "in_credit_lc": ("जमा", "in credit"),
    "amount": ("रकम", "Amount"),
    "payment_mode": ("भुगतान का तरीका", "Payment mode"),
    "mode_Cash": ("नकद", "Cash"),
    "mode_Cheque": ("चेक", "Cheque"),
    "mode_Bank Transfer": ("बैंक ट्रांसफर", "Bank Transfer"),
    "bank_note": ("बैंक ट्रांसफर तब गिना जाएगा जब मालिक बैंक में पैसे देखकर पक्का कर देंगे।",
                  "A bank transfer waits for the owner to confirm it in the bank."),
    "reference": ("रेफ़रेंस नंबर", "Reference number"),
    "ref_ph": ("चेक नंबर / UTR", "Cheque no. / UTR"),
    "notes": ("टिप्पणी", "Notes"),
    "record_payment": ("भुगतान दर्ज करें", "Record payment"),

    # ── expense ────────────────────────────────────────────────────────────
    "title_expense": ("खर्च दर्ज करें", "Log Expense"),
    "category": ("श्रेणी", "Category"),
    "description": ("विवरण", "Description"),
    "desc_ph": ("जैसे: जनरेटर का डीज़ल", "e.g. Diesel generator fuel"),
    "date": ("तारीख़", "Date"),
    "log_expense": ("खर्च दर्ज करें", "Log expense"),
    "cat_Staff": ("स्टाफ़", "Staff"),
    "cat_Maintenance": ("मरम्मत / रखरखाव", "Maintenance"),
    "cat_Utilities": ("बिजली-पानी आदि", "Utilities"),
    "cat_Supplies": ("सामान", "Supplies"),
    "cat_Misc": ("अन्य", "Misc"),
    "cat_EMI": ("किस्त (EMI)", "EMI"),
    "sub_category": ("उप-श्रेणी", "Sub-category"),
    "choose_sub": ("चुनें", "Choose"),
    "err_subcategory": ("उप-श्रेणी चुनें।", "Choose a sub-category."),
    "sub_Salary": ("वेतन", "Salary"),
    "sub_Advance": ("एडवांस", "Advance"),
    "sub_Other": ("अन्य", "Other"),
    "sub_Dispenser / Pump": ("डिस्पेंसर / पंप", "Dispenser / Pump"),
    "sub_Tank & Pipeline": ("टैंक और पाइपलाइन", "Tank & Pipeline"),
    "sub_Electrical": ("बिजली का काम", "Electrical"),
    "sub_Building": ("बिल्डिंग", "Building"),
    "sub_Electricity": ("बिजली बिल", "Electricity"),
    "sub_Water": ("पानी", "Water"),
    "sub_Phone / Internet": ("फ़ोन / इंटरनेट", "Phone / Internet"),
    "sub_Stationery": ("स्टेशनरी", "Stationery"),
    "sub_Cleaning": ("सफ़ाई", "Cleaning"),
    "sub_Printing": ("प्रिंटिंग", "Printing"),
    "sub_Tanker EMI": ("टैंकर किस्त", "Tanker EMI"),
    "sub_Loan EMI": ("लोन किस्त", "Loan EMI"),
    "sub_Vehicle EMI": ("गाड़ी किस्त", "Vehicle EMI"),

    # ── mock drill ─────────────────────────────────────────────────────────
    "title_drill": ("मॉक ड्रिल", "Mock Drill"),
    "task_drill": ("मॉक ड्रिल (हर 3 महीने में ज़रूरी)", "Mock drill (needed every 3 months)"),
    "drill_never": ("अभी तक दर्ज नहीं — जल्द करें", "None recorded yet — do one soon"),
    "drill_due_on": ("अगली ड्रिल: {date}", "Next drill due: {date}"),
    "drill_overdue": ("ड्रिल की तारीख़ निकल चुकी: {date}", "Overdue — was due {date}"),
    "drill_rule": ("मॉक ड्रिल हर {months} महीने में एक बार करना ज़रूरी है।",
                   "A mock drill must be done at least once every {months} months."),
    "drill_date": ("ड्रिल की तारीख़", "Drill date"),
    "drill_notes": ("टिप्पणी (कौन-कौन शामिल थे)", "Notes (who took part)"),
    "log_drill": ("मॉक ड्रिल दर्ज करें", "Record mock drill"),
    "drill_history": ("पिछली ड्रिल", "Previous drills"),
    "drill_logged": ("मॉक ड्रिल दर्ज: {date}", "Mock drill recorded: {date}"),
    "err_drill_date": ("सही तारीख़ डालें (आज या उससे पहले की)।", "Enter a valid date (today or earlier)."),

    # ── lube ───────────────────────────────────────────────────────────────
    "title_lube": ("ल्यूब बिक्री", "Lube Sale"),
    "product": ("उत्पाद", "Product"),
    "select_product": ("उत्पाद चुनें", "Select product"),
    "quantity": ("मात्रा", "Quantity"),
    "unit_price": ("एक का दाम", "Unit price"),
    "payment": ("भुगतान", "Payment"),
    "pay_cash": ("नकद", "Cash"),
    "pay_credit": ("उधार", "Credit"),
    "log_sale": ("बिक्री दर्ज करें", "Log sale"),

    # ── coming soon ────────────────────────────────────────────────────────
    "coming_soon": ("जल्द आ रहा है", "Coming soon"),
    "not_available": ("{feature} अभी उपलब्ध नहीं है।", "{feature} is not available yet."),
    "feature_invoice": ("बिल बनाना", "Generate Invoice"),

    # ── sign-out confirmation ──────────────────────────────────────────────
    "signout_q": ("बाहर निकलें?", "Sign out?"),
    "signout_yes": ("हाँ, बाहर निकलें", "Yes, sign out"),
    "signout_no": ("नहीं, वापस जाएँ", "No, go back"),

    # ── messages from the server ───────────────────────────────────────────
    "err_product": ("सही उत्पाद चुनें।", "Choose a valid product."),
    "err_qty": ("0 से ज़्यादा मात्रा भरें।", "Enter a valid quantity greater than zero."),
    "err_unit_price": ("0 से ज़्यादा दाम भरें।", "Enter a valid unit price greater than zero."),
    "err_mode": ("भुगतान का सही तरीका चुनें।", "Choose a valid payment mode."),
    "err_credit_customer": ("उधार बिक्री के लिए सही ग्राहक चुनें।", "Choose a valid customer for credit sale."),
    "lube_logged": ("ल्यूब बिक्री दर्ज: ₹{amount} — {product}", "Lube sale logged: ₹{amount} — {product}"),
    "err_amount": ("0 से ज़्यादा रकम भरें।", "Enter a valid amount greater than zero."),
    "err_category": ("सही श्रेणी चुनें।", "Choose a valid category."),
    "expense_logged": ("खर्च दर्ज: ₹{amount} — {category}", "Expense logged: ₹{amount} — {category}"),
    "err_customer": ("सही ग्राहक चुनें।", "Choose a valid customer."),
    "payment_recorded": ("भुगतान दर्ज: {customer} से ₹{amount}", "Payment recorded: ₹{amount} from {customer}"),
    "transfer_recorded": ("बैंक ट्रांसफर दर्ज हुआ — मालिक की पुष्टि का इंतज़ार",
                          "Bank transfer recorded — awaiting owner verification"),
    "no_vehicle": ("गाड़ी चुनें।", "Choose the vehicle."),
    "bad_product": ("तेल चुनें।", "Choose the fuel."),
    "no_rate": ("इस तेल का अभी कोई रेट नहीं है। मालिक से पूछें।", "There is no current rate for this fuel. Ask the owner."),
    "qty_not_positive": ("0 से ज़्यादा रकम या मात्रा भरें।", "Enter an amount or quantity greater than zero."),
    "qty_not_number": ("सही रकम या मात्रा भरें।", "Enter a valid amount or quantity."),
    "suspended": ("यह खाता बंद है। उधार देने से पहले मालिक से पूछें।",
                  "This account is suspended. Ask the owner before giving credit."),
}

_DAYS_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
_MONTHS_HI = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त",
              "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]


def lang() -> str:
    """'hi' (default) or 'en', from this phone's cookie."""
    return "en" if request.cookies.get(COOKIE) == "en" else "hi"


def tr(key: str, language: str | None = None, **fmt) -> str:
    hi, en = STRINGS[key]
    text = en if (language or lang()) == "en" else hi
    return text.format(**fmt) if fmt else text


def label(prefix: str, value: str) -> str:
    """Translated label for a stored value (cat_<category>, mode_<mode>), else the value itself."""
    key = f"{prefix}_{value}"
    return tr(key) if key in STRINGS else value


def fmt_date(d) -> str:
    if lang() == "en":
        return d.strftime("%d %b %Y").lstrip("0")
    return f"{d.day} {_MONTHS_HI[d.month - 1]} {d.year}"


def fmt_short(d) -> str:
    if lang() == "en":
        return d.strftime("%d %b").lstrip("0")
    return f"{d.day} {_MONTHS_HI[d.month - 1]}"


def weekday(d) -> str:
    return d.strftime("%A") if lang() == "en" else _DAYS_HI[d.weekday()]
