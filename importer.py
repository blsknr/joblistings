"""Read public job ads. No login, form filling, or submission."""
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

import certifi
from bs4 import BeautifulSoup

import tracker
from taxonomy import infer_job_type
from job_metadata import extract_metadata, locations_from_text, postings, select_posting

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(tracker.ROOT / ".browsers"))
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job-import")
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class PageReadError(ValueError):
    def __init__(self, status):
        if status in {404, 410}:
            message = f"Die Anzeige ist unter diesem Link nicht mehr erreichbar (HTTP {status}). Dein gespeicherter Eintrag bleibt erhalten."
        elif status in {401, 403, 429}:
            message = f"Die Karriereseite erlaubt den automatischen Abruf gerade nicht (HTTP {status}). Bitte später erneut versuchen oder den Anzeigentext einfügen."
        else:
            message = f"Die Karriereseite antwortet mit einem Fehler (HTTP {status}). Bitte später erneut versuchen."
        super().__init__(message)


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
    try:
        response = opener.open(Request(url, headers={"User-Agent": AGENT, "Accept-Language": "de-DE,de;q=0.9"}), timeout=25)
    except HTTPError as exc:
        exc.close()
        raise PageReadError(exc.code) from exc
    with response:
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
    yield from postings(soup)


def first_match(pattern, text, flags=re.I):
    found = re.search(pattern, text, flags)
    return found.group(0).strip() if found else ""


def parse_html(html, url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = list(structured_jobs(soup))
    job = select_posting(candidates, url, soup)
    metadata, metadata_sources, scope = extract_metadata(soup, job)
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    site_meta = soup.select_one('meta[property="og:site_name"]')
    site_name = site_meta.get("content", "") if site_meta else ""
    og_title = soup.select_one('meta[property="og:title"]')
    title = plain(metadata.get("title", ""))
    soup = BeautifulSoup(str(scope), "html.parser")
    for unwanted in soup.select("script, style, nav, footer, aside, noscript, svg, [role='navigation'], #onetrust-banner-sdk, #onetrust-consent-sdk, .cookie-banner"):
        unwanted.decompose()
    for header in soup.select("header"):
        if not header.select_one("h1"):
            header.decompose()
    heading = soup.select_one("h1")
    if not title:
        title = heading.get_text(" ", strip=True) if heading else ""
    if not title:
        title = og_title.get("content", "") if og_title else re.split(r"\s+[|–]\s+", page_title)[0]
    description = plain(metadata.get("description", ""))
    main = soup.select_one("main, article, [role='main']") or soup.body or soup
    if not description:
        description = main.get_text("\n", strip=True)
        # On corporate sites the actual job starts at the heading, after navigation.
        if heading and title in description:
            description = description[description.find(title):]
    description = re.sub(r"\n{3,}", "\n\n", description)[:100000]
    company = metadata.get("company", "")
    hostname = urlsplit(url).hostname or ""
    known = {"avat.de": "AVAT", "personio.de": "AVAT" if "avat." in hostname else "", "transnetbw.de": "TransnetBW", "hensoldt.net": "HENSOLDT"}
    if not company:
        company = next((v for domain, v in known.items() if hostname == domain or hostname.endswith("." + domain)), "")
    if not company:
        company = site_name or hostname.removeprefix("www.")
    location = metadata.get("location") or locations_from_text(description) or locations_from_text(main.get_text("\n", strip=True))
    hours = str(job.get("workHours") or metadata.get("hours") or "")
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
                industry=industry, job_type=infer_job_type(title, job.get("employmentType") or metadata.get("employment_type", "")), deadline=str(job.get("validThrough") or metadata.get("deadline") or ""), description=description, apply_url=apply_url)
    data["sources"] = {key: ("Aus Anzeigentext abgeleitet · bitte prüfen" if key == "industry" else "Aus Stellenanzeige übernommen")
                       for key, value in data.items() if value}
    data["sources"].update({key: source for key, source in metadata_sources.items() if key in data and data[key]})
    if location and not metadata.get("location"):
        data["sources"]["location"] = "Beschrifteter Arbeitsort im Anzeigentext"
    # A generic board title or consent page isn't a successfully imported ad.
    board = re.fullmatch(r"stellenangebote|karriere|jobs|careers|jobangebote|offene stellen|stellenübersicht|access denied|just a moment.*", title.strip(), re.I)
    useful = bool(title and not board and (apply_url or re.search(r"aufgaben|anforderungen|profil|wir bieten|qualifikationen|responsibilities|requirements|qualifications", description, re.I)))
    ambiguous = len(candidates) > 1 and not job
    data["_complete"] = not ambiguous and bool(job.get("title") and job.get("description") or useful and len(description) > 250)
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
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if response and response.status >= 400:
                raise PageReadError(response.status)
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


def read_ad(url, manual_fields=()):
    data = {}
    error = None
    if "jobDetailId=" not in url:
        try:
            data = parse_html(fetch_html(url), url)
        except Exception as exc:
            error = exc
    missing_location = not data.get("location") and data.get("remote") != "Vollständig remote" and "location" not in manual_fields
    if not data.get("_complete") or missing_location:
        try:
            rendered = parse_html(render_html(url), url)
            if rendered.get("_complete"):
                if not data.get("_complete"):
                    data = rendered
                elif re.sub(r"\W+", "", data["title"]).casefold() == re.sub(r"\W+", "", rendered["title"]).casefold():
                    # Enrich the same ad; never replace a good static result with a shell,
                    # a different job, or empty browser fields.
                    for key, value in rendered.items():
                        if key not in {"sources", "_complete"} and value and not data.get(key):
                            data[key] = value
                            if key in rendered["sources"]:
                                data["sources"][key] = rendered["sources"][key]
        except Exception as exc:
            error = exc
    if not data.pop("_complete", False):
        if isinstance(error, PageReadError):
            raise error
        raise ValueError("Die Stellenanzeige konnte nicht eindeutig erkannt werden. Der Link bleibt gespeichert. Du kannst den Anzeigentext ergänzen oder den Import erneut versuchen.")
    message = "Anzeige übernommen. Offene Angaben kannst du später ergänzen." if data.get("location") else "Anzeige übernommen. Der Standort ist auf der Seite nicht eindeutig erkennbar."
    data.update(import_state="ready", import_message=message, imported_at=tracker.now())
    return data


def run_import(job_id):
    try:
        entry = tracker.get_job(job_id)
        data = read_ad(entry["url"], entry["manual_fields"])
        tracker.update_job(job_id, data, imported=True)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Die Seite konnte gerade nicht gelesen werden. Dein Link ist gespeichert. Erneut versuchen oder Anzeigentext einfügen."
        tracker.update_job(job_id, {"import_state": "error", "import_message": message}, imported=True)
        logging.getLogger(__name__).error("Stellenimport %s fehlgeschlagen: %s", job_id, message)


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
