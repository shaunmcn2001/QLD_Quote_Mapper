# (same as before, shortened for brevity in this template)
import io, re
from typing import List, Optional, Dict, Any
from pdfminer.high_level import extract_text as pdfminer_extract
from pdf2image import convert_from_bytes
import pytesseract

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    try:
        txt = pdfminer_extract(io.BytesIO(pdf_bytes))
        if txt and len(txt.strip()) > 40: return txt
    except Exception: pass
    try:
        images = convert_from_bytes(pdf_bytes, dpi=250)
        return "\n".join([pytesseract.image_to_string(img) for img in images])
    except Exception:
        return ""

def parse_lotplan_from_text(text: str) -> List[str]:
    found = []
    for m in re.finditer(r"(?:Lot|L)\s*(\d+[A-Z]?)\s*(?:on\s*)?(RP\d+|SP\d+|CP\d+|DP\d+|CH\d+|CC\d+|BUP\d+|GTP\d+|HBL\d+|HBP\d+)", text, flags=re.I):
        token = f"{m.group(1).upper()} {m.group(2).upper()}"
        if token not in found: found.append(token)
    for m in re.finditer(r"(\d+)\s*/\s*(RP\d+|SP\d+|CP\d+|DP\d+|CH\d+|CC\d+|BUP\d+|GTP\d+|HBL\d+|HBP\d+)", text, flags=re.I):
        token = f"{m.group(1).upper()} {m.group(2).upper()}"
        if token not in found: found.append(token)
    return found

def parse_au_address_structured(text: str) -> List[Dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    results = []
    pat = re.compile(r'(?:^"?(?P<prop>[^",]+?)"?\s*,?\s+)?(?:(?P<number>\d{1,5}[A-Z]?)\s+)?(?P<street>[A-Za-z0-9 .\'\-]+?)\s+(?P<suffix>Road|Rd|Street|St|Avenue|Ave|Highway|Hwy|Drive|Dr|Court|Ct|Place|Pl|Boulevard|Blvd|Way|Lane|Ln|Crescent|Cres|Terrace|Tce|Close|Cl)?\s*,\s*(?P<suburb>[A-Za-z ]+)\s*,\s*(?P<state>QLD|NSW|VIC|SA|WA|TAS|NT|ACT)\b(?:\s+(?P<pcode>\d{4}))?\s*$', re.I)
    for ln in lines:
        m = pat.search(ln.replace(" – ", " - ").replace("—", "-"))
        if not m: continue
        prop = (m.group("prop") or "").strip(' "\'')
        num = m.group("number")
        street = (m.group("street") or "").replace(" - ", "-").replace(" -", "-").replace("- ", "-")
        suffix = (m.group("suffix") or "").upper()
        suburb = (m.group("suburb") or "").upper()
        state = (m.group("state") or "").upper()
        pcode = m.group("pcode")
        results.append({
            "original": ln,
            "property_name": prop,
            "house_number": int(re.sub(r"[^0-9]", "", num)) if num else None,
            "street": street.upper(),
            "suffix": suffix,
            "suburb": suburb,
            "state": state,
            "postcode": int(pcode) if pcode else None
        })
    return results[:10]
