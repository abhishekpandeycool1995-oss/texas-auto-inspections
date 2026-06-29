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

PROMPT = """7 photos of a Texas 173-point inspection checklist.

For EVERY numbered item (1-173) that has a handwritten mark:
- Output the item number
- Name the column header where the mark is (e.g. OK, PASS, FAIL, WORKS, BROKEN, etc.)
- Include any handwritten notes

Format: item_NUMBER | COLUMN_HEADER | optional_notes

Also read header info and customer concerns:
header | FIELD_NAME | value
concern | NUMBER | text

Examples:
item_1|OK|no wind noise
item_2|PASS|
header|vin|1HGCM82633A004352
concern|1|engine vibration

Output ONLY pipe-delimited lines. No introductions or explanations.
"""

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
        if not line or line.startswith('#') or line.startswith('//') or '|' not in line:
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

            image_parts = []
            for f in files:
                try:
                    img_bytes = await f.read()
                except Exception as e:
                    print(f"  READ ERROR for {f.filename}: {type(e).__name__}: {e}")
                    raise
                print(f"Processing {f.filename}: {len(img_bytes)} bytes, content_type={f.content_type}")
                try:
                    image_data, mime_type = normalize_image(img_bytes, f.content_type)
                except Exception as e:
                    print(f"NORMALIZE ERROR for {f.filename}: {type(e).__name__}: {e}")
                    raise
                image_parts.append(types.Part.from_bytes(data=image_data, mime_type=mime_type))

            extracted_json = {}
            model_used = None
            for model_name in MODELS_TO_TRY:
                response = None
                for attempt in range(5):
                    try:
                        print(f"  Trying {model_name} (attempt {attempt+1}/5) with {len(image_parts)} images")
                        response = client.models.generate_content(
                            model=model_name,
                            contents=image_parts + [PROMPT],
                            config=types.GenerateContentConfig(
                                temperature=0.1,
                            )
                        )
                        print(f"  {model_name} OK")
                        break
                    except HTTPException:
                        raise
                    except Exception as e:
                        err_str = str(e)
                        if "503" in err_str or "UNAVAILABLE" in err_str:
                            print(f"  {model_name} overloaded, retrying in {2**attempt}s...")
                            time.sleep(2 ** attempt)
                            all_errors.append((model_name, type(e).__name__, err_str))
                            continue
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                            print(f"  {model_name} quota exhausted, trying next model...")
                            all_errors.append((model_name, type(e).__name__, err_str))
                            break
                        print(f"  {model_name} FAILED: {type(e).__name__}: {e}")
                        all_errors.append((model_name, type(e).__name__, err_str))
                        continue
                if response:
                    raw = response.text.strip()
                    lines_count = len([l for l in raw.split('\n') if '|' in l])
                    print(f"  RAW ({model_name}): {len(raw)} chars, {lines_count} lines")
                    print(f"  RAW FIRST 300: {raw[:300]}")
                    print(f"  RAW LAST 300: {raw[-300:]}")
                    if raw.startswith("```"): raw = raw[3:]
                    if raw.endswith("```"): raw = raw[:-3]
                    raw = raw.strip()
                    extracted_json = parse_ai_output(raw)
                    model_used = model_name
                    if not any(k.startswith("item_") for k in extracted_json):
                        all_errors.append((model_name, "ZERO_ITEMS", raw[:300]))
                        print(f"  {model_name} returned 0 items, trying next...")
                        continue
                    break

            if model_used:
                n = sum(1 for k in extracted_json if k.startswith("item_"))
                print(f"  {model_used}: {n} items, {len(extracted_json)} keys")
            else:
                print(f"WARNING: All AI models failed. Errors: {[m for m,_,_ in all_errors]}")
                extracted_json = {}
        except HTTPException:
            raise
        except Exception as e:
            import traceback
            print(f"AI ERROR (falling back): {traceback.format_exc()}")
            extracted_json = {}

    item_count = sum(1 for k in extracted_json if k.startswith("item_") or k.startswith("concern_") or k.startswith("note_"))
    print(f"Total extracted items: {item_count}, keys: {list(extracted_json.keys())[:20]}")

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
        raw_snippet = ""
        for m, t, d in reversed(all_errors):
            if len(d) > 300: d = d[:300] + "..."
            raw_snippet = f"  [{m}] {d}"
            break
        err_detail = "; ".join(f"{m}: {t}" for m, t in all_errors[-3:]) if all_errors else "unknown"
        response.headers["X-Warning"] = f"AI error - {err_detail}. Raw: {raw_snippet}"
    return response

from fastapi.staticfiles import StaticFiles
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
