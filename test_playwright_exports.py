import sys
import os
import time
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        failed_requests = []
        page.on("response", lambda res: failed_requests.append(f"{res.status} {res.url}") if res.status >= 400 else None)
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("1. Navigating to http://127.0.0.1:8000...")
        page.goto("http://127.0.0.1:8000")
        page.wait_for_load_state("networkidle")

        print("2. Switching to Method tab before running a job...")
        page.locator('button.navlink[data-view="method"]').click()
        page.wait_for_timeout(500)

        btn_json = page.locator("#ex-json")
        btn_geo = page.locator("#ex-geo")
        btn_note_pdf = page.locator("#ex-note")
        btn_note_html = page.locator("#ex-note-html")

        print("3. Checking buttons initially disabled:")
        print("   JSON:", btn_json.inner_text(), "Disabled:", btn_json.is_disabled())
        print("   GeoJSON:", btn_geo.inner_text(), "Disabled:", btn_geo.is_disabled())
        print("   PDF:", btn_note_pdf.inner_text(), "Disabled:", btn_note_pdf.is_disabled())
        print("   HTML:", btn_note_html.inner_text(), "Disabled:", btn_note_html.is_disabled())
        assert btn_json.is_disabled()
        assert btn_geo.is_disabled()
        assert btn_note_pdf.is_disabled()
        assert btn_note_html.is_disabled()

        sample_job = "job_20260907T105656_b095b5"
        print(f"4. Navigating to stored job with Method view: #job={sample_job}&view=method...")
        page.goto(f"http://127.0.0.1:8000/#job={sample_job}&view=method")
        page.wait_for_load_state("networkidle")

        # Explicitly ensure Method tab is selected
        page.locator('button.navlink[data-view="method"]').click()
        page.wait_for_selector("#ex-json:not([disabled])", timeout=10000)

        print("5. Checking buttons enabled after job load:")
        print("   JSON:", btn_json.inner_text(), "Disabled:", btn_json.is_disabled())
        print("   GeoJSON:", btn_geo.inner_text(), "Disabled:", btn_geo.is_disabled())
        print("   PDF:", btn_note_pdf.inner_text(), "Disabled:", btn_note_pdf.is_disabled())
        print("   HTML:", btn_note_html.inner_text(), "Disabled:", btn_note_html.is_disabled())
        assert not btn_json.is_disabled()
        assert not btn_geo.is_disabled()
        assert not btn_note_pdf.is_disabled()
        assert not btn_note_html.is_disabled()

        # Test download of Job JSON
        print("6. Testing Job JSON export...")
        with page.expect_download() as download_info:
            btn_json.click()
        download = download_info.value
        json_path = download.path()
        print(f"   Downloaded: {download.suggested_filename} ({os.path.getsize(json_path)} bytes)")
        assert download.suggested_filename == f"tidetrace_{sample_job}.json"

        # Test download of GeoJSON
        print("7. Testing GeoJSON export...")
        with page.expect_download() as download_info:
            btn_geo.click()
        download = download_info.value
        geo_path = download.path()
        print(f"   Downloaded: {download.suggested_filename} ({os.path.getsize(geo_path)} bytes)")
        assert download.suggested_filename == f"tidetrace_{sample_job}.geojson"

        # Test download of Attribution Note PDF
        print("8. Testing Attribution Note (PDF) export...")
        with page.expect_download() as download_info:
            btn_note_pdf.click()
        download = download_info.value
        pdf_path = download.path()
        pdf_size = os.path.getsize(pdf_path)
        with open(pdf_path, "rb") as f:
            header = f.read(5)
        print(f"   Downloaded: {download.suggested_filename} ({pdf_size} bytes, header={header})")
        assert download.suggested_filename == f"attribution_{sample_job}.pdf"
        assert header == b"%PDF-", "PDF header invalid!"
        assert pdf_size > 5000, "PDF suspiciously small!"

        # Test Alt+click on HTML button to download HTML
        print("9. Testing Attribution Note (HTML) download via Alt-click...")
        with page.expect_download() as download_info:
            btn_note_html.click(modifiers=["Alt"])
        download = download_info.value
        html_path = download.path()
        html_size = os.path.getsize(html_path)
        with open(html_path, "r", encoding="utf-8") as f:
            html_text = f.read()
        print(f"   Downloaded: {download.suggested_filename} ({html_size} bytes)")
        assert download.suggested_filename == f"attribution_{sample_job}.html"
        assert "Maritime Pollution Attribution Note" in html_text
        assert "SHA-256" in html_text
        assert "Chain of Custody" in html_text

        # Test regular click on HTML button (opens popup in new tab)
        print("10. Testing Attribution Note (HTML) popup view...")
        with page.expect_popup() as popup_info:
            btn_note_html.click()
        popup = popup_info.value
        popup.wait_for_load_state("networkidle")
        print(f"   Popup URL: {popup.url}")
        print(f"   Popup Title: {popup.title()}")
        popup_body = popup.content()
        assert "Maritime Pollution Attribution Note" in popup_body
        assert "SHA-256" in popup_body
        popup.close()

        # Capture a screenshot of the export section in Method tab
        screenshot_path = "verified_method_exports.png"
        page.screenshot(path=screenshot_path)
        print(f"11. Screenshot saved to {screenshot_path}")

        print(f"Failed requests (first 5): {failed_requests[:5]}")
        # Map tiles or optical chips may return 404 in offline dev environment, which is normal for Leaflet tilelayers
        js_runtime_errors = [e for e in console_errors if "Failed to load resource" not in e]
        print(f"JS runtime errors: {js_runtime_errors}")
        assert len(js_runtime_errors) == 0, f"Encountered JS runtime errors: {js_runtime_errors}"

        browser.close()
        print("\n>>> ALL PLAYWRIGHT EXPORT TESTS PASSED PERFECTLY! <<<")

if __name__ == "__main__":
    main()
