from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body, Request
from fastapi.responses import StreamingResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from io import BytesIO
import re
import os

from app.services.pdf_address import (
    parse_lotplan_from_text,
    parse_au_address_structured,
    extract_pdf_insights,
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

class LotPlanGroup(BaseModel):
    label: Optional[str] = None
    address: Optional[AddressIn] = None
    lotplans: Optional[List[str]] = None
    relax_no_number: Optional[bool] = None

class GroupedKmzRequest(BaseModel):
    groups: List[LotPlanGroup]
    default_label: Optional[str] = None
    max_results: int = 1000

_LOTPLAN_FINDER = re.compile(
    r"\d+[A-Z]?(?:\s*/\s*|\s*[-]?\s*)?[A-Z]{1,4}\s*\d+",
    re.IGNORECASE,
)

def _safe_folder_name(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum() or ch in " -_,")\
        .replace(",,", ",").strip().strip(",") or "parcels"

def _kmz_stream_response(features: List[Dict[str, Any]], folder_name: str, grouped: Optional[Dict[str, List[Dict[str, Any]]]] = None):
    display_name = folder_name or "parcels"
    safe_name = _safe_folder_name(display_name)
    kmz_bytes = to_kmz(features, folder_name=display_name, grouped_features=grouped)
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}.kmz"'}
    return StreamingResponse(BytesIO(kmz_bytes), media_type="application/vnd.google-earth.kmz", headers=headers)

def _extract_lotplan_tokens(raw: str) -> List[str]:
    if not raw:
        return []
    normalized = raw.replace("\n", ",").replace(";", ",").replace("&", ",")
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

@app.post("/analyze_pdf")
async def analyze_pdf(pdf: UploadFile = File(...)):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    content = await pdf.read()
    try:
        insights = extract_pdf_insights(content)
    except Exception as exc:
        raise HTTPException(500, f"Failed to analyze PDF: {exc}") from exc
    return insights

@app.post("/kmz_by_groups")
def kmz_by_groups(payload: GroupedKmzRequest):
    if not payload.groups:
        raise HTTPException(400, "Provide at least one group entry.")
    grouped_features: Dict[str, List[Dict[str, Any]]] = {}
    all_parcels: List[Dict[str, Any]] = []
    labels: List[str] = []

    for group in payload.groups:
        lot_tokens = group.lotplans or []
        features: List[Dict[str, Any]] = []
        for token in lot_tokens:
            token = token.strip()
            if not token:
                continue
            try:
                norm = normalize_lotplan(token)
            except ValueError as exc:
                raise HTTPException(400, f"Unsupported lot/plan token: {token}") from exc
            features.extend(query_parcels_by_lotplan(norm, max_results=payload.max_results))
        addr_payload: Optional[Dict[str, Any]] = group.address.model_dump() if group.address else None
        relax_flag = group.relax_no_number if group.relax_no_number is not None else False
        if not features and addr_payload:
            try:
                features = query_parcels_from_address(addr_payload, relax_no_number=relax_flag, max_results=payload.max_results)
            except ValueError:
                features = []
        if not features:
            continue
        fallback_label = group.label
        if not fallback_label and addr_payload:
            fallback_label = addr_payload.get("original")
        folder_label = best_folder_name_from_parcels(features, fallback_label)
        grouped_features.setdefault(folder_label, []).extend(features)
        labels.append(folder_label)
        all_parcels.extend(features)

    if not all_parcels:
        raise HTTPException(404, "No parcels found for the provided groups.")

    root_label = payload.default_label
    if not root_label:
        root_label = " & ".join(dict.fromkeys(labels))[:120] or "parcels"
    return _kmz_stream_response([], root_label, grouped=grouped_features)

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
    insights = extract_pdf_insights(content)
    pages = insights.get("pages", [])
    full_text = "\n".join(page.get("text", "") for page in pages)

    grouped_features: Dict[str, List[Dict[str, Any]]] = {}
    all_parcels: List[Dict[str, Any]] = []
    ungrouped_parcels: List[Dict[str, Any]] = []
    processed_tokens: set[str] = set()
    used_addresses: set[str] = set()
    group_labels: List[str] = []

    groups = insights.get("address_lotplan_groups", []) or []
    for group in groups:
        structured_addr = group.get("address") or {}
        raw_address = group.get("raw_address") or (structured_addr.get("original") if structured_addr else None)
        lot_tokens = group.get("lotplans") or []
        group_parcels: List[Dict[str, Any]] = []
        for token in lot_tokens:
            raw_token = token.strip()
            if not raw_token:
                continue
            try:
                norm_token = normalize_lotplan(raw_token)
            except ValueError:
                norm_token = raw_token.replace(" ", "").upper()
            if norm_token in processed_tokens:
                continue
            processed_tokens.add(norm_token)
            group_parcels.extend(query_parcels_by_lotplan(norm_token, max_results=max_results))
        if not group_parcels and structured_addr:
            try:
                group_parcels = query_parcels_from_address(structured_addr, relax_no_number=relax_no_number, max_results=max_results)
            except ValueError:
                group_parcels = []
        if group_parcels:
            fallback_label = raw_address or structured_addr.get("original")
            folder_label = best_folder_name_from_parcels(group_parcels, fallback_label)
            grouped_features.setdefault(folder_label, []).extend(group_parcels)
            group_labels.append(folder_label)
            all_parcels.extend(group_parcels)
            if structured_addr.get("original"):
                used_addresses.add(structured_addr["original"])

    lotplan_records = insights.get("lotplans", []) or []
    for record in lotplan_records:
        token = record.get("lotplan", "")
        if not token:
            continue
        try:
            norm = normalize_lotplan(token)
        except ValueError:
            norm = token.replace(" ", "").upper()
        if norm in processed_tokens:
            continue
        processed_tokens.add(norm)
        hits = query_parcels_by_lotplan(norm, max_results=max_results)
        ungrouped_parcels.extend(hits)
        all_parcels.extend(hits)

    # Remaining addresses not already used by groups
    address_records = insights.get("addresses", []) or []
    for record in address_records:
        addr = record.get("address") or {}
        original = addr.get("original")
        if original and original in used_addresses:
            continue
        try:
            hits = query_parcels_from_address(addr, relax_no_number=relax_no_number, max_results=max_results)
        except ValueError:
            hits = []
        if hits:
            folder_label = best_folder_name_from_parcels(hits, original or addr.get("street"))
            grouped_features.setdefault(folder_label, []).extend(hits)
            group_labels.append(folder_label)
            all_parcels.extend(hits)
            if original:
                used_addresses.add(original)

    # Fallback: try parsing text directly if insights missed something
    if not all_parcels:
        lotplans = parse_lotplan_from_text(full_text)
        for lp in lotplans[:100]:
            try:
                norm = normalize_lotplan(lp)
            except ValueError:
                norm = lp.replace(" ", "").upper()
            hits = query_parcels_by_lotplan(norm, max_results=max_results)
            ungrouped_parcels.extend(hits)
            all_parcels.extend(hits)
        if not all_parcels:
            text_addresses = parse_au_address_structured(full_text)
            for addr in text_addresses[:5]:
                try:
                    hits = query_parcels_from_address(addr, relax_no_number=relax_no_number, max_results=max_results)
                except ValueError:
                    hits = []
                if hits:
                    folder_label = best_folder_name_from_parcels(hits, addr.get("original"))
                    grouped_features.setdefault(folder_label, []).extend(hits)
                    group_labels.append(folder_label)
                    all_parcels.extend(hits)
                    break

    if not all_parcels:
        raise HTTPException(404, "No parcels found for the extracted details.")

    folder_name = None
    if group_labels:
        folder_name = " & ".join(dict.fromkeys(group_labels))[:120] or None
    if not folder_name:
        folder_name = best_folder_name_from_parcels(all_parcels, None)
    return _kmz_stream_response(ungrouped_parcels, folder_name, grouped=grouped_features or None)

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
