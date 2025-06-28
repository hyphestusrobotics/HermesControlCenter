"""

@Description: Hermes Control Center is a Python-based application built using FastAPI, designed to provide a comprehensive and user-friendly interface for
monitoring and controlling rocket systems. The platform visualizes real-time telemetry data through intuitive plots, displays rocket orientation,
and includes essential control mechanisms that support mission-critical operations. It serves as a vital tool for the launch team to manage and
oversee key functionalities of the rocket with precision and efficiency.


 /$$   /$$
| $$  | $$
| $$  | $$  /$$$$$$   /$$$$$$  /$$$$$$/$$$$   /$$$$$$   /$$$$$$$
| $$$$$$$$ /$$__  $$ /$$__  $$| $$_  $$_  $$ /$$__  $$ /$$_____/
| $$__  $$| $$$$$$$$| $$  \__/| $$ \ $$ \ $$| $$$$$$$$|  $$$$$$
| $$  | $$| $$_____/| $$      | $$ | $$ | $$| $$_____/ \____  $$
| $$  | $$|  $$$$$$$| $$      | $$ | $$ | $$|  $$$$$$$ /$$$$$$$/
|__/  |__/ \_______/|__/      |__/ |__/ |__/ \_______/|_______/



                           *     .--.
                                / /  `
               +               | |
                      '         \ \__,
                  *          +   '--'  *
                      +   /\
         +              .'  '.   *
                *      /======\      +
                      ;:.  _   ;
                      |:. (_)  |
                      |:.  _   |
            +         |:. (_)  |          *
                      ;:.      ;
                    .' \:.    / `.
                   / .-'':._.'`-. \
                   |/    /||\    \|
             jgs _..--'''````'''--.._
           _.-'``                    ``'-._
         -'                                '-


"""
import math
import os
import time

from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.websockets import WebSocket, WebSocketDisconnect
from termcolor import colored
from datetime import datetime
import random
import asyncio
import serial

# CONFIG: Match your actual port
SERIAL_PORT = '/dev/ttyACM0'
BAUD_RATE = 115200

ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
awaitable_lock = asyncio.Lock()  # prevent concurrent serial access


# In windows , to get the terminal colors you need the following command
os.system('color')

# Set up templates directory
templates = Jinja2Templates(directory="resources/templates")


def log(message: str, level: str = "INFO"):
    level = level.upper()

    color_map = {
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red"
    }

    level_color = color_map.get(level, "white")  # Default to white if unknown level
    date_str = colored(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]", "yellow")
    level_str = colored(f" - {level} - ", level_color)
    message_str = colored(message, "white")

    print(date_str + level_str + message_str)


last_altitude = 0.0
last_time = time.time()

def parse_telemetry(line: str):
    global last_altitude, last_time

    try:
        if not line.startswith("Unknown Packet:"):
            return None

        parts = line.split(":")[1].split(",")
        if len(parts) < 8:
            return None

        # Extract values
        acc_x = float(parts[1])
        acc_y = float(parts[2])
        acc_z = float(parts[3])
        gyro_x = float(parts[4])
        gyro_y = float(parts[5])
        gyro_z = float(parts[6])
        temp = float(parts[7])

        # Compute G-force
        g_force = round(math.sqrt(acc_x**2 + acc_y**2 + acc_z**2), 2)

        # Estimate Altitude via basic vertical acceleration integration
        # Remove 1G from vertical (acc_z) to get net acceleration
        net_acc_z = acc_z - 1.0  # 1G in Z direction
        current_time = time.time()
        dt = current_time - last_time
        last_time = current_time

        # Basic vertical velocity estimate (simple integration)
        velocity = net_acc_z * 9.80665 * dt  # Convert G to m/s²
        altitude = last_altitude + velocity * dt  # Integrate to altitude
        last_altitude = altitude

        # Calculate pressure from altitude using barometric formula
        P0 = 101.325  # kPa at sea level
        pressure = P0 * pow(1 - (altitude / 44330.0), 5.255) if altitude < 44330 else 0

        return {
            "G-force": g_force,
            "Altitude": round(altitude, 2),
            "Vertical Speed": round(velocity, 2),
            "Pressure": round(pressure, 2),
            "gyro": {
                "x": round(gyro_x, 2),
                "y": round(gyro_y, 2),
                "z": round(gyro_z, 2)
            },
            "temperature": round(temp, 2)
        }

    except Exception as e:
        log(f"Error parsing telemetry: {e}", level="ERROR")
        return None

print(log("INITIALIZING SERVER..."))

app = FastAPI()

# Allow frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

clients = set()

# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def get_index_page(request: Request):
    client_ip = request.client.host
    log(f"Request received from IP: {client_ip}", level="info")
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/deploy")
async def deploy_command():
    try:
        duration = 5  # seconds
        interval = 0.01  # seconds
        repetitions = int(duration / interval)

        for _ in range(repetitions):
            ser.write(b'CMD_TERMINATE\n')
            await asyncio.sleep(interval)

        log("Sent CMD_DEPLOY to serial for 20 seconds", level="INFO")
        return {"status": "success", "message": "CMD_DEPLOY sent for 20 seconds"}
    except Exception as e:
        log(f"Error sending CMD_DEPLOY: {e}", level="ERROR")
        return {"status": "error", "message": str(e)}


@app.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    log("WebSocket client connected.")

    try:
        while True:
            async with awaitable_lock:
                if ser.in_waiting:
                    raw = ser.readline()
                    try:
                        line = raw.decode("utf-8", errors="ignore").strip()
                        telemetry = parse_telemetry(line)
                        if telemetry:
                            await websocket.send_json(telemetry)
                            log(f"Sent: {telemetry}")
                    except Exception as e:
                        log(f"Decode error: {e}", level="WARNING")

            await asyncio.sleep(0.05)  # ~20Hz
    except WebSocketDisconnect:
        log("WebSocket client disconnected.", level="INFO")
    except Exception as e:
        log(f"Unexpected WebSocket error: {e}", level="ERROR")
    finally:
        clients.discard(websocket)



