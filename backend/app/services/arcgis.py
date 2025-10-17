import os, json, requests, zipfile, io
from typing import List, Dict, Any, Optional, Tuple
from shapely.geometry import shape
import simplekml

BASE_MAPSERVER = os.getenv("QLD_MAPSERVER_BASE", "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer")
ADDRESS_LAYER = int(os.getenv("QLD_ADDRESS_LAYER", "0"))
PARCELS_LAYER = int(os.getenv("QLD_PARCELS_LAYER", "4"))
ARCGIS_TOKEN = os.getenv("ARCGIS_AUTH_TOKEN","")

ADDR = {
    "lotplan": "lotplan",
    "street_number": "street_number",
    "street_name": "street_name",
    "street_type": "street_type",
    "street_suffix": "street_suffix",
    "locality": "locality",
    "state": "state",
    "address": "address",
    "latitude": "latitude",
    "longitude": "longitude",
    "objectid": "objectid"
}
PAR = {
    "lotplan": "lotplan",
    "objectid": "objectid",
    "lot": "lot",
    "plan": "plan",
    "tenure": "tenure",
    "locality": "locality",
    "shire_name": "shire_name",
}

def _layer_url(layer_index: int) -> str:
    return f"{BASE_MAPSERVER.rstrip('/')}/{layer_index}"

def _query(layer_index: int, params: dict) -> dict:
    base = _layer_url(layer_index) + "/query"
    payload = {**params, "f": "geojson", "outFields": "*", "returnGeometry": "true", "outSR": 4326}
    if ARCGIS_TOKEN: payload["token"] = ARCGIS_TOKEN
    r = requests.get(base, params=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def _sql_escape(v: str) -> str:
    return v.replace("'", "''")

def address_where(addr: Dict[str,Any], relax_no_number: bool=False) -> str:
    parts = []
    if addr.get("original"):
        s = _sql_escape(addr["original"])
        parts.append(f"UPPER({ADDR['address']}) = UPPER('{s}')")
    if addr.get("house_number") is not None:
        parts.append(f"UPPER({ADDR['street_number']}) = UPPER('{_sql_escape(str(addr['house_number']))}')")
    elif not relax_no_number:
        raise ValueError("Missing house number and relax_no_number is False")
    if addr.get("street"):
        parts.append(f"UPPER({ADDR['street_name']}) LIKE '%{_sql_escape(addr['street'])}%'")
    if addr.get("suffix"):
        s = _sql_escape(addr["suffix"])
        parts.append(f"(UPPER({ADDR['street_type']}) LIKE '%{s}%' OR UPPER({ADDR['street_suffix']}) LIKE '%{s}%')")
    if addr.get("suburb"):
        parts.append(f"UPPER({ADDR['locality']}) = UPPER('{_sql_escape(addr['suburb'])}')")
    if addr.get("state"):
        parts.append(f"UPPER({ADDR['state']}) = UPPER('{_sql_escape(addr['state'])}')")
    return " AND ".join(parts) if parts else "1=1"

def resolve_lotplans_from_address(addr: Dict[str,Any], relax_no_number: bool=False, max_results: int=50) -> Tuple[List[str], Optional[Tuple[float,float]]]:
    w = address_where(addr, relax_no_number=relax_no_number)
    data = _query(ADDRESS_LAYER, {"where": w, "resultRecordCount": max_results})
    feats = data.get("features", [])
    lps: List[str] = []
    pt: Optional[Tuple[float,float]] = None
    for f in feats:
        p = f.get("properties", {}) or {}
        lp = (p.get(ADDR["lotplan"]) or "").strip()
        if lp:
            lps.append(lp.upper())
        lat = p.get(ADDR["latitude"]); lon = p.get(ADDR["longitude"])
        if lat is not None and lon is not None and pt is None:
            try:
                pt = (float(lat), float(lon))
            except Exception:
                pass
    lps = list(dict.fromkeys(lps))
    return lps, pt

def query_parcels_by_lotplan(lotplan_token: str, max_results: int=500) -> List[Dict[str,Any]]:
    lp = _sql_escape(lotplan_token.strip().upper())
    data = _query(PARCELS_LAYER, {"where": f"UPPER({PAR['lotplan']}) LIKE '%{lp}%'", "resultRecordCount": max_results})
    return data.get("features", [])

def query_parcels_by_point(lat: float, lon: float, max_results: int=50) -> List[Dict[str,Any]]:
    geom = {"x": float(lon), "y": float(lat), "spatialReference": {"wkid": 4326}}
    params = {"geometry": json.dumps(geom), "geometryType": "esriGeometryPoint", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "resultRecordCount": max_results}
    data = _query(PARCELS_LAYER, params)
    return data.get("features", [])

def query_parcels_from_address(addr: Dict[str,Any], relax_no_number: bool=False, max_results: int=500) -> List[Dict[str,Any]]:
    lotplans, pt = resolve_lotplans_from_address(addr, relax_no_number=relax_no_number, max_results=max_results)
    out: List[Dict[str,Any]] = []
    for lp in lotplans:
        out.extend(query_parcels_by_lotplan(lp, max_results=max_results))
    if not out and pt:
        out = query_parcels_by_point(pt[0], pt[1], max_results=max_results)
    seen = set(); uniq = []
    for f in out:
        p = f.get("properties", {}) or {}
        key = (p.get(PAR["objectid"]), p.get(PAR["lotplan"]))
        if key not in seen:
            uniq.append(f); seen.add(key)
    return uniq

# KML styling
def _apply_style(pol):
    from simplekml import Color
    pol.style.polystyle.color = Color.rgb(0xA2, 0x3F, 0x97, 102)  # ~40% alpha
    pol.style.polystyle.fill = 1
    pol.style.linestyle.color = Color.rgb(0xA2, 0x3F, 0x97, 255)
    pol.style.linestyle.width = 3

def _add_feature_to_folder(kml_folder, f):
    geom = f.get("geometry")
    props = f.get("properties", {}) or {}
    if not geom: return
    shp = shape(geom)
    name = props.get(PAR["lotplan"]) or f"Parcel {props.get(PAR['objectid'],'')}" or "parcel"
    keep = ["lot","plan","lotplan","shire_name","locality","tenure"]
    desc_lines = [f"{k}: {props.get(k)}" for k in keep if props.get(k) not in (None, "")]
    desc = "\n".join(desc_lines)
    if shp.geom_type == "Polygon":
        coords = list(shp.exterior.coords)
        pol = kml_folder.newpolygon(name=name, description=desc)
        pol.outerboundaryis = [(x, y) for (x, y) in coords]
        for interior in getattr(shp, "interiors", []):
            pol.innerboundaryis = [[(x, y) for (x, y) in interior.coords]]
        _apply_style(pol)
    elif shp.geom_type == "MultiPolygon":
        for idx, poly in enumerate(shp.geoms):
            coords = list(poly.exterior.coords)
            pol = kml_folder.newpolygon(name=f"{name} ({idx+1})", description=desc)
            pol.outerboundaryis = [(x, y) for (x, y) in coords]
            for interior in getattr(poly, "interiors", []):
                pol.innerboundaryis = [[(x, y) for (x, y) in interior.coords]]
            _apply_style(pol)
    else:
        x, y = shp.representative_point().x, shp.representative_point().y
        kml_folder.newpoint(name=name, description=desc, coords=[(x, y)])

def to_kmz(features: List[Dict[str,Any]], folder_name: str = "parcels") -> bytes:
    import simplekml
    kml = simplekml.Kml()
    fol = kml.newfolder(name=folder_name)
    for f in features:
        _add_feature_to_folder(fol, f)
    kml_bytes = kml.kml().encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_bytes)
    return buf.getvalue()
