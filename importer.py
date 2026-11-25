"""Read public job ads. No login, form filling, or submission."""
import ipaddress
import json
import os
import re
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

import certifi
from bs4 import BeautifulSoup

import tracker
from taxonomy import infer_job_type

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(tracker.ROOT / ".browsers"))
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job-import")
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def validate_public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Nur öffentliche HTTP- oder HTTPS-Stellenlinks sind möglich.")
    if parts.port and parts.port not in {80, 443}:
        raise ValueError("Bitte einen öffentlichen Stellenlink ohne besonderen Port verwenden.")
    addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Lokale und interne Netzwerkadressen können nicht importiert werden.")
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_html(url):
    validate_public_url(url)
    opener = build_opener(SafeRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    with opener.open(Request(url, headers={"User-Agent": AGENT, "Accept-Language": "de-DE,de;q=0.9"}), timeout=25) as response:
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Dieser Link führt nicht zu einer lesbaren Webseite.")
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Die Seite ist zu groß für den automatischen Import.")
        return raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def plain(html):
    return BeautifulSoup(str(html), "html.parser").get_text("\n", strip=True)


def structured_jobs(soup):
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                yield value
            for key, item in value.items():
                if key in {"@graph", "mainEntity", "itemListElement", "item"}:
                    yield from walk(item)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue


def first_match(pattern, text, flags=re.I):
    found = re.search(pattern, text, flags)
    return found.group(0).strip() if found else ""


def parse_html(html, url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = list(structured_jobs(soup))
    job = candidates[0] if len(candidates) == 1 else {}
    # Avoid importing the first item of a whole job board as the selected job.
    if len(candidates) > 1:
        ids = re.findall(r"(?:jobDetailId=|/job/|/)(\d{4,})", url)
        for candidate in candidates:
            identity = json.dumps([candidate.get("identifier"), candidate.get("url")])
            if any(identifier in identity for identifier in ids):
                job = candidate
                break
    title = plain(job.get("title", ""))
    # Some boards show a job in a dialog beside the full result list.
    detail = soup.select_one("main.job-detail, [role='dialog'] main, [role='dialog'] article")
    if detail:
        soup = BeautifulSoup(str(detail), "html.parser")
    for unwanted in soup.select("script, style, nav, footer, aside, noscript, svg, [role='navigation'], #onetrust-banner-sdk, #onetrust-consent-sdk, .cookie-banner"):
        unwanted.decompose()
    for header in soup.select("header"):
        if not header.select_one("h1"):
            header.decompose()
    heading = soup.select_one("h1")
    if not title:
        title = heading.get_text(" ", strip=True) if heading else ""
    if not title and soup.title:
        title = re.split(r"\s+[|–]\s+", soup.title.get_text(" ", strip=True))[0]
    description = plain(job.get("description", ""))
    main = soup.select_one("main, article, [role='main']") or soup.body or soup
    if not description:
        description = main.get_text("\n", strip=True)
        # On corporate sites the actual job starts at the heading, after navigation.
        if heading and title in description:
            description = description[description.find(title):]
    description = re.sub(r"\n{3,}", "\n\n", description)[:100000]
    company = job.get("hiringOrganization", {})
    company = company.get("name", "") if isinstance(company, dict) else ""
    hostname = urlsplit(url).hostname or ""
    known = {"avat.de": "AVAT", "personio.de": "AVAT" if "avat." in hostname else "", "transnetbw.de": "TransnetBW", "hensoldt.net": "HENSOLDT"}
    if not company:
        company = next((v for domain, v in known.items() if hostname == domain or hostname.endswith("." + domain)), "")
    if not company:
        meta = soup.select_one('meta[property="og:site_name"]')
        company = meta.get("content", "") if meta else hostname.removeprefix("www.")
    locations = job.get("jobLocation", [])
    locations = locations if isinstance(locations, list) else [locations]
    cities = []
    for location in locations:
        address = location.get("address", {}) if isinstance(location, dict) else {}
        if isinstance(address, dict) and address.get("addressLocality"):
            cities.append(address["addressLocality"])
    location = ", ".join(dict.fromkeys(cities))
    if not location:
        found = re.search(r"(?:Einsatzort\s+ist|(?:Standort|Arbeitsort|Dienstort)\s*:)[ \t\n]*([^\n.]{2,85})", description, re.I)
        if found:
            location = found.group(1).strip()
        else:
            location = first_match(r"\b(?:Stuttgart|Böblingen|Sindelfingen|Tübingen|Reutlingen|Walldorf|Ulm|München|Berlin|Karlsruhe|Esslingen|Filderstadt|Leinfelden-Echterdingen)\b", title + "\n" + description[:1200])
    hours = str(job.get("workHours", ""))
    if not hours:
        hours = first_match(r"(?:\b(?:bis\s+zu|mindestens|maximal|ca\.)\s+)?\b\d{1,2}(?:[.,]\d)?(?:\s*(?:–|-|bis)\s*\d{1,2}(?:[.,]\d)?)?\s*(?:Wochenstunden|(?:Stunden|Std\.?|h)\s*(?:/\s*(?:Woche|Wo\.?|week)|pro\s+Woche|wöchentlich))", description)
    remote = ""
    remote_percent = first_match(r"remotefähig\s*:\s*(?:bis\s*)?\d{1,3}\s*%", description)
    if remote_percent:
        remote = re.sub(r"\s+", " ", remote_percent).strip().capitalize()
    elif re.search(r"nicht\s+remotefähig|kein(?:e|en)?\s+(?:Homeoffice|Remote-Arbeit)|ausschließlich\s+vor\s+Ort", description, re.I):
        remote = "Vor Ort"
    elif str(job.get("jobLocationType", "")).upper() == "TELECOMMUTE" or re.search(r"100\s*%\s*(?:remote|Homeoffice)|vollständig\s+remote|fully\s+remote", description, re.I):
        remote = "Vollständig remote"
    elif re.search(r"hybrid", description, re.I):
        remote = "Hybrid · Umfang offen"
    elif re.search(r"mobil(?:es|en|em)?(?:\s+und\s+flexibl(?:es|en|em))?\s+Arbeiten|Homeoffice|Home-Office|remote", description, re.I):
        remote = "Mobiles Arbeiten · Umfang offen"
    salary = ""
    base = job.get("baseSalary", {})
    if isinstance(base, dict):
        value = base.get("value", {})
        if isinstance(value, dict):
            amount = value.get("value") or (f'{value["minValue"]}–{value["maxValue"]}' if "minValue" in value and "maxValue" in value else "")
            if amount:
                salary = f'{amount} {base.get("currency", "EUR")} / {value.get("unitText", "Zeitraum offen")}'
    if not salary:
        salary = first_match(r"\d{1,3}(?:[.,]\d{1,2})?\s*(?:€|EUR)(?:\s*/?\s*brutto)?\s*(?:/\s*Stunde|pro\s+Stunde|/\s*h)", description)
    # Keep industry inference narrow and visible as inference in sources.
    industry = ""
    combined = company + "\n" + description[:5000]
    if re.search(r"Energieversorgung|Energiewende|Stromnetz|Energiewirtschaft|erneuerbare.{0,15}Energie", combined, re.I):
        industry = "Energie"
    elif re.search(r"HENSOLDT|Rüstungsindustrie|Verteidigungsindustrie|Defence|Defense", combined, re.I):
        industry = "Rüstung"
    elif re.search(r"Cybersecurity|Cyber Security|IT-Sicherheit", combined, re.I):
        industry = "Cybersecurity"
    elif re.search(r"Versicherungsunternehmen|Versicherungsbranche", combined, re.I):
        industry = "Versicherungen"
    elif re.search(r"Bauunternehmen|Bauindustrie|Bauwirtschaft", combined, re.I):
        industry = "Bau"
    elif re.search(r"Automobilzulieferer", combined, re.I):
        industry = "Automobilzulieferer"
    elif re.search(r"Automobilhersteller|Automobilindustrie|Automotive", combined, re.I):
        industry = "Automobil"
    apply_url = ""
    for link in main.find_all("a", href=True):
        if re.search(r"(?:jetzt|hier|stelle|online|direkt|now).{0,20}(?:bewerben|apply)|auf diese Stelle bewerben|apply now", link.get_text(" ", strip=True), re.I):
            target = urljoin(url, link["href"])
            if urlsplit(target).scheme in {"http", "https"}:
                apply_url = target
                break
    data = dict(title=title, company=company, location=location, hours=hours, remote=remote, salary=salary,
                industry=industry, job_type=infer_job_type(title, job.get("employmentType", "")), deadline=str(job.get("validThrough", "")), description=description, apply_url=apply_url)
    data["sources"] = {key: ("Aus Anzeigentext abgeleitet · bitte prüfen" if key == "industry" else "Aus Stellenanzeige übernommen")
                       for key, value in data.items() if value}
    # A generic board title or consent page isn't a successfully imported ad.
    board = re.fullmatch(r"stellenangebote|karriere|jobs|careers|jobangebote|offene stellen|stellenübersicht|access denied|just a moment.*", title.strip(), re.I)
    useful = bool(title and not board and (apply_url or re.search(r"aufgaben|anforderungen|profil|wir bieten|qualifikationen|responsibilities|requirements|qualifications", description, re.I)))
    data["_complete"] = bool(job or useful and len(description) > 250)
    return data


def render_html(url):
    from playwright.sync_api import sync_playwright
    validate_public_url(url)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=AGENT, locale="de-DE", ignore_https_errors=False)
            validated = set()

            def route_request(route):
                target = route.request.url
                if urlsplit(target).scheme not in {"http", "https"}:
                    route.abort()
                    return
                if route.request.resource_type in {"image", "font", "media"}:
                    route.abort()
                    return
                try:
                    host = urlsplit(target).netloc
                    if host not in validated:
                        validate_public_url(target)
                        validated.add(host)
                    route.continue_()
                except (ValueError, OSError):
                    route.abort()

            page.route("**/*", route_request)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Allow asynchronous corporate career widgets to populate; bounded wait.
            try:
                if "jobDetailId=" in url:
                    page.wait_for_selector("main.job-detail h1, [role='dialog'] h1", timeout=15000)
                else:
                    page.wait_for_function("() => /Aufgaben|Anforderungen|Profil|Wir bieten|Responsibilities|Requirements|Qualifications/i.test(document.body.innerText) && document.body.innerText.length > 1000", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            content = page.content()
            # Include job content embedded in same- or cross-origin frames.
            for frame in page.frames[1:]:
                try:
                    frame_html = frame.content()
                    if parse_html(frame_html, frame.url).get("_complete"):
                        content = frame_html
                        break
                except Exception:
                    continue
            return content
        finally:
            browser.close()


def run_import(job_id):
    try:
        entry = tracker.get_job(job_id)
        url = entry["url"]
        data = {}
        if "jobDetailId=" not in url:
            try:
                data = parse_html(fetch_html(url), url)
            except Exception:
                pass
        if not data.get("_complete"):
            data = parse_html(render_html(url), url)
        complete = data.pop("_complete", False)
        if not complete:
            raise ValueError("Die Stellenanzeige konnte nicht eindeutig erkannt werden. Der Link bleibt gespeichert. Du kannst den Anzeigentext ergänzen oder den Import erneut versuchen.")
        data.update(import_state="ready", import_message="Anzeige übernommen. Offene Angaben kannst du später ergänzen.", imported_at=tracker.now())
        tracker.update_job(job_id, data, imported=True)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Die Seite konnte gerade nicht gelesen werden. Dein Link ist gespeichert. Erneut versuchen oder Anzeigentext einfügen."
        tracker.update_job(job_id, {"import_state": "error", "import_message": message}, imported=True)


def enqueue(job_id):
    tracker.update_job(job_id, {"import_state": "pending", "import_message": "Stellenanzeige wird gelesen …"}, imported=True)
    POOL.submit(run_import, job_id)


def import_text(job_id, text):
    from html import escape
    job = tracker.get_job(job_id)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(text.strip()) < 80:
        raise ValueError("Bitte den vollständigen Anzeigentext einfügen (mindestens 80 Zeichen).")
    title = next((line for line in lines[:15] if re.search(r"Werkstudent|Working Student|studentische", line, re.I)), lines[0])
    data = parse_html(f"<html><body><main><h1>{escape(title)}</h1><p>{escape(text)}</p></main></body></html>", job["url"])
    data.pop("_complete", None)
    data.update(import_state="ready", import_message="Aus eingefügtem Anzeigentext übernommen.", imported_at=tracker.now())
    return tracker.update_job(job_id, data, imported=True)
