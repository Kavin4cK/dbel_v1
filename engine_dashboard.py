import tkinter as tk
import serial, threading, json, time, math
from collections import deque
from datetime import datetime

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

def log(cid, message):
    history.appendleft((datetime.now().strftime('%H:%M:%S'), cid, message))

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
                            log(cid, 'CONNECTED')
                        c = conditions(d)
                        p = previous.get(cid, set())
                        for event in sorted(c-p): log(cid,event)
                        for event in sorted(p-c): log(cid,event+' CLEARED')
                        previous[cid] = c
                        latest[cid] = dict(d, coach_id=cid, reported_id=reported_id, port=port, seen=now, online=True)
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
