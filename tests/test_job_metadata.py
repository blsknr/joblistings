import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import importer
import tracker


def ad(location="", title="Pflegefachkraft"):
    return '<main><h1>' + title + '</h1><h2>Ihre Aufgaben</h2><p>' + 'Menschen begleiten und im Alltag unterstützen. ' * 10 + '</p>' + location + '<a href="/apply">Jetzt bewerben</a></main>'


class JobMetadataTests(unittest.TestCase):
    def test_successfactors_metadata_outside_description_is_used(self):
        html = '<div class="jobDisplayShell" itemscope itemtype="http://schema.org/JobPosting"><meta itemprop="hiringOrganization" content="Example Services GmbH"><h1><span itemprop="title">Werkstudent Logistik</span></h1><span itemprop="description">' + 'Ihre Aufgaben sind Planung und Organisation. ' * 10 + '</span><p><span class="jobGeoLocation">Obersontheim, DE, 74423</span></p><span data-careersite-propertyid="city">Obersontheim</span></div>'
        data = importer.parse_html(html, "https://example.com/job/123456/")
        self.assertEqual(data["location"], "Obersontheim, DE, 74423")
        self.assertEqual(data["company"], "Example Services GmbH")
        self.assertEqual(data["sources"]["location"], "Metadaten der Stellenanzeige")
        self.assertTrue(data["_complete"])

    def test_multiple_json_locations_and_address_strings(self):
        posting = {"@type": "JobPosting", "title": "Berater", "description": "Ihre Aufgaben", "hiringOrganization": "Example GmbH",
                   "jobLocation": [{"address": {"addressLocality": "Hamburg"}}, {"address": "Hannover"}, {"address": {"addressLocality": "Hamburg"}}]}
        data = importer.parse_html('<script type="application/ld+json">' + json.dumps(posting) + '</script>', "https://example.com/job")
        self.assertEqual(data["location"], "Hamburg; Hannover")
        self.assertEqual(data["company"], "Example GmbH")

    def test_graph_references_are_resolved(self):
        graph = {"@graph": [{"@id": "#job", "@type": "https://schema.org/JobPosting", "title": "Analyst", "description": "Daten auswerten", "jobLocation": {"@id": "#place"}, "hiringOrganization": {"@id": "#company"}},
                            {"@id": "#place", "@type": "Place", "address": {"@id": "#address"}},
                            {"@id": "#address", "@type": "PostalAddress", "addressLocality": "Bremen"},
                            {"@id": "#company", "@type": "Organization", "name": "Example GmbH"}]}
        data = importer.parse_html('<script type="application/ld+json; charset=utf-8">' + json.dumps(graph) + '</script>', "https://example.com/job")
        self.assertEqual(data["location"], "Bremen")
        self.assertEqual(data["company"], "Example GmbH")

    def test_selected_job_id_wins_over_first_related_posting(self):
        jobs = [{"@type": "JobPosting", "identifier": {"value": value}, "title": "Analyst " + value,
                 "description": "Daten auswerten", "jobLocation": {"address": {"addressLocality": city}}} for value, city in (("111111", "Berlin"), ("222222", "Dresden"))]
        html = '<script type="application/ld+json">' + json.dumps(jobs) + '</script>'
        data = importer.parse_html(html, "https://example.com/job/222222/")
        self.assertEqual(data["location"], "Dresden")
        self.assertEqual(data["title"], "Analyst 222222")

    def test_query_specific_postings_are_not_selected_by_shared_path(self):
        jobs = [{"@type": "JobPosting", "url": "https://example.com/job?id=" + value, "title": "Analyst " + value,
                 "description": "Daten auswerten", "jobLocation": {"address": {"addressLocality": city}}} for value, city in (("111", "Berlin"), ("222", "Dresden"))]
        data = importer.parse_html('<script type="application/ld+json">' + json.dumps(jobs) + '</script>', "https://example.com/job?id=222")
        self.assertEqual(data["location"], "Dresden")

    def test_mentions_footer_and_company_headquarters_are_not_workplaces(self):
        posting = {"@type": "JobPosting", "title": "Analyst", "description": "Ihre Aufgaben: Zusammenarbeit mit dem Team in Stuttgart.",
                   "hiringOrganization": {"name": "Example", "address": {"addressLocality": "Berlin"}}}
        html = '<script type="application/ld+json">' + json.dumps(posting) + '</script>' + ad() + '<footer><span class="jobGeoLocation">Stuttgart</span>Standort: Berlin</footer>'
        self.assertEqual(importer.parse_html(html, "https://example.com/job")["location"], "")

    def test_residence_requirement_is_not_a_remote_job_workplace(self):
        posting = {"@type": "JobPosting", "title": "Analyst", "description": "Ihre Aufgaben", "jobLocationType": "TELECOMMUTE", "applicantLocationRequirements": {"name": "Deutschland"}}
        data = importer.parse_html('<script type="application/ld+json">' + json.dumps(posting) + '</script>', "https://example.com/job")
        self.assertEqual(data["location"], "")
        self.assertEqual(data["remote"], "Vollständig remote")

    def test_labelled_place_is_not_limited_to_preselected_cities(self):
        for label in ("Standort:", "Arbeitsort:", "Einsatzort:", "Location:"):
            with self.subTest(label=label):
                data = importer.parse_html(ad(f'<dl><dt>{label}</dt><dd>Göttingen</dd></dl>'), "https://example.com/job")
                self.assertEqual(data["location"], "Göttingen")

    def test_search_filters_and_next_field_do_not_become_places(self):
        for markup in ('<p>Standort:</p><select><option>Alle</option><option>Berlin</option></select>', '<p>Standort:</p><p>Vertragsart: Unbefristet</p>'):
            with self.subTest(markup=markup):
                self.assertEqual(importer.parse_html(ad(markup), "https://example.com/job")["location"], "")


class ImportFallbackTests(unittest.TestCase):
    def test_browser_can_supply_missing_location_without_losing_static_fields(self):
        with patch("importer.fetch_html", return_value=ad()), patch("importer.render_html", return_value=ad('<p class="jobGeoLocation">Göttingen</p>')):
            data = importer.read_ad("https://example.com/job")
        self.assertEqual(data["location"], "Göttingen")
        self.assertEqual(data["import_state"], "ready")

    def test_failed_browser_does_not_destroy_a_readable_static_ad(self):
        with patch("importer.fetch_html", return_value=ad()), patch("importer.render_html", side_effect=RuntimeError("Browser unavailable")):
            data = importer.read_ad("https://example.com/job")
        self.assertEqual(data["title"], "Pflegefachkraft")
        self.assertEqual(data["import_state"], "ready")
        self.assertEqual(data["location"], "")

    def test_different_rendered_job_does_not_supply_location(self):
        with patch("importer.fetch_html", return_value=ad()), patch("importer.render_html", return_value=ad('<p class="jobGeoLocation">Berlin</p>', title="Buchhalter")):
            self.assertEqual(importer.read_ad("https://example.com/job")["location"], "")

    def test_manual_place_avoids_unnecessary_browser_enrichment(self):
        with patch("importer.fetch_html", return_value=ad()), patch("importer.render_html") as browser:
            importer.read_ad("https://example.com/job", manual_fields=["location"])
            browser.assert_not_called()

    def test_removed_page_is_reported_and_existing_data_survives(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tracker, "DATA_DIR", Path(directory)):
            tracker.initialize()
            job, _ = tracker.add_job("https://example.com/job")
            tracker.update_job(job["id"], {"title": "Meine Stelle", "notes": "Kontakt aufnehmen", "status": "Beworben", "location": "Hamburg"})
            with patch("importer.fetch_html", side_effect=importer.PageReadError(404)), patch("importer.render_html", side_effect=importer.PageReadError(404)), self.assertLogs("importer", level="ERROR"):
                importer.run_import(job["id"])
            saved = tracker.get_job(job["id"])
            self.assertEqual(saved["import_state"], "error")
            self.assertIn("404", saved["import_message"])
            self.assertEqual(saved["location"], "Hamburg")
            self.assertEqual(saved["status"], "Beworben")
            self.assertEqual(saved["notes"], "Kontakt aufnehmen")


if __name__ == "__main__":
    unittest.main()
