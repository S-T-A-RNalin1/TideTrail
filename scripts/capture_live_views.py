import time
from pathlib import Path
from playwright.sync_api import sync_playwright

out_dir = Path("docs/live_screenshots")
out_dir.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="chrome")
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    
    print("Loading app...")
    page.goto("http://127.0.0.1:8000/#theme=light&view=investigate")
    page.wait_for_timeout(3000)
    
    # Click the Run analysis button (#run)
    print("Triggering Run analysis...")
    page.click("#run")
    
    # Wait for the run button to re-enable after pipeline finishes
    page.wait_for_selector("#run:not([disabled])", timeout=45000)
    page.wait_for_timeout(3000)
    print("Pipeline execution finished.")
    
    # 1. Investigate View
    page.screenshot(path=str(out_dir / "live_investigate_light.png"))
    print("Saved live_investigate_light.png")
    
    # 2. Vessels View (Leaderboard)
    print("Switching to Vessels view...")
    page.click("button[data-view='vessels']")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(out_dir / "live_vessels_light.png"))
    print("Saved live_vessels_light.png")

    # 3. Drift View
    print("Switching to Drift view...")
    page.click("button[data-view='drift']")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(out_dir / "live_drift_light.png"))
    print("Saved live_drift_light.png")

    browser.close()
    print("All live screenshots captured successfully.")
