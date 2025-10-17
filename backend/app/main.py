from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Body, Request
from fastapi.responses import StreamingResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from io import BytesIO
import os

from app.services.pdf_address import (
    extract_text_from_pdf,
    parse_addresses_from_text,
    parse_lotplan_from_text,
    parse_au_address_structured
)
from app.services.arcgis import (
    query_parcels_by_point,
    query_parcels_by_lotplan,
    query_parcels_from_address,
    to_kmz,
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
    house_number: Optional[int] = None
    street: Optional[str] = None
    suffix: Optional[str] = None
    suburb: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[int] = None
    original: Optional[str] = None

def _safe_folder_name(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum() or ch in " -_,")\
        .replace(",,", ",").strip().strip(",") or "parcels"

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

    folder_name = None
    addrs = parse_au_address_structured(text)
    if addrs and not parcels:
        for addr in addrs[:5]:
            base_label = addr["original"]
            if addr.get("property_name"):
                if base_label.lower().startswith(addr["property_name"].lower()):
                    folder_name = base_label
                else:
                    folder_name = f'{addr["property_name"]} {base_label}'
            else:
                folder_name = base_label
            # Query via Address layer -> lotplan -> Parcels
            hits = query_parcels_from_address(addr, relax_no_number=relax_no_number, max_results=max_results)
            if hits:
                parcels = hits
                break

    if not parcels:
        raise HTTPException(404, "No parcels found for the extracted details.")

    if not folder_name:
        props = parcels[0].get("properties",{}) if parcels else {}
        folder_name = props.get("lotplan") or "parcels"

    kmz_bytes = to_kmz(parcels, folder_name=_safe_folder_name(folder_name))
    headers = {"Content-Disposition": f'attachment; filename="{_safe_folder_name(folder_name)}.kmz"'}
    return StreamingResponse(BytesIO(kmz_bytes), media_type="application/vnd.google-earth.kmz", headers=headers)

@app.get("/kmz_by_lotplan")
def kmz_by_lotplan(lotplan: str, max_results: int = Query(1000, ge=1, le=5000)):
    tokens = [t.strip() for t in lotplan.split(",") if t.strip()]
    if not tokens:
        raise HTTPException(400, "Provide ?lotplan=2 RP12345 or comma-separated list.")
    parcels: List[Dict[str,Any]] = []
    for tok in tokens:
        parcels.extend(query_parcels_by_lotplan(tok, max_results=max_results))
    if not parcels:
        raise HTTPException(404, "No parcels found for given Lot/Plan token(s).")
    folder_name = " & ".join(tokens)[:120]
    kmz_bytes = to_kmz(parcels, folder_name=_safe_folder_name(folder_name or "lotplans"))
    headers = {"Content-Disposition": f'attachment; filename="{_safe_folder_name(folder_name)}.kmz"'}
    return StreamingResponse(BytesIO(kmz_bytes), media_type="application/vnd.google-earth.kmz", headers=headers)

@app.post("/kmz_by_address_fields")
def kmz_by_address_fields(addr: AddressIn, max_results: int = Query(1000, ge=1, le=5000), relax_no_number: bool = Query(False)):
    hits = query_parcels_from_address(addr.model_dump(), relax_no_number=relax_no_number, max_results=max_results)
    if not hits:
        raise HTTPException(404, "No parcels found from provided address.")
    folder_name = (addr.property_name + " " if addr.property_name else "") + (addr.original or f"{addr.house_number or ''} {addr.street or ''}, {addr.suburb or ''}, {addr.state or 'QLD'} {addr.postcode or ''}")
    kmz_bytes = to_kmz(hits, folder_name=_safe_folder_name(folder_name))
    headers = {"Content-Disposition": f'attachment; filename="{_safe_folder_name(folder_name)}.kmz"'}
    return StreamingResponse(BytesIO(kmz_bytes), media_type="application/vnd.google-earth.kmz", headers=headers)
