"""Read job-specific metadata without guessing workplaces from arbitrary city mentions."""
import json
import re
from urllib.parse import unquote, urlsplit

LABEL = r"(?:Standort(?:e)?|Arbeitsort(?:e)?|Einsatzort(?:e)?|Dienstort|Ort|Job\s+Location|Location(?:s)?)"
PLACEHOLDERS = {"alle", "all", "bitte wählen", "please select", "standort", "standorte", "location", "locations", "ort", "arbeitsort", "einsatzort"}


def text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def node_value(node):
    if node is None:
        return ""
    return text(node.get("content") or node.get("value") or node.get_text(" ", strip=True))


def unique(values):
    seen = set()
    result = []
    for value in values:
        value = text(value)
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


def postings(soup):
    roots = []
    for script in soup.find_all("script"):
        if str(script.get("type", "")).split(";")[0].strip().lower() != "application/ld+json":
            continue
        try:
            roots.append(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue

    def walk(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    nodes = list(walk(roots))
    refs = {node["@id"]: node for node in nodes if isinstance(node.get("@id"), str) and len(node) > 1}

    def resolve(value, visited=frozenset(), depth=0):
        if depth > 12:
            return {}
        if isinstance(value, list):
            return [resolve(item, visited, depth + 1) for item in value]
        if not isinstance(value, dict):
            return value
        identity = value.get("@id")
        if isinstance(identity, str) and identity in refs and identity not in visited:
            return resolve({**refs[identity], **value}, visited | {identity}, depth + 1)
        return {key: resolve(item, visited, depth + 1) for key, item in value.items()}

    for node in nodes:
        kinds = node.get("@type", [])
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if any(str(kind).rstrip("/").rsplit("/", 1)[-1] == "JobPosting" for kind in kinds):
            yield resolve(node)


def select_posting(candidates, url, soup):
    if len(candidates) == 1:
        return candidates[0]
    decoded = unquote(url)
    ids = re.findall(r"(?:jobDetailId=|/job/|/)([\w-]*\d[\w-]{3,})(?=/|&|$)", decoded)
    for candidate in candidates:
        identity = json.dumps([candidate.get("identifier"), candidate.get("url"), candidate.get("@id")], ensure_ascii=False)
        if any(re.search(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", identity) for identifier in ids):
            return candidate
        target = candidate.get("url")
        if isinstance(target, str) and urlsplit(target) == urlsplit(url):
            return candidate
    heading = soup.select_one("h1")
    matches = [candidate for candidate in candidates if heading and text(candidate.get("title")).casefold() == node_value(heading).casefold()]
    return matches[0] if len(matches) == 1 else {}


def job_scope(soup):
    for selector in ("main.job-detail", "[role='dialog'] main", "[role='dialog'] article", ".jobDisplayShell", "[itemscope][itemtype$='/JobPosting']", "main", "article", "[role='main']"):
        nodes = soup.select(selector)
        if len(nodes) == 1:
            return nodes[0]
    return soup.body or soup


def valid_place(value):
    value = re.sub(r"^" + LABEL + r"\s*:\s*", "", text(value), flags=re.I).strip(" :;|")
    if not value or value.casefold() in PLACEHOLDERS or len(value) > 180 or not re.search(r"[^\W\d_]", value):
        return ""
    if re.match(r"(?:Nach .+ suchen|Kontakt|Ansprechpartner|Ihre Aufgaben|Your responsibilities)\b", value, re.I):
        return ""
    return value


def location_values(value):
    if isinstance(value, list):
        return [place for item in value for place in location_values(item)]
    if isinstance(value, str):
        return [valid_place(value)]
    if not isinstance(value, dict):
        return []
    address = value.get("address", value)
    if isinstance(address, str):
        return [valid_place(address)]
    if isinstance(address, dict):
        city = address.get("addressLocality")
        if city:
            return location_values(city)
    return [valid_place(value.get("name", ""))]


def eligible(node, scope, location=False):
    for parent in [node, *node.parents]:
        if parent.name in {"footer", "nav", "aside"} or parent.get("id") in {"similar-jobs", "related-jobs"}:
            return False
        if location and "hiringOrganization" in parent.get("itemprop", []):
            return False
        if parent is scope:
            break
    return node.name not in {"select", "option", "input"} and node.find_parent("select") is None


def locations_from_text(description):
    # A label must have its own value; search filters and subsequent headings aren't locations.
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        match = re.fullmatch(LABEL + r"\s*:\s*(.*)", line, re.I)
        if match:
            value = match[1] or (lines[index + 1] if index + 1 < len(lines) else "")
            next_field = re.match(r"[\w /-]{1,40}:\s*", value)
            if valid_place(value) and not next_field and not re.match(r"(?:Stellen-ID|Referenz|Vertrag|Unternehmens|Bewerb|Aufgaben|Anforderungen|Profil|Remotefähig)\w*\b", value, re.I):
                return valid_place(value)
    match = re.search(r"(?:der\s+)?(?:Einsatzort|Arbeitsort|Dienstort)\s+ist\s+([^\n.!?]{2,100})", description, re.I)
    return valid_place(match[1]) if match else ""


def extract_metadata(soup, job):
    scope = job_scope(soup)
    values = {}
    sources = {}

    def put(key, value, source):
        if value and not values.get(key):
            values[key] = value
            sources[key] = source

    put("title", text(job.get("title")), "JobPosting-Daten: Jobtitel")
    company = job.get("hiringOrganization", {})
    put("company", text(company.get("name")) if isinstance(company, dict) else text(company) if isinstance(company, str) else "", "JobPosting-Daten: Unternehmen")
    places = unique(place for place in location_values(job.get("jobLocation")) if place)
    put("location", "; ".join(places), "JobPosting-Daten: Arbeitsort")
    if isinstance(job.get("description"), str):
        put("description", job["description"], "JobPosting-Daten: Anzeigentext")

    selectors = {
        "title": ["[itemprop~='title']", "[data-careersite-propertyid='title']"],
        "company": ["[itemprop~='hiringOrganization'] [itemprop~='name']", "meta[itemprop~='hiringOrganization']", "[data-careersite-propertyid='companyname']", "[data-careersite-propertyid='facility']", "[itemprop~='hiringOrganization']"],
        "location": ["[itemprop~='jobLocation'] [itemprop~='addressLocality']", ".jobGeoLocation", "[data-careersite-propertyid='location']", "[data-careersite-propertyid='city']", "[data-testid='job-location']", ".job-location", "#job-location"],
        "employment_type": ["[itemprop~='employmentType']", "[data-careersite-propertyid='shift']"],
        "hours": ["[itemprop~='workHours']"],
        "deadline": ["[itemprop~='validThrough']"],
    }
    for key, candidates in selectors.items():
        for selector in candidates:
            nodes = [node for node in scope.select(selector) if eligible(node, scope, location=key == "location")]
            found = unique((valid_place(node_value(node)) if key == "location" else node_value(node)) for node in nodes)
            put(key, "; ".join(found) if key == "location" else next(iter(found), ""), "Metadaten der Stellenanzeige")
            if values.get(key):
                break
    for node in scope.select("[itemprop~='description'], [data-careersite-propertyid='description']"):
        if eligible(node, scope):
            put("description", str(node), "Anzeigentext der Stellenanzeige")
            break
    # Metadata in the document head can supplement an otherwise valid, scoped ad.
    for key, selector in (("location", 'meta[name="job-location"], meta[property="job:location"]'), ("company", 'meta[itemprop="hiringOrganization"]')):
        for node in soup.head.select(selector) if soup.head else []:
            value = valid_place(node_value(node)) if key == "location" else node_value(node)
            put(key, value, "Metadaten der Stellenanzeige")
    return values, sources, scope
