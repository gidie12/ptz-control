from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    for vw, vh in ((1440, 900), (900, 1000), (390, 800)):
        pg = b.new_page(viewport={"width": vw, "height": vh}); errs = []; cmds = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("request", lambda r: cmds.append(r.url) if "/api/cmd" in r.url else None)
        pg.goto("http://127.0.0.1:8080/"); pg.wait_for_timeout(1500)
        assert pg.is_visible("#tourCard"), "tour niet gestart"
        n = pg.evaluate("TOUR_STEPS.length"); probs = []
        for i in range(n):
            pg.wait_for_timeout(350)
            info = pg.evaluate("""() => { const st = TOUR_STEPS[tourIdx], el = tourTarget(st), c = $('#tourCard').getBoundingClientRect();
              const r = el && el.getBoundingClientRect();
              const overlap = r ? !(c.right <= r.left || c.left >= r.right || c.bottom <= r.top || c.top >= r.bottom) : false;
              return {i: tourIdx, title: st.title, hasTarget: !!el, wanted: !!st.target, inView: c.left >= 0 && c.top >= 0 && c.right <= innerWidth && c.bottom <= innerHeight, overlap,
                      tgtInView: r ? r.top >= -1 && r.bottom <= innerHeight + 1 : true} }""")
            if (info["wanted"] and not info["hasTarget"]) or not info["inView"] or info["overlap"] or not info["tgtInView"]: probs.append(info)
            if i == 2: pg.keyboard.press("ArrowLeft"); pg.keyboard.press("ArrowRight"); pg.keyboard.press("ArrowUp"); pg.keyboard.press("q")
            pg.keyboard.press("ArrowRight") if i % 2 else pg.click("#tourNext")
        pg.wait_for_timeout(300)
        print(vw, "steps", n, "open na einde:", pg.is_visible("#tour"), "tourDone:", pg.evaluate("localStorage.tourDone"), "cmds:", len(cmds), "errs:", errs)
        for x in probs: print("  PROBLEEM", x)
        pg.reload(); pg.wait_for_timeout(1500); print("  na herladen tour zichtbaar:", pg.is_visible("#tour"))
        pg.click("#btnPad"); pg.click("#btnTour"); pg.wait_for_timeout(400); print("  via menu gestart:", pg.is_visible("#tourCard"), pg.text_content("#tourCount"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(200); print("  Esc sluit:", not pg.is_visible("#tour"))
        pg.close()
    b.close()
