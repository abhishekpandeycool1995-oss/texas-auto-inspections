import os, json
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

# Explicit prompt mapping handwriting to specific keys.
PROMPT = """
You are an expert handwriting transcriber. I have attached MULTIPLE IMAGES that together form ONE vehicle pre-purchase inspection checklist — the Texas First Auto Inspection 173-point form. These images may be in ANY order — reassemble them mentally as a single form.
I need you to extract the information and map it EXACTLY to the following JSON structure.

For each item number N (1 through 173), determine:
1. What status was marked (the column that was ticked/circled)
2. Any notes written in the Details column

IMPORTANT: Output ONLY the status keys that are TRUE/ticked. Do NOT output false values.
The value for each status key MUST be `true` (boolean), NOT a string like "PASS" or "FAIL".
Example correct format: {"item_1_PASS": true, "item_4_OK": true}
Example WRONG format: {"item_1_PASS": "PASS", "item_4_OK": "OK"}

Use these key formats based on which section the item belongs to:

ITEMS 1-8 (Interior): OK | PASS | FAIL
  - "item_N_OK": true, "item_N_PASS": true, "item_N_FAIL": true

ITEMS 9-24 (Seats & Carpet): PASS | BLEMISH | DIRTY
  - "item_N_PASS": true, "item_N_BLEMISH": true, "item_N_DIRTY": true

ITEMS 25-63 (Electrical System): WORKS | BROKEN | CRACKED
  - "item_N_WORKS": true, "item_N_BROKEN": true, "item_N_CRACKED": true

ITEMS 64-78 (Dashboard): PASS | FAIL | NA
ITEMS 79-83 (Safety): PASS | FAIL | NA

ITEMS 84-101 (Exterior): OK | SCRATCH | DING | CHIP | RUST | DENT
  - "item_N_OK": true, "item_N_SCRATCH": true, "item_N_DING": true, "item_N_CHIP": true, "item_N_RUST": true, "item_N_DENT": true

ITEMS 102-113 (Glass): OK | CHIP | SCRATCH | CRACKED
ITEMS 114-116 (Mirrors): OK | CHIPS | CRACK | HAZY | MISSING
ITEMS 117-124 (Tires & Wheels): EXCELLENT | GOOD | FAIR | POOR
ITEMS 125-146 (Under Hood): NO | YES | NA
ITEMS 147-151 (Suspension): PASS | FAIL | NA
ITEMS 152-157 (Under Carriage): PASS | FAIL | NA
ITEMS 158-161 (Test Drive): EXCELLENT | GOOD | FAIR | POOR
ITEMS 162-167 (Brake System): PASS | FAIL | NA
ITEMS 168-170 (Diagnostics): PASS | FAIL | NA
ITEM 171 (Overall Condition): EXCELLENT | GOOD | FAIR | POOR
ITEM 172 (Frame Damage): YES | NO | NA
ITEM 173 (Flood Damage): YES | NO | NA

For notes on any item: include "note_N": "text of their note"

Example format:
{"item_1_OK": true, "note_1": "no wind noise", "item_4_PASS": true, "item_126_YES": true, "note_126": "small leak at front", "item_64_PASS": true}

Header information at the top of the form:
- "s_iname": "inspector name"
- "s_date": "date of inspection"
- "vin": "VIN number"
- "odo": "odometer reading"
- "make_model": "make and model"
- "client": "client name"
- "sales_rep": "sales rep name"
- "dealership": "dealership name"
- "address": "address"
- "extra_notes": "any overall notes at the bottom"

Customer main concerns (page 7):
- "concern_1": "first concern"
- "concern_2": "second concern"
- etc. up to "concern_5"

Please analyze the handwriting. For any item you can read, output the corresponding JSON keys.
For items you cannot see in the image, or that are left blank, do NOT include them.

ONLY output valid JSON. Do not use markdown code blocks like ```json, just output the raw JSON object.
"""

@app.post("/api/process-inspection")
async def process_inspection(files: List[UploadFile] = File(...)):
    check_quota()
    api_key = os.environ.get("GEMINI_API_KEY")
    last_error = None
    if not api_key:
        print("No API key set. Set GEMINI_API_KEY environment variable.")
        extracted_json = {}
    else:
        MODELS_TO_TRY = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
        ]

        import time

        def normalize_image(data, orig_ct):
            ct = (orig_ct or "image/png").lower().replace("image/jpg", "image/jpeg")
            try:
                img = Image.open(BytesIO(data))
                buf = BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue(), "image/png"
            except Exception:
                return data, ct

        try:
            client = genai.Client(api_key=api_key, http_options={'api_version': 'v1'})

            # Read and normalize all images first
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

            # Send ALL images in a single API call so the AI sees the full form
            extracted_json = {}
            response = None
            for model_name in MODELS_TO_TRY:
                for attempt in range(5):
                    try:
                        print(f"  Trying {model_name} (attempt {attempt+1}/5) with {len(image_parts)} images")
                        response = client.models.generate_content(
                            model=model_name,
                            contents=image_parts + [PROMPT]
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
                            last_error = (model_name, type(e).__name__, err_str)
                            continue
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                            print(f"  {model_name} quota exhausted, trying next model...")
                            last_error = (model_name, type(e).__name__, err_str)
                            break
                        print(f"  {model_name} FAILED: {type(e).__name__}: {e}")
                        last_error = (model_name, type(e).__name__, err_str)
                        continue
                if response:
                    break

            if response:
                text = response.text.strip()
                if text.startswith("```json"): text = text[7:-3]
                elif text.startswith("```"): text = text[3:-3]
                try:
                    extracted_json = json.loads(text)
                except json.JSONDecodeError as je:
                    print(f"  JSON ERROR from {model_name}: {text[:200]}")
                    last_error = (model_name, "JSONDecodeError", str(je))
                    extracted_json = {}
                n = sum(1 for k in extracted_json if k.startswith("item_"))
                print(f"  Parsed: {n} items, {len(extracted_json)} keys")
            else:
                print(f"WARNING: All AI models failed. Last error: {last_error}")
                extracted_json = {}
            print(f"Total: {len(extracted_json)} keys")
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
        err_detail = str(last_error) if last_error else "unknown"
        response.headers["X-Warning"] = f"AI could not read the handwriting. Reason: {err_detail}. Returning blank form."
    return response

from fastapi.staticfiles import StaticFiles
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
