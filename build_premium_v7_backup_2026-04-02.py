"""
Search Sentinel Premium PDF v7 -- 3 DENSE pages, ZERO blank space.
Page 1: Cover + Metrics + Chart + Evidence + Findings + Causes (top half of analysis)
Page 2: Reviews + Competitor + Table + Priority Actions
Page 3: Templates + Scorecard + Outlook + About + Branding
Every page fills from top_bar to footer with no gaps.
"""
import tempfile
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor, Color
from reportlab.pdfgen import canvas
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

W, H = letter  # 612, 792
M = 22  # tight margins
FH = 28  # footer height
CB = FH + 4  # content bottom

# ── Palette ────────────────────────────────────────────────────────────────────
C_DARK      = HexColor("#0c1222")
C_DARK2     = HexColor("#162036")
C_ACCENT    = HexColor("#2563eb")
C_ACCENT2   = HexColor("#7c3aed")
C_GREEN     = HexColor("#16a34a")
C_RED       = HexColor("#dc2626")
C_ORANGE    = HexColor("#d97706")
C_WHITE     = HexColor("#ffffff")
C_OFF_WHITE = HexColor("#f8fafc")
C_GRAY      = HexColor("#94a3b8")
C_DGRAY     = HexColor("#64748b")
C_TEXT      = HexColor("#1e293b")
C_LTGRAY    = HexColor("#e2e8f0")
C_BLUE_BG   = HexColor("#eff6ff")
C_RED_BG    = HexColor("#fef2f2")
C_GREEN_BG  = HexColor("#f0fdf4")
C_AMBER_BG  = HexColor("#fffbeb")
C_VLTBLUE   = HexColor("#f0f5ff")

def rr(c, x, y, w, h, r=4, fill=None, stroke=None, sw=0.3):
    p = c.beginPath(); p.roundRect(x, y, w, h, r)
    if fill: c.setFillColor(fill)
    if stroke: c.setStrokeColor(stroke); c.setLineWidth(sw)
    c.drawPath(p, fill=1 if fill else 0, stroke=1 if stroke else 0)

def gradient(c, x, y, w, h, c1, c2, steps=30):
    r1,g1,b1=c1.red,c1.green,c1.blue; r2,g2,b2=c2.red,c2.green,c2.blue
    sh=h/steps
    for i in range(steps):
        t=i/steps; c.setFillColor(Color(r1+(r2-r1)*t,g1+(g2-g1)*t,b1+(b2-b1)*t))
        c.rect(x,y+h-(i+1)*sh,w,sh+0.5,fill=1,stroke=0)

def wrap_text(c, x, y, text, font, size, max_w, leading=None, color=C_TEXT):
    if leading is None: leading=size*1.25
    c.setFont(font,size); c.setFillColor(color)
    words=text.split(); line=""; cy=y
    for word in words:
        test=line+" "+word if line else word
        if c.stringWidth(test,font,size)<max_w: line=test
        else: c.drawString(x,cy,line); cy-=leading; line=word
    if line: c.drawString(x,cy,line); cy-=leading
    return cy

def sec(c, y, title, accent=C_ACCENT):
    c.setFillColor(Color(accent.red,accent.green,accent.blue,0.04))
    c.rect(0,y-3,W,15,fill=1,stroke=0)
    c.setFillColor(accent); c.rect(M,y-1,3,11,fill=1,stroke=0)
    c.setFillColor(C_TEXT); c.setFont("Helvetica-Bold",9); c.drawString(M+7,y,title)

def foot(c, text, pn):
    gradient(c,0,0,W,FH,C_DARK2,C_DARK); gradient(c,0,FH,W,2,C_ACCENT,C_ACCENT2)
    c.setFillColor(C_WHITE); c.setFont("Helvetica",6); c.drawString(M,9,text)
    c.setFillColor(HexColor("#60a5fa")); c.setFont("Helvetica-Bold",6)
    c.drawRightString(W-M,9,f"Page {pn} of 3")

def tbar(c, left, right):
    gradient(c,0,H-26,W,26,C_DARK,C_DARK2); gradient(c,0,H-29,W,3,C_ACCENT,C_ACCENT2)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",7); c.drawString(M,H-18,left)
    c.setFillColor(C_GRAY); c.setFont("Helvetica",6.5); c.drawRightString(W-M,H-18,right)

def badge(c, x, y, text, bg, tc, fs=5.5):
    tw=c.stringWidth(text,"Helvetica-Bold",fs)+8
    rr(c,x,y,tw,10,r=5,fill=bg); c.setFillColor(tc); c.setFont("Helvetica-Bold",fs)
    c.drawString(x+4,y+2.5,text); return tw

def mcrd(c, x, y, w, h, label, val, sub=None, vc=C_TEXT, ac=C_ACCENT):
    rr(c,x,y,w,h,r=3,fill=C_WHITE,stroke=C_LTGRAY,sw=0.3)
    c.setFillColor(ac); c.rect(x,y,2,h,fill=1,stroke=0)
    c.setFillColor(C_DGRAY); c.setFont("Helvetica",5.5); c.drawString(x+7,y+h-9,label.upper())
    c.setFillColor(vc); c.setFont("Helvetica-Bold",14); c.drawString(x+7,y+4,str(val))
    if sub:
        sw2=c.stringWidth(str(val),"Helvetica-Bold",14)
        c.setFont("Helvetica",6); c.setFillColor(C_DGRAY); c.drawString(x+10+sw2,y+6,sub)

def divider(c, y):
    c.setStrokeColor(C_LTGRAY); c.setLineWidth(0.2); c.line(M,y,W-M,y)

# ── Charts (compact) ───────────────────────────────────────────────────────────

def chart_rank(history):
    fig,ax=plt.subplots(figsize=(6.8,1.7),dpi=200)
    wk=list(range(1,len(history)+1)); ax.invert_yaxis()
    ax.fill_between(wk,history,max(history)+1.5,alpha=0.06,color="#dc2626")
    ax.axhspan(0.5,3.5,alpha=0.07,color="#16a34a",zorder=0)
    ax.text(len(wk)+0.15,2,"Top 3",fontsize=6,color="#16a34a",ha="left",va="center",style="italic",fontweight="bold")
    ax.plot(wk,history,color="#dc2626",linewidth=2.2,marker="o",markersize=8,
            markerfacecolor="white",markeredgewidth=2.2,markeredgecolor="#dc2626",zorder=5)
    for w,r in zip(wk,history):
        cl="#16a34a" if r<=3 else "#dc2626" if r>=6 else "#1e293b"
        ax.annotate(f"#{r}",(w,r),textcoords="offset points",xytext=(0,11),
                    ha="center",fontsize=8,fontweight="bold",color=cl)
    ax.set_xticks(wk); ax.set_xticklabels([f"W{w}" for w in wk],fontsize=6.5,color="#94a3b8")
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.set_xlim(0.4,len(wk)+0.6); ax.set_ylim(max(history)+1.5,0.5)
    ax.tick_params(axis="both",labelsize=6,colors="#94a3b8")
    for s in ax.spines.values(): s.set_visible(False)
    ax.grid(axis="y",alpha=0.06,linestyle="--")
    fig.subplots_adjust(bottom=0.14,top=0.95,left=0.05,right=0.92)
    t=tempfile.NamedTemporaryFile(suffix=".png",delete=False)
    fig.savefig(t.name,bbox_inches="tight",facecolor="white",dpi=200); plt.close(fig); return t.name

def chart_reviews(vel, yours, avg):
    fig,(ax1,ax2)=plt.subplots(1,2,figsize=(6.2,1.15),dpi=200,gridspec_kw={"width_ratios":[1,1.2]})
    bc="#dc2626" if vel<1.5 else "#d97706" if vel<3 else "#16a34a"
    ax1.barh(0,vel,height=0.4,color=bc,zorder=3); ax1.barh(0,5,height=0.4,color="#f1f5f9",zorder=1)
    ax1.axvline(x=3.0,color="#2563eb",linestyle="--",linewidth=1,zorder=4)
    ax1.text(3.05,0.25,"Target",fontsize=5,color="#2563eb",va="bottom",fontweight="bold")
    ax1.set_xlim(0,5); ax1.set_yticks([]); ax1.set_title("Review Velocity",fontsize=7,fontweight="bold",color="#1e293b",pad=3)
    ax1.set_xlabel("Reviews/Week",fontsize=5.5,color="#64748b")
    for s in ax1.spines.values(): s.set_visible(False)
    ax1.tick_params(axis="x",labelsize=5.5,colors="#94a3b8")
    bars=ax2.barh(["You","Market"],[yours,avg],height=0.5,color=["#dc2626","#2563eb"])
    for b,v in zip(bars,[yours,avg]):
        ax2.text(v+2,b.get_y()+b.get_height()/2,str(v),va="center",fontsize=7.5,fontweight="bold",color="#1e293b")
    ax2.set_xlim(0,max(yours,avg)*1.25)
    ax2.set_title("Total Reviews",fontsize=7,fontweight="bold",color="#1e293b",pad=3)
    for s in ax2.spines.values(): s.set_visible(False)
    ax2.tick_params(axis="both",labelsize=5.5,colors="#94a3b8"); ax2.invert_yaxis()
    fig.subplots_adjust(bottom=0.22,top=0.82,left=0.04,right=0.96,wspace=0.3)
    t=tempfile.NamedTemporaryFile(suffix=".png",delete=False)
    fig.savefig(t.name,bbox_inches="tight",facecolor="white",dpi=200); plt.close(fig); return t.name

def chart_comp(yr,yrat,ygain,cn,cr,crat,cgain):
    fig,(a1,a2,a3)=plt.subplots(1,3,figsize=(6.5,1.2),dpi=200,gridspec_kw={"width_ratios":[1,1,1]})
    x=np.array([0,0.65]); scn=cn[:13]
    for ax,vals,labs,title in [(a1,[yr,cr],[f"#{yr}",f"#{cr}"],"Rank"),(a2,[yrat,crat],[f"{yrat}",f"{crat}"],"Rating"),(a3,[ygain,cgain],[f"+{ygain}",f"+{cgain}"],"14d Review Gain")]:
        colors=["#1e293b","#2563eb"] if ax!=a3 else ["#dc2626","#16a34a"]
        b=ax.bar(x,vals,width=0.38,color=colors,edgecolor="white",linewidth=0.3)
        for bar,lab in zip(b,labs):
            ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+max(vals)*0.05,lab,ha="center",fontsize=7.5,fontweight="bold",color="#1e293b")
        ax.set_xticks(x); ax.set_xticklabels(["You",scn],fontsize=6,color="#64748b")
        ax.set_title(title,fontsize=7,fontweight="bold",color="#1e293b",pad=3)
        ax.set_ylim(0,max(vals)*1.3)
        for s in ax.spines.values(): s.set_visible(False)
        ax.tick_params(axis="y",labelsize=5.5,colors="#94a3b8")
    fig.subplots_adjust(bottom=0.16,top=0.8,left=0.04,right=0.98,wspace=0.28)
    t=tempfile.NamedTemporaryFile(suffix=".png",delete=False)
    fig.savefig(t.name,bbox_inches="tight",facecolor="white",dpi=200); plt.close(fig); return t.name

def chart_scorecard(scores):
    fig,ax=plt.subplots(figsize=(4.5,1.3),dpi=200)
    labels=[s[0] for s in scores]; vals=[s[1] for s in scores]
    colors=["#16a34a" if v>=7 else "#d97706" if v>=4 else "#dc2626" for v in vals]
    y=np.arange(len(labels))
    ax.barh(y,[10]*len(vals),height=0.5,color="#f1f5f9",zorder=1)
    bars=ax.barh(y,vals,height=0.5,color=colors,zorder=2,edgecolor="white",linewidth=0.3)
    for i,(b,v) in enumerate(zip(bars,vals)):
        ax.text(v+0.2,b.get_y()+b.get_height()/2,f"{v}/10",va="center",fontsize=7,fontweight="bold",color=colors[i])
    ax.set_yticks(y); ax.set_yticklabels(labels,fontsize=6.5,color="#1e293b",fontweight="bold")
    ax.set_xlim(0,11); ax.set_xticks([]); ax.invert_yaxis()
    for s in ax.spines.values(): s.set_visible(False)
    ax.tick_params(left=False)
    fig.subplots_adjust(bottom=0.05,top=0.98,left=0.3,right=0.95)
    t=tempfile.NamedTemporaryFile(suffix=".png",delete=False)
    fig.savefig(t.name,bbox_inches="tight",facecolor="white",dpi=200); plt.close(fig); return t.name

# ══════════════════════════════════════════════════════════════════════════════
def build():
    out="/sessions/adoring-jolly-ritchie/mnt/claude_busines/sample_audit_report.pdf"
    c=canvas.Canvas(out,pagesize=letter)
    c.setTitle("Search Sentinel -- Ranking Audit Report")

    BIZ="Capital City Plumbing & Drain"; CAT="Plumber"; CITY="Austin, TX"
    DATE="April 01, 2026"; RID="SS-20260401-AUS-PLM"
    PREV=3; CURR=7; DROP=4; RATING=4.6; REVIEWS=94; WEEKS=6; CONF=8
    COMP="Austin ProFlow Services"

    ch_rank=chart_rank([2,3,3,4,5,7])
    ch_rev=chart_reviews(0.2,94,148)
    ch_comp=chart_comp(CURR,RATING,1,COMP,2,4.8,12)
    ch_score=chart_scorecard([("Visibility",3),("Review Momentum",2),("Profile Freshness",2),("Competitive Pressure",3),("Data Confidence",8)])

    # ═══════════════════════════════════════════════════════════════════════
    # PAGE 1 -- COVER + METRICS + CHART + EVIDENCE + FINDINGS + CAUSES
    # ═══════════════════════════════════════════════════════════════════════
    hdr=160
    gradient(c,0,H-hdr,W,hdr,C_DARK,C_DARK2)
    gradient(c,0,H-hdr-3,W,3,C_ACCENT,C_ACCENT2)
    c.setFillColor(C_ACCENT); c.rect(0,H-hdr,3,hdr,fill=1,stroke=0)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",24); c.drawString(M,H-36,"Search Sentinel")
    c.setFillColor(HexColor("#60a5fa")); c.setFont("Helvetica",9); c.drawString(M,H-50,"Local Business Visibility Intelligence")
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",12); c.drawString(M,H-72,"Ranking Drop Alert  --  Audit Report")
    c.setFillColor(C_GRAY); c.setFont("Helvetica",7.5)
    c.drawString(M,H-84,f"Scan Date: {DATE}  |  Report ID: {RID}  |  6 weeks (Feb 18 -- Apr 1, 2026)")
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",13); c.drawString(M,H-108,BIZ)
    c.setFillColor(C_GRAY); c.setFont("Helvetica",8.5); c.drawString(M,H-122,f"{CAT}  |  {CITY}  |  {WEEKS} Weeks Tracked")

    bx=W-130
    rr(c,bx,H-88,106,52,r=6,fill=HexColor("#1a2744"),stroke=HexColor("#334155"),sw=0.5)
    c.setFillColor(C_RED); c.setFont("Helvetica-Bold",24); c.drawCentredString(bx+53,H-60,f"#{CURR}")
    c.setFillColor(C_GRAY); c.setFont("Helvetica",6.5); c.drawCentredString(bx+53,H-72,"CURRENT RANK")
    c.setFillColor(HexColor("#fca5a5")); c.setFont("Helvetica-Bold",7); c.drawCentredString(bx+53,H-82,f"was #{PREV}  |  -{DROP} pos")
    rr(c,bx,H-122,106,20,r=4,fill=HexColor("#1a2744"),stroke=HexColor("#334155"),sw=0.5)
    c.setFillColor(C_GREEN); c.setFont("Helvetica-Bold",7.5); c.drawCentredString(bx+53,H-116,f"CONFIDENCE: {CONF}/10 HIGH")

    # Metrics
    y=H-hdr-6
    c.setFillColor(C_VLTBLUE); c.rect(0,y-68,W,72,fill=1,stroke=0)
    cw=(W-2*M-10)/3
    mcrd(c,M,y-30,cw,28,"Current Rank",f"#{CURR}",None,C_RED,C_RED)
    mcrd(c,M+cw+5,y-30,cw,28,"Star Rating",f"{RATING}","/ 5.0",C_TEXT,C_ACCENT)
    mcrd(c,M+2*(cw+5),y-30,cw,28,"Total Reviews",f"{REVIEWS}","+1 (6wk)",C_RED,C_ORANGE)
    mcrd(c,M,y-62,cw,28,"Previous Rank",f"#{PREV}",None,C_TEXT,C_ACCENT)
    mcrd(c,M+cw+5,y-62,cw,28,"Positions Lost",f"-{DROP}",None,C_RED,C_RED)
    mcrd(c,M+2*(cw+5),y-62,cw,28,"Weeks Tracked",f"{WEEKS}",None,C_GREEN,C_GREEN)
    y-=72

    # Alert
    y-=2
    rr(c,M,y-20,W-2*M,20,r=3,fill=C_RED_BG,stroke=HexColor("#fca5a5"),sw=0.3)
    c.setFillColor(C_RED); c.rect(M,y-20,3,20,fill=1,stroke=0)
    c.setFont("Helvetica-Bold",7.5); c.setFillColor(C_RED); c.drawString(M+7,y-8,"SUSTAINED DECLINE")
    c.setFont("Helvetica",6.5); c.setFillColor(C_DGRAY); c.drawString(155,y-8,"Confirmed 2+ consecutive declining scans. Not temporary.")
    y-=24

    # Rank chart
    sec(c,y,"Rank Trend  --  6 Weeks"); y-=10
    ch=110
    c.drawImage(ch_rank,M-4,y-ch,width=W-2*M+8,height=ch,preserveAspectRatio=True,mask="auto")
    y-=ch+1
    c.setFont("Helvetica-Oblique",5.5); c.setFillColor(C_DGRAY)
    c.drawString(M,y,f"Source: Public Google Maps scan. Query: '{CAT} {CITY}'.")
    y-=7

    # Evidence
    divider(c,y); y-=10
    sec(c,y,"Scan Evidence")
    y-=6
    evs=[
        ("Rank:",f"#7 (was #3 -- sustained decline)",C_RED),
        ("Velocity:","0.2/wk (market: 3.2/wk)",C_ORANGE),
        ("Competitor:",f"{COMP}: +12 reviews, #6 to #2",C_RED),
        ("Profile:","No activity 31d; top 3 posted within 14d",C_ORANGE),
    ]
    ph=len(evs)*12+8
    rr(c,M,y-ph,W-2*M,ph,r=3,fill=C_BLUE_BG,stroke=HexColor("#bfdbfe"),sw=0.3)
    c.setFillColor(C_ACCENT); c.rect(M,y-ph,2,ph,fill=1,stroke=0)
    ey=y-7
    for lbl,val,cl in evs:
        c.setFont("Helvetica-Bold",7); c.setFillColor(C_DGRAY); c.drawString(M+6,ey,lbl)
        lw=c.stringWidth(lbl,"Helvetica-Bold",7)
        c.setFont("Helvetica-Bold",7); c.setFillColor(cl); c.drawString(M+6+lw+3,ey,val)
        ey-=12
    y-=ph+6

    # Findings
    divider(c,y); y-=10
    sec(c,y,"What Our Scan Found")
    y-=12
    findings=(
        f"Our scan recorded {BIZ} at #7 in Google Maps for '{CAT}' in {CITY}, down from #3 "
        f"over 2 prior scans. The business gained 1 review in 6 weeks (0.2/wk), while {COMP} "
        f"added 12 in 14 days and climbed from #6 to #2. Profile dormant 31 days."
    )
    y=wrap_text(c,M,y,findings,"Helvetica",8,W-2*M,leading=11,color=C_TEXT)
    y-=8

    # Causes
    sec(c,y,"Probable Causes")
    y-=6
    causes=[
        ("HIGH",C_RED,HexColor("#fee2e2"),"Competitor review acceleration",
         f"{COMP} gained 12 reviews in 14d, jumping from #6 to #2. You gained 1 review in 6 weeks. This velocity gap is the primary ranking factor driving your decline."),
        ("HIGH",C_RED,HexColor("#fee2e2"),"Profile inactivity (31 days)",
         "No photos or posts in 31 days. All top 3 competitors posted within 14 days. Google favors fresh, active profiles in local results and penalizes dormancy."),
        ("MED",C_ORANGE,C_AMBER_BG,"Sustained multi-week decline",
         "Rank trajectory: #2 > #3 > #3 > #4 > #5 > #7 over 6 consecutive scans. This is a structural gap widening over time, not temporary noise or fluctuation."),
    ]
    for lvl,cl,bg,title,body in causes:
        y-=6
        bw=badge(c,M,y,"  "+lvl+"  ",bg,cl)
        c.setFont("Helvetica-Bold",8.5); c.setFillColor(C_TEXT); c.drawString(M+bw+4,y+1,title)
        y-=13
        y=wrap_text(c,M+6,y,body,"Helvetica",7.5,W-2*M-12,leading=10,color=C_DGRAY)
        y-=5

    # Key Insight callout -- fill remaining space down to footer
    y-=6
    # Calculate remaining space: y is current position, need to leave room for footer (FH+4)
    remaining = y - CB - 6  # space available above footer
    rr(c,M,y-remaining,W-2*M,remaining,r=4,fill=C_AMBER_BG,stroke=HexColor("#fcd34d"),sw=0.3)
    c.setFillColor(C_ORANGE); c.rect(M,y-remaining,3,remaining,fill=1,stroke=0)
    c.setFont("Helvetica-Bold",8.5); c.setFillColor(C_ORANGE); c.drawString(M+8,y-12,"Key Takeaway")
    ki_text=(
        "This is not a single-factor drop. The combination of competitor review acceleration, "
        "profile dormancy, and review stagnation creates a compounding effect. Each factor reinforces "
        "the others -- fewer reviews reduce visibility, which reduces customer flow, which further "
        "slows review growth. Reversing the trend requires simultaneous action on all three fronts. "
        "See Priority Actions on page 2 for the recommended action sequence ranked by expected impact."
    )
    wrap_text(c,M+8,y-25,ki_text,"Helvetica",7.5,W-2*M-16,leading=10.5,color=C_TEXT)

    foot(c,f"Search Sentinel  |  sutraflow.org/sentinel  |  {RID}",1)

    # ═══════════════════════════════════════════════════════════════════════
    # PAGE 2 -- REVIEWS + IMPACT + COMPETITOR + TABLE + ACTIONS
    # ═══════════════════════════════════════════════════════════════════════
    c.showPage()
    tbar(c,"Search Sentinel  |  Intelligence & Actions",f"{BIZ}  |  {DATE}")
    y=H-40

    # Review Intelligence
    sec(c,y,"Review Intelligence  --  You vs. Market"); y-=6
    rv_h=80
    c.drawImage(ch_rev,M-4,y-rv_h,width=W-2*M+8,height=rv_h,preserveAspectRatio=True,mask="auto")
    y-=rv_h+2
    c.setFont("Helvetica-Oblique",6); c.setFillColor(C_DGRAY)
    c.drawString(M,y,f"'Market Avg' = average of top 20 results for '{CAT} {CITY}'. Velocity = reviews per week over 6-week window.")
    y-=8
    rev_text=(
        "Your review growth has stalled at 0.2 reviews/week -- well below the market average of 3.2/week "
        f"for '{CAT}' in {CITY}. Businesses averaging 3+ reviews/week consistently outrank those below "
        "1/week in Google Maps. Closing this velocity gap is the single highest-impact action available."
    )
    y=wrap_text(c,M,y,rev_text,"Helvetica",7.5,W-2*M,leading=10.5,color=C_TEXT)
    y-=6

    # Business Impact
    ih=32
    rr(c,M,y-ih,W-2*M,ih,r=4,fill=C_RED_BG,stroke=HexColor("#fca5a5"),sw=0.3)
    c.setFillColor(C_RED); c.rect(M,y-ih,3,ih,fill=1,stroke=0)
    c.setFont("Helvetica-Bold",8); c.setFillColor(C_RED); c.drawString(M+8,y-11,"Position #7 is below the fold on mobile.")
    c.setFont("Helvetica",7); c.setFillColor(C_DGRAY)
    c.drawString(M+8,y-22,"Industry data: 90% of clicks go to top 3 Maps results. At #7, you are losing ~88% of potential")
    c.drawString(M+8,y-31,"search-driven leads. Each week at this rank represents missed customer calls and bookings.")
    y-=ih+8

    # Competitor Head-to-Head
    divider(c,y); y-=12
    sec(c,y,f"Head-to-Head  --  You vs. {COMP}"); y-=6
    cp_h=82
    c.drawImage(ch_comp,M-4,y-cp_h,width=W-2*M+8,height=cp_h,preserveAspectRatio=True,mask="auto")
    y-=cp_h+6

    # Comparison Table
    sec(c,y,"Competitor Comparison"); y-=6
    c0=M; c1x=M+170; c2x=M+340; rh=14
    rr(c,M,y-rh,W-2*M,rh,r=2,fill=C_DARK)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",7)
    c.drawString(c0+6,y-10,"METRIC"); c.drawCentredString(c1x+70,y-10,BIZ[:22]); c.drawCentredString(c2x+55,y-10,COMP[:22])
    y-=rh
    rows=[
        ("Current Rank",f"#{CURR}","#2",C_RED,C_GREEN),
        ("Star Rating",f"{RATING}","4.8",C_TEXT,C_GREEN),
        ("Total Reviews",f"{REVIEWS}","148",C_RED,C_TEXT),
        ("Review Gain (14d)","+1","+12",C_RED,C_GREEN),
        ("Review Velocity","0.2/wk","6.0/wk",C_RED,C_GREEN),
        ("Last Profile Activity","31 days ago","4 days ago",C_RED,C_GREEN),
        ("Momentum","Declining","Climbing (+3)",C_RED,C_GREEN),
    ]
    for i,(met,v1,v2,c1c,c2c) in enumerate(rows):
        bg=C_OFF_WHITE if i%2==0 else C_WHITE
        c.setFillColor(bg); c.rect(M,y-rh,W-2*M,rh,fill=1,stroke=0)
        c.setFont("Helvetica",7); c.setFillColor(C_TEXT); c.drawString(c0+6,y-10,met)
        c.setFont("Helvetica-Bold",7); c.setFillColor(c1c); c.drawCentredString(c1x+70,y-10,v1)
        c.setFillColor(c2c); c.drawCentredString(c2x+55,y-10,v2)
        y-=rh
    y-=6

    # Profile Attribute Gap
    sec(c,y,"Profile Attribute Gap"); y-=6
    c.setFont("Helvetica",7); c.setFillColor(C_DGRAY)
    c.drawString(M,y,"You have 1 profile attribute. Top 3 competitors average 5. Missing attributes reduce visibility in filtered searches.")
    y-=10
    rr(c,M,y-14,W-2*M,14,r=2,fill=C_DARK)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",7)
    c.drawString(c0+6,y-10,"ATTRIBUTE"); c.drawCentredString(c1x+70,y-10,"You"); c.drawCentredString(c2x+55,y-10,"Top 3")
    y-=14
    attrs=[("Online appointments","Missing","Yes",C_RED,C_GREEN),("On-site services","Missing","Yes",C_RED,C_GREEN),
           ("Free consultation","Missing","Yes",C_RED,C_GREEN),("Emergency service","Yes","Yes",C_GREEN,C_GREEN)]
    for i,(attr,v1,v2,c1c,c2c) in enumerate(attrs):
        bg=C_OFF_WHITE if i%2==0 else C_WHITE
        c.setFillColor(bg); c.rect(M,y-14,W-2*M,14,fill=1,stroke=0)
        c.setFont("Helvetica",7); c.setFillColor(C_TEXT); c.drawString(c0+6,y-10,attr)
        c.setFont("Helvetica-Bold",7); c.setFillColor(c1c); c.drawCentredString(c1x+70,y-10,v1)
        c.setFillColor(c2c); c.drawCentredString(c2x+55,y-10,v2)
        y-=14
    y-=6

    gap=(f"Primary gap: {COMP} gaining reviews at 30x your rate (6.0 vs 0.2/wk). Combined with a 54-review "
         "count gap, superior profile freshness (4 days vs 31 days), and 3 additional profile attributes, "
         "multiple factors compound to create the ranking differential.")
    y=wrap_text(c,M,y,gap,"Helvetica",7.5,W-2*M,leading=10,color=C_DGRAY)
    y-=8

    # Priority Actions
    divider(c,y); y-=10
    sec(c,y,"Priority Actions  --  Ranked by Expected Impact"); y-=4

    actions=[
        ("01","Request reviews from 5 recent customers this week",
         f"Your velocity: 0.2/wk vs leader 6.0/wk. {COMP} gained 12 reviews in 14 days. Review count and "
         "recency are the strongest measurable local ranking factors.",
         "Low","High",C_GREEN),
        ("02","Post on Google Business Profile + add 3 job photos today",
         "31 days since last activity. All top-3 competitors posted within 14 days. Google treats recent "
         "profile activity as a freshness signal in local results.",
         "Low","Medium",C_ORANGE),
        ("03","Respond to all existing reviews (especially negative ones)",
         "8 of 94 reviews have no owner response. Response rate signals active business management and can "
         "improve conversion from searches to calls.",
         "Low","Medium",C_ORANGE),
        ("04","Hold paid ad changes -- monitor next 2 scans first",
         "Decline confirmed over 2 scans but still early. Wait for Apr 8 and Apr 15 data before making "
         "budget decisions. Organic fixes (#1-3) address the root cause directly.",
         "None","High (risk)",C_GREEN),
    ]
    for num,title,why,eff,imp,ic in actions:
        y-=8
        c.setFillColor(C_DARK); c.circle(M+7,y,8,fill=1,stroke=0)
        c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",8); c.drawCentredString(M+7,y-3,num)
        c.setFont("Helvetica-Bold",8.5); c.setFillColor(C_TEXT); c.drawString(M+20,y,title)
        y-=11
        y=wrap_text(c,M+20,y,why,"Helvetica",7,W-M-40,leading=9.5,color=C_DGRAY)
        y-=1
        bw1=badge(c,M+20,y,f"Effort: {eff}",C_BLUE_BG,C_ACCENT)
        badge(c,M+20+bw1+4,y,f"Impact: {imp}",C_GREEN_BG if "High" in imp else C_AMBER_BG,ic)
        y-=14

    foot(c,f"Search Sentinel  |  {BIZ}  |  {DATE}",2)

    # ═══════════════════════════════════════════════════════════════════════
    # PAGE 3 -- TEMPLATES + SCORECARD + OUTLOOK + ABOUT + BRANDING
    # ═══════════════════════════════════════════════════════════════════════
    c.showPage()
    tbar(c,"Search Sentinel  |  Ready Assets, Scorecard & Outlook",f"{BIZ}  |  {DATE}")
    y=H-40

    # Do This Today
    gradient(c,0,y-2,W,3,C_ACCENT,C_ACCENT2)
    y-=16
    sec(c,y,"Do This Today  --  Copy-Paste Ready Assets",C_ACCENT2); y-=10

    # SMS
    rr(c,M,y-14,W-2*M,14,r=2,fill=C_DARK)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",7); c.drawString(M+6,y-10,"SMS REVIEW REQUEST TEMPLATES  (under 160 chars)")
    y-=18
    sms=[
        ("A:",'"Hi [Name], thanks for choosing Capital City Plumbing! A quick Google review helps Austin neighbors find us: [link] Takes 60 sec!"'),
        ("B:",'"Hey [Name] -- plumbing issue is fixed, we hope! Share your experience? Austin homeowners rely on reviews: [link]"'),
    ]
    for lb,txt in sms:
        c.setFont("Helvetica-Bold",7); c.setFillColor(C_ACCENT); c.drawString(M+4,y,lb)
        lw=c.stringWidth(lb,"Helvetica-Bold",7)
        y=wrap_text(c,M+4+lw+3,y,txt,"Helvetica",7,W-2*M-lw-10,leading=10,color=C_TEXT)
        y-=5

    # GBP Post
    y-=4
    rr(c,M,y-14,W-2*M,14,r=2,fill=C_DARK)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",7); c.drawString(M+6,y-10,"GOOGLE BUSINESS PROFILE POST DRAFT")
    y-=18
    gbp='"Austin homeowners: clogged drains and water heater issues get worse in spring. Capital City Plumbing serves all Austin neighborhoods with same-day emergency service. Rated 4.6 stars by 94 Austin customers. Call or book online today."'
    y=wrap_text(c,M+4,y,gbp,"Helvetica",7,W-2*M-8,leading=10,color=C_TEXT)
    y-=6

    # Review Response
    rr(c,M,y-14,W-2*M,14,r=2,fill=C_DARK)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",7); c.drawString(M+6,y-10,"REVIEW RESPONSE TEMPLATES")
    y-=18
    tmpl=[
        ("Positive:",'"Thank you [Name]! We appreciate you trusting Capital City Plumbing with your [service]. Our Austin team takes pride in every job -- we are here whenever you need us! Your recommendation helps other Austin families find reliable plumbing."'),
        ("Negative:",'"[Name], we are sorry to hear about your experience. We would like to make this right. Please call us at [phone] so we can address your concerns directly. Customer satisfaction is our top priority and we take every review seriously."'),
    ]
    for lb,txt in tmpl:
        c.setFont("Helvetica-Bold",7); c.setFillColor(C_ACCENT); c.drawString(M+4,y,lb)
        lw=c.stringWidth(lb,"Helvetica-Bold",7)
        y=wrap_text(c,M+4+lw+3,y,txt,"Helvetica",7,W-2*M-lw-10,leading=10,color=C_TEXT)
        y-=6

    # Scorecard
    y-=4; divider(c,y); y-=14
    sec(c,y,"Business Visibility Scorecard"); y-=8
    c.setFont("Helvetica",7); c.setFillColor(C_DGRAY)
    c.drawString(M+7,y,"All scores from scan-measured data. Scale: 1 (critical) to 10 (excellent).")
    y-=8

    sc_h=92
    c.drawImage(ch_score,M-4,y-sc_h,width=255,height=sc_h,preserveAspectRatio=True,mask="auto")
    notes=[("Visibility: 3/10","Ranked #7. Only 1/6 weeks in top 3.",C_RED),
           ("Review Momentum: 2/10","0.2 reviews/wk vs market avg 3.2/wk.",C_RED),
           ("Profile Freshness: 2/10","No photos (31d), no posts (28d).",C_RED),
           ("Competitive Pressure: 3/10","Rival gained +4 positions, +12 reviews.",C_ORANGE),
           ("Data Confidence: 8/10","6 weeks history + competitor benchmarks.",C_GREEN)]
    ny=y-8
    for t,n,cl in notes:
        c.setFont("Helvetica-Bold",7.5); c.setFillColor(cl); c.drawString(292,ny,t)
        c.setFont("Helvetica",7); c.setFillColor(C_DGRAY); c.drawString(292,ny-10,n)
        ny-=20
    y-=sc_h+6

    # Overall score
    rr(c,M,y-28,W-2*M,28,r=4,fill=C_RED_BG,stroke=HexColor("#fca5a5"),sw=0.3)
    c.setFillColor(C_RED); c.rect(M,y-28,3,28,fill=1,stroke=0)
    c.setFont("Helvetica-Bold",9.5); c.setFillColor(C_RED); c.drawString(M+8,y-10,"OVERALL SCORE: 2.5/10  --  CRITICAL")
    c.setFont("Helvetica",7); c.setFillColor(C_DGRAY)
    c.drawString(M+8,y-22,"Weighted avg across 4 categories (excl. confidence). Immediate action on reviews + profile activity recommended.")
    y-=36

    # Data Confidence
    divider(c,y); y-=12
    sec(c,y,"Data Confidence  --  Limitations",C_GREEN); y-=8
    conf_h=56
    rr(c,M,y-conf_h,W-2*M,conf_h,r=4,fill=C_GREEN_BG,stroke=HexColor("#86efac"),sw=0.3)
    c.setFillColor(C_GREEN); c.rect(M,y-conf_h,3,conf_h,fill=1,stroke=0)
    ci=[("Data sources:","6 weekly rank scans, review counts, competitor profiles, category benchmarks"),
        ("What is measured:","Public Google Maps results, review velocity, profile activity recency"),
        ("Current limitation:","Single scan point per city. Multi-point geo-grid scanning planned for future."),
        ("Confidence score:",f"{CONF}/10 -- high. 6 weeks of consistent data + cross-referenced benchmarks.")]
    cy=y-10
    for lbl,val in ci:
        c.setFont("Helvetica-Bold",7); c.setFillColor(C_GREEN); c.drawString(M+8,cy,lbl)
        lw=c.stringWidth(lbl,"Helvetica-Bold",7)
        c.setFont("Helvetica",7); c.setFillColor(C_TEXT); c.drawString(M+8+lw+4,cy,val)
        cy-=12
    y-=conf_h+8

    # What Happens Next
    divider(c,y); y-=12
    sec(c,y,"What Happens Next",C_ACCENT2); y-=10
    tl=[("Apr 8, 2026","Next automated scan. If ranking improves, you receive a 'win' notification."),
        ("Apr 15, 2026","Third data point. If still declining: high-priority updated alert with new data."),
        ("May 1, 2026","Monthly rollup: 30-day trajectory, actions vs outcomes, velocity change."),
        ("Ongoing","Weekly scans continue. Alerts only fire after 2+ consecutive declining scans.")]
    for d,desc in tl:
        c.setFillColor(C_ACCENT2); c.circle(M+5,y,3,fill=1,stroke=0)
        c.setFont("Helvetica-Bold",7.5); c.setFillColor(C_ACCENT2); c.drawString(M+12,y-2,d)
        tw=c.stringWidth(d,"Helvetica-Bold",7.5)
        c.setFont("Helvetica",7); c.setFillColor(C_TEXT); c.drawString(M+12+tw+5,y-2,desc)
        y-=16

    # About
    y-=4; divider(c,y); y-=12
    sec(c,y,"About This Report",C_DGRAY); y-=10
    about=(
        "Rank data is collected via automated public data scans of Google Maps results for the specified "
        "business category and location. Scans run weekly. All metrics (rank position, review counts, "
        "velocity, profile activity) are computed by our rules engine before the report narrative is "
        "generated. Probable causes are identified based on publicly observable correlation data. "
        "This report does not represent an affiliation with or endorsement by Google LLC. Google does "
        "not disclose algorithm details; all analysis reflects measured data patterns and publicly "
        "documented ranking factors. Scores from measured scan data only. Contact: support@sutraflow.org."
    )
    y=wrap_text(c,M+6,y,about,"Helvetica",7,W-2*M-12,leading=10.5,color=C_DGRAY)

    # Reading guide - quick reference
    y-=10
    rr(c,M,y-50,W-2*M,50,r=4,fill=C_VLTBLUE,stroke=HexColor("#bfdbfe"),sw=0.3)
    c.setFillColor(C_ACCENT); c.rect(M,y-50,3,50,fill=1,stroke=0)
    c.setFont("Helvetica-Bold",7.5); c.setFillColor(C_ACCENT); c.drawString(M+8,y-10,"How to Use This Report")
    guide_items=[
        "Priority Actions (page 2) are ranked by expected impact -- start with Action #1 for fastest results.",
        "Copy-paste the SMS and review templates above directly into your workflow. Personalize [Name] and [link] fields.",
        "The scorecard tracks your progress over time. Scores update automatically with each weekly scan.",
        "Share this report with your team or marketing agency to coordinate local SEO improvements."
    ]
    gy=y-22
    for gi in guide_items:
        c.setFillColor(C_ACCENT); c.circle(M+12,gy+2,1.5,fill=1,stroke=0)
        c.setFont("Helvetica",6.5); c.setFillColor(C_TEXT); c.drawString(M+18,gy,gi)
        gy-=10

    # Bottom branding
    gradient(c,0,FH+2,W,28,C_DARK,C_DARK2)
    gradient(c,0,FH+30,W,2,C_ACCENT,C_ACCENT2)
    c.setFillColor(C_WHITE); c.setFont("Helvetica-Bold",8.5); c.drawString(M,FH+13,"Search Sentinel")
    c.setFillColor(HexColor("#60a5fa")); c.setFont("Helvetica",7)
    c.drawString(120,FH+13,"by SutraFlow  |  sutraflow.org/sentinel")
    c.setFillColor(C_GRAY); c.setFont("Helvetica",6.5)
    c.drawRightString(W-M,FH+13,"Automated visibility intelligence for local businesses")

    foot(c,f"Search Sentinel  |  {RID}  |  Confidential",3)

    c.save()
    print(f"OK: {out}")
    for f in [ch_rank,ch_rev,ch_comp,ch_score]:
        try: Path(f).unlink()
        except: pass
    return out

if __name__=="__main__":
    build()
