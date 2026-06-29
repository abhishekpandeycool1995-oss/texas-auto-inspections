import os, json, base64
from typing import List
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
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
You are an expert forensic handwriting transcriber for a vehicle inspection form. You will receive MULTIPLE PHOTOS of a single 173-point inspection checklist. They may be in any order — reassemble them mentally.

Your task: transcribe EVERY item you can read, exactly as marked. Be thorough — look carefully at each image. If handwriting is unclear, make your best guess.

JSON rules:
- Output ONLY status keys that are TRUE/ticked/circled. No false values.
- Each value MUST be `true` (boolean), NOT a string like "PASS".
- For blank items, do NOT include them.

Key formats by section:

ITEMS 1-8 (Interior → OK/PASS/FAIL):
  "item_1_OK": true, "item_1_PASS": true, "item_1_FAIL": true

ITEMS 9-24 (Seats & Carpet → PASS/BLEMISH/DIRTY):
  "item_9_PASS": true, "item_9_BLEMISH": true, "item_9_DIRTY": true

ITEMS 25-63 (Electrical → WORKS/BROKEN/CRACKED):
  "item_25_WORKS": true, "item_25_BROKEN": true, "item_25_CRACKED": true

ITEMS 64-83 (Dashboard & Safety → PASS/FAIL/NA):
  "item_64_PASS": true, "item_64_FAIL": true, "item_64_NA": true

ITEMS 84-101 (Exterior → OK/SCRATCH/DING/CHIP/RUST/DENT):
  "item_84_OK": true, "item_84_SCRATCH": true

ITEMS 102-113 (Glass → OK/CHIP/SCRATCH/CRACKED)
ITEMS 114-116 (Mirrors → OK/CHIPS/CRACK/HAZY/MISSING)
ITEMS 117-124 (Tires → EXCELLENT/GOOD/FAIR/POOR)
ITEMS 125-146 (Under Hood → NO/YES/NA)
ITEMS 147-151 (Suspension → PASS/FAIL/NA)
ITEMS 152-157 (Under Carriage → PASS/FAIL/NA)
ITEMS 158-161 (Test Drive → EXCELLENT/GOOD/FAIR/POOR)
ITEMS 162-167 (Brakes → PASS/FAIL/NA)
ITEMS 168-170 (Diagnostics → PASS/FAIL/NA)
ITEM 171 (Overall → EXCELLENT/GOOD/FAIR/POOR)
ITEM 172 (Frame Damage → YES/NO/NA)
ITEM 173 (Flood Damage → YES/NO/NA)

Notes: "note_N": "text of their handwritten note"
Header: "s_iname", "s_date", "vin", "odo", "make_model", "client", "sales_rep", "dealership", "address", "extra_notes"
Customer concerns (page 7): "concern_1" through "concern_5"

Example output:
{"item_1_OK": true, "note_1": "no wind noise", "item_126_YES": true, "note_126": "small leak", "s_iname": "John", "vin": "1HGCM82633A004352"}

Look at every image page by page. Do not skip any. Output only valid JSON.
"""

@app.post("/api/process-inspection")
async def process_inspection(files: List[UploadFile] = File(...)):
    check_quota()
    api_key = os.environ.get("GROQ_API_KEY")
    last_error = None
    if not api_key:
        print("No API key set. Set GROQ_API_KEY environment variable.")
        extracted_json = {}
    else:
        MODELS_TO_TRY = [
            "llama-3.2-90b-vision-preview",
            "llama-3.2-11b-vision-preview",
        ]

        import time

        def normalize_image(data, orig_ct):
            ct = (orig_ct or "image/png").lower().replace("image/jpg", "image/jpeg")
            try:
                img = Image.open(BytesIO(data))
                w, h = img.size
                if max(w, h) > 1800:
                    ratio = 1800 / max(w, h)
                    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=90)
                return buf.getvalue(), "image/jpeg"
            except Exception:
                return data, ct

        try:
            client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

            # Read and normalize all images first
            image_data_list = []
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
                image_data_list.append((image_data, mime_type))

            # Send ALL images in a single API call so the AI sees the full form
            extracted_json = {}
            response = None
            for model_name in MODELS_TO_TRY:
                for attempt in range(5):
                    try:
                        print(f"  Trying {model_name} (attempt {attempt+1}/5) with {len(image_data_list)} images")
                        content_parts = [{"type": "text", "text": PROMPT}]
                        for img_data, mime in image_data_list:
                            b64 = base64.b64encode(img_data).decode("utf-8")
                            content_parts.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"}
                            })
                        response = client.chat.completions.create(
                            model=model_name,
                            messages=[{"role": "user", "content": content_parts}],
                            temperature=0.0,
                            max_tokens=4096,
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
                text = response.choices[0].message.content.strip()
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
