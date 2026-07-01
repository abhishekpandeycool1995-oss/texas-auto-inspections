import os, json, base64
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from typing import List
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
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

CLAUDE_PROMPT = """You are inspecting a Texas 173-point vehicle inspection form. These photos show the filled paper form.

YOUR TASK:
Read EVERY row carefully. For each item (1-173), identify:
1. Which checkbox column is marked (checkmark, X, or filled box)
2. Any handwritten text in the far-right "Details" column — read the handwriting carefully
3. Header info at top: date, odo reading, VIN, make/model, client, sales rep, dealership, address
4. Customer concerns/notes written at the bottom of the form

Pay special attention to handwritten notes in the Details column — many rows have small handwriting there.

Return ONLY this exact JSON structure — no markdown, no code fences, no extra text:
{
  "header": {"date":"","odo":"","vin":"","make_model":"","client":"","sales_rep":"","dealership":"","address":""},
  "items": {
    "1": {"col":"","detail":""},
    "2": {"col":"","detail":""},
    ...all 173 items...
  },
  "concerns": ["",""]
}

Column values per item range (use EXACTLY these):
- Items 1-8: OK / PASS / FAIL
- Items 9-24: PASS / BLEMISH / DIRTY
- Items 25-63: WORKS / BROKEN / CRACKED
- Items 64-78: PASS / FAIL / NA
- Items 79-83: PASS / FAIL / NA
- Items 84-101: OK / SCRATCH / DING / CHIP / RUST / DENT
- Items 102-113: OK / CHIP / CRACKED
- Items 114-116: OK / CHIPS / CRACK / HAZY / MISSING
- Items 117-124: EXCELLENT / GOOD / FAIR / POOR
- Items 125-146: NO / YES / NA
- Items 147-151: PASS / FAIL / NA
- Items 152-157: FAIL / PASS / NA
- Items 158-161: EXCELLENT / GOOD / FAIR / POOR
- Items 162-167: FAIL / PASS / NA
- Items 168-170: PASS / FAIL / NA
- Items 171: EXCELLENT / GOOD / FAIR / POOR
- Items 172-173: YES / NO / NA

CRITICAL RULES:
- If no box is ticked, use col:""
- Copy EVERY handwritten Detail text exactly as written
- Output ALL 173 items — include every number even if blank
- Double-check you didn't skip any rows
- Return ONLY the raw JSON, nothing else."""

VALID_COLUMNS = {
    "OK", "PASS", "FAIL", "WORKS", "BROKEN", "CRACKED",
    "BLEMISH", "DIRTY", "NA", "YES", "NO",
    "SCRATCH", "DING", "CHIP", "RUST", "DENT",
    "CHIPS", "CRACK", "HAZY", "MISSING",
    "EXCELLENT", "GOOD", "FAIR", "POOR",
}

def claude_json_to_flat(data):
    result = {}
    header = data.get("header", {})
    hdr_map = {
        "date": "s_date", "odo": "odo", "vin": "vin",
        "make_model": "make_model", "client": "client",
        "sales_rep": "sales_rep", "dealership": "dealership",
        "address": "address",
    }
    for ck, pk in hdr_map.items():
        val = header.get(ck, "")
        if val:
            result[pk] = val.strip()

    items = data.get("items", {})
    for num_str, item_data in items.items():
        try:
            item_num = int(num_str)
            if item_num < 1 or item_num > 173:
                continue
        except ValueError:
            continue
        col = item_data.get("col", "").strip().upper()
        detail = item_data.get("detail", "").strip()
        if col and col in VALID_COLUMNS:
            result[f"item_{item_num}_{col}"] = True
        elif col:
            print(f"  UNKNOWN COLUMN '{col}' for item {item_num}")
        if detail:
            result[f"note_{item_num}"] = detail

    concerns = data.get("concerns", [])
    for i, concern_text in enumerate(concerns):
        if concern_text and concern_text.strip():
            result[f"concern_{i+1}"] = concern_text.strip()

    return result

def extract_json_from_text(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1]
        else:
            raw = raw[3:]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end+1])
        raise ValueError(f"Could not parse JSON. Raw[:300]: {raw[:300]}")

def extract_with_claude(image_parts, api_key, model_name="claude-sonnet-4-6"):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    content = []
    for i, (img_data, mime, fname) in enumerate(image_parts):
        b64 = base64.b64encode(img_data).decode()
        content.append({"type": "text", "text": f"=== IMAGE {i+1} of {len(image_parts)} ==="})
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
    content.append({"type": "text", "text": CLAUDE_PROMPT})

    response = client.messages.create(
        model=model_name,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}]
    )
    raw = "".join(block.text for block in response.content if hasattr(block, "text"))
    return raw

@app.post("/api/process-inspection")
async def process_inspection(files: List[UploadFile] = File(...)):
    try:
        check_quota()
    except Exception as e:
        print(f"Quota error (non-fatal): {e}")
    claude_key = os.environ.get("ANTHROPIC_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    all_errors = []
    raw_responses = []
    extracted_json = {}

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
            photo_data.append((image_data, mime_type, f.filename))

        if not photo_data:
            print("No photos uploaded.")
            extracted_json = {}
        elif claude_key:
            print(f"  Using Claude API with {len(photo_data)} photos")
            for model_name in ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"]:
                success = False
                for attempt in range(3):
                    try:
                        raw = extract_with_claude(photo_data, claude_key, model_name)
                        print(f"  RAW ({model_name}): {len(raw)} chars")
                        raw_responses.append(f"=== {model_name} ===\n{raw[:500]}")
                        parsed = extract_json_from_text(raw)
                        extracted_json = claude_json_to_flat(parsed)
                        n = sum(1 for k in extracted_json if k.startswith("item_"))
                        print(f"  Parsed: {n} items")
                        success = True
                        break
                    except HTTPException:
                        raise
                    except Exception as e:
                        err_str = str(e)
                        print(f"  {model_name} FAILED: {type(e).__name__}: {err_str[:200]}")
                        all_errors.append((model_name, type(e).__name__, err_str[:200]))
                        time.sleep(1)
                        continue
                if success:
                    break
        elif gemini_key:
            print(f"  Fallback to Gemini API with {len(photo_data)} photos")
            from google import genai
            from google.genai import types as gemini_types
            client = genai.Client(api_key=gemini_key, http_options={'api_version': 'v1'})
            gemini_parts = [gemini_types.Part.from_bytes(data=d, mime_type=m) for d, m, _ in photo_data]
            for model_name in ["gemini-2.5-flash", "gemini-2.5-flash-lite"]:
                success = False
                for attempt in range(3):
                    try:
                        print(f"    {model_name} attempt {attempt+1}/3")
                        response = client.models.generate_content(
                            model=model_name,
                            contents=gemini_parts + [CLAUDE_PROMPT],
                        )
                        raw = response.text.strip()
                        print(f"    RAW: {len(raw)} chars")
                        raw_responses.append(f"=== {model_name} ===\n{raw[:500]}")
                        if raw.startswith("```"): raw = raw[3:]
                        if raw.endswith("```"): raw = raw[:-3]
                        raw = raw.strip()
                        parsed = extract_json_from_text(raw)
                        extracted_json = claude_json_to_flat(parsed)
                        n = sum(1 for k in extracted_json if k.startswith("item_"))
                        print(f"    Parsed: {n} items")
                        success = True
                        break
                    except HTTPException:
                        raise
                    except Exception as e:
                        err_str = str(e)
                        print(f"    FAILED: {type(e).__name__}: {err_str[:200]}")
                        all_errors.append((model_name, type(e).__name__, err_str[:200]))
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                            break
                        time.sleep(1)
                        continue
                if success:
                    break
        else:
            print("No API key set. Set ANTHROPIC_API_KEY or GEMINI_API_KEY.")
            extracted_json = {}

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
    try:
        response = FileResponse(output_pdf, media_type="application/pdf", filename="Texas_1st_Auto_Inspection_Report.pdf")
        response.headers["X-Data-Count"] = str(item_count)
        if item_count == 0:
            actual_errors = all_errors[-3:]
            err_detail = "; ".join(f"{t}: {d[:80]}" for _, t, d in actual_errors) if actual_errors else "unknown"
            response.headers["X-Warning"] = f"0 items. Errors: {err_detail}"
        return response
    except Exception as e:
        import traceback
        print(f"FATAL: {traceback.format_exc()}")
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={"error": str(e)[:200]},
            status_code=200,
            headers={"X-Warning": f"Server error, blank PDF sent. {str(e)[:100]}"}
        )

from fastapi.staticfiles import StaticFiles
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
