"""
Tool: compare-page
Role: report
Output: json   (a descriptor; the report itself is HTML files written to --out)
Stdin: none
Summary: Build local Safari pages comparing each new paperhanger output with its old Pixelmator counterpart, one pair per page, at true 1:1.

Usage (library config from the environment, see library/_library.py):
    uv run python docs/research/2026-09-28-tier-4-full-corpus-run/compare_page.py --sources DIR --out DIR
      DIR holds the sources whose outputs to compare (e.g. processing/originals
      after the pilot). Every output with an old counterpart gets a page; outputs
      from photos with no model run are included too (old ML vs new real pixels).

Output shape: {"index": str, "pages": int, "pairs": [{"key": [stem, pos, device],
               "old": str, "new": str, "band": int, "factor": str}]}

Pages: synced panes (scroll position shared as a FRACTION, since the two files
differ in pixel size), images drawn at naturalWidth/devicePixelRatio so one
image pixel is one screen pixel on Retina. Keys: f = flip A/B in one pane,
z = fit/1:1, n/p = next/previous pair. Safari renders HEIC natively; Chrome does not.
Nothing leaves the Mac: files are referenced by file:// URL.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import os

import _instruments as ins
from _instruments import lib
from paperhanger import bands, imaging

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
:root{{color-scheme:light dark}} body{{margin:0;font:13px -apple-system,sans-serif;background:#111;color:#ddd}}
header{{padding:6px 10px;display:flex;gap:16px;align-items:center;background:#222}}
header a{{color:#8cf}} .row{{display:flex;height:calc(100vh - 64px)}}
.pane{{flex:1;overflow:auto;position:relative;border-left:1px solid #333}}
.lab{{position:sticky;top:0;left:0;background:#000c;padding:3px 8px;z-index:2;display:inline-block}}
body.flip .pane.b{{display:none}} body.fit img{{width:100%!important;height:auto!important}}
kbd{{background:#333;padding:0 4px;border-radius:3px}}
</style></head><body>
<header><b>{title}</b><span>{nav}</span>
<span><kbd>f</kbd> flip A/B · <kbd>z</kbd> fit/1:1 · <kbd>n</kbd>/<kbd>p</kbd> next/prev</span><span id="mode">side by side</span></header>
<div class="row">
 <div class="pane a"><div class="lab" id="la">OLD · {old_label}</div><br><img id="ia" src="{old_src}"></div>
 <div class="pane b"><div class="lab">NEW · {new_label}</div><br><img id="ib" src="{new_src}"></div>
</div>
<script>
const dpr=window.devicePixelRatio||1, A=document.querySelector('.pane.a'), B=document.querySelector('.pane.b');
const ia=document.getElementById('ia'), ib=document.getElementById('ib');
const old={{src:ia.src,label:document.getElementById('la').textContent}}, neu={{src:ib.src,label:'NEW · {new_label_js}'}};
function size(i){{ if(i.naturalWidth) i.style.width=(i.naturalWidth/dpr)+'px'; }}
[ia,ib].forEach(i=>{{ i.complete?size(i):i.onload=()=>size(i); }});
let lock=false;
function sync(f,t){{ if(lock) return; lock=true;
  const fx=f.scrollLeft/Math.max(1,f.scrollWidth-f.clientWidth), fy=f.scrollTop/Math.max(1,f.scrollHeight-f.clientHeight);
  t.scrollLeft=fx*(t.scrollWidth-t.clientWidth); t.scrollTop=fy*(t.scrollHeight-t.clientHeight);
  requestAnimationFrame(()=>lock=false); }}
A.onscroll=()=>sync(A,B); B.onscroll=()=>sync(B,A);
let flipped=false, showingNew=false;
document.onkeydown=e=>{{
  if(e.key==='z'){{ document.body.classList.toggle('fit'); }}
  if(e.key==='n'&&'{next}') location.href='{next}';
  if(e.key==='p'&&'{prev}') location.href='{prev}';
  if(e.key==='f'){{
    if(!flipped){{ flipped=true; document.body.classList.add('flip'); }}
    const fx=A.scrollLeft/Math.max(1,A.scrollWidth-A.clientWidth), fy=A.scrollTop/Math.max(1,A.scrollHeight-A.clientHeight);
    showingNew=!showingNew; const s=showingNew?neu:old;
    ia.onload=()=>{{ size(ia); A.scrollLeft=fx*(A.scrollWidth-A.clientWidth); A.scrollTop=fy*(A.scrollHeight-A.clientHeight); }};
    ia.src=s.src; document.getElementById('la').textContent=s.label;
    document.getElementById('mode').textContent='flip: showing '+(showingNew?'NEW':'OLD');
  }}
}};
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Old-vs-new wallpaper comparison pages.")
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--names", nargs="*", default=None,
                    help="limit to these source filenames")
    args = ap.parse_args()
    if not args.sources.is_dir():
        sys.exit(f"not a directory: {args.sources}")
    cfg = lib.load_config(os.environ)

    works, _ = ins.build_works(args.sources, args.sources.parent)
    if args.names:
        wanted = {lib.nfc(n) for n in args.names}
        works = [w for w in works if lib.nfc(w.source.name) in wanted]
    old_by_key: dict = {}
    for r in ins.parse_library(cfg):
        if r["ext"] == "heic":          # .jpeg twins in the old library duplicate a .heic
            old_by_key.setdefault((r["stem"], r["pos"], r["device"]), r)

    pairs = []
    for w in sorted(works, key=lambda w: (not w.needs_upscale, w.source.name)):
        for p in w.plans:
            r = old_by_key.get(ins.new_key(p))
            if r is None or not p.destination.exists():
                continue
            pairs.append((p, r))

    args.out.mkdir(parents=True, exist_ok=True)
    rows, described = [], []
    for i, (p, r) in enumerate(pairs):
        oldp, newp = Path(r["path"]), p.destination
        op = imaging.probe(oldp)
        olabel = f"{oldp.name} ({op[0]}×{op[1]})" if op else oldp.name
        nlabel = (f"{newp.name} ({p.out_width}×{p.out_height}, band {p.band} "
                  f"{bands.NAMES.get(p.band, '')}, {p.factor_token})")
        name = f"pair-{i:02d}.html"
        nav = f'<a href="index.html">index</a> · {i + 1}/{len(pairs)}'
        page = PAGE.format(
            title=html.escape(f"{p.source.stem} · {p.position or 'full'} · {p.device}"),
            nav=nav, old_label=html.escape(olabel), new_label=html.escape(nlabel),
            new_label_js=html.escape(nlabel).replace("'", "\\'"),
            old_src=oldp.as_uri(), new_src=newp.as_uri(),
            next=f"pair-{i + 1:02d}.html" if i + 1 < len(pairs) else "",
            prev=f"pair-{i - 1:02d}.html" if i > 0 else "")
        (args.out / name).write_text(page, encoding="utf-8")
        kind = "upscaled" if p.needs_upscale else "real pixels"
        rows.append(f'<tr><td><a href="{name}">{html.escape(p.source.stem)}</a></td>'
                    f"<td>{p.position or 'full'}</td><td>{p.device}</td>"
                    f"<td>{kind}</td><td>{p.factor_token}</td>"
                    f"<td>{html.escape(olabel)}</td></tr>")
        described.append({"key": list(ins.new_key(p)), "old": str(oldp), "new": str(newp),
                          "band": p.band, "factor": p.factor_token})

    index = ("<!doctype html><html><head><meta charset='utf-8'><title>Pilot comparison</title>"
             "<style>body{font:14px -apple-system,sans-serif;margin:24px}"
             "td,th{padding:3px 10px;text-align:left}tr:nth-child(even){background:#8881}</style>"
             "</head><body><h1>Pilot: old Pixelmator vs new paperhanger</h1>"
             "<p>Open in <b>Safari</b> (HEIC). Each page: old left, new right, synced scroll, "
             "true 1:1. Keys: <b>f</b> flip A/B, <b>z</b> fit, <b>n/p</b> next/prev. "
             "'real pixels' rows compare the old ML upscale with the new un-enlarged output.</p>"
             "<table><tr><th>source</th><th>slice</th><th>device</th><th>new kind</th>"
             "<th>factor</th><th>old file</th></tr>" + "".join(rows) + "</table></body></html>")
    (args.out / "index.html").write_text(index, encoding="utf-8")

    json.dump({"index": str(args.out / "index.html"), "pages": len(pairs),
               "pairs": described}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
