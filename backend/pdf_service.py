import fitz

ITEM_POSITIONS = {
    1: ("interior", 0, 427),   2: ("interior", 0, 443),   3: ("interior", 0, 459),
    4: ("interior", 0, 475),   5: ("interior", 0, 491),   6: ("interior", 0, 507),
    7: ("interior", 0, 523),   8: ("interior", 0, 539),
    9: ("seats", 0, 618),     10: ("seats", 0, 634),     11: ("seats", 0, 650),
    12: ("seats", 0, 666),    13: ("seats", 0, 682),     14: ("seats", 0, 698),
    15: ("seats", 0, 714),    16: ("seats", 0, 730),     17: ("seats", 0, 746),
    18: ("seats", 1, 42),     19: ("seats", 1, 58),      20: ("seats", 1, 74),
    21: ("seats", 1, 90),     22: ("seats", 1, 106),     23: ("seats", 1, 122),
    24: ("seats", 1, 138),
    25: ("electrical", 1, 227), 26: ("electrical", 1, 243), 27: ("electrical", 1, 259),
    28: ("electrical", 1, 275), 29: ("electrical", 1, 291), 30: ("electrical", 1, 307),
    31: ("electrical", 1, 323), 32: ("electrical", 1, 339), 33: ("electrical", 1, 355),
    34: ("electrical", 1, 371), 35: ("electrical", 1, 387), 36: ("electrical", 1, 403),
    37: ("electrical", 1, 419), 38: ("electrical", 1, 435), 39: ("electrical", 1, 451),
    40: ("electrical", 1, 467), 41: ("electrical", 1, 483), 42: ("electrical", 1, 499),
    43: ("electrical", 1, 515), 44: ("electrical", 1, 531), 45: ("electrical", 1, 547),
    46: ("electrical", 1, 563), 47: ("electrical", 1, 579), 48: ("electrical", 1, 595),
    49: ("electrical", 1, 611), 50: ("electrical", 1, 627), 51: ("electrical", 1, 643),
    52: ("electrical", 1, 659), 53: ("electrical", 1, 675), 54: ("electrical", 1, 691),
    55: ("electrical", 1, 707), 56: ("electrical", 1, 723), 57: ("electrical", 1, 739),
    58: ("electrical", 2, 42),  59: ("electrical", 2, 58),  60: ("electrical", 2, 74),
    61: ("electrical", 2, 90),  62: ("electrical", 2, 106), 63: ("electrical", 2, 122),
    64: ("dashboard", 2, 201), 65: ("dashboard", 2, 217), 66: ("dashboard", 2, 233),
    67: ("dashboard", 2, 249), 68: ("dashboard", 2, 265), 69: ("dashboard", 2, 281),
    70: ("dashboard", 2, 297), 71: ("dashboard", 2, 313), 72: ("dashboard", 2, 329),
    73: ("dashboard", 2, 345), 74: ("dashboard", 2, 361), 75: ("dashboard", 2, 377),
    76: ("dashboard", 2, 393), 77: ("dashboard", 2, 409), 78: ("dashboard", 2, 425),
    79: ("safety", 2, 504),    80: ("safety", 2, 520),    81: ("safety", 2, 536),
    82: ("safety", 2, 552),    83: ("safety", 2, 568),
    84: ("exterior", 2, 656),  85: ("exterior", 2, 672),  86: ("exterior", 2, 688),
    87: ("exterior", 2, 704),  88: ("exterior", 2, 720),  89: ("exterior", 2, 736),
    90: ("exterior", 3, 42),   91: ("exterior", 3, 58),   92: ("exterior", 3, 74),
    93: ("exterior", 3, 90),   94: ("exterior", 3, 106),  95: ("exterior", 3, 122),
    96: ("exterior", 3, 138),  97: ("exterior", 3, 154),  98: ("exterior", 3, 170),
    99: ("exterior", 3, 186),  100: ("exterior", 3, 202), 101: ("exterior", 3, 218),
    102: ("glass", 3, 307),    103: ("glass", 3, 323),    104: ("glass", 3, 339),
    105: ("glass", 3, 355),    106: ("glass", 3, 371),    107: ("glass", 3, 387),
    108: ("glass", 3, 403),    109: ("glass", 3, 419),    110: ("glass", 3, 435),
    111: ("glass", 3, 451),    112: ("glass", 3, 467),    113: ("glass", 3, 483),
    114: ("mirrors", 3, 562),  115: ("mirrors", 3, 578),  116: ("mirrors", 3, 594),
    117: ("tires", 3, 683),    118: ("tires", 3, 699),    119: ("tires", 3, 715),
    120: ("tires", 3, 731),    121: ("tires", 3, 747),
    122: ("tires", 4, 42),     123: ("tires", 4, 58),     124: ("tires", 4, 74),
    125: ("underhood", 4, 153), 126: ("underhood", 4, 169), 127: ("underhood", 4, 185),
    128: ("underhood", 4, 201), 129: ("underhood", 4, 217), 130: ("underhood", 4, 233),
    131: ("underhood", 4, 249), 132: ("underhood", 4, 265), 133: ("underhood", 4, 281),
    134: ("underhood", 4, 297), 135: ("underhood", 4, 313), 136: ("underhood", 4, 329),
    137: ("underhood", 4, 345), 138: ("underhood", 4, 361), 139: ("underhood", 4, 377),
    140: ("underhood", 4, 393), 141: ("underhood", 4, 409), 142: ("underhood", 4, 425),
    143: ("underhood", 4, 441), 144: ("underhood", 4, 457), 145: ("underhood", 4, 473),
    146: ("underhood", 4, 489),
    147: ("suspension", 4, 568), 148: ("suspension", 4, 584), 149: ("suspension", 4, 600),
    150: ("suspension", 4, 616), 151: ("suspension", 4, 632),
    152: ("undercarriage", 4, 711), 153: ("undercarriage", 4, 727), 154: ("undercarriage", 4, 743),
    155: ("undercarriage", 5, 42), 156: ("undercarriage", 5, 58), 157: ("undercarriage", 5, 74),
    158: ("testdrive", 5, 163), 159: ("testdrive", 5, 179), 160: ("testdrive", 5, 195),
    161: ("testdrive", 5, 211),
    162: ("brake", 5, 290),   163: ("brake", 5, 306),   164: ("brake", 5, 322),
    165: ("brake", 5, 338),   166: ("brake", 5, 354),   167: ("brake", 5, 370),
    168: ("diagnostics", 5, 449), 169: ("diagnostics", 5, 465), 170: ("diagnostics", 5, 481),
    171: ("overall", 5, 570),
    172: ("framedamage", 5, 649),
    173: ("flooddamage", 5, 712),
}

HEADER_FIELDS = {
    "Date of Inspection": (0, 113, 290, 152),
    "VIN #": (0, 365, 580, 152),
    "ODO": (0, 113, 290, 172),
    "Make / Model": (0, 365, 580, 172),
    "Client": (0, 113, 290, 192),
    "Sales Rep": (0, 365, 580, 192),
    "Dealership": (0, 113, 290, 212),
    "Address": (0, 365, 580, 212),
}

HEADER_TEXT_X = {"left": 135, "right": 390}

SECTION_COLUMNS = {
    "interior":      {"OK": 286, "PASS": 329, "FAIL": 372},
    "seats":         {"PASS": 286, "BLEMISH": 329, "DIRTY": 372},
    "electrical":    {"WORKS": 286, "BROKEN": 329, "CRACKED": 372},
    "dashboard":     {"PASS": 286, "FAIL": 329, "NA": 372},
    "safety":        {"PASS": 286, "FAIL": 329, "NA": 372},
    "exterior":      {"OK": 241, "SCRATCH": 280, "DING": 319, "CHIP": 354, "RUST": 388, "DENT": 430},
    "glass":         {"OK": 255, "CHIP": 298, "SCRATCH": 354, "CRACKED": 354},
    "mirrors":       {"OK": 233, "CHIPS": 269, "CRACK": 298, "HAZY": 337, "MISSING": 368},
    "tires":         {"EXCELLENT": 265, "GOOD": 319, "FAIR": 354, "POOR": 354},
    "underhood":     {"NO": 325, "YES": 365, "NA": 405},
    "suspension":    {"PASS": 286, "FAIL": 329, "NA": 372},
    "undercarriage": {"FAIL": 286, "PASS": 329, "NA": 372},
    "testdrive":     {"EXCELLENT": 265, "GOOD": 319, "FAIR": 354, "POOR": 354},
    "brake":         {"FAIL": 286, "PASS": 329, "NA": 372},
    "diagnostics":   {"PASS": 286, "FAIL": 329, "NA": 372},
    "overall":       {"EXCELLENT": 265, "GOOD": 319, "FAIR": 354, "POOR": 354},
    "framedamage":   {"YES": 302, "NO": 354, "NA": 405},
    "flooddamage":   {"YES": 302, "NO": 354, "NA": 405},
}

def draw_checkbox(page, x, y, checked, field_name, size=4):
    s = size
    rect = fitz.Rect(x - s, y - s, x + s, y + s)
    page.draw_rect(rect, color=(0, 0, 0), width=0.5, fill=(1, 1, 1))
    if checked:
        cx, cy = x, y
        page.draw_line((cx - s + 1, cy + s - 3), (cx - 1, cy + 1), color=(0, 0, 0), width=1.2)
        page.draw_line((cx - 1, cy + 1), (cx + s - 1, cy - s + 2), color=(0, 0, 0), width=1.2)
    try:
        w = fitz.Widget()
        w.rect = rect
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.field_name = field_name
        w.field_value = "Yes" if checked else "Off"
        w.field_flags = 0
        w.border_width = 0.5
        w.border_color = (0, 0, 0)
        w.fill_color = (1, 1, 1)
        page.add_widget(w)
        w.update()
    except Exception:
        pass

def draw_text(page, x, y, text, fontsize=7):
    if text:
        page.insert_text((x, y - 3), text.strip(), fontsize=fontsize, color=(0, 0, 0), fontname="helv")

def draw_editable_text(page, x, y, text, field_name, fontsize=7.5, width=160):
    if not text or not text.strip():
        return
    text = text.strip()
    try:
        tw = min(width, len(text) * fontsize * 0.5 + 6)
        w = fitz.Widget()
        w.rect = fitz.Rect(x, y - fontsize - 1, x + tw, y + 1)
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = field_name
        w.field_value = text
        w.field_flags = 0
        w.text_font = "helv"
        w.text_fontsize = fontsize
        w.text_color = (0, 0, 0)
        w.fill_color = None
        w.border_width = 0
        page.add_widget(w)
        w.update()
    except Exception:
        pass

def fill_pdf(input_pdf_path, output_pdf_path, data_dict):
    doc = fitz.open(input_pdf_path)

    header_map = [
        ("s_date", "Date of Inspection"),
        ("vin", "VIN #"),
        ("odo", "ODO"),
        ("make_model", "Make / Model"),
        ("client", "Client"),
        ("sales_rep", "Sales Rep"),
        ("dealership", "Dealership"),
        ("address", "Address"),
    ]

    for data_key, field_label in header_map:
        val = data_dict.get(data_key)
        if val:
            info = HEADER_FIELDS.get(field_label)
            if info:
                page_idx, x1, x2, y = info
                text_x = HEADER_TEXT_X["left"] if x1 == 113 else HEADER_TEXT_X["right"]
                width = 150 if x1 == 113 else 180
                draw_editable_text(doc[page_idx], text_x, y + 16, str(val), data_key, fontsize=9, width=width)

    SECTION_HEADERS = {
        0: [(387, 399), (578, 590)],
        1: [(177, 190)],
        2: [(161, 174), (464, 477), (607, 620)],
        3: [(257, 270), (522, 535), (633, 646)],
        4: [(113, 126), (528, 541), (671, 684)],
        5: [(113, 126), (250, 263), (409, 422), (520, 533), (609, 622), (672, 685)],
    }

    for item_num, (section, page_idx, y_center) in ITEM_POSITIONS.items():
        if any(y0 <= y_center <= y1 for (y0, y1) in SECTION_HEADERS.get(page_idx, [])):
            continue
        page = doc[page_idx]
        cols = SECTION_COLUMNS[section]

        note = None
        status = None

        for note_key in [f"note_{item_num}", f"notes_{item_num}", f"detail_{item_num}",
                         f"item_{item_num}_note", f"item_{item_num}_details"]:
            nv = data_dict.get(note_key)
            if nv and isinstance(nv, str) and nv.strip():
                note = nv.strip()
                break

        for col_key in cols:
            for variant in [f"item_{item_num}_{col_key}",
                            f"item_{item_num}_{col_key.lower()}",
                            f"{item_num}_{col_key}",
                            f"{item_num}_{col_key.lower()}",
                            f"i{item_num}_{col_key}"]:
                val = data_dict.get(variant)
                if val is True:
                    status = col_key
                    break
                elif isinstance(val, str) and val.strip().upper() in ("TRUE", "X", "YES", col_key.upper()):
                    status = col_key
                    break
            if status:
                break

        for col_key, x_center in cols.items():
            draw_checkbox(page, x_center, y_center, col_key == status, f"chk_{item_num}_{col_key}")

        if note:
            draw_editable_text(page, 475, y_center + 3, note, f"note_{item_num}", fontsize=6.5, width=100)

    extra_notes = data_dict.get("extra_notes", "")
    if extra_notes and len(doc) > 6:
        draw_editable_text(doc[6], 60, 55, str(extra_notes)[:200], "extra_notes", fontsize=8, width=480)

    for i in range(1, 6):
        concern_key = f"concern_{i}"
        concern_val = data_dict.get(concern_key, "")
        if concern_val and len(doc) > 6:
            y_pos = 44 + (i - 1) * 31
            draw_editable_text(doc[6], 60, y_pos + 12, str(concern_val)[:100], f"concern_{i}", fontsize=8, width=480)

    doc.save(output_pdf_path, deflate=True)
    doc.close()
