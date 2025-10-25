# (same as before, shortened for brevity in this template)
import io, re
from typing import List, Optional, Dict, Any, Tuple
from pdfminer.high_level import extract_text as pdfminer_extract
from pdfminer.pdfpage import PDFPage
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

def _pdfminer_page_texts(pdf_bytes: bytes) -> List[str]:
    pages: List[str] = []
    try:
        with io.BytesIO(pdf_bytes) as buffer:
            page_numbers = list(enumerate(PDFPage.get_pages(buffer), start=1))
        if not page_numbers:
            return pages
        for index, _ in page_numbers:
            try:
                text = pdfminer_extract(io.BytesIO(pdf_bytes), page_numbers=[index - 1])
            except Exception:
                text = ""
            pages.append(text or "")
    except Exception:
        return []
    return pages

def _ocr_page_texts(pdf_bytes: bytes) -> List[str]:
    try:
        images = convert_from_bytes(pdf_bytes, dpi=250)
    except Exception:
        return []
    ocr_texts: List[str] = []
    for image in images:
        try:
            ocr_texts.append(pytesseract.image_to_string(image))
        except Exception:
            ocr_texts.append("")
    return ocr_texts

def extract_pdf_pages(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    pdfminer_pages = _pdfminer_page_texts(pdf_bytes)
    pages_out: List[Dict[str, Any]] = []
    has_useful_pdfminer = any(txt.strip() for txt in pdfminer_pages)

    if pdfminer_pages:
        pages_out = [
            {"page_number": idx + 1, "text": txt, "source": "pdfminer"}
            for idx, txt in enumerate(pdfminer_pages)
        ]

    if not pages_out or not has_useful_pdfminer:
        ocr_texts = _ocr_page_texts(pdf_bytes)
        if ocr_texts:
            pages_out = [
                {"page_number": idx + 1, "text": txt, "source": "ocr"}
                for idx, txt in enumerate(ocr_texts)
            ]
            return pages_out

    # Fill blank pdfminer pages with OCR if available
    if pages_out:
        missing_indexes = [idx for idx, page in enumerate(pages_out) if not page["text"].strip()]
        if missing_indexes:
            ocr_texts = _ocr_page_texts(pdf_bytes)
            for idx in missing_indexes:
                if idx < len(ocr_texts) and ocr_texts[idx].strip():
                    pages_out[idx] = {
                        "page_number": idx + 1,
                        "text": ocr_texts[idx],
                        "source": "ocr",
                    }
    return pages_out

def _address_key(addr: Dict[str, Any]) -> Tuple:
    return (
        addr.get("original") or "",
        addr.get("property_name") or "",
        addr.get("house_number") or "",
        addr.get("street") or "",
        addr.get("suffix") or "",
        addr.get("suburb") or "",
        addr.get("state") or "",
        addr.get("postcode") or "",
    )

def parse_address_and_lotplans(line: str) -> Optional[Dict[str, Any]]:
    cleaned = line.replace(" – ", " - ").replace("—", "-")
    if " - " not in cleaned:
        return None
    addr_part, lot_part = cleaned.split(" - ", 1)
    addr_candidates = parse_au_address_structured(addr_part)
    if not addr_candidates:
        return None
    lot_tokens = parse_lotplan_from_text(lot_part)
    if not lot_tokens:
        return None
    primary = addr_candidates[0]
    return {
        "address": primary,
        "raw_address": addr_part.strip(),
        "lotplans": lot_tokens,
    }

def extract_pdf_insights(pdf_bytes: bytes) -> Dict[str, Any]:
    pages = extract_pdf_pages(pdf_bytes)
    lotplan_records: List[Dict[str, Any]] = []
    address_records: List[Dict[str, Any]] = []
    seen_lotplans: Dict[str, Dict[str, Any]] = {}
    seen_addresses: Dict[Tuple, Dict[str, Any]] = {}
    groups: List[Dict[str, Any]] = []
    seen_groups: set[Tuple[str, Tuple[str, ...]]] = set()

    for page in pages:
        text = page.get("text", "")
        if not text.strip():
            continue
        page_number = page.get("page_number")
        source = page.get("source")

        for line in text.splitlines():
            grp = parse_address_and_lotplans(line.strip())
            if grp:
                lots_tuple = tuple(grp["lotplans"])
                key = (grp["raw_address"].upper(), lots_tuple)
                if key not in seen_groups:
                    groups.append({
                        **grp,
                        "page_number": page_number,
                        "extraction_source": source,
                    })
                    seen_groups.add(key)

        for lp in parse_lotplan_from_text(text):
            lp_norm = lp.upper()
            if lp_norm not in seen_lotplans:
                seen_lotplans[lp_norm] = {
                    "lotplan": lp_norm,
                    "page_number": page_number,
                    "extraction_source": source,
                }

        for addr in parse_au_address_structured(text):
            key = _address_key(addr)
            if key not in seen_addresses:
                seen_addresses[key] = {
                    "page_number": page_number,
                    "extraction_source": source,
                    "address": addr,
                }

    lotplan_records = list(seen_lotplans.values())
    address_records = list(seen_addresses.values())

    summary = {
        "total_pages": len(pages),
        "lotplans_found": len(lotplan_records),
        "addresses_found": len(address_records),
    }

    return {
        "summary": summary,
        "pages": pages,
        "lotplans": sorted(lotplan_records, key=lambda r: (r["page_number"], r["lotplan"])),
        "addresses": sorted(address_records, key=lambda r: r["page_number"]),
        "address_lotplan_groups": sorted(groups, key=lambda g: g["page_number"]),
    }

_LOTPLAN_PATTERN = re.compile(
    r"(?:(?:LOT|L)\s*(\d+[A-Z]?)(?:\s*ON)?)\s*(?:[-/\\]|\s)+([A-Z]{1,4}\d+)",
    re.IGNORECASE,
)
_LOTPLAN_SLASH_PATTERN = re.compile(
    r"(\d+[A-Z]?)\s*/\s*([A-Z]{1,4}\d+)",
    re.IGNORECASE,
)

def parse_lotplan_from_text(text: str) -> List[str]:
    found: List[str] = []
    for pattern in (_LOTPLAN_PATTERN, _LOTPLAN_SLASH_PATTERN):
        for m in pattern.finditer(text):
            lot = (m.group(1) or "").upper()
            plan = (m.group(2) or "").upper()
            if not lot or not plan:
                continue
            token = f"{lot} {plan}"
            if token not in found:
                found.append(token)
    return found

def parse_au_address_structured(text: str) -> List[Dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    results = []
    pat = re.compile(
        r'(?:^"?(?P<prop>[^",]+?)"?\s*,?\s+)?'
        r'(?:(?P<number>\d{1,5}[A-Z]?)\s+)?'
        r'(?P<street>[A-Za-z0-9 .\'\-]+?)\s+'
        r'(?P<suffix>Road|Rd|Street|St|Avenue|Ave|Highway|Hwy|Drive|Dr|Court|Ct|Place|Pl|Boulevard|Blvd|Way|Lane|Ln|Crescent|Cres|Terrace|Tce|Close|Cl)?'
        r'\s*,\s*(?P<suburb>[A-Za-z ]+)\s*(?:,\s*|\s+)(?P<state>QLD|NSW|VIC|SA|WA|TAS|NT|ACT)\b'
        r'(?:\s+(?P<pcode>\d{4}))?\s*$',
        re.I,
    )
    for ln in lines:
        m = pat.search(ln.replace(" – ", " - ").replace("—", "-"))
        if not m: continue
        prop = (m.group("prop") or "").strip(' "\'')
        num = m.group("number")
        if not num and prop and re.fullmatch(r"\d+[A-Z]?", prop):
            num = prop
            prop = ""
        street = (m.group("street") or "").replace(" - ", "-").replace(" -", "-").replace("- ", "-")
        suffix = (m.group("suffix") or "").upper()
        suburb = (m.group("suburb") or "").upper()
        state = (m.group("state") or "").upper()
        pcode = m.group("pcode")
        results.append({
            "original": ln,
            "property_name": prop,
            "house_number": num.strip().upper() if num else None,
            "street": street.upper(),
            "suffix": suffix,
            "suburb": suburb,
            "state": state,
            "postcode": int(pcode) if pcode else None
        })
    return results[:10]
