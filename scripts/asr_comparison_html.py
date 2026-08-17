"""Charts and page for the ASR (speech-to-text) comparison.

WHY THIS DOES NOT REUSE `model_comparison_html.charts_js`.

The house style is shared and must not fork -- `STYLE`, `esc` and `num` are imported below,
not copied. The CHARTS are a different matter. `charts_js` draws four charts whose semantics
are welded in at authoring time, and every one of them would state something false here:

  * `best = Math.max(...)` bolds the highest value and labels it "(highest)". CER is an error
    rate: that would bold the WORST transcriber and congratulate it.
  * the axis is fixed at [0, 0.25, 0.5, 0.75, 1.0] with bar width `val * barW`. A measured CER
    of 0.0034 is then a 2-pixel bar, clamped to a 1px minimum -- every arm renders as an
    identical sliver and the chart says nothing.
  * `aria-label="Weighted F1 by label type"` and the tooltip string "weighted F1 …" are
    literals. A screen-reader user would be told the CER chart is an F1 chart.
  * the stability chart coerces a missing value with `||0` and prints "most consistent". An
    insertion rate is legitimately null when a file has no non-speech to measure over; that
    would render as a perfect score for a figure that does not exist.

So the generic scaffolding (`el`, `v`, `bind`, `col`) is reused verbatim -- twelve lines, no
semantics -- and the row-bar itself becomes ONE function parameterised by direction, ticks,
formatter and prose. `charts_js` inlines that layout four times; here it is written once and
called with four configs. Direction, scale and caption are therefore data, which means the
caption can never contradict the min/max that picked the winner -- the exact class of bug
that put "(highest)" next to a lowest-is-best number in the first place.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_comparison_html import STYLE, esc, num          # noqa: E402  house style, not forked

__all__ = ["STYLE", "EXTRA_CSS", "esc", "num", "asr_charts_js"]


EXTRA_CSS = """
.arms{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 18px}
.arm{flex:1 1 260px;border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.arm .n{font-weight:700;font-size:14px}
.arm .r{font-size:12px;color:var(--ink2);margin-top:3px}
.arm .m{font-family:var(--mono);font-size:11px;color:var(--ink3);margin-top:6px}
.asym{border-left:3px solid var(--line2);padding:2px 0 2px 14px;margin:16px 0}
.asym p{margin:8px 0}
.win{font-family:var(--mono);font-size:11px;padding:1px 6px;border-radius:3px;
background:var(--st-good-bg);color:var(--st-good)}
.absent{color:var(--ink3);font-style:italic}
"""


# The generic scaffolding, lifted unchanged from model_comparison_html so the two pages
# behave identically on hover and in a screen reader. Nothing below it is shared.
_SCAFFOLD = """
var NS="http://www.w3.org/2000/svg";
function el(n,a,t){var e=document.createElementNS(NS,n);for(var k in a){if(a[k]!==null)
e.setAttribute(k,a[k]);}if(t!==undefined)e.textContent=t;return e;}
function v(n){return "var("+n+")";}
var tip=document.getElementById("tip");
function bind(node,text){node.classList.add("hit");
node.addEventListener("mousemove",function(e){tip.textContent=text;tip.style.opacity="1";
var x=e.clientX+14,y=e.clientY-34;
if(x+tip.offsetWidth>window.innerWidth-8)x=e.clientX-tip.offsetWidth-14;
tip.style.left=x+"px";tip.style.top=y+"px";});
node.addEventListener("mouseleave",function(){tip.style.opacity="0";});
node.appendChild(el("title",{},text));}
"""

# One row-bar, configured. `direction` decides both which value is highlighted AND which word
# the caption uses, from the same object, so they cannot disagree.
_ROWBAR = """
function nice(x){
  if(!(x>0))return 1;
  var p=Math.pow(10,Math.floor(Math.log10(x))),m=x/p;
  return (m<=1?1:m<=2?2:m<=2.5?2.5:m<=5?5:10)*p;
}
function rowbar(cfg){
  var host=document.getElementById(cfg.id);
  if(!host)return;
  var rows=cfg.rows;                       // [{label,value,colour,note}]
  var W=860,padL=cfg.padL||150,padR=96,padT=26,rowH=32,padB=34;
  var H=padT+rows.length*rowH+padB,barW=W-padL-padR;
  var vals=rows.filter(function(r){return r.value!==null&&r.value!==undefined;})
               .map(function(r){return r.value;});
  if(!vals.length){host.appendChild(el("p",{},cfg.nullText||"not measured"));return;}
  var maxV=nice(Math.max.apply(null,vals)*1.12);
  var best=cfg.direction==="low"?Math.min.apply(null,vals):Math.max.apply(null,vals);
  var svg=el("svg",{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":cfg.ariaLabel});
  svg.appendChild(el("text",{x:padL,y:13,"font-size":9.5,"letter-spacing":"1.2",
    fill:v("--ink-faint")},cfg.caption));
  var TICKS=cfg.ticks||[0,maxV/4,maxV/2,maxV*3/4,maxV];
  TICKS.forEach(function(t){
    if(t>maxV)return;
    var x=padL+(t/maxV)*barW;
    svg.appendChild(el("line",{x1:x,y1:padT-6,x2:x,y2:padT+rows.length*rowH-8,
      stroke:t===0?v("--hairline-firm"):v("--hairline"),"stroke-width":1}));
    svg.appendChild(el("text",{x:x,y:H-14,"font-size":10,"text-anchor":"middle"},
      cfg.fmtTick?cfg.fmtTick(t):cfg.fmt(t)));
  });
  rows.forEach(function(r,i){
    var y=padT+i*rowH;
    svg.appendChild(el("text",{x:padL-10,y:y+15,"font-size":11.5,"text-anchor":"end",
      fill:v("--ink")},r.label));
    if(r.value===null||r.value===undefined){
      // Never a zero-length bar: an unmeasured value would then read as the best possible
      // score on a lower-is-better chart, which is the failure this whole module avoids.
      svg.appendChild(el("text",{x:padL,y:y+15,"font-size":11,"font-style":"italic",
        fill:v("--ink3")},r.note||cfg.nullText||"not measured"));
      return;
    }
    var w=(r.value/maxV)*barW,isBest=r.value===best;
    var rect=el("rect",{x:padL,y:y,width:Math.max(w,1),height:20,rx:2,
      fill:isBest?v("--s-best"):r.colour});
    bind(rect,r.label+"  |  "+cfg.fmt(r.value)+(r.note?"  |  "+r.note:"")+
      (isBest?"  ("+cfg.bestWord+")":""));
    svg.appendChild(rect);
    svg.appendChild(el("text",{x:padL+w+9,y:y+15,"font-size":11.5,fill:v("--ink"),
      "font-weight":isBest?700:600},cfg.fmt(r.value)+(isBest?"  "+cfg.bestWord:"")));
  });
  host.appendChild(svg);
}
"""


def asr_charts_js(charts: list[dict]) -> str:
    """Return the <script> that draws every chart in `charts`.

    Each entry is a rowbar config: {id, caption, ariaLabel, direction, bestWord, fmt (a JS
    expression name), rows:[{label,value,colour,note}]}. `fmt` is named rather than embedded
    so a formatter cannot be injected from data.
    """
    return ("<script>\n(function(){\n\"use strict\";\n"
            + _SCAFFOLD + _ROWBAR
            + "var FMT={"
              "cer:function(x){return (x*100).toFixed(2)+'%';},"
              "pct:function(x){return (x*100).toFixed(1)+'%';},"
              "rate:function(x){return x.toFixed(1);},"
              "secs:function(x){return x.toFixed(1)+'s';}"
              "};\n"
            + "var CHARTS=" + _json.dumps(charts) + ";\n"
            + "CHARTS.forEach(function(c){c.fmt=FMT[c.fmt];rowbar(c);});\n"
            + "})();\n</script>")
