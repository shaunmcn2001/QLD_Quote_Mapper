import os, json, requests, zipfile, io, re
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple
from shapely.geometry import shape, Polygon, MultiPolygon, GeometryCollection, mapping
from shapely.ops import unary_union
import simplekml
from functools import lru_cache

BASE_MAPSERVER = os.getenv("QLD_MAPSERVER_BASE", "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer")
ADDRESS_LAYER = int(os.getenv("QLD_ADDRESS_LAYER", "0"))
PARCELS_LAYER = int(os.getenv("QLD_PARCELS_LAYER", "3"))
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

_LOTPLAN_WITH_SPACE = re.compile(
    r"^(?P<lot>\d+[A-Z]?)\s+(?P<prefix>[A-Z]+)\s*(?P<number>\d+)$",
    re.IGNORECASE,
)
_LOTPLAN_COMPACT = re.compile(
    r"^(?P<lot>\d+[A-Z]?)(?P<prefix>[A-Z]+)(?P<number>\d+)$",
    re.IGNORECASE,
)

def _parse_lotplan_token(token: str) -> Optional[Tuple[str, str, str]]:
    if not token:
        return None
    raw = token.strip()
    if not raw:
        return None
    compact = re.sub(r"[\\/\-]+", " ", raw.strip().upper())
    compact = re.sub(r"\s+", " ", compact)
    m = _LOTPLAN_WITH_SPACE.match(compact)
    if not m:
        compact = re.sub(r"\s+", "", raw.strip().upper())
        m = _LOTPLAN_COMPACT.match(compact)
    if not m:
        return None
    lot = m.group("lot").upper()
    prefix = m.group("prefix").upper()
    number = m.group("number")
    plan = f"{prefix}{number}"
    compact = f"{lot}{plan}"
    return compact, lot, plan

def normalize_lotplan(token: str) -> str:
    parsed = _parse_lotplan_token(token)
    if not parsed:
        raise ValueError(f"Unsupported lot/plan token: {token}")
    return parsed[0]

def _format_address_label(attrs: Dict[str, Any]) -> Optional[str]:
    if not attrs:
        return None
    prop_name = (attrs.get("property_name") or "").strip()
    street_no = (attrs.get("street_number") or attrs.get("street_no_1") or "").strip()
    street_full = (attrs.get("street_full") or "").strip()
    street_name = (attrs.get("street_name") or "").strip()
    street_type = (attrs.get("street_type") or "").strip()
    street_suffix = (attrs.get("street_suffix") or "").strip()
    locality = (attrs.get("locality") or "").strip()
    state = (attrs.get("state") or "").strip()

    street_parts: List[str] = []
    if street_no:
        street_parts.append(street_no)
    if street_full:
        street_parts.append(street_full)
    else:
        textual = " ".join(part for part in [street_name, street_type, street_suffix] if part)
        if textual:
            street_parts.append(textual.strip())

    address_components = []
    if street_parts:
        address_components.append(" ".join(street_parts).strip())
    addr_field = (attrs.get("address") or "").strip()
    if not address_components and addr_field:
        # As a fallback use the raw address string
        address_components.append(addr_field)
    if locality:
        address_components.append(locality)
    if state:
        address_components.append(state)

    if not address_components:
        return None
    label = ", ".join(comp for comp in address_components if comp)
    if prop_name:
        return f"\"{prop_name}\", {label}"
    return label

@lru_cache(maxsize=1024)
def _address_label_for_lotplan(lotplan: str) -> Optional[str]:
    clean = (lotplan or "").strip()
    if not clean:
        return None
    try:
        clean = normalize_lotplan(clean)
    except ValueError:
        pass
    where = f"UPPER({ADDR['lotplan']}) = UPPER('{_sql_escape(clean)}')"
    data = _query(ADDRESS_LAYER, {"where": where, "resultRecordCount": 1})
    for feat in data.get("features", []):
        label = _format_address_label(feat.get("properties", {}) or {})
        if label:
            return label
    return None

def best_folder_name_from_parcels(parcels: List[Dict[str, Any]], fallback: Optional[str] = None) -> str:
    seen: set[str] = set()
    for feat in parcels:
        props = feat.get("properties", {}) or {}
        lotplan = (props.get(PAR["lotplan"]) or "").strip()
        if not lotplan:
            continue
        if lotplan in seen:
            continue
        seen.add(lotplan)
        norm_lp = lotplan
        try:
            norm_lp = normalize_lotplan(lotplan)
        except ValueError:
            pass
        label = _address_label_for_lotplan(norm_lp)
        if label:
            return label
    if fallback and fallback.strip():
        return fallback.strip()
    for feat in parcels:
        props = feat.get("properties", {}) or {}
        lotplan = (props.get(PAR["lotplan"]) or "").strip()
        if lotplan:
            return lotplan
    return "parcels"

def address_where(addr: Dict[str,Any], relax_no_number: bool=False) -> str:
    parts = []
    if addr.get("original"):
        s = _sql_escape(addr["original"])
        parts.append(f"UPPER({ADDR['address']}) = UPPER('{s}')")
    if addr.get("house_number") is not None:
        parts.append(f"UPPER({ADDR['street_number']}) = UPPER('{_sql_escape(str(addr['house_number']))}')")
    elif not relax_no_number and not addr.get("original"):
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
        lp_raw = (p.get(ADDR["lotplan"]) or "").strip()
        if lp_raw:
            try:
                lps.append(normalize_lotplan(lp_raw))
            except ValueError:
                lps.append(lp_raw.upper())
        lat = p.get(ADDR["latitude"]); lon = p.get(ADDR["longitude"])
        if lat is not None and lon is not None and pt is None:
            try:
                pt = (float(lat), float(lon))
            except Exception:
                pass
    lps = list(dict.fromkeys(lps))
    return lps, pt

def query_parcels_by_lotplan(lotplan_token: str, max_results: int=500) -> List[Dict[str,Any]]:
    parsed = _parse_lotplan_token(lotplan_token)
    if parsed:
        lotplan_compact, _, _ = parsed
        where = f"UPPER({PAR['lotplan']}) = UPPER('{_sql_escape(lotplan_compact)}')"
    else:
        lp = _sql_escape(lotplan_token.strip().upper())
        where = f"UPPER({PAR['lotplan']}) LIKE '%{lp}%'"
    data = _query(PARCELS_LAYER, {"where": where, "resultRecordCount": max_results})
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

def _collect_polygons(shp) -> List[Polygon]:
    if shp.is_empty:
        return []
    if isinstance(shp, Polygon):
        return [shp]
    if isinstance(shp, MultiPolygon):
        return list(shp.geoms)
    if isinstance(shp, GeometryCollection):
        polys: List[Polygon] = []
        for geom in shp.geoms:
            polys.extend(_collect_polygons(geom))
        return polys
    return []

def _merge_features_by_lotplan(features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Tuple[str, Dict[str, Any]]]] = defaultdict(list)
    passthrough: List[Dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties", {}) or {}
        lp_value = props.get(PAR["lotplan"])
        key = None
        display = None
        if isinstance(lp_value, str) and lp_value.strip():
            key = re.sub(r"\s+", "", lp_value.upper())
            display = lp_value.strip()
        elif props.get(PAR["objectid"]) is not None:
            key = f"OBJ_{props[PAR['objectid']]}"
            display = str(props[PAR["objectid"]])
        if key:
            grouped[key].append((display or key, feat))
        else:
            passthrough.append(feat)

    merged: List[Dict[str, Any]] = []
    for key, entries in grouped.items():
        display_name, first_feat = entries[0]
        geoms = []
        props_template: Dict[str, Any] = {}
        for _, feat in entries:
            geom = feat.get("geometry")
            if not geom:
                continue
            shp = shape(geom)
            if shp.is_empty:
                continue
            geoms.append(shp)
            if not props_template:
                props_template = feat.get("properties", {}) or {}
        if not geoms:
            continue
        unioned = unary_union(geoms) if len(geoms) > 1 else geoms[0]
        props_copy = {**props_template}
        if PAR["lotplan"] not in props_copy or not props_copy.get(PAR["lotplan"]):
            props_copy[PAR["lotplan"]] = display_name
        merged.append({
            "geometry": mapping(unioned),
            "properties": props_copy,
        })
    if merged or passthrough:
        return merged + passthrough
    return features

def _add_feature_to_folder(kml_folder, f):
    geom = f.get("geometry")
    props = f.get("properties", {}) or {}
    if not geom: return
    shp = shape(geom)
    name = props.get(PAR["lotplan"]) or f"Parcel {props.get(PAR['objectid'],'')}" or "parcel"
    keep = ["lot","plan","lotplan","shire_name","locality","tenure"]
    desc_lines = [f"{k}: {props.get(k)}" for k in keep if props.get(k) not in (None, "")]
    desc = "\n".join(desc_lines)
    polygons = _collect_polygons(shp)
    if polygons:
        if len(polygons) == 1:
            poly = polygons[0]
            kml_poly = kml_folder.newpolygon(name=name, description=desc)
            kml_poly.outerboundaryis = [(x, y) for (x, y) in poly.exterior.coords]
            interior_coords = [[(x, y) for (x, y) in interior.coords] for interior in getattr(poly, "interiors", [])]
            if interior_coords:
                kml_poly.innerboundaryis = interior_coords
            _apply_style(kml_poly)
        else:
            multi = kml_folder.newmultigeometry(name=name, description=desc)
            for poly in polygons:
                kml_poly = multi.newpolygon()
                kml_poly.outerboundaryis = [(x, y) for (x, y) in poly.exterior.coords]
                interior_coords = [[(x, y) for (x, y) in interior.coords] for interior in getattr(poly, "interiors", [])]
                if interior_coords:
                    kml_poly.innerboundaryis = interior_coords
                _apply_style(kml_poly)
    else:
        point = shp.representative_point()
        kml_folder.newpoint(name=name, description=desc, coords=[(point.x, point.y)])

def to_kmz(features: List[Dict[str,Any]], folder_name: str = "parcels") -> bytes:
    import simplekml
    kml = simplekml.Kml()
    fol = kml.newfolder(name=folder_name)
    merged_features = _merge_features_by_lotplan(features)
    for f in merged_features:
        _add_feature_to_folder(fol, f)
    kml_bytes = kml.kml().encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_bytes)
    return buf.getvalue()
