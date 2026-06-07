from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Metro area groupings — cities that share a metro area and don't require
# relocation from one another. Key: lowercase city name, value: metro ID.
# ---------------------------------------------------------------------------

METRO_AREAS: dict[str, str] = {
    # San Francisco Bay Area
    "san francisco": "sf_bay",
    "sf": "sf_bay",
    "oakland": "sf_bay",
    "berkeley": "sf_bay",
    "san jose": "sf_bay",
    "sj": "sf_bay",
    "palo alto": "sf_bay",
    "mountain view": "sf_bay",
    "sunnyvale": "sf_bay",
    "santa clara": "sf_bay",
    "cupertino": "sf_bay",
    "redwood city": "sf_bay",
    "menlo park": "sf_bay",
    "burlingame": "sf_bay",
    "foster city": "sf_bay",
    "san mateo": "sf_bay",
    "fremont": "sf_bay",
    "hayward": "sf_bay",
    "san ramon": "sf_bay",
    "walnut creek": "sf_bay",
    "pleasanton": "sf_bay",
    "milpitas": "sf_bay",
    "santa cruz": "sf_bay",
    # Los Angeles / Orange County
    "los angeles": "la",
    "la": "la",
    "santa monica": "la",
    "burbank": "la",
    "culver city": "la",
    "pasadena": "la",
    "glendale": "la",
    "el segundo": "la",
    "manhattan beach": "la",
    "torrance": "la",
    "long beach": "la",
    "irvine": "la",
    "orange": "la",
    "anaheim": "la",
    "santa ana": "la",
    "costa mesa": "la",
    "newport beach": "la",
    "huntington beach": "la",
    "garden grove": "la",
    "fullerton": "la",
    "los angeles county": "la",
    # New York / New Jersey
    "new york": "nyc",
    "nyc": "nyc",
    "manhattan": "nyc",
    "brooklyn": "nyc",
    "queens": "nyc",
    "bronx": "nyc",
    "jersey city": "nyc",
    "newark": "nyc",
    "hoboken": "nyc",
    "weehawken": "nyc",
    "new york city": "nyc",
    # Seattle
    "seattle": "seattle",
    "bellevue": "seattle",
    "redmond": "seattle",
    "kirkland": "seattle",
    "bothell": "seattle",
    "renton": "seattle",
    # Austin
    "austin": "austin",
    "round rock": "austin",
    "cedar park": "austin",
    # Boston
    "boston": "boston",
    "cambridge": "boston",
    "somerville": "boston",
    "waltham": "boston",
    "burlington": "boston",
    # Chicago
    "chicago": "chicago",
    "evanston": "chicago",
    "naperville": "chicago",
    # Denver
    "denver": "denver",
    "boulder": "denver",
    "aurora": "denver",
    "lakewood": "denver",
    # Washington DC
    "washington": "dc",
    "washington dc": "dc",
    "dc": "dc",
    "arlington": "dc",
    "mclean": "dc",
    "tysons": "dc",
    "bethesda": "dc",
    "silver spring": "dc",
    "reston": "dc",
    "herndon": "dc",
    # Dallas / Fort Worth
    "dallas": "dfw",
    "fort worth": "dfw",
    "plano": "dfw",
    "irving": "dfw",
    "richardson": "dfw",
    "frisco": "dfw",
    # Atlanta
    "atlanta": "atlanta",
    "alpharetta": "atlanta",
    "dunwoody": "atlanta",
    # Miami
    "miami": "miami",
    "fort lauderdale": "miami",
    "boca raton": "miami",
    # Phoenix
    "phoenix": "phoenix",
    "scottsdale": "phoenix",
    "tempe": "phoenix",
    "chandler": "phoenix",
    "mesa": "phoenix",
    # San Diego
    "san diego": "san_diego",
    "la jolla": "san_diego",
    # Portland
    "portland": "portland",
    "beaverton": "portland",
    "hillsboro": "portland",
    # Minneapolis
    "minneapolis": "minneapolis",
    "saint paul": "minneapolis",
    "st paul": "minneapolis",
    # Pittsburgh
    "pittsburgh": "pittsburgh",
    # Philadelphia
    "philadelphia": "philly",
    "king of prussia": "philly",
    # Salt Lake City
    "salt lake city": "slc",
    "provo": "slc",
    # Nashville
    "nashville": "nashville",
}

# States where we always treat the entire state as one area (small states or
# states where tech jobs cluster in a single region).
_SMALL_STATES = {"de", "ri", "vt", "nh", "me", "ct", "hi", "ak"}

# Full state/territory names → USPS abbreviation, so locations that spell the
# state out ("Stamford, Connecticut") resolve the same as abbreviated ones
# ("Stamford, CT").
_STATE_NAMES: dict[str, str] = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri",
    "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
    "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
    "district of columbia": "dc",
}


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and filler words."""
    text = text.lower().strip()
    text = re.sub(r"[,;|()\-]", " ", text)
    # Drop state abbreviations and country names that appear after the city
    text = re.sub(r"\b(usa?|united states|remote|hybrid|on.?site)\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_city(location: str) -> str:
    """Best-effort city extraction from strings like 'San Francisco, CA'."""
    if not location:
        return ""
    parts = [p.strip() for p in re.split(r"[,;|]", location) if p.strip()]
    # The first part is usually the city.
    return parts[0].lower() if parts else _normalise(location)


def _extract_state(location: str) -> str:
    """Extract the state/region as a lowercase USPS abbreviation if present.

    Prefers an explicit two-letter abbreviation ("Stamford, CT"); falls back to
    a spelled-out state name ("Stamford, Connecticut"). Abbreviation first so
    "Washington, DC" resolves to ``dc`` rather than Washington state.
    """
    match = re.search(r"\b([A-Z]{2})\b", location)
    if match:
        return match.group(1).lower()
    lowered = location.lower()
    for name, abbr in _STATE_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            return abbr
    return ""


def same_metro(loc1: str, loc2: str) -> bool:
    """
    Returns True when loc1 and loc2 are in the same metropolitan area
    (i.e. commuting distance — no relocation needed).
    """
    if not loc1 or not loc2:
        return False

    city1 = _extract_city(loc1)
    city2 = _extract_city(loc2)
    state1 = _extract_state(loc1)
    state2 = _extract_state(loc2)

    # Exact city match (same state or ambiguous) → same place
    if city1 == city2:
        return True

    # Both cities mapped to the same metro bucket
    metro1 = METRO_AREAS.get(city1)
    metro2 = METRO_AREAS.get(city2)
    if metro1 and metro2 and metro1 == metro2:
        return True

    # Small states: if both are in the same small state treat as same area
    if state1 and state1 == state2 and state1 in _SMALL_STATES:
        return True

    return False


def relocation_answer(user_location: str | None, job_location: str | None) -> str:
    """
    Returns "Yes" (relocation needed) or "No" (no relocation needed).
    Defaults to "Yes" when either location is missing or unparseable,
    since the candidate is open to relocation.
    """
    if not user_location or not job_location:
        return "Yes"

    # Remote jobs never require relocation
    jl = job_location.lower()
    if "remote" in jl or "anywhere" in jl or "worldwide" in jl:
        return "No"

    return "No" if same_metro(user_location, job_location) else "Yes"
