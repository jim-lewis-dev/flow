"""OS-level XTest interaction audit; no GUI callback invocations or synthetic Tk events."""
import argparse
import ctypes as C
import ctypes.util
import os, sys, tempfile, time, json, traceback, faulthandler
faulthandler.dump_traceback_later(25, exit=True)
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--report', type=Path, help='Write JSON outcomes and popup map geometry here.')
args=parser.parse_args()
if not os.environ.get('DISPLAY'):
    raise SystemExit('Run on an isolated Xvfb display; see tools/README.txt.')
from flowledger.core import Repository
from flowledger.demo import seed_demo
from flowledger.gui import FlowApp
from flowledger.widgets import FilterCombo
import tkinter as tk

x = C.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
xtst_library=os.environ.get('FLOW_XTST_LIBRARY') or ctypes.util.find_library('Xtst')
if not xtst_library:
    raise SystemExit('libXtst is required only for this UI audit (Ubuntu: libxtst6).')
xt = C.CDLL(xtst_library)
x.XOpenDisplay.argtypes = [C.c_char_p]; x.XOpenDisplay.restype=C.c_void_p
x.XStringToKeysym.argtypes=[C.c_char_p];x.XStringToKeysym.restype=C.c_ulong
x.XKeysymToKeycode.argtypes=[C.c_void_p,C.c_ulong];x.XKeysymToKeycode.restype=C.c_uint
x.XSetInputFocus.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_ulong]
x.XFlush.argtypes=[C.c_void_p]
x.XSync.argtypes=[C.c_void_p,C.c_int]
xt.XTestFakeMotionEvent.argtypes=[C.c_void_p,C.c_int,C.c_int,C.c_int,C.c_ulong]
xt.XTestFakeButtonEvent.argtypes=[C.c_void_p,C.c_uint,C.c_int,C.c_ulong]
xt.XTestFakeKeyEvent.argtypes=[C.c_void_p,C.c_uint,C.c_int,C.c_ulong]
display=x.XOpenDisplay(os.environ['DISPLAY'].encode());assert display
path=Path(tempfile.mkdtemp(prefix='flow-real-input-'))/'demo.sqlite3'
report=args.report or path.parent/'interaction_report.json'
report.parent.mkdir(parents=True,exist_ok=True)
success=False
repo=Repository(path);seed_demo(repo)
app=FlowApp(repo,demo=True);app.geometry('1920x1100+0+0')
errors=[];maps=[];results=[]
def report_error(cls,exc,tb):
    value=''.join(traceback.format_exception(cls,exc,tb));errors.append(value);print('CALLBACK_ERROR',value,flush=True)
app.report_callback_exception=report_error
def mapped(event):
    if isinstance(event.widget, tk.Toplevel):
        w=event.widget
        maps.append(dict(path=str(w),geometry=w.winfo_geometry(),override=bool(w.overrideredirect())))
app.bind_all('<Map>',mapped,add='+')
def pump(seconds=.12):
    faulthandler.dump_traceback_later(25, exit=True)
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.update();time.sleep(.008)
    app.update()
def move(px,py):
    xt.XTestFakeMotionEvent(display,0,int(px),int(py),0);x.XSync(display,False);pump(.03)
def click_xy(px,py,count=1):
    move(px,py)
    for n in range(count):
        xt.XTestFakeButtonEvent(display,1,True,0)
        xt.XTestFakeButtonEvent(display,1,False,0);x.XSync(display,False);pump(.04)
    pump(.18)
def click(widget,count=1):
    assert widget.winfo_viewable(),str(widget)
    click_xy(widget.winfo_rootx()+widget.winfo_width()/2,widget.winfo_rooty()+widget.winfo_height()/2,count)
def key(name,down=None):
    code=x.XKeysymToKeycode(display,x.XStringToKeysym(name.encode()));assert code,name
    if down is None:
        xt.XTestFakeKeyEvent(display,code,True,0);xt.XTestFakeKeyEvent(display,code,False,0)
    else:xt.XTestFakeKeyEvent(display,code,bool(down),0)
    x.XSync(display,False);pump(.04)
def chord(modifier,name):
    key(modifier,True);key(name);key(modifier,False);pump(.15)
def type_text(text):
    for char in text:
        mapping={' ':'space','\n':'Return','-':'minus','_':'underscore',',':'comma','.':'period',':':'colon', '"':'quotedbl',"'":'apostrophe','/':'slash','+':'plus'}
        symbol=mapping.get(char,char)
        shifted=char.isupper() or char in ':_+"'
        if shifted:key('Shift_L',True)
        key(symbol)
        if shifted:key('Shift_L',False)
def replace(widget,text):
    click(widget);key('Home');key('Shift_L',True);key('End');key('Shift_L',False);key('BackSpace');type_text(text)
def widgets(root=None):
    root=app if root is None else root
    yield root
    for child in root.winfo_children():yield from widgets(child)
def find_text(text,root=None):
    for w in widgets(root):
        try:
            if w.winfo_viewable() and str(w.cget('text'))==text:return w
        except tk.TclError:pass
    raise AssertionError('Missing visible control: '+text)
def click_text(text,root=None):click(find_text(text,root))
def flow_named(name):return next(f for f in repo.list_flows() if f['name']==name)
def row(tree,iid,column=None,count=1):
    tree.see(iid);pump(.05)
    bbox=tree.bbox(iid,column or '#1');assert bbox,(iid,column)
    px,py,w,h=bbox;click_xy(tree.winfo_rootx()+px+w/2,tree.winfo_rooty()+py+h/2,count)
def popups():return [w for w in widgets() if isinstance(w,FilterCombo) and w.popup is not None]
def ok(label):
    assert not errors,errors
    results.append(label);print('PASS',label,flush=True)
def clean_popups(label):
    pump(.3);assert not popups(),[(str(w),w.get()) for w in popups()];ok(label)
pump(.3);x.XSetInputFocus(display,app.winfo_id(),1,0);x.XSync(display,False);pump()
try:
    exec(Path(__file__).with_name('ui_smoke_cases.py').read_text())
    success=True
    print(f'PASS {len(results)} interaction checks; {len(maps)} positioned popup/dialog maps; no callback exceptions.',flush=True)
finally:
    report.write_text(json.dumps({'success':success,'input':'X11 XTest mouse and keyboard events','passed':results,'errors':errors,'maps':maps,'database':str(path)},indent=2))
    print('Report:',report,flush=True)
    app.destroy();repo.close()
