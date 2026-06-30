import os, json, re
from typing import List
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types
import tempfile
from io import BytesIO
from PIL import Image
import pillow_heif
pillow_heif.register_heif_opener()
from backend.pdf_service import fill_pdf
from datetime import date

QUOTA_LIMIT = 1500
QUOTA_FILE = os.path.join(os.path.dirname(__file__), "quota.json")

def check_quota():
    today = str(date.today())
    data = {}
    try:
        with open(QUOTA_FILE) as f:
            data = json.load(f)
    except:
        pass
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    if data["count"] >= QUOTA_LIMIT:
        print(f"WARNING: Daily quota ({QUOTA_LIMIT}) exceeded, still proceeding.")
    else:
        data["count"] += 1
    with open(QUOTA_FILE, "w") as f:
        json.dump(data, f)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/quota")
def get_quota():
    today = str(date.today())
    data = {}
    try:
        with open(QUOTA_FILE) as f:
            data = json.load(f)
    except:
        pass
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    return {"used": data["count"], "limit": QUOTA_LIMIT, "remaining": QUOTA_LIMIT - data["count"]}

BATCH1_PROMPT = """These are PHOTOS 1-4 of a 7-page Texas 173-point vehicle inspection form.

The pages contain:
- Page 1: Header info (Date, VIN, ODO, Make/Model, Client, Sales Rep, Dealership, Address) + Interior checklist (items 1-8) + Seats checklist (items 9-24)
- Pages 2-3: Electrical checklist (items 25-63) + Dashboard (items 64-78) + Safety (items 79-83) + Exterior (items 84-101)
- Page 4: Glass (items 102-113) + Mirrors (items 114-116) + start of Tires (items 117-121)

For EVERY item number that has a handwritten checkmark, X, or pen mark in a checkbox:
item_N|COLUMN_HEADER|notes

COLUMN_HEADER = the printed text at the top of the column (OK, PASS, FAIL, WORKS, BROKEN, CRACKED, BLEMISH, DIRTY, NA, YES, NO, SCRATCH, DING, CHIP, RUST, DENT, CHIPS, CRACK, HAZY, MISSING, EXCELLENT, GOOD, FAIR, POOR).

Read header fields:
header|s_date|value
header|vin|value
header|odo|value
header|make_model|value
header|client|value
header|sales_rep|value
header|dealership|value
header|address|value

Only output pipe-delimited lines. No other text."""

BATCH2_PROMPT = """These are PHOTOS 5-7 of a 7-page Texas 173-point vehicle inspection form.

The pages contain:
- Page 5: Tires continued (items 122-124) + Underhood (items 125-146) + Suspension (items 147-151) + Undercarriage (items 152-157)
- Page 6: Test Drive (items 158-161) + Brake (items 162-167) + Diagnostics (items 168-170) + Overall (item 171) + Frame Damage (item 172) + Flood Damage (item 173)
- Page 7: Customer concerns

For EVERY item number that has a handwritten checkmark, X, or pen mark in a checkbox:
item_N|COLUMN_HEADER|notes

COLUMN_HEADER = the printed text at the top of the column (OK, PASS, FAIL, WORKS, BROKEN, CRACKED, BLEMISH, DIRTY, NA, YES, NO, SCRATCH, DING, CHIP, RUST, DENT, CHIPS, CRACK, HAZY, MISSING, EXCELLENT, GOOD, FAIR, POOR).

Read customer concerns from the last page:
concern|1|text
concern|2|text
concern|3|text
concern|4|text
concern|5|text

Only output pipe-delimited lines. No other text."""

VALID_COLUMNS = {
    "OK", "PASS", "FAIL", "WORKS", "BROKEN", "CRACKED",
    "BLEMISH", "DIRTY", "NA", "YES", "NO",
    "SCRATCH", "DING", "CHIP", "RUST", "DENT",
    "CHIPS", "CRACK", "HAZY", "MISSING",
    "EXCELLENT", "GOOD", "FAIR", "POOR",
}

def parse_ai_output(text):
    result = {}
    for line in text.split('\n'):
        line = line.strip()
        if not line or '|' not in line:
            continue
        parts = [p.strip() for p in line.split('|')]

        if len(parts) >= 2 and parts[0].startswith('item_'):
            try:
                item_num = int(parts[0].split('_')[1])
                if item_num < 1 or item_num > 173:
                    continue
            except (ValueError, IndexError):
                continue
            status = parts[1].upper().replace('.', '').strip()
            if status in ('NONE', 'NONE.', 'SKIPPED', 'N/A'):
                continue
            if status in VALID_COLUMNS:
                result[f"item_{item_num}_{status}"] = True
            elif status:
                print(f"  UNKNOWN COLUMN '{status}' for item {item_num}")
            if len(parts) >= 3 and parts[2]:
                result[f"note_{item_num}"] = parts[2].strip()

        elif len(parts) >= 2 and parts[0].lower() == 'header':
            field = parts[1].strip().lower().replace(' ', '_')
            val = parts[2].strip() if len(parts) > 2 else ''
            result[field] = val

        elif len(parts) >= 2 and parts[0].lower() == 'concern':
            try:
                cn = int(parts[1])
                if 1 <= cn <= 5:
                    result[f"concern_{cn}"] = parts[2].strip() if len(parts) > 2 else ''
            except ValueError:
                pass

    return result

def merge_results(existing, new):
    for k, v in new.items():
        if k not in existing:
            existing[k] = v

@app.post("/api/process-inspection")
async def process_inspection(files: List[UploadFile] = File(...)):
    check_quota()
    api_key = os.environ.get("GEMINI_API_KEY")
    all_errors = []
    if not api_key:
        print("No API key set. Set GEMINI_API_KEY environment variable.")
        extracted_json = {}
    else:
        import time

        def normalize_image(data, orig_ct):
            ct = (orig_ct or "image/png").lower().replace("image/jpg", "image/jpeg")
            try:
                img = Image.open(BytesIO(data))
                w, h = img.size
                if max(w, h) > 2000:
                    ratio = 2000 / max(w, h)
                    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=90)
                return buf.getvalue(), "image/jpeg"
            except Exception:
                return data, ct

        try:
            client = genai.Client(api_key=api_key, http_options={'api_version': 'v1'})

            photo_data = []
            for f in files:
                try:
                    img_bytes = await f.read()
                except Exception as e:
                    print(f"  READ ERROR for {f.filename}: {type(e).__name__}: {e}")
                    raise
                print(f"  {f.filename}: {len(img_bytes)} bytes, {f.content_type}")
                try:
                    image_data, mime_type = normalize_image(img_bytes, f.content_type)
                except Exception as e:
                    print(f"  NORMALIZE ERROR for {f.filename}: {type(e).__name__}: {e}")
                    raise
                photo_data.append(types.Part.from_bytes(data=image_data, mime_type=mime_type))

            mid = len(photo_data) // 2
            batches = [
                (photo_data[:mid], BATCH1_PROMPT, "batch1"),
                (photo_data[mid:], BATCH2_PROMPT, "batch2"),
            ]

            extracted_json = {}
            for batch_parts, batch_prompt, batch_name in batches:
                if not batch_parts:
                    continue
                print(f"  Sending {batch_name} ({len(batch_parts)} photos)")

                for model_name in ["gemini-2.5-flash", "gemini-2.5-flash-lite"]:
                    success = False
                    for attempt in range(3):
                        try:
                            print(f"    {model_name} attempt {attempt+1}/3")
                            response = client.models.generate_content(
                                model=model_name,
                                contents=batch_parts + [batch_prompt],
                            )
                            raw = response.text.strip()
                            lines_count = len([l for l in raw.split('\n') if '|' in l])
                            print(f"    RAW: {len(raw)} chars, {lines_count} lines")
                            print(f"    RAW: {raw[:500]}")
                            if raw.startswith("```"): raw = raw[3:]
                            if raw.endswith("```"): raw = raw[:-3]
                            raw = raw.strip()
                            partial = parse_ai_output(raw)
                            n = sum(1 for k in partial if k.startswith("item_"))
                            print(f"    Parsed: {n} items")
                            merge_results(extracted_json, partial)
                            success = True
                            break
                        except HTTPException:
                            raise
                        except Exception as e:
                            err_str = str(e)
                            print(f"    FAILED: {type(e).__name__}: {err_str[:150]}")
                            all_errors.append((model_name, batch_name, err_str[:200]))
                            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                                print(f"    Quota exhausted")
                                break
                            time.sleep(1)
                            continue
                    if success:
                        break

                time.sleep(2)

            n = sum(1 for k in extracted_json if k.startswith("item_"))
            print(f"  TOTAL: {n} items, {len(extracted_json)} keys")

        except HTTPException:
            raise
        except Exception as e:
            import traceback
            print(f"ERROR: {traceback.format_exc()}")
            extracted_json = {}

    item_count = sum(1 for k in extracted_json if k.startswith("item_") or k.startswith("concern_") or k.startswith("note_"))
    print(f"Total: {item_count} items")

    if item_count == 0:
        print("WARNING: AI returned no data.")
        extracted_json = {}

    data_json = json.dumps(extracted_json)
    input_pdf = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Texas_First_Auto_Inspection_Blank_v3.pdf"))
    output_pdf = os.path.join(tempfile.gettempdir(), f"inspection_{os.urandom(4).hex()}.pdf")
    try:
        fill_pdf(input_pdf, output_pdf, extracted_json)
    except Exception as e:
        import traceback, shutil
        print(f"PDF Error (returning blank): {traceback.format_exc()}")
        shutil.copy(input_pdf, output_pdf)
    response = FileResponse(output_pdf, media_type="application/pdf", filename="Texas_1st_Auto_Inspection_Report.pdf")
    response.headers["X-Data-Count"] = str(item_count)
    response.headers["X-Extracted-Json"] = data_json[:2000]
    if item_count == 0:
        actual_errors = all_errors[-3:]
        err_detail = "; ".join(f"{t}: {d[:80]}" for _, t, d in actual_errors) if actual_errors else "unknown"
        response.headers["X-Warning"] = f"0 items. {err_detail}"
    return response

from fastapi.staticfiles import StaticFiles
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
