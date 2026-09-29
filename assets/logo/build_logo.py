import math, io, os, zipfile
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

NAVY="#0a1930"; NAVY2="#0f2942"; GOLD="#c9a227"; GOLD2="#d9b84a"; CREAM="#faf7ef"
F="node_modules/@fontsource/cairo/files/cairo-arabic-700-normal.woff"
tt=TTFont(F); buf=io.BytesIO(); tt.flavor=None; tt.save(buf); data=buf.getvalue()
tt=TTFont(io.BytesIO(data)); gs=tt.getGlyphSet(); order=tt.getGlyphOrder()

def word_path(text, height):
    face=hb.Face(data); font=hb.Font(face)
    b=hb.Buffer(); b.add_str(text); b.guess_segment_properties(); hb.shape(font,b,{})
    x=0; parts=[]
    for info,pos in zip(b.glyph_infos,b.glyph_positions):
        parts.append((order[info.codepoint],x+pos.x_offset,pos.y_offset)); x+=pos.x_advance
    # bounds
    bp=BoundsPen(gs)
    for n,ox,oy in parts:
        gs[n].draw(TransformPen(bp,(1,0,0,1,ox,oy)))
    x0,y0,x1,y1=bp.bounds
    s=height/(y1-y0)
    pen=SVGPathPen(gs, ntos=lambda v:f"{v:.1f}")
    for n,ox,oy in parts:
        # flip y, scale, shift so bbox top-left = 0,0
        gs[n].draw(TransformPen(pen,(s,0,0,-s,(ox-x0)*s,(y1-oy)*s)))
    return pen.getCommands(), (x1-x0)*s, height

def sq(cx,cy,a,b,n=4.6,k=120):
    pts=[]
    for i in range(k):
        t=2*math.pi*i/k; c,s=math.cos(t),math.sin(t)
        x=cx+a*math.copysign(abs(c)**(2/n),c); y=cy+b*math.copysign(abs(s)**(2/n),s)
        pts.append(f"{x:.1f} {y:.1f}")
    return "M"+" L".join(pts)+"Z"

def icon(head,screen,eye,acc,shine):
    return f'''<g>
<rect x="249" y="124" width="14" height="42" rx="7" fill="{head}"/>
<circle cx="256" cy="104" r="26" fill="{acc}"/>
<rect x="46" y="250" width="44" height="96" rx="22" fill="{acc}"/>
<rect x="422" y="250" width="44" height="96" rx="22" fill="{acc}"/>
<path d="{sq(256,296,180,138)}" fill="{head}"/>
<path d="{sq(256,296,148,108)}" fill="{screen}"/>
<ellipse cx="196" cy="276" rx="26" ry="32" fill="{eye}"/>
<ellipse cx="316" cy="276" rx="26" ry="32" fill="{eye}"/>
<circle cx="205" cy="264" r="7" fill="{shine}"/><circle cx="325" cy="264" r="7" fill="{shine}"/>
<path d="M208 340 Q256 384 304 340" fill="none" stroke="{acc}" stroke-width="16" stroke-linecap="round"/>
</g>'''

def favicon_glyph(tile,head,screen,eye,acc):
    return f'''<rect width="512" height="512" rx="112" fill="{tile}"/>
<rect x="238" y="92" width="36" height="70" rx="6" fill="{acc}"/>
<circle cx="256" cy="72" r="34" fill="{acc}"/>
<path d="{sq(256,300,196,150)}" fill="{head}"/>
<path d="{sq(256,300,160,116)}" fill="{screen}"/>
<circle cx="192" cy="284" r="32" fill="{eye}"/><circle cx="320" cy="284" r="32" fill="{eye}"/>
<path d="M200 354 Q256 406 312 354" fill="none" stroke="{acc}" stroke-width="26" stroke-linecap="round"/>'''

def svg(w,h,vb,body,bg=None,title="سمير"):
    r=f'<rect width="{vb[0]}" height="{vb[1]}" fill="{bg}"/>' if bg else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {vb[0]} {vb[1]}" role="img" aria-label="{title}"><title>{title}</title>{r}{body}</svg>'

L=dict(head=NAVY,screen=NAVY2,eye=CREAM,acc=GOLD,shine=NAVY2)
D=dict(head=CREAM,screen=NAVY2,eye=CREAM,acc=GOLD2,shine=NAVY2)

def icon_svg(th,bg): return svg(512,512,(512,512),icon(**th),bg)

# horizontal RTL: icon right, text left
path,tw,th_=word_path("سمير",170)
def horiz(th,textcol,bg):
    gap=36; pad=64; ih=420; sc=ih/512*1.0
    W=pad+tw+gap+ 512*sc*0.92 +pad
    H=ih+2*pad-40
    # icon occupies x from W-pad-512*sc ; visual bbox of icon x 46..466 -> scale
    ix=W-pad-466*sc; iy=(H-ih)/2 + 0
    ty=(H-th_)/2+ 4
    body=f'<g transform="translate({ix-46*sc:.1f} {iy-0:.1f}) scale({sc:.4f})" >{icon(**th)}</g>'
    # recompute: icon vertical bbox 78..434 -> center 256
    body=f'<g transform="translate({W-pad-466*sc:.1f} {H/2-256*sc:.1f}) scale({sc:.4f}) translate(-0 0)">{icon(**th)}</g>'
    body=body.replace("translate("+f"{W-pad-466*sc:.1f}", "translate("+f"{W-pad-466*sc+46*sc-46*sc:.1f}")
    body+=f'<path transform="translate({pad} {ty:.1f})" d="{path}" fill="{textcol}"/>'
    return svg(round(W),round(H),(round(W),round(H)),body,bg)

def fav(tile,head,screen,eye,acc): return svg(512,512,(512,512),favicon_glyph(tile,head,screen,eye,acc),None)

os.makedirs("out/svg",exist_ok=True); os.makedirs("out/png",exist_ok=True)
files={
 "1-icon-light.svg":icon_svg(L,CREAM),
 "2-icon-dark.svg":icon_svg(D,NAVY),
 "3-horizontal-light.svg":horiz(L,NAVY,CREAM),
 "4-horizontal-dark.svg":horiz(D,CREAM,NAVY),
 "icon-light-transparent.svg":icon_svg(L,None),
 "icon-dark-transparent.svg":icon_svg(D,None),
 "horizontal-light-transparent.svg":horiz(L,NAVY,None),
 "horizontal-dark-transparent.svg":horiz(D,CREAM,None),
 "favicon-app-icon.svg":fav(NAVY,CREAM,NAVY2,CREAM,GOLD2),
 "favicon-app-icon-light.svg":fav(CREAM,NAVY,NAVY2,CREAM,GOLD),
}
for k,v in files.items(): open("out/svg/"+k,"w").write(v)
print("ok",tw)
