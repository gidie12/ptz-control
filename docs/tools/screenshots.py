import sys
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; URL = "http://127.0.0.1:8080/?notour"
YT = "ytUpdate({type:'youtube', channel:'@kerkzwolle', live:true, upcoming:false, video_id:null, title:'Kerkdienst zondag 10:00', checked:'09:58:12', error:null});"
LIVE = "lastPreset={links:1, midden:2, rechts:4}; renderPresets(); renderSide();"
def page(b, theme="dark", w=1440, h=900):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    ctx.add_init_script(f"localStorage.setItem('theme','{theme}'); localStorage.setItem('tourDone','1'); localStorage.setItem('ytAuto','0');")
    pg = ctx.new_page(); pg.goto(URL); pg.wait_for_timeout(1500); pg.evaluate(YT + LIVE); pg.wait_for_timeout(300)
    return pg
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = page(b); pg.screenshot(path=f"{OUT}/hoofdscherm.png")
    pg.click("#btnYt"); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/youtube-menu.png", clip={"x": 640, "y": 0, "width": 800, "height": 520}); pg.keyboard.press("Escape")
    pg.click("#btnPad"); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/menu.png", clip={"x": 840, "y": 0, "width": 600, "height": 520}); pg.keyboard.press("Escape")
    pg.click("#presetArea .tile .edit"); pg.wait_for_timeout(500); pg.screenshot(path=f"{OUT}/preset-bewerken.png"); pg.click("#pCancel")
    pg.click("#btnPad"); pg.click("#btnSettings"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/cameras.png"); pg.click("#btnCancel")
    pg.click("#btnPad"); pg.click("#btnKeys"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/sneltoetsen.png"); pg.click("#keysCancel")
    pg.click("#btnMap2"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/knoppenindeling.png"); pg.click("#btnMapClose")
    pg.click("#btnPad"); pg.click("#btnBackup"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/back-up.png"); pg.keyboard.press("Escape")
    pg.evaluate("setWall(true)"); pg.wait_for_timeout(600); pg.screenshot(path=f"{OUT}/volledig-scherm.png"); pg.evaluate("setWall(false)")
    pg.evaluate("tourStart()"); pg.wait_for_timeout(300)
    for _ in range(2): pg.click("#tourNext")
    pg.wait_for_timeout(500); pg.screenshot(path=f"{OUT}/rondleiding.png")
    pg.evaluate("tourEnd(); updPreview = true; updRender()"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/update-melding.png", clip={"x": 1040, "y": 600, "width": 400, "height": 300}); pg.evaluate("updPreview = false; updRender()")
    pl = page(b, "light"); pl.screenshot(path=f"{OUT}/hoofdscherm-licht.png")
    pm = page(b, "dark", 390, 844); pm.screenshot(path=f"{OUT}/telefoon.png", full_page=False)
    b.close()
print("klaar")
