"""Hindi strings for the attendant app.

Why this file exists
--------------------
Pumpvision's attendants are Hindi-speaking, Hindi-reading rural staff. Every
label, instruction, tab name, and button in the *attendant* app is shown in
Hindi. The owner and manager apps stay in English — they are not translated
here.

What stays in English / Roman on purpose
----------------------------------------
- Digits and numbers (meter readings, amounts, litres, dates)
- Product codes: HS, MS, X2, XG, CNG  (attendants read these directly)
- Machine codes: DU, RO number, nozzle names (HS1, MS2, …), IDs
- ₹ and unit letters L / kg

Everything else is here, in ONE place, so a native speaker can review and
correct the wording without touching any HTML. Change a value here and it
updates everywhere the key is used.

Usage
-----
Templates:  {{ hi.close_shift }}          (registered as a Jinja global `hi`)
Routes:     from .i18n import HI; flash(HI["reading_saved"].format(product=p))
"""

HI = {
    # ── Greetings (home) ────────────────────────────────────────────────
    "good_morning": "सुप्रभात",
    "good_afternoon": "नमस्ते",
    "good_evening": "शुभ संध्या",
    "good_night": "शुभ रात्रि",

    # ── Bottom-nav tabs ─────────────────────────────────────────────────
    "nav_home": "होम",
    "nav_activity": "गतिविधि",
    "nav_profile": "प्रोफ़ाइल",
    "nav_shift": "शिफ्ट",

    # ── Common actions / words ──────────────────────────────────────────
    "back": "वापस",
    "edit": "बदलें",
    "done": "हो गया",
    "active": "चालू",
    "save": "सेव करें",
    "notifications": "सूचनाएँ",
    "opening": "शुरुआती रीडिंग",
    "closing": "आखिरी रीडिंग",
    "opening_short": "शुरुआती",
    "closing_short": "आखिरी",
    "reading": "रीडिंग",
    "customer": "ग्राहक",
    "vehicle": "गाड़ी",
    "product": "उत्पाद",
    "rate": "रेट",
    "amount": "रकम",
    "litres_word": "लीटर",
    "total": "कुल",
    "contact_owner": "मालिक से संपर्क करें।",

    # ── Product descriptions (codes stay Roman; these explain them) ──────
    "prod_hs_full": "हाई स्पीड डीज़ल",
    "prod_ms_full": "पेट्रोल (मोटर स्पिरिट)",
    "prod_x2_full": "एक्स्ट्रा प्रीमियम 95",
    "prod_xg_full": "एक्स्ट्रा ग्रीन",
    "prod_cng_full": "सीएनजी · किलो",

    # ── Home screen ─────────────────────────────────────────────────────
    "log_credit_sale": "उधार बिक्री दर्ज करें",
    "log_credit_sale_sub": "उधार पर तेल की बिक्री दर्ज करें",
    "close_shift": "शिफ्ट बंद करें",
    "close_shift_sub": "नोज़ल की आखिरी रीडिंग भरें",
    # "{date} की शिफ्ट बंद हो गई ✓"  — date is prepended in the template
    "shift_closed_suffix": "की शिफ्ट बंद हो गई ✓",
    "shift_in_progress": "आज की शिफ्ट ({date}) चल रही है। इसे कल 06:00 बजे के बाद बंद करें।",
    "shift_not_closed_suffix": "की शिफ्ट बंद नहीं हुई",
    "tap_close_shift": "आखिरी रीडिंग भरने के लिए “शिफ्ट बंद करें” दबाएँ।",

    # ── Shift settlement flow ───────────────────────────────────────────
    "shift_settlement": "शिफ्ट का हिसाब",
    "select_product": "उत्पाद चुनें",
    "select_product_sub": "अभी के हिसाब के लिए तेल चुनें।",
    "all_locked": "{date} की सभी रीडिंग जमा हो चुकी हैं और लॉक हैं।",
    "review_submit": "जाँचें और जमा करें",
    "select_du_sub": "मशीन (DU) चुनें और आखिरी रीडिंग भरें।",
    "no_opening_contact": "कोई शुरुआती रीडिंग नहीं। मालिक से संपर्क करें।",
    "tap_to_enter": "रीडिंग भरने के लिए दबाएँ",
    "confirm_readings": "रीडिंग पक्की करें",

    # ── Numpad ──────────────────────────────────────────────────────────
    "enter_reading": "रीडिंग भरें",
    "opening_label": "शुरुआती:",
    "no_opening_on_file": "फ़ाइल में कोई शुरुआती रीडिंग नहीं",
    "nozzle_label": "नोज़ल",
    "save_confirm": "सेव करके पक्का करें",
    "reading_must_be_higher": "रीडिंग शुरुआती से ज़्यादा होनी चाहिए",
    "confirm_no_fuel": "⚠ पक्का करें कि कोई तेल नहीं बिका",
    "litres_dispensed": "बिका तेल",
    "kg_sold": "बिका माल",

    # ── Shift summary ───────────────────────────────────────────────────
    "review_submit_heading": "जाँचें और जमा करें",
    "confirm_before_lock": "शिफ्ट लॉक करने से पहले आखिरी रीडिंग जाँच लें।",
    "attendant_word": "कर्मचारी",
    "not_entered": "नहीं भरा",
    "missing": "अधूरा",
    "totals_by_product": "हर उत्पाद का कुल",
    "net_after_pump_test": "हर नोज़ल के 5 L पंप टेस्ट घटाकर",
    "warn_closing_equals_opening": "{name} — आखिरी और शुरुआती रीडिंग बराबर हैं। पक्का करें कि कोई तेल नहीं बिका।",
    "warn_below_pump_test": "{name} — सिर्फ़ {litres} L दर्ज (पंप टेस्ट से कम)। रीडिंग जाँचें।",
    "submit_shift": "शिफ्ट जमा करें",
    "save_draft": "बाद के लिए सेव करें",

    # ── Select customer ─────────────────────────────────────────────────
    "select_customer": "ग्राहक चुनें",
    "select_customer_sub": "लेन-देन दर्ज करने के लिए खाता चुनें।",
    "search_placeholder": "नाम, ID या गाड़ी खोजें",
    "filter_recent": "हाल के",
    "filter_frequent": "अक्सर",
    "filter_all": "सभी खाते",
    "credit_customers": "उधार ग्राहक",
    "n_found": "मिले",
    "credit_active": "उधार चालू",
    "credit_blocked": "उधार बंद",
    "no_customers": "कोई ग्राहक नहीं मिला।",

    # ── Log sale details ────────────────────────────────────────────────
    "new_transaction": "नया लेन-देन",
    "log_sale_details": "बिक्री की जानकारी भरें",
    "vehicle_reg_number": "गाड़ी नंबर",
    "unregistered": "बिना नंबर",
    "container": "कंटेनर",
    "select_vehicle": "गाड़ी चुनें",
    "dispensed_quantity": "कितना तेल दिया",
    "amount_rupees": "रकम (₹)",
    "litres_L": "लीटर (L)",
    "select_to_see_rate": "रेट देखने के लिए उत्पाद चुनें",
    "select_product_first_units": "यूनिट बदलने से पहले उत्पाद चुनें।",
    "no_rate_for": "का रेट नहीं है। मालिक से संपर्क करें।",  # "{prod} " prepended in JS
    "rate_label": "रेट",
    "total_label": "कुल",
    "confirm_log_sale": "बिक्री पक्की करें",

    # ── Transaction confirmed ───────────────────────────────────────────
    "credit_sale": "उधार बिक्री",
    "sale_logged": "बिक्री दर्ज हो गई",
    "collect_parchi": "उधार पर दर्ज। ग्राहक से पर्ची लें।",
    "total_amount": "कुल रकम",
    "volume_label": "मात्रा",
    "date_time": "तारीख़ और समय",
    "reminder_parchi": "याद रखें · ग्राहक को साइन की हुई पर्ची दें",
    "log_new_sale": "नई बिक्री दर्ज करें",
    "go_home": "होम पर जाएँ",

    # ── Activity / Profile stubs ────────────────────────────────────────
    "activity_heading": "गतिविधि",
    "activity_coming_soon": "लेन-देन और शिफ्ट का इतिहास यहाँ दिखेगा। जल्द आ रहा है।",
    "sign_out": "बाहर निकलें",

    # ── Legacy day-close flow ───────────────────────────────────────────
    "totalizer_suffix": "टोटलाइज़र",
    "day_close": "दिन का हिसाब",
    "last_recorded": "पिछली रीडिंग:",
    "no_previous_reading": "कोई पिछली रीडिंग नहीं",
    "enter_totalizer": "टोटलाइज़र रीडिंग भरें",
    "save_readings_suffix": "रीडिंग सेव करें",
    "all_submitted_locked": "इस दिन की सभी रीडिंग जमा और लॉक हैं।",
    "day_already_closed": "दिन पहले ही बंद है",
    "close_day": "दिन बंद करें",
    "complete_all_first": "पहले सभी उत्पाद पूरे करें",

    # ── Legacy log-transaction form ─────────────────────────────────────
    "log_credit_transaction": "उधार लेन-देन दर्ज करें",
    "select_customer_dash": "— ग्राहक चुनें —",
    "select_customer_first_dash": "— पहले ग्राहक चुनें —",
    "select_vehicle_dash": "— गाड़ी चुनें —",
    "select_product_dash": "— उत्पाद चुनें —",
    "rate_per_litre": "प्रति लीटर रेट (₹)",
    "auto_from_rsp": "— मौजूदा रेट से अपने आप",
    "litres_required": "लीटर",
    "eg_litres": "जैसे 50.00",
    "amount_from_meter": "— मीटर रीडिंग से",
    "eg_amount": "जैसे 4699.50",
    "attendant_name": "कर्मचारी का नाम",
    "your_name": "आपका नाम",
    "notes": "टिप्पणी",
    "optional": "ज़रूरी नहीं",
    "submit_transaction": "लेन-देन जमा करें",
    "loading": "लोड हो रहा है...",
    "error_loading_vehicles": "गाड़ियाँ लोड नहीं हुईं",
    "fixed_unreg_desc": "डिलीवरी से पहले / बिना नंबर",
    "fixed_container_desc": "तेल का कंटेनर",

    # ── Flash messages (routes.py) ──────────────────────────────────────
    "flash_record_all_products": "शिफ्ट बंद करने से पहले सभी उत्पाद दर्ज करें।",
    "flash_day_close_submitted": "{date} का दिन बंद कर दिया गया। मालिक को सूचना भेज दी गई।",
    "flash_readings_locked": "इस दिन की रीडिंग लॉक हैं।",
    "flash_readings_saved": "{product} रीडिंग सेव हो गईं।",
    "flash_txn_saved": "लेन-देन सेव हो गया।",
    "flash_valid_number": "सही संख्या भरें।",
    "flash_closing_ge_opening_kg": "आखिरी रीडिंग ({closing} kg) शुरुआती ({opening} kg) से कम नहीं हो सकती।",
    "flash_shift_already_submitted": "आज की शिफ्ट पहले ही जमा हो चुकी है।",
    "flash_reading_locked": "यह रीडिंग पहले ही जमा और लॉक है।",
    "flash_closing_ge_opening": "आखिरी रीडिंग ({closing}) शुरुआती ({opening}) से कम नहीं हो सकती।",
    "flash_all_six_nozzles": "जमा करने से पहले सभी 6 नोज़ल की रीडिंग भरें।",
    "flash_shift_closed": "शिफ्ट बंद हो गई। सभी रीडिंग जमा हो गईं।",
    "flash_txn_saved_util": "लेन-देन सेव हो गया। ध्यान दें: {name} अब {pct}% उधार सीमा पर है।",
    # Per-nozzle totalizer validation (legacy day-close). {label} stays Roman.
    "flash_label_gt_zero": "{label}: शून्य से ज़्यादा होनी चाहिए।",
    "flash_label_valid_number": "{label}: सही संख्या भरें।",
    # Legacy credit-transaction form validation
    "err_select_customer": "कृपया ग्राहक चुनें।",
    "err_select_vehicle": "कृपया गाड़ी चुनें।",
    "err_select_valid_product": "कृपया सही उत्पाद चुनें।",
    "err_select_product": "कृपया उत्पाद चुनें।",
    "err_attendant_name_required": "कर्मचारी का नाम ज़रूरी है।",
    "err_litres_gt_zero": "लीटर शून्य से ज़्यादा होने चाहिए।",
    "err_litres_valid": "लीटर सही संख्या में भरें।",
    "err_amount_gt_zero": "रकम शून्य से ज़्यादा होनी चाहिए।",
    "err_amount_valid": "रकम सही संख्या में भरें।",
    "err_no_current_rate": "{product} का मौजूदा रेट नहीं मिला। मालिक से संपर्क करें।",
    "err_quantity_gt_zero": "मात्रा शून्य से ज़्यादा होनी चाहिए।",
    "err_quantity_valid": "कृपया सही मात्रा भरें।",

    # ── Page <title>s (browser tab) ─────────────────────────────────────
    "title_home": "होम",
    "title_shift": "शिफ्ट का हिसाब",
    "title_enter_reading": "रीडिंग भरें",
    "title_select_customer": "ग्राहक चुनें",
    "title_log_sale": "बिक्री दर्ज करें",
    "title_sale_logged": "बिक्री दर्ज हो गई",
    "title_activity": "गतिविधि",
    "title_profile": "प्रोफ़ाइल",
    "title_day_close": "दिन का हिसाब",

    # ── Field-First skin toggle (Option B, per-device trial) ────────────
    "ff_heading": "बड़ा दिखाओ",
    "ff_explain": "धूप में पढ़ने के लिए बड़े अक्षर और बड़े बटन। सिर्फ़ इसी फ़ोन पर लागू।",
    "ff_on_label": "चालू है",
    "ff_off_label": "बंद है",
    "ff_turn_on": "चालू करें",
    "ff_turn_off": "बंद करें",
    "ff_enabled": "बड़ा दिखाओ चालू हो गया।",
    "ff_disabled": "बड़ा दिखाओ बंद हो गया।",
}
