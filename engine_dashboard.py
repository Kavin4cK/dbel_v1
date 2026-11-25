import tkinter as tk
import serial, threading, json, time, math, queue
from collections import deque
from datetime import datetime

PORTS = ('/dev/ttyUSB0', '/dev/ttyUSB1')
BAUD = 115200
TEMP_LIMIT, SMOKE_LIMIT = 45, 400
CLOSED_LIMIT, OPEN_LIMIT = 5, 10
TIMEOUT = 3
BG, CARD, FG, MUTED = '#080f1e', '#17243a', '#f1f5f9', '#a5b4c8'
GREEN, RED, AMBER, CYAN = '#4ade80', '#fb7185', '#fbbf24', '#38bdf8'
lock = threading.Lock()
stop = threading.Event()
latest = {}
previous = {}
history = deque(maxlen=100)

def classify(d):
    t, s, e, x = (d[k] for k in ('temperature','smoke','emergency','door_distance'))
    return {
        'TEMP': ('FAULT', AMBER) if not -55 <= t <= 125 else (f'{t:.1f} °C', RED if t >= TEMP_LIMIT else GREEN),
        'SMOKE': ('FAULT', AMBER) if not 0 <= s <= 1023 else (f'{s:.0f}', RED if s >= SMOKE_LIMIT else GREEN),
        'CHAIN': ('PULLED', RED) if e else ('OFF', GREEN),
        'DOOR': ('UNKNOWN', AMBER) if not 2 <= x <= 400 else ('CLOSED', GREEN) if x <= CLOSED_LIMIT else ('PARTIAL', AMBER) if x < OPEN_LIMIT else ('OPEN', AMBER)
    }

def conditions(d):
    t,s,e,x = (d[k] for k in ('temperature','smoke','emergency','door_distance'))
    c = set()
    if not -55 <= t <= 125: c.add('TEMP FAULT')
    elif t >= TEMP_LIMIT: c.add('OVERHEAT')
    if not 0 <= s <= 1023: c.add('SMOKE FAULT')
    elif s >= SMOKE_LIMIT: c.add('SMOKE ALERT')
    if e: c.add('CHAIN PULLED')
    if not 2 <= x <= 400: c.add('DOOR FAULT')
    elif x > CLOSED_LIMIT: c.add('DOOR OPEN' if x >= OPEN_LIMIT else 'DOOR PARTIAL')
    return c

def log(cid, message):
    history.appendleft((datetime.now().strftime('%H:%M:%S'), cid, message))

def receive(port):
    while not stop.is_set():
        try:
            with serial.Serial(port, BAUD, timeout=0.5) as ser:
                while not stop.is_set():
                    raw = ser.readline(512)
                    if not raw.endswith(b'\n'): continue
                    try:
                        d = json.loads(raw)
                        if not isinstance(d, dict): continue
                        cid = d['coach_id']
                        if type(cid) is not int or cid not in (1,2): continue
                        if type(d['emergency']) is not int or d['emergency'] not in (0,1): continue
                        if not all(type(d[k]) in (int,float) and math.isfinite(d[k]) for k in ('temperature','smoke','door_distance')): continue
                    except (ValueError, KeyError, TypeError, OverflowError):
                        continue
                    now = time.monotonic()
                    with lock:
                        old = latest.get(cid)
                        if old and old['port'] != port and now-old['seen'] <= TIMEOUT:
                            continue  # duplicate ID must not overwrite a live coach
                        if not old or not old['online'] or now-old['seen'] > TIMEOUT:
                            log(cid, 'CONNECTED')
                        c = conditions(d)
                        p = previous.get(cid, set())
                        for event in sorted(c-p): log(cid,event)
                        for event in sorted(p-c): log(cid,event+' CLEARED')
                        previous[cid] = c
                        latest[cid] = dict(d, port=port, seen=now, online=True)
        except (serial.SerialException, OSError):
            stop.wait(2)

class Dashboard:
    def __init__(self, root):
        self.root = root
        root.title('Railway Engine')
        root.configure(bg=BG)
        root.attributes('-fullscreen', True)
        root.bind('<Escape>', lambda e: root.attributes('-fullscreen', False))
        root.bind('<F11>', lambda e: root.attributes('-fullscreen', not bool(root.attributes('-fullscreen'))))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.page = 'live'
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.canvas.bind('<Button-1>', self.tap)
        self.hits = []
        self.offset = 0
        self.refresh()

    def close(self):
        stop.set()
        self.root.destroy()

    def tap(self, event):
        for x1,y1,x2,y2,action in self.hits:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                if action == 'exit': self.close(); return
                if action == 'older': self.offset += self.rows
                elif action == 'newer': self.offset = max(0,self.offset-self.rows)
                else: self.page = action; self.offset = 0
                self.draw(); return

    def refresh(self):
        with lock:
            for cid,d in latest.items():
                if d['online'] and time.monotonic()-d['seen'] > TIMEOUT:
                    d['online'] = False
                    log(cid,'DISCONNECTED')
        self.draw()
        self.root.after(200,self.refresh)

    def draw(self):
        c = self.canvas
        w,h = c.winfo_width(),c.winfo_height()
        if w < 100 or h < 100: return
        c.delete('all'); self.hits = []
        # Use a logical coordinate system, scaled to fit landscape or portrait.
        portrait = h > w
        W,H = (320,480) if portrait else (480,320)
        scale = min(w/W,h/H)
        ox,oy = (w-W*scale)/2,(h-H*scale)/2
        def rect(x,y,a,b,color):
            c.create_rectangle(ox+x*scale,oy+y*scale,ox+a*scale,oy+b*scale,fill=color,outline='')
        def txt(x,y,s,size=12,color=FG,anchor='w',bold=False):
            c.create_text(ox+x*scale,oy+y*scale,text=s,fill=color,anchor=anchor,font=('DejaVu Sans',-max(8,round(size*scale)),'bold' if bold else 'normal'))
        def button(x,y,a,b,label,action,active=False):
            rect(x,y,a,b,'#24516b' if active else CARD)
            txt((x+a)/2,(y+b)/2,label,12,FG,'center',True)
            self.hits.append((ox+x*scale,oy+y*scale,ox+a*scale,oy+b*scale,action))
        with lock:
            data = {k:v.copy() for k,v in latest.items()}
            events = list(history)
        online = [i for i in (1,2) if i in data and data[i]['online'] and time.monotonic()-data[i]['seen'] <= TIMEOUT]
        critical = [i for i in online if conditions(data[i]) & {'SMOKE ALERT','CHAIN PULLED','OVERHEAT'}]
        warning = [i for i in online if conditions(data[i])]
        txt(9,16,'RAILWAY ENGINE',15,CYAN,bold=True)
        txt(W-9,16,datetime.now().strftime('%H:%M:%S'),12,MUTED,'e')
        status = 'CRITICAL: COACH '+', '.join(map(str,critical)) if critical else 'COACH LINK WARNING' if len(online)<2 else 'CHECK COACH CONDITIONS' if warning else 'ALL SYSTEMS NORMAL'
        color = RED if critical else AMBER if len(online)<2 or warning else GREEN
        rect(8,31,W-8,59,CARD); txt(15,45,status,12,color,bold=True)
        txt(W-15,45,f'{len(online)}/2',12,color,'e',True)
        if self.page == 'live':
            for i in (1,2):
                if portrait: x,y,cw,ch = 8,66+(i-1)*182,W-16,174
                else: x,y,cw,ch = 8+(i-1)*234,66,230,202
                rect(x,y,x+cw,y+ch,CARD)
                on = i in online
                txt(x+9,y+17,f'COACH {i:02}',15,FG,bold=True)
                txt(x+cw-9,y+17,'ONLINE' if on else 'OFFLINE',10,GREEN if on else AMBER,'e',True)
                vals = classify(data[i]) if on else {k:('--',MUTED) for k in ('TEMP','SMOKE','CHAIN','DOOR')}
                step = 31 if portrait else 38
                for j,k in enumerate(('TEMP','SMOKE','CHAIN','DOOR')):
                    yy = y+39+j*step
                    rect(x+7,yy-13,x+cw-7,yy+14,BG)
                    txt(x+13,yy,k,11,MUTED,bold=True)
                    value,col = vals[k]
                    txt(x+cw-13,yy,value,15,col,'e',True)
                port = data[i]['port'].split('/')[-1] if on else 'No live data'
                txt(x+9,y+ch-10,port,10,MUTED)
        else:
            self.rows = max(1,(H-145)//24)
            self.offset = min(self.offset,max(0,len(events)-1))
            txt(10,77,'EVENT HISTORY (RAM)',13,CYAN,bold=True)
            if not events: txt(12,108,'No events yet',12,MUTED)
            for j,(stamp,cid,msg) in enumerate(events[self.offset:self.offset+self.rows]):
                yy=98+j*24
                rect(8,yy-10,W-8,yy+11,CARD)
                txt(12,yy,f'{stamp}  C{cid}  {msg}',11,FG)
            button(8,H-82,W/2-3,H-42,'Newer','newer')
            button(W/2+3,H-82,W-8,H-42,'Older','older')
        b=(W-24)/3
        for j,(label,action) in enumerate((('LIVE','live'),('EVENTS','events'),('EXIT','exit'))):
            x=8+j*(b+4)
            button(x,H-38,x+b,H-3,label,action,self.page==action)

if __name__ == '__main__':
    for port in PORTS:
        threading.Thread(target=receive,args=(port,),daemon=True).start()
    root=tk.Tk()
    Dashboard(root)
    root.mainloop()
