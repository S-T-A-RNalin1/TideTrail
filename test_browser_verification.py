from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge')
    page = browser.new_page(viewport={'width': 1600, 'height': 900})
    page.goto('http://127.0.0.1:8000')
    page.wait_for_selector('#inc-title')

    # 1. Turn on Optical (S2) checkbox
    optical_cb = page.locator('#layers input').first
    optical_cb.check()
    time.sleep(2)
    page.screenshot(path='verified_optical_layer.png')
    print('Captured optical layer screenshot')

    # 2. Select Santa Barbara Channel natural seeps
    sb_btn = page.locator('.scene-row', has_text='Santa Barbara')
    sb_btn.click()
    time.sleep(1)

    # Click Run Analysis
    run_btn = page.locator('#run')
    run_btn.click()
    print('Clicked Run analysis on Santa Barbara')

    # Wait for busy overlay to appear then disappear
    page.wait_for_selector('#busy.on', state='attached', timeout=5000)
    page.wait_for_selector('#busy.on', state='detached', timeout=60000)
    time.sleep(2)

    page.screenshot(path='verified_santa_barbara_run.png')
    print('Captured Santa Barbara run screenshot')
    browser.close()
