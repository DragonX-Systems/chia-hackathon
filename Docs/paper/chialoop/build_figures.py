"""Generate vector diagrams for the IEEE manuscript; requires reportlab."""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from math import atan2, cos, sin, pi
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab

font_dir = Path('/System/Library/Fonts/Supplemental')
if (font_dir / 'Arial.ttf').exists():
    regular, bold = font_dir / 'Arial.ttf', font_dir / 'Arial Bold.ttf'
else:
    font_dir = Path(reportlab.__file__).parent / 'fonts'
    regular, bold = font_dir / 'Vera.ttf', font_dir / 'VeraBd.ttf'
pdfmetrics.registerFont(TTFont('Diagram', str(regular)))
pdfmetrics.registerFont(TTFont('Diagram-Bold', str(bold)))


OUT = Path(__file__).resolve().parent / 'figures'
OUT.mkdir(exist_ok=True)
INK = HexColor('#203343')
BLUE = HexColor('#eaf1f7')
GRAY = HexColor('#f3f4f5')
GREEN = HexColor('#edf4ef')

def box(c, x, y, w, h, title, lines=(), fill=GRAY, title_size=9, size=8):
    c.setStrokeColor(INK)
    c.setFillColor(fill)
    c.setLineWidth(.75)
    c.roundRect(x, y, w, h, 4, fill=1, stroke=1)
    c.setFillColor(INK)
    c.setFont('Diagram-Bold', title_size)
    c.drawCentredString(x+w/2, y+h-15, title)
    c.setFont('Diagram', size)
    for i, line in enumerate(lines):
        c.drawCentredString(x+w/2, y+h-29-i*11, line)

def arrow(c, points, dashed=False):
    c.setStrokeColor(INK)
    c.setFillColor(INK)
    c.setLineWidth(.8)
    c.setDash(3, 2) if dashed else c.setDash()
    p = c.beginPath()
    p.moveTo(*points[0])
    for q in points[1:]: p.lineTo(*q)
    c.drawPath(p)
    c.setDash()
    (x0,y0),(x,y) = points[-2:]
    a = atan2(y-y0,x-x0)
    p = c.beginPath(); p.moveTo(x,y)
    p.lineTo(x-5*cos(a-pi/7),y-5*sin(a-pi/7))
    p.lineTo(x-5*cos(a+pi/7),y-5*sin(a+pi/7))
    p.close(); c.drawPath(p, fill=1, stroke=0)

def label(c,x,y,text,size=7.5):
    c.setFillColor(white)
    w = pdfmetrics.stringWidth(text, 'Diagram', size)
    c.rect(x-w/2-2,y-2,w+4,size+4,fill=1,stroke=0)
    c.setFillColor(INK); c.setFont('Diagram',size)
    c.drawCentredString(x,y,text)

c = canvas.Canvas(str(OUT/'architecture.pdf'), pagesize=(516,233), initialFontName='Diagram')
c.setTitle('ChiaLoop architecture and completion-driven feedback')
box(c,1,146,125,85,'FROZEN CAMPAIGN',[
    'Objective: minimize Compute95', 'Target 95%; compute budget B',
    'Coverage, jobs, versions', 'Resource and proof rules'], size=7.7)
box(c,161,146,171,85,'CHIALOOP CONTROLLER',[
    'Coverage state + recent-yield table', 'Probe / exploit / explore',
    'Replaceable allocation policy', 'Admission + asynchronous dispatch'], BLUE)
box(c,367,146,148,85,'CHIA FUNCTION NODES',[
    'run_crv: compute + sim token', 'run_directed: compute + sim token',
    'run_formal: compute + formal token', 'One invocation = one job'], size=7.5)
box(c,367,34,148,77,'HOMOGENEOUS POOL',[
    'Compute slot 1   |   Compute slot 2', 'sim token: 1   |   formal token: 1',
    'Jobs complete independently'], size=7.5)
box(c,161,34,171,77,'EVIDENCE VALIDATION',[
    'Check scope, assumptions, artifacts', 'Merge novel hits / complete proofs',
    'Update yield and consumed cost'], GREEN)
box(c,1,34,125,77,'AUDITABLE OUTPUTS',[
    'Append-only campaign events', 'Coverage and resource history',
    'Recomputable summaries'], size=7.6)
arrow(c,[(126,186),(161,186)])
arrow(c,[(332,186),(367,186)])
arrow(c,[(441,146),(441,111)])
arrow(c,[(367,72),(332,72)])
arrow(c,[(161,72),(126,72)])
arrow(c,[(246.5,111),(246.5,146)])
label(c,284,124,'replan',7.5)
label(c,258,9,'Static-Hybrid changes only the selection policy; evidence and execution paths are shared.',8)
c.save()

c = canvas.Canvas(str(OUT/'evidence.pdf'), pagesize=(252,183), initialFontName='Diagram')
c.setTitle('Evidence-governed coverage-state transitions')
box(c,29,144,194,37,'VALIDATE RESULT',[
    'Predicate, scope, versions, assumptions'], BLUE, size=7.5)
arrow(c,[(126,144),(126,124)])
box(c,83,94,86,30,'OPEN', title_size=9)
arrow(c,[(83,109),(48,109),(48,61)])
arrow(c,[(169,109),(204,109),(204,61)])
label(c,44,84,'legal hit / witness',7)
label(c,207,84,'complete proof',7)
box(c,1,29,96,32,'HIT',fill=GREEN)
box(c,135,29,116,32,'UNREACHABLE',fill=GREEN,title_size=8)
arrow(c,[(126,94),(126,71),(102,71),(102,94)],dashed=True)
label(c,126,18,'Timeout / unknown / bounded non-hit: remains open',7.2)
label(c,126,5,'Conflicting or incompatible evidence: reject and log',7.2)
c.save()
print('Generated architecture.pdf and evidence.pdf')
