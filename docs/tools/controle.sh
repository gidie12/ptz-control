#!/usr/bin/env bash
# Snelle controles na een wijziging (zie CLAUDE.md §10). Exit 0 = alles goed.
# Gebruikt door de agent `controle` en door /loop.
set -u
cd "$(dirname "$0")/../.."
fout=0
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== Python compileren"
python3 -m py_compile ptz_control.py tracker.py backup.py youtube.py updater.py wininstall.py \
  midi.py roland_v1hd.py actlog.py oefen.py liederen.py || fout=1

echo "== JavaScript in index.html"
python3 - "$tmp" <<'PY' || fout=1
import re, sys
html = open("index.html", encoding="utf-8").read()
for i, m in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)):
    open(f"{sys.argv[1]}/a{i}.js", "w", encoding="utf-8").write(m)
PY
for f in "$tmp"/a*.js; do node --check "$f" || fout=1; done

echo "== Shell-scripts"
bash -n "Start PTZ Control.command" || fout=1
bash -n "PTZ Control.app/Contents/MacOS/PTZ Control" || fout=1

echo "== Regelaar van persoon volgen"
if python3 -c "import numpy, cv2" 2>/dev/null; then
  uit="$(python3 docs/tools/tracker_test.py 2>&1)" || { echo "$uit" | grep -v '^OK '; fout=1; }
  uit="$(python3 docs/tools/volg_test.py --check 2>&1)" || fout=1
  echo "$uit" | tail -2
else
  echo "overgeslagen (numpy/opencv niet geïnstalleerd)"
fi

echo "== Geen geheimen of lokale data in git"
for p in cameras.json thumbs toegang.json workspaces.json activiteit.log volgen.csv volgen.1.csv; do
  if git ls-files --error-unmatch "$p" >/dev/null 2>&1; then echo "FOUT: $p staat in git"; fout=1; fi
done

[ $fout -eq 0 ] && echo "OK: alle controles geslaagd" || echo "MISLUKT: zie hierboven"
exit $fout
