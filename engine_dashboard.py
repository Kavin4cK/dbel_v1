import tkinter as tk
import serial, threading, json, time, math
from collections import deque
from datetime import datetime, timezone
import uuid, sqlite3, queue, traceback
from pathlib import Path
import requests

# Firebase Realtime Database configuration (public test database only)
FIREBASE_URL = "https://dbel-65121-default-rtdb.europe-west1.firebasedatabase.app/"
TRAIN_ID = "train_001"
LIVE_INTERVAL = 0.5  # seconds; latest state, not a new history entry each time
OUTBOX_FILE = Path(__file__).resolve().with_name("firebase_outbox.sqlite3")
cloud_queue = queue.SimpleQueue()
cloud_status = "CONNECTING"
cloud_last_success = None

def utc_now():
    return datetime.now(timezone.utc).isoformat()

class FirebaseREST:
    def __init__(self):
        from urllib.parse import urlsplit
        parsed = urlsplit(FIREBASE_URL)
        if (parsed.scheme != 'https' or not parsed.hostname or
            not parsed.hostname.endswith(('.firebaseio.com', '.firebasedatabase.app')) or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username):
            raise ValueError("Set FIREBASE_URL to your Realtime Database root URL")
        self.session = requests.Session()

    def update(self, updates):
        url = FIREBASE_URL.rstrip('/') + f'/trains/{TRAIN_ID}.json'
        response = self.session.patch(url, json=updates, timeout=(4, 8))
        if not response.ok:
            if response.status_code in (401, 403):
                raise RuntimeError(f'HTTP {response.status_code}: Permission denied; check database rules')
            raise RuntimeError(f'Firebase HTTP {response.status_code}: {response.text[:200]}')

    def close(self):
        self.session.close()


def cloud_worker():
    global cloud_status, cloud_last_success
    try:
        con = sqlite3.connect(OUTBOX_FILE)
        con.execute("CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        con.commit()
    except Exception as exc:
        cloud_status = "LOCAL STORAGE ERROR"
        print(f"[Firebase] Local storage error: {exc}", flush=True)
        return
    ref = None
    next_live = next_retry = 0
    while not stop.is_set() or not cloud_queue.empty():
        try:
            while True:
                event = cloud_queue.get_nowait()
                con.execute("INSERT OR IGNORE INTO outbox VALUES (?, ?)", (event["event_id"], json.dumps(event)))
                con.commit()
        except queue.Empty:
            pass
        except Exception as exc:
            cloud_status = "LOCAL STORAGE ERROR"
            print(f"[Firebase] Outbox error: {exc}", flush=True)
            cloud_queue.put(event)
            time.sleep(1)
            continue
        if stop.is_set(): break
        now = time.monotonic()
        if now >= next_retry:
            try:
                if ref is None:
                    ref = FirebaseREST()
                # Stable event IDs make retries idempotent.
                rows = con.execute("SELECT id, payload FROM outbox ORDER BY rowid LIMIT 20").fetchall()
                if rows:
                    ref.update({f"events/{eid}": json.loads(payload) for eid, payload in rows})
                    con.executemany("DELETE FROM outbox WHERE id=?", [(eid,) for eid, _ in rows])
                    con.commit()
                if now >= next_live:
                    with lock:
                        snapshot = {cid: d.copy() for cid, d in latest.items()}
                    updates = {}
                    for cid in (1, 2):
                        d = snapshot.get(cid, {})
                        online = bool(d) and d["online"] and time.monotonic()-d["seen"] <= TIMEOUT
                        state = {"coach_id": cid, "online": online, "port": f"/dev/ttyUSB{cid-1}", "uploaded_at": utc_now(), "server_timestamp_ms": {".sv": "timestamp"}, "stale": not online}
                        if d:
                            state.update({k: d[k] for k in ("temperature", "smoke", "emergency", "door_distance", "received_at")})
                            state["conditions"] = sorted(conditions(d)) if online else []
                            state["door_status"] = classify(d)["DOOR"][0] if online else "UNKNOWN"
                            state["stale"] = not online
                        updates[f"live_data/coach_{cid}"] = state
                    ref.update(updates)
                    cloud_last_success = time.monotonic()
                    if cloud_status != "ONLINE":
                        print("[Firebase] CONNECTED — live data upload confirmed", flush=True)
                    cloud_status = "ONLINE"
                    next_live = time.monotonic() + LIVE_INTERVAL
            except Exception as exc:
                cloud_status = "OFFLINE / RETRYING"
                print(f"[Firebase] {type(exc).__name__}: " + ("Network request failed" if isinstance(exc, requests.RequestException) else str(exc)), flush=True)
                # Avoid printing request URLs, which may contain authentication tokens.
                if ref is not None: ref.close()
                ref = None
                next_retry = time.monotonic() + 5
        stop.wait(0.2)
    if ref is not None: ref.close()
    con.close()

PORTS = {'/dev/ttyUSB0': 1, '/dev/ttyUSB1': 2}
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
port_ids = {}
port_errors = {}

def report(port, message):
    with lock:
        if port_errors.get(port) != message:
            port_errors[port] = message
            print(f"[{port}] {message}", flush=True)

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

def log(cid, message, data=None):
    history.appendleft((datetime.now().strftime('%H:%M:%S'), cid, message))
    d = data if data is not None else latest.get(cid, {})
    event = {"event_id": uuid.uuid4().hex, "coach_id": cid,
             "event_type": message, "timestamp": utc_now(),
             "port": f"/dev/ttyUSB{cid-1}",
             "sensor_values": {k: d[k] for k in ("temperature", "smoke", "emergency", "door_distance", "sequence", "uptime_ms") if k in d}}
    cloud_queue.put(event)

def receive(port):
    cid = PORTS[port]
    while not stop.is_set():
        try:
            with serial.Serial(port, BAUD, timeout=0.5, exclusive=True) as ser:
                report(port, "Serial opened; waiting for sensor data")
                if stop.wait(2): return
                while not stop.is_set():
                    raw = ser.readline(512)
                    if not raw.endswith(b'\n'): continue
                    try:
                        d = json.loads(raw)
                        if not isinstance(d, dict): continue
                        reported_id = d.get('coach_id')
                        if type(d['emergency']) is not int or d['emergency'] not in (0,1): continue
                        if not all(type(d[k]) in (int,float) and math.isfinite(d[k]) for k in ('temperature','smoke','door_distance')): continue
                    except (ValueError, KeyError, TypeError, OverflowError):
                        continue
                    now = time.monotonic()
                    with lock:
                        if port_ids.get(port) != (cid, reported_id):
                            port_ids[port] = (cid, reported_id)
                            print(f'[{port}] Assigned Coach {cid}; reported ID: {reported_id}', flush=True)
                        old = latest.get(cid)
                        port_errors.pop(port, None)
                        if not old or not old['online'] or now-old['seen'] > TIMEOUT:
                            log(cid, 'CONNECTED', d)
                        c = conditions(d)
                        p = previous.get(cid, set())
                        for event in sorted(c-p): log(cid,event,d)
                        for event in sorted(p-c): log(cid,event+' CLEARED',d)
                        previous[cid] = c
                        latest[cid] = dict(d, coach_id=cid, reported_id=reported_id, port=port, seen=now, online=True, received_at=utc_now())
        except (serial.SerialException, OSError) as exc:
            report(port, str(exc))
            with lock:
                port_ids.pop(port, None)
                for cid, d in latest.items():
                    if d['port'] == port and d['online']:
                        d['online'] = False
                        log(cid, 'DISCONNECTED')
            return

def discover():
    workers = {}
    while not stop.is_set():
        paths = set(PORTS)
        with lock:
            for missing in set(port_errors) - paths:
                port_errors.pop(missing, None)
        for port in sorted(paths):
            if port not in workers or not workers[port].is_alive():
                worker = threading.Thread(target=receive, args=(port,), daemon=True)
                workers[port] = worker
                worker.start()
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
        db_status = cloud_status
        if db_status == "ONLINE" and (cloud_last_success is None or time.monotonic()-cloud_last_success > 10):
            db_status = "UPLOAD DELAYED"
        db_label = "CONNECTED" if db_status == "ONLINE" else db_status
        db_color = GREEN if db_status == "ONLINE" else AMBER if db_status in ("CONNECTING", "UPLOAD DELAYED") else RED
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
                port = data[i]['port'].split('/')[-1] if on else f'ttyUSB{i-1} / No data'
                txt(x+9,y+ch-10,port,10,MUTED)
            txt(W/2,H-45,"FIREBASE: "+db_label,11,db_color,"center",True)
        else:
            self.rows = max(1,(H-145)//24)
            self.offset = min(self.offset,max(0,len(events)-1))
            txt(10,77,'EVENTS | DB: '+db_label,11,db_color,bold=True)
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
    worker = threading.Thread(target=cloud_worker, daemon=True)
    worker.start()
    threading.Thread(target=discover,daemon=True).start()
    root=tk.Tk()
    Dashboard(root)
    try:
        root.mainloop()
    finally:
        stop.set()
        worker.join(timeout=15)
