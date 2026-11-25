import tkinter as tk
from tkinter import ttk
import serial
import threading
import json
import time
import math
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================

PORTS = [
    "/dev/ttyUSB0",
    "/dev/ttyUSB1"
]

BAUD_RATE = 115200

TEMP_THRESHOLD = 45.0
SMOKE_THRESHOLD = 400

DOOR_CLOSED_DISTANCE = 5.0
DOOR_OPEN_DISTANCE = 10.0

CONNECTION_TIMEOUT = 3

# ==========================================
# THEME
# ==========================================

BG = "#080F1E"
CARD = "#111C30"
PANEL = "#192740"

WHITE = "#F1F5F9"
MUTED = "#94A3B8"

GREEN = "#22C55E"
RED = "#EF4444"
YELLOW = "#F59E0B"
CYAN = "#38BDF8"

# ==========================================
# REAL-TIME MEMORY
# ==========================================

coach_data = {
    1: {},
    2: {}
}

data_lock = threading.Lock()

running = True


# ==========================================
# PACKET VALIDATION
# ==========================================

def validate_packet(data):

    if not isinstance(data, dict):
        return False

    fields = [
        "coach_id",
        "temperature",
        "smoke",
        "emergency",
        "door_distance"
    ]

    if not all(field in data for field in fields):
        return False

    if type(data["coach_id"]) is not int:
        return False

    if data["coach_id"] not in (1, 2):
        return False

    for field in [
        "temperature",
        "smoke",
        "door_distance"
    ]:

        value = data[field]

        if type(value) not in (int, float):
            return False

        if not math.isfinite(value):
            return False

    if data["emergency"] not in (0, 1):
        return False

    return True


# ==========================================
# SERIAL COMMUNICATION
# ==========================================

def read_serial(port):

    while running:

        try:

            with serial.Serial(
                port,
                BAUD_RATE,
                timeout=1
            ) as ser:

                time.sleep(2)

                while running:

                    raw = ser.readline(512)

                    if not raw:
                        continue

                    if not raw.endswith(b"\n"):
                        continue

                    try:

                        line = raw.decode().strip()

                        data = json.loads(line)

                        if not validate_packet(data):
                            continue

                        coach_id = data["coach_id"]

                        data["port"] = port

                        data["last_seen"] = (
                            time.monotonic()
                        )

                        with data_lock:

                            coach_data[coach_id] = data

                    except (
                        UnicodeDecodeError,
                        json.JSONDecodeError
                    ):
                        continue

        except (serial.SerialException, OSError):

            time.sleep(2)


# ==========================================
# COACH CARD
# ==========================================

class CoachCard:

    def __init__(self, parent, coach_id):

        self.coach_id = coach_id

        self.frame = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground="#263750",
            highlightthickness=1
        )

        # Coach heading

        header = tk.Frame(
            self.frame,
            bg=CARD
        )

        header.pack(
            fill="x",
            padx=20,
            pady=15
        )

        tk.Label(
            header,
            text=f"COACH {coach_id:02d}",
            font=("Arial", 22, "bold"),
            bg=CARD,
            fg=WHITE
        ).pack(side="left")

        self.connection = tk.Label(
            header,
            text="● WAITING",
            bg=CARD,
            fg=YELLOW,
            font=("Arial", 11, "bold")
        )

        self.connection.pack(side="right")

        # Overall coach status

        self.status = tk.Label(
            self.frame,
            text="WAITING FOR DATA",
            font=("Arial", 15, "bold"),
            bg=PANEL,
            fg=YELLOW,
            pady=12
        )

        self.status.pack(
            fill="x",
            padx=20,
            pady=(0, 10)
        )

        # Sensor panels

        self.temperature = self.create_sensor(
            "TEMPERATURE",
            "-- °C"
        )

        self.smoke = self.create_sensor(
            "SMOKE LEVEL",
            "-- / 1023"
        )

        self.smoke_bar = ttk.Progressbar(
            self.frame,
            maximum=1023,
            mode="determinate",
            style="Smoke.Horizontal.TProgressbar"
        )

        self.smoke_bar.pack(
            fill="x",
            padx=30,
            pady=(0, 8)
        )

        self.emergency = self.create_sensor(
            "EMERGENCY CHAIN",
            "UNKNOWN"
        )

        self.door = self.create_sensor(
            "DOOR STATUS",
            "UNKNOWN"
        )

        self.distance = tk.Label(
            self.frame,
            text="Door distance: -- cm",
            bg=CARD,
            fg=MUTED,
            font=("Arial", 11)
        )

        self.distance.pack(pady=4)

        self.port = tk.Label(
            self.frame,
            text="USB: Not connected",
            bg=CARD,
            fg=MUTED,
            font=("Arial", 10)
        )

        self.port.pack(pady=4)

        self.updated = tk.Label(
            self.frame,
            text="Last update: --",
            bg=CARD,
            fg=MUTED,
            font=("Arial", 10)
        )

        self.updated.pack(pady=(0, 15))


    def create_sensor(self, title, value):

        box = tk.Frame(
            self.frame,
            bg=PANEL
        )

        box.pack(
            fill="x",
            padx=20,
            pady=5
        )

        tk.Label(
            box,
            text=title,
            bg=PANEL,
            fg=MUTED,
            font=("Arial", 10, "bold")
        ).pack(
            anchor="w",
            padx=15,
            pady=(8, 0)
        )

        label = tk.Label(
            box,
            text=value,
            bg=PANEL,
            fg=WHITE,
            font=("Arial", 22, "bold")
        )

        label.pack(
            anchor="w",
            padx=15,
            pady=(0, 8)
        )

        return label


    def set_offline(self, waiting=False):

        self.connection.config(
            text="● WAITING" if waiting
            else "● OFFLINE",
            fg=YELLOW if waiting else RED
        )

        self.status.config(
            text="WAITING FOR DATA" if waiting
            else "COACH DISCONNECTED",
            fg=YELLOW if waiting else RED,
            bg=PANEL
        )

        self.temperature.config(
            text="-- °C",
            fg=MUTED
        )

        self.smoke.config(
            text="-- / 1023",
            fg=MUTED
        )

        self.smoke_bar["value"] = 0

        self.emergency.config(
            text="UNKNOWN",
            fg=MUTED
        )

        self.door.config(
            text="UNKNOWN",
            fg=MUTED
        )

        self.distance.config(
            text="Door distance: -- cm"
        )

        self.updated.config(
            text="Sensor data unavailable"
        )


    def update_data(self, data):

        self.connection.config(
            text="● CONNECTED",
            fg=GREEN
        )

        temperature = data["temperature"]
        smoke = data["smoke"]
        emergency = data["emergency"]
        distance = data["door_distance"]

        conditions = []

        # Temperature

        if not -55 <= temperature <= 125:

            self.temperature.config(
                text="SENSOR FAULT",
                fg=YELLOW
            )

            conditions.append(
                ("TEMPERATURE_FAULT", YELLOW)
            )

        elif temperature >= TEMP_THRESHOLD:

            self.temperature.config(
                text=f"{temperature:.1f} °C",
                fg=RED
            )

            conditions.append(
                ("OVERHEATING", YELLOW)
            )

        else:

            self.temperature.config(
                text=f"{temperature:.1f} °C",
                fg=GREEN
            )

        # Smoke sensor

        if not 0 <= smoke <= 1023:

            self.smoke.config(
                text="SENSOR FAULT",
                fg=YELLOW
            )

            self.smoke_bar["value"] = 0

            conditions.append(
                ("SMOKE_SENSOR_FAULT", YELLOW)
            )

        elif smoke >= SMOKE_THRESHOLD:

            self.smoke.config(
                text=f"{smoke:.0f} — SMOKE",
                fg=RED
            )

            self.smoke_bar["value"] = smoke

            conditions.append(
                ("SMOKE_DETECTED", RED)
            )

        else:

            self.smoke.config(
                text=f"{smoke:.0f} / 1023",
                fg=GREEN
            )

            self.smoke_bar["value"] = smoke

        # Emergency chain

        if emergency == 1:

            self.emergency.config(
                text="CHAIN PULLED",
                fg=RED
            )

            conditions.append(
                ("EMERGENCY_CHAIN", RED)
            )

        else:

            self.emergency.config(
                text="OFF — NORMAL",
                fg=GREEN
            )

        # Door status

        if distance < 2 or distance > 400:

            self.door.config(
                text="UNKNOWN",
                fg=YELLOW
            )

            conditions.append(
                ("DOOR_SENSOR_FAULT", YELLOW)
            )

        elif distance <= DOOR_CLOSED_DISTANCE:

            self.door.config(
                text="CLOSED",
                fg=GREEN
            )

        elif distance < DOOR_OPEN_DISTANCE:

            self.door.config(
                text="PARTIALLY OPEN",
                fg=YELLOW
            )

            conditions.append(
                ("DOOR_PARTIALLY_OPEN", YELLOW)
            )

        else:

            self.door.config(
                text="OPEN",
                fg=YELLOW
            )

            conditions.append(
                ("DOOR_OPEN", YELLOW)
            )

        # Overall coach status

        critical = any(
            color == RED
            for _, color in conditions
        )

        if critical:

            self.status.config(
                text="CRITICAL ALERT",
                bg="#450A0A",
                fg=RED
            )

        elif conditions:

            self.status.config(
                text="ATTENTION REQUIRED",
                bg="#422006",
                fg=YELLOW
            )

        else:

            self.status.config(
                text="ALL SYSTEMS NORMAL",
                bg="#052E16",
                fg=GREEN
            )

        self.distance.config(
            text=f"Door distance: {distance:.2f} cm"
        )

        self.port.config(
            text=f"USB: {data['port']}"
        )

        age = time.monotonic() - data["last_seen"]

        self.updated.config(
            text=f"Last packet: {age:.1f}s ago"
        )

        return conditions


# ==========================================
# MAIN DASHBOARD
# ==========================================

class EngineDashboard:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "Railway Engine Monitoring System"
        )

        self.root.geometry("1280x900")

        self.root.configure(bg=BG)

        self.fullscreen = False

        self.previous_conditions = {
            1: set(),
            2: set()
        }

        self.previous_connections = {
            1: None,
            2: None
        }

        self.root.bind(
            "<F11>",
            self.toggle_fullscreen
        )

        self.root.bind(
            "<Escape>",
            self.exit_fullscreen
        )

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )

        # Progress bar theme

        style = ttk.Style()

        style.theme_use("clam")

        style.configure(
            "Smoke.Horizontal.TProgressbar",
            troughcolor=PANEL,
            background=CYAN,
            bordercolor=PANEL,
            thickness=8
        )

        style.configure(
            "Treeview",
            background=CARD,
            foreground=WHITE,
            fieldbackground=CARD,
            rowheight=27
        )

        style.configure(
            "Treeview.Heading",
            background=PANEL,
            foreground=WHITE,
            font=("Arial", 10, "bold")
        )

        # Header

        header = tk.Frame(
            root,
            bg=BG
        )

        header.pack(
            fill="x",
            padx=25,
            pady=15
        )

        tk.Label(
            header,
            text="RAILWAY CONTROL SYSTEM",
            bg=BG,
            fg=WHITE,
            font=("Arial", 26, "bold")
        ).pack(anchor="w")

        tk.Label(
            header,
            text="ENGINE CONTROL PANEL  /  LIVE MONITORING",
            bg=BG,
            fg=CYAN,
            font=("Arial", 11, "bold")
        ).pack(anchor="w")

        self.clock = tk.Label(
            header,
            bg=BG,
            fg=MUTED,
            font=("Arial", 12)
        )

        self.clock.pack(anchor="e")

        # System summary

        summary = tk.Frame(
            root,
            bg=BG
        )

        summary.pack(
            fill="x",
            padx=25,
            pady=(0, 10)
        )

        self.total = self.create_summary(
            summary,
            "TOTAL COACHES",
            "02",
            CYAN
        )

        self.connected = self.create_summary(
            summary,
            "CONNECTED",
            "00",
            GREEN
        )

        self.critical = self.create_summary(
            summary,
            "CRITICAL COACHES",
            "00",
            RED
        )

        self.warnings = self.create_summary(
            summary,
            "WARNING COACHES",
            "00",
            YELLOW
        )

        # Coach cards

        coaches = tk.Frame(
            root,
            bg=BG
        )

        coaches.pack(
            fill="both",
            expand=True,
            padx=25,
            pady=5
        )

        coaches.columnconfigure(0, weight=1)
        coaches.columnconfigure(1, weight=1)
        coaches.rowconfigure(0, weight=1)

        self.cards = {}

        for coach_id in (1, 2):

            card = CoachCard(
                coaches,
                coach_id
            )

            card.frame.grid(
                row=0,
                column=coach_id - 1,
                sticky="nsew",
                padx=7
            )

            self.cards[coach_id] = card

        # Alert banner

        self.alert_banner = tk.Label(
            root,
            text="INITIALIZING SYSTEM",
            bg=PANEL,
            fg=YELLOW,
            font=("Arial", 14, "bold"),
            pady=10
        )

        self.alert_banner.pack(
            fill="x",
            padx=32,
            pady=10
        )

        # Event history

        event_frame = tk.Frame(
            root,
            bg=CARD
        )

        event_frame.pack(
            fill="x",
            padx=32,
            pady=(0, 10)
        )

        tk.Label(
            event_frame,
            text="LIVE EVENT LOG",
            bg=CARD,
            fg=WHITE,
            font=("Arial", 12, "bold")
        ).pack(
            anchor="w",
            padx=10,
            pady=5
        )

        self.events = ttk.Treeview(
            event_frame,
            columns=(
                "time",
                "coach",
                "event"
            ),
            show="headings",
            height=4
        )

        self.events.heading(
            "time", text="TIME"
        )

        self.events.heading(
            "coach", text="COACH"
        )

        self.events.heading(
            "event", text="EVENT"
        )

        self.events.column(
            "time", width=100
        )

        self.events.column(
            "coach", width=100
        )

        self.events.column(
            "event", width=500
        )

        self.events.pack(
            fill="x",
            padx=10,
            pady=5
        )

        # Footer

        tk.Label(
            root,
            text=(
                "ENGINE NODE: RASPBERRY PI"
                "   |   USB SERIAL"
                "   |   F11: FULLSCREEN"
                "   |   ESC: EXIT FULLSCREEN"
            ),
            bg=BG,
            fg=MUTED,
            font=("Arial", 9)
        ).pack(pady=5)

        self.refresh()


    def create_summary(
        self,
        parent,
        title,
        value,
        color
    ):

        box = tk.Frame(
            parent,
            bg=CARD
        )

        box.pack(
            side="left",
            fill="both",
            expand=True,
            padx=7
        )

        tk.Label(
            box,
            text=title,
            bg=CARD,
            fg=MUTED,
            font=("Arial", 10, "bold")
        ).pack(pady=(8, 0))

        label = tk.Label(
            box,
            text=value,
            bg=CARD,
            fg=color,
            font=("Arial", 25, "bold")
        )

        label.pack(pady=(0, 8))

        return label


    def add_event(self, coach_id, message):

        timestamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        self.events.insert(
            "",
            0,
            values=(
                timestamp,
                f"COACH {coach_id:02d}",
                message
            )
        )

        rows = self.events.get_children()

        # Keep only the latest 100 events

        if len(rows) > 100:

            self.events.delete(rows[-1])


    def refresh(self):

        self.clock.config(
            text=datetime.now().strftime(
                "%d %B %Y  |  %H:%M:%S"
            )
        )

        connected_count = 0

        critical_coaches = []

        warning_count = 0

        with data_lock:

            snapshot = {
                cid: data.copy()
                for cid, data in coach_data.items()
            }

        for coach_id, data in snapshot.items():

            card = self.cards[coach_id]

            online = bool(data) and (
                time.monotonic() - data["last_seen"]
                <= CONNECTION_TIMEOUT
            )

            if not online:

                card.set_offline(
                    waiting=not bool(data)
                )

                if (
                    self.previous_connections[coach_id]
                    is True
                ):

                    self.add_event(
                        coach_id,
                        "COACH DISCONNECTED"
                    )

                self.previous_connections[
                    coach_id
                ] = False

                continue

            connected_count += 1

            if not self.previous_connections[
                coach_id
            ]:

                self.add_event(
                    coach_id,
                    "COACH CONNECTED"
                )

            self.previous_connections[
                coach_id
            ] = True

            conditions = card.update_data(data)

            current = {
                name
                for name, _ in conditions
            }

            previous = self.previous_conditions[
                coach_id
            ]

            # Newly detected events

            for event in current - previous:

                self.add_event(
                    coach_id,
                    event.replace("_", " ")
                )

            # Cleared events

            for event in previous - current:

                self.add_event(
                    coach_id,
                    event.replace("_", " ")
                    + " CLEARED"
                )

            self.previous_conditions[
                coach_id
            ] = current

            if any(
                color == RED
                for _, color in conditions
            ):

                critical_coaches.append(
                    coach_id
                )

            elif conditions:

                warning_count += 1

        self.connected.config(
            text=f"{connected_count:02d}"
        )

        self.critical.config(
            text=f"{len(critical_coaches):02d}"
        )

        self.warnings.config(
            text=f"{warning_count:02d}"
        )

        if critical_coaches:

            names = ", ".join(
                f"COACH {cid:02d}"
                for cid in critical_coaches
            )

            self.alert_banner.config(
                text=f"CRITICAL ALERT — {names}",
                bg="#450A0A",
                fg=RED
            )

        elif connected_count < 2:

            self.alert_banner.config(
                text="WARNING — COACH CONNECTION LOST OR PENDING",
                bg="#422006",
                fg=YELLOW
            )

        elif warning_count:

            self.alert_banner.config(
                text="WARNING — CHECK COACH CONDITIONS",
                bg="#422006",
                fg=YELLOW
            )

        else:

            self.alert_banner.config(
                text="ALL COACHES OPERATING NORMALLY",
                bg="#052E16",
                fg=GREEN
            )

        # GUI refresh every 200 ms

        self.root.after(
            200,
            self.refresh
        )


    def toggle_fullscreen(self, event=None):

        self.fullscreen = not self.fullscreen

        self.root.attributes(
            "-fullscreen",
            self.fullscreen
        )


    def exit_fullscreen(self, event=None):

        self.fullscreen = False

        self.root.attributes(
            "-fullscreen",
            False
        )


    def close(self):

        global running

        running = False

        self.root.destroy()


# ==========================================
# MAIN PROGRAM
# ==========================================

if __name__ == "__main__":

    for port in PORTS:

        thread = threading.Thread(
            target=read_serial,
            args=(port,),
            daemon=True
        )

        thread.start()

    root = tk.Tk()

    dashboard = EngineDashboard(root)

    root.mainloop()
