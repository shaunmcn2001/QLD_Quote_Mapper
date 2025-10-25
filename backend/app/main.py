from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body, Request
from fastapi.responses import StreamingResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from io import BytesIO
import re
import os

from app.services.pdf_address import (
    extract_text_from_pdf,
    parse_lotplan_from_text,
    parse_au_address_structured
)
from app.services.arcgis import (
    query_parcels_by_point,
    query_parcels_by_lotplan,
    query_parcels_from_address,
    to_kmz,
    normalize_lotplan,
    best_folder_name_from_parcels,
)

API_KEY = os.getenv("X_API_KEY", "")

app = FastAPI(title="Parcel Agent", version="0.4.0-qld")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AddressIn(BaseModel):
    property_name: Optional[str] = None
    house_number: Optional[str] = None
    street: Optional[str] = None
    suffix: Optional[str] = None
    suburb: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[int] = None
    original: Optional[str] = None

class AddressLookup(BaseModel):
    address: str
    relax_no_number: bool = False
    max_results: int = 500
    property_name: Optional[str] = None

_LOTPLAN_FINDER = re.compile(
    r"\d+[A-Z]?(?:\s*/\s*|\s*[-]?\s*)?(?:RP|SP|CP|DP|CH|CC|BUP|GTP|HBL|HBP)\s*\d+",
    re.IGNORECASE,
)

def _safe_folder_name(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum() or ch in " -_,")\
        .replace(",,", ",").strip().strip(",") or "parcels"

def _kmz_stream_response(features: List[Dict[str, Any]], folder_name: str):
    display_name = folder_name or "parcels"
    safe_name = _safe_folder_name(display_name)
    kmz_bytes = to_kmz(features, folder_name=display_name)
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.kmz"'}
    return StreamingResponse(BytesIO(kmz_bytes), media_type="application/vnd.google-earth.kmz", headers=headers)

def _extract_lotplan_tokens(raw: str) -> List[str]:
    if not raw:
        return []
    normalized = raw.replace("\n", ",").replace(";", ",")
    tokens = [tok.strip() for tok in normalized.split(",") if tok.strip()]
    if tokens:
        return tokens
    return [m.group(0) for m in _LOTPLAN_FINDER.finditer(raw)]

@app.middleware("http")
async def require_key(request: Request, call_next):
    if API_KEY:
        key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        if key != API_KEY:
            return JSONResponse(status_code=401, content={"detail":"Unauthorized"})
    return await call_next(request)

@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"

@app.post("/process_pdf_kmz")
async def process_pdf_kmz(
    pdf: UploadFile = File(...),
    state: Optional[str] = Query(None),
    max_results: int = Query(300, ge=1, le=2000),
    relax_no_number: bool = Query(False)
):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    content = await pdf.read()
    text = extract_text_from_pdf(content)

    # Try Lot/Plan from text first
    parcels: List[Dict[str,Any]] = []
    lotplans = parse_lotplan_from_text(text)
    if lotplans:
        for lp in lotplans[:100]:
            parcels.extend(query_parcels_by_lotplan(lp, max_results=max_results))
        # dedup
        seen = set(); uniq = []
        for f in parcels:
            props = f.get("properties", {}) or {}
            key = (props.get("objectid"), props.get("lotplan"))
            if key not in seen:
                uniq.append(f); seen.add(key)
        parcels = uniq

    folder_fallback = None
    addrs = parse_au_address_structured(text)
    if addrs and not parcels:
        for addr in addrs[:5]:
            base_label = addr["original"]
            if addr.get("property_name"):
                if base_label.lower().startswith(addr["property_name"].lower()):
                    folder_fallback = base_label
                else:
                    folder_fallback = f'{addr["property_name"]} {base_label}'
            else:
                folder_fallback = base_label
            # Query via Address layer -> lotplan -> Parcels
            hits = query_parcels_from_address(addr, relax_no_number=relax_no_number, max_results=max_results)
            if hits:
                parcels = hits
                break

    if not parcels:
        raise HTTPException(404, "No parcels found for the extracted details.")

    folder_name = best_folder_name_from_parcels(parcels, folder_fallback)
    return _kmz_stream_response(parcels, folder_name)

@app.get("/kmz_by_lotplan")
def kmz_by_lotplan(lotplan: str, max_results: int = Query(1000, ge=1, le=5000)):
    raw_tokens = _extract_lotplan_tokens(lotplan)
    if not raw_tokens:
        raise HTTPException(400, "Provide lot/plan tokens like '4rp30439, 3rp048958'.")
    if len(raw_tokens) > 50:
        raise HTTPException(400, "Too many lot/plan tokens provided (limit 50).")
    try:
        normalized_tokens = [normalize_lotplan(tok) for tok in raw_tokens]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    unique_tokens = list(dict.fromkeys(normalized_tokens))
    parcels: List[Dict[str,Any]] = []
    for tok in unique_tokens:
        parcels.extend(query_parcels_by_lotplan(tok, max_results=max_results))
    if not parcels:
        raise HTTPException(404, "No parcels found for given Lot/Plan token(s).")
    fallback = " & ".join(unique_tokens)[:120] or "lotplans"
    folder_name = best_folder_name_from_parcels(parcels, fallback)
    return _kmz_stream_response(parcels, folder_name)

@app.post("/kmz_by_address")
def kmz_by_address(query: AddressLookup):
    if not query.address.strip():
        raise HTTPException(400, "Address is required.")
    candidates = parse_au_address_structured(query.address)
    if not candidates:
        candidates = [{"original": query.address.strip()}]
    parcels: Optional[List[Dict[str, Any]]] = None
    fallback_label = query.property_name or (candidates[0].get("original") or query.address.strip())
    for candidate in candidates[:5]:
        candidate_payload = {**candidate}
        if query.property_name:
            candidate_payload["property_name"] = query.property_name
        relax = query.relax_no_number or candidate_payload.get("house_number") in (None, "")
        try:
            hits = query_parcels_from_address(candidate_payload, relax_no_number=relax, max_results=query.max_results)
        except ValueError:
            hits = []
        if hits:
            parcels = hits
            fallback_label = candidate_payload.get("original") or fallback_label
            break
    if not parcels:
        raise HTTPException(404, "No parcels found for the provided address.")
    if query.property_name and fallback_label:
        fallback_label = f"\"{query.property_name}\", {fallback_label}"
    folder_name = best_folder_name_from_parcels(parcels, fallback_label or "address")
    return _kmz_stream_response(parcels, folder_name)

@app.post("/kmz_by_address_fields")
def kmz_by_address_fields(addr: AddressIn, max_results: int = Query(1000, ge=1, le=5000), relax_no_number: bool = Query(False)):
    hits = query_parcels_from_address(addr.model_dump(), relax_no_number=relax_no_number, max_results=max_results)
    if not hits:
        raise HTTPException(404, "No parcels found from provided address.")
    fallback = addr.original or f"{addr.house_number or ''} {addr.street or ''}, {addr.suburb or ''}, {addr.state or 'QLD'} {addr.postcode or ''}"
    if addr.property_name:
        fallback = f"\"{addr.property_name}\", {fallback}"
    folder_name = best_folder_name_from_parcels(hits, fallback)
    return _kmz_stream_response(hits, folder_name)
