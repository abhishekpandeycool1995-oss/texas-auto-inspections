from pypdf import PdfReader
import json

try:
    reader = PdfReader("AutoShield_PPI_Checklist.pdf")
    fields = reader.get_fields()
    
    if fields:
        field_info = {}
        for k, v in fields.items():
            field_type = v.get('/FT', 'Unknown')
            # Extract only essential info to keep output clean
            field_info[k] = str(field_type)
        print(json.dumps(field_info, indent=2))
    else:
        print("No form fields found in the PDF.")
except Exception as e:
    print(f"Error reading PDF: {e}")
