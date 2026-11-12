import base64, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import oefen  # noqa: E402  (nagebootste camera tekent het OSD-menu)
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; URL = "http://127.0.0.1:8080/?notour"
YT = "ytUpdate({type:'youtube', channel:'@kerkzwolle', live:true, upcoming:false, video_id:null, title:'Kerkdienst zondag 10:00', checked:'09:58:12', error:null});"
LIVE = ("lastPreset={links:1, midden:2, rechts:4}; "
        "camst={links:{ok:true,checked:'09:58:00'},midden:{ok:true,checked:'09:58:00'},rechts:{ok:true,checked:'09:58:00'}}; "
        "moving={rechts:{n:4}}; renderTabs(); renderPresets(); renderSide();")
# nagemaakt activiteitenlog (demodata, geen echte dienst)
LOG = """(() => { const d = new Date(); d.setHours(9, 41, 0, 0); const t0 = d.getTime() / 1000; const r = [
 [0, 'systeem', 'systeem', '', 'PTZ Control gestart (versie v1.0.6, workspace Standaard)'],
 [12, 'systeem', 'systeem', '', 'Videomixer verbonden (V-1HD)'],
 [300, 'actie', 'Mac regie (deze computer)', 'Links', 'Preset 1 (Totaal) opgeroepen'],
 [420, 'actie', 'iPad Jan (192.168.100.23)', 'Midden', 'Preset 2 (Spreker close) opgeroepen'],
 [455, 'systeem', 'systeem', '', 'Mixer: Midden in beeld'],
 [610, 'actie', 'Mac regie (deze computer)', 'Rechts', 'Handbediening', 4],
 [640, 'actie', 'Mac regie (deze computer)', 'Rechts', 'Preset 4 (Band breed) opgeslagen'],
 [700, 'fout', 'systeem', 'Rechts', 'Camera niet bereikbaar: geen verbinding'],
 [735, 'systeem', 'systeem', 'Rechts', 'Camera weer bereikbaar'],
 [900, 'systeem', 'systeem', '', 'YouTube-stream live: Kerkdienst zondag 10:00'],
 [1130, 'actie', 'iPad Jan (192.168.100.23)', 'Links', 'Preset 2 (Preekstoel) opgeroepen'],
 [1160, 'systeem', 'systeem', '', 'Mixer: Links in beeld'],
 [1300, 'fout', 'Telefoon (192.168.100.40)', '', 'Bediening geweigerd: geen pincode ingevoerd', 2],
 [950, 'lied', 'Voordienst app', '', 'Liederen geladen: Zondag 10 november (4 liederen): Opwekking 733; Psalm 42:1 en 3; 10.000 redenen; Opwekking 710'],
 [960, 'lied', 'ProPresenter', '', 'Playlist: Zondag 10:00 (7 items): Welkom en mededelingen; Opwekking 733; Psalm 42:1 en 3; 10.000 redenen; Preek: Ruth 1; Opwekking 710; Zegen'],
 [1020, 'lied', 'ProPresenter', '', '▶ Opwekking 733 - Heer, U bent mijn leven (2 van 7 in Zondag 10:00, voordienst nr. 1)'],
 [1380, 'lied', 'ProPresenter', '', '▶ Psalm 42:1 en 3 (3 van 7 in Zondag 10:00, voordienst nr. 2)'],
 [1500, 'actie', 'Mac regie (deze computer)', 'Midden', 'Persoon volgen aan'],
]; logEntries = new Map(r.map((x, i) => [i + 1, { id: i + 1, t: t0 + x[0], kind: x[1], who: x[2], cam: x[3], msg: x[4], n: x[5] || 1, last: t0 + x[0] + 40 }]));
$('#logDevName').value = 'Mac regie'; logCams(); logRender(); })()"""
def osd_jpeg():     # beeld van een nagebootste camera met het OSD-menu open (verzonnen, geen echte camera)
    c = oefen.Cam("Links", "DEMO"); c.osd_set(True)
    for k in ("down", "down", "enter", "down"):
        c.osd_key(k)
    c._stop.set()
    return "data:image/jpeg;base64," + base64.b64encode(c.jpeg()).decode()
def volg_jpeg():    # getekend podium met drie personen (verzonnen) voor het venster Volgen instellen
    import io
    from PIL import Image, ImageDraw
    W, H = 960, 540
    im = Image.new("RGB", (W, H)); d = ImageDraw.Draw(im)
    for yy in range(H):
        t = yy / H; d.line([(0, yy), (W, yy)], fill=(int(70 + 60*t), int(52 + 38*t), int(44 + 22*t)))
    d.rectangle([0, int(H * .74), W, H], fill=(96, 64, 40))
    d.rectangle([W//2 - 9, 50, W//2 + 9, 210], fill=(230, 205, 150)); d.rectangle([W//2 - 55, 98, W//2 + 55, 116], fill=(230, 205, 150))
    for x, shirt, s in ((300, (40, 60, 90), 1.0), (560, (150, 50, 40), 0.95), (800, (60, 110, 70), 0.8)):
        top = int(H * .2 + (1 - s) * 120)
        d.ellipse([x - 26*s, top, x + 26*s, top + 56*s], fill=(222, 186, 160))
        d.rounded_rectangle([x - 46*s, top + 60*s, x + 46*s, H * .74 + 10], radius=int(18*s), fill=shirt)
        d.rectangle([x - 34*s, H * .74 + 10, x + 34*s, H - 8], fill=(45, 40, 40))
    b = io.BytesIO(); im.save(b, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
VOLG = """(() => { trkCamId = 'midden'; $('#dlgTrack').showModal();
  track.midden = {type:'track', cam:'midden', on:true, status:'volgt gekozen persoon', picked:true, fps:11, detector:'yolox'};
  cfg.cameras.find(c => c.id === 'midden').track_frame = {l: 0.22, r: 0.62, x: 0.36};
  trkLookData = {source:'volgbeeld', aspect:16/9, people:[
    {i:0, cx:0.3125, cy:0.505, w:0.1, h:0.59, target:true}, {i:1, cx:0.583, cy:0.515, w:0.095, h:0.57, target:false},
    {i:2, cx:0.833, cy:0.54, w:0.08, h:0.52, target:false}]};
  trkLim = {limits: {left: -1200, right: 900}, pan: 150, pan_error: ''};
  const i = $('#trkImg'); i.src = '%s'; i.hidden = false; $('#trkEmpty').hidden = true; trkRender(); renderSide(); })()"""
def practice(on):   # oefenstand aan/uit (alle schermen herladen)
    import json, urllib.request
    urllib.request.urlopen(urllib.request.Request(URL.split("/?")[0] + "/api/practice", json.dumps({"on": on}).encode(),
                                                  {"Content-Type": "application/json"})).read()
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
    pg.click("#btnPad"); pg.click("#btnSettings"); pg.wait_for_timeout(300); pg.click("#btnWizard"); pg.wait_for_timeout(500)
    pg.evaluate("$$('#wzList .wzres')[0].className='wzres ok'; $$('#wzList .wzres')[0].textContent='✓ beeld ontvangen'; $('#wzScanInfo').textContent='3 gevonden in 192.168.100.0/24'")
    pg.screenshot(path=f"{OUT}/wizard.png"); pg.click("#wzSkip")
    pg.evaluate("undoOffer('Midden · Preset 3 opgeslagen', 'midden', 3)"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/ongedaan-maken.png", clip={"x": 420, "y": 760, "width": 600, "height": 140}); pg.evaluate("$('#undoNote').hidden = true")
    pg.click("#btnPad"); pg.click("#btnMidi"); pg.wait_for_timeout(1500); pg.screenshot(path=f"{OUT}/videomixer.png"); pg.click("#midiClose")
    urllib.request.urlopen("http://127.0.0.1:50001/test/trigger?i=4").read(); pg.wait_for_timeout(2500)
    pg.keyboard.press("l"); pg.wait_for_timeout(600); pg.screenshot(path=f"{OUT}/liederenvenster.png"); pg.keyboard.press("l"); pg.wait_for_timeout(200)
    pg.click("#btnPad"); pg.click("#btnSongs"); pg.wait_for_timeout(800); pg.screenshot(path=f"{OUT}/liederen.png")
    pg.locator("#dlgSongs").evaluate("d => d.scrollTop = 99999"); pg.wait_for_timeout(200); pg.screenshot(path=f"{OUT}/voordienst-app.png"); pg.click("#songsClose")
    pg.click("#btnPad"); pg.click("#btnAccess"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/toegang.png"); pg.click("#acClose")
    pg.click("#btnPad"); pg.click("#btnOsd"); pg.wait_for_timeout(300)   # 🖥 OSD-menu (status nagebootst: demo-camera's bestaan niet)
    pg.evaluate("osdOpen = {[osdCamId]: true}; osdRender(); renderTabs(); renderPresets(); const i = $('#osdImg'); i.hidden = false; i.src = '%s'" % osd_jpeg())
    pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/osd-menu.png")
    pg.evaluate("osdOpen = {}; osdRender(); renderTabs(); renderPresets()"); pg.click("#osdDone")
    pg.set_viewport_size({"width": 1440, "height": 1150})   # venster is hoog: helemaal in beeld
    pg.evaluate(VOLG % volg_jpeg()); pg.wait_for_timeout(500)   # 🎯 Volgen instellen (beeld en personen nagebootst)
    pg.locator("#dlgTrack").screenshot(path=f"{OUT}/volgen-instellen.png"); pg.evaluate("track = {}; $('#dlgTrack').close(); renderSide()")
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.click("#btnPad"); pg.click("#btnLog"); pg.wait_for_timeout(400); pg.evaluate(LOG); pg.wait_for_timeout(200)
    pg.screenshot(path=f"{OUT}/activiteitenlog.png"); pg.click("#logClose")
    pg.evaluate("camst.rechts = {ok:false, error:'geen verbinding'}; renderTabs(); renderPresets(); alertCheck()"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/foutmelding.png", clip={"x": 0, "y": 0, "width": 1440, "height": 420})
    pg.evaluate("camst.rechts = {ok:true}; renderTabs(); renderPresets(); alertCheck(); $('#alertBar').classList.remove('show')")
    practice(True); pg.wait_for_timeout(2500)
    po = page(b); po.screenshot(path=f"{OUT}/oefenstand.png", clip={"x": 0, "y": 0, "width": 1440, "height": 420}); po.close()
    practice(False); pg.wait_for_timeout(2500)
    pg.goto(URL); pg.wait_for_timeout(1500); pg.evaluate(YT + LIVE); pg.wait_for_timeout(300)
    pg.click("#btnPad"); pg.click("#btnBackup"); pg.wait_for_timeout(600); pg.locator("#dlgBackup").evaluate("d => d.scrollTop = 99999"); pg.wait_for_timeout(200)
    pg.screenshot(path=f"{OUT}/automatische-back-up.png"); pg.click("#bkClose")
    pg.click("#btnPad"); pg.click("#btnKeys"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/sneltoetsen.png"); pg.click("#keysCancel")
    pg.click("#btnMap2"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/knoppenindeling.png"); pg.click("#btnMapClose")
    pg.click("#btnPad"); pg.click("#btnBackup"); pg.wait_for_timeout(400); pg.screenshot(path=f"{OUT}/back-up.png"); pg.keyboard.press("Escape")
    pg.evaluate("setWall(true)"); pg.wait_for_timeout(600); pg.screenshot(path=f"{OUT}/volledig-scherm.png")
    pg.evaluate("ytShow(true); setCardShown('ctrlCard', true); setCardShown('padCard', true)"); pg.wait_for_timeout(800); pg.screenshot(path=f"{OUT}/volledig-scherm-bediening.png")
    pg.evaluate("songWinShow(true); dockLayout()"); pg.wait_for_timeout(800); pg.screenshot(path=f"{OUT}/volledig-scherm-bediening-boven.png")
    pg.evaluate("store.set('padFloat', JSON.stringify({x: 40, y: 470, w: 360})); dockLayout()")
    pg.wait_for_timeout(800); pg.screenshot(path=f"{OUT}/volledig-scherm-zwevend.png")
    pg.evaluate("store.set('padFloat', ''); setCardShown('ctrlCard', false); setCardShown('padCard', false); songWinShow(false); ytShow(false); setWall(false)")
    pg.evaluate("tourStart()"); pg.wait_for_timeout(300)
    for _ in range(2): pg.click("#tourNext")
    pg.wait_for_timeout(500); pg.screenshot(path=f"{OUT}/rondleiding.png")
    pg.evaluate("tourEnd(); updPreview = true; updRender()"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/update-melding.png", clip={"x": 1040, "y": 600, "width": 400, "height": 300}); pg.evaluate("updPreview = false; updRender()")
    pl = page(b, "light"); pl.screenshot(path=f"{OUT}/hoofdscherm-licht.png")
    ctx = b.new_context(viewport={"width": 1024, "height": 768}, device_scale_factor=1, has_touch=True, is_mobile=True, screen={"width": 1024, "height": 768})
    ctx.add_init_script("localStorage.setItem('theme','light'); localStorage.setItem('tourDone','1'); localStorage.setItem('ytAuto','0');")
    pt = ctx.new_page(); pt.goto(URL); pt.wait_for_timeout(1500); pt.evaluate(YT + LIVE); pt.wait_for_timeout(300)
    pt.screenshot(path=f"{OUT}/tablet.png")
    pm = page(b, "dark", 390, 844); pm.screenshot(path=f"{OUT}/telefoon.png", full_page=False)
    b.close()
print("klaar")
