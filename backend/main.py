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

PAGE_PROMPT_BODY = """You are analyzing a scanned/photographed inspection form page with rows of checkboxes.

CRITICAL INSTRUCTIONS — follow these exactly:

1. For each row, there are multiple empty square checkboxes side by side (e.g. □ □ □). Only ONE of them may contain a pen mark (checkmark, tick, X, or filled-in square).

2. Do NOT guess based on what answer seems "likely" or "typical" for that item. Only report a box as checked if you can visually confirm a pen mark INSIDE that specific box's boundaries.

3. If multiple checkboxes in a row look ambiguous or unclear, examine the exact pixel area inside each box individually before deciding.

4. Distinguish between:
   - An EMPTY box (just the square outline, no mark inside) → not checked
   - A box with a clear tick/checkmark/X inside it → checked
   - A faint smudge, shadow, or printing artifact → treat as NOT checked unless it's clearly a deliberate pen stroke

5. If NO box in a row has a visible mark, skip that row entirely — do not default to any particular column.

6. If a mark appears to overlap two adjacent boxes, choose the box where the majority of the ink/mark falls.

7. Before finalizing your answer for each row, do a self-check: "Did I find an actual pen mark inside this exact box, or am I assuming based on the row label?" Only confirm if it's the former.

8. Work through the image section by section, row by row, in order — do not skip around.

Output each item as: item_NUMBER|COLUMN_HEADER|optional_notes

COLUMN_HEADER is the text printed at the top of the column (e.g. OK, PASS, FAIL, WORKS, BROKEN, etc.).

Examples:
item_1|OK|no wind noise
item_5|PASS|slight wear

Only output pipe-delimited lines. No introductions."""

FIRST_PAGE_PROMPT = PAGE_PROMPT_BODY + """

Also read the header fields at the TOP of this page:
header|FIELD_NAME|value

Field names: s_date, vin, odo, make_model, client, sales_rep, dealership, address
Example: header|vin|1HGCM82633A004352"""

LAST_PAGE_PROMPT = PAGE_PROMPT_BODY + """

Also read any customer concerns at the bottom of this page:
concern|NUMBER|text

Example: concern|1|engine vibration"""

VALID_COLUMNS = {
    "OK", "PASS", "FAIL", "WORKS", "BROKEN", "CRACKED",
    "BLEMISH", "DIRTY", "NA", "YES", "NO",
    "SCRATCH", "DING", "CHIP", "RUST", "DENT",
    "CHIPS", "CRACK", "HAZY", "MISSING",
    "EXCELLENT", "GOOD", "FAIR", "POOR",
}

def merge_results(existing, new):
    for k, v in new.items():
        if k not in existing:
            existing[k] = v

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
            if status in VALID_COLUMNS:
                result[f"item_{item_num}_{status}"] = True
            else:
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

@app.post("/api/process-inspection")
async def process_inspection(files: List[UploadFile] = File(...)):
    check_quota()
    api_key = os.environ.get("GEMINI_API_KEY")
    all_errors = []
    if not api_key:
        print("No API key set. Set GEMINI_API_KEY environment variable.")
        extracted_json = {}
    else:
        MODELS_TO_TRY = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        ]

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

            # Read and normalize all photos
            photo_data = []
            for idx, f in enumerate(files):
                try:
                    img_bytes = await f.read()
                except Exception as e:
                    print(f"  READ ERROR for {f.filename}: {type(e).__name__}: {e}")
                    raise
                print(f"Photo {idx+1} ({f.filename}): {len(img_bytes)} bytes, {f.content_type}")
                try:
                    image_data, mime_type = normalize_image(img_bytes, f.content_type)
                except Exception as e:
                    print(f"NORMALIZE ERROR for {f.filename}: {type(e).__name__}: {e}")
                    raise
                photo_data.append((image_data, mime_type, f.filename))

            extracted_json = {}
            total_photos = len(photo_data)

            for idx, (image_data, mime_type, filename) in enumerate(photo_data):
                if idx == 0 and total_photos > 0:
                    prompt = FIRST_PAGE_PROMPT
                elif idx == total_photos - 1 and total_photos > 1:
                    prompt = LAST_PAGE_PROMPT
                else:
                    prompt = PAGE_PROMPT_BODY

                print(f"  Processing photo {idx+1}/{total_photos} ({filename})")

                success = False
                for model_name in MODELS_TO_TRY:
                    if success:
                        break
                    for attempt in range(3):
                        try:
                            print(f"    {model_name} (attempt {attempt+1}/3)")
                            response = client.models.generate_content(
                                model=model_name,
                                contents=[types.Part.from_bytes(data=image_data, mime_type=mime_type), prompt],
                            )
                            raw = response.text.strip()
                            lines_count = len([l for l in raw.split('\n') if '|' in l])
                            print(f"    RAW: {len(raw)} chars, {lines_count} lines")
                            print(f"    RAW: {raw[:400]}")
                            if raw.startswith("```"): raw = raw[3:]
                            if raw.endswith("```"): raw = raw[:-3]
                            raw = raw.strip()
                            partial = parse_ai_output(raw)
                            n = sum(1 for k in partial if k.startswith("item_"))
                            print(f"    Parsed: {n} items, {len(partial)} keys")
                            merge_results(extracted_json, partial)
                            success = True
                            break
                        except HTTPException:
                            raise
                        except Exception as e:
                            err_str = str(e)
                            print(f"    {model_name} FAILED: {type(e).__name__}: {e}")
                            all_errors.append((model_name, f"photo_{idx+1}", err_str[:200]))
                            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                                print(f"    Quota exhausted on {model_name}")
                            else:
                                time.sleep(1)
                            continue

                # Delay between photos to avoid rate limits
                if idx < total_photos - 1:
                    time.sleep(1.5)

            item_count = sum(1 for k in extracted_json if k.startswith("item_")
                             or k.startswith("concern_") or k.startswith("note_"))
            print(f"Total: {item_count} items from {total_photos} photos, keys: {list(extracted_json.keys())[:30]}")

        except HTTPException:
            raise
        except Exception as e:
            import traceback
            print(f"ERROR: {traceback.format_exc()}")
            extracted_json = {}

    item_count = sum(1 for k in extracted_json if k.startswith("item_") or k.startswith("concern_") or k.startswith("note_"))

    if item_count == 0:
        print("WARNING: AI returned no data.")
        extracted_json = {}

    import json as _json
    data_json = _json.dumps(extracted_json)
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
        err_detail = "; ".join(f"photo {t}: {m}" for m, t, _ in all_errors[-5:]) if all_errors else "unknown"
        response.headers["X-Warning"] = f"AI error - {err_detail}"
    return response

from fastapi.staticfiles import StaticFiles
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
