from typing import List, Optional
import json
import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()

# ==============================
# CORS
# ==============================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================
# GLOBAL STATE
# ==============================
device:         Optional[WebSocket] = None
mission_worker: Optional[WebSocket] = None
auto_pilot:     Optional[WebSocket] = None
obstacle_ws:    Optional[WebSocket] = None
clients: List[WebSocket] = []
_mission: dict = {}   # last uploaded mission, re-sent to new browser clients
_health:  dict = {}   # last health snapshot, re-sent to new browser clients


# ==============================
# HEALTH CHECK
# ==============================
@app.get("/api/status")
def status():
    return {"status": "ASV backend running", "clients": len(clients), "device": device is not None}


# ==============================
# MISSION PLAN  (POST from Jetson, GET from browser)
# ==============================
@app.post("/api/mission")
async def upload_mission(payload: dict = Body(...)):
    global _mission
    _mission = payload
    msg = json.dumps({"type": "mission", **payload})
    dead = []
    for client in clients:
        try:
            await client.send_text(msg)
        except Exception:
            dead.append(client)
    for c in dead:
        if c in clients:
            clients.remove(c)
    return {"status": "ok", "clients_notified": len(clients) - len(dead)}

@app.get("/api/mission")
def get_mission():
    return _mission


# ==============================
# DEVICE (JETSON)
# ==============================
@app.websocket("/ws/device")
async def device_ws(websocket: WebSocket):
    global device

    await websocket.accept()
    device = websocket
    print("🚤 Device connected", flush=True)

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            text = msg.get("text")
            data = msg.get("bytes")

            # ---------- TEXT ----------
            if text is not None:
                try:
                    parsed = json.loads(text)
                    msg_type = parsed.get("type", "")
                except Exception:
                    msg_type = ""

                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    continue

                dead = []
                for client in clients:
                    try:
                        await client.send_text(text)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    if c in clients:
                        clients.remove(c)

            # ---------- BINARY ----------
            elif data is not None:
                dead = []
                for client in clients:
                    try:
                        await client.send_bytes(data)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    if c in clients:
                        clients.remove(c)

    except Exception as e:
        print(f"❌ Device error: {e}", flush=True)

    finally:
        print("⚠️ Device disconnected", flush=True)
        device = None


# ==============================
# CLIENT (BROWSER)
# ==============================
@app.websocket("/ws/client")
async def client_ws(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    print(f"🌐 Client connected ({len(clients)})", flush=True)

    # Replay last known mission and health to newly-connected browsers
    if _mission:
        try:
            await websocket.send_text(json.dumps({"type": "mission", **_mission}))
        except Exception:
            pass
    if _health:
        try:
            await websocket.send_text(json.dumps(_health))
        except Exception:
            pass

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            text = msg.get("text")

            if text is not None:
                try:
                    parsed = json.loads(text)
                    msg_type = parsed.get("type", "")
                except Exception:
                    msg_type = ""
                    parsed = {}

                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "ts": parsed.get("ts")}))
                    continue

                if msg_type in ("start_autonomous", "stop_autonomous"):
                    if auto_pilot:
                        try:
                            await auto_pilot.send_text(text)
                        except Exception:
                            print("⚠️ Failed to send to auto_pilot", flush=True)
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "autonomous_status", "status": "error",
                            "log": "Autonomous pilot not connected on Jetson",
                            "level": "error",
                        }))
                    continue

                if msg_type in ("mission_request", "regenerate_path",
                                "start_simulation", "stop_simulation"):
                    if mission_worker:
                        try:
                            await mission_worker.send_text(text)
                        except Exception:
                            print("⚠️ Failed to send to mission worker", flush=True)
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "mission_status", "status": "error",
                            "msg": f"Mission service not connected on Jetson ({msg_type})"
                        }))
                    continue

                if device:
                    try:
                        await device.send_text(text)
                    except Exception:
                        print("⚠️ Failed to send to device", flush=True)

    except WebSocketDisconnect:
        pass

    except Exception as e:
        print(f"❌ Client error: {e}", flush=True)

    finally:
        if websocket in clients:
            clients.remove(websocket)
        print(f"🔌 Client removed ({len(clients)} left)", flush=True)


# ==============================
# JETSON HEALTH  (Jetson → server → browsers)
# ==============================
@app.websocket("/ws/health")
async def health_ws(websocket: WebSocket):
    global _health
    await websocket.accept()
    print("💻 Health monitor connected", flush=True)
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text:
                try:
                    _health = json.loads(text)
                except Exception:
                    continue
                dead = []
                for client in clients:
                    try:
                        await client.send_text(text)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    if c in clients:
                        clients.remove(c)
    except Exception as e:
        print(f"❌ Health monitor error: {e}", flush=True)
    finally:
        print("💻 Health monitor disconnected", flush=True)


# ==============================
# MISSION WORKER  (Jetson mission generator)
# ==============================
@app.websocket("/ws/mission_worker")
async def mission_worker_ws(websocket: WebSocket):
    global mission_worker
    await websocket.accept()
    mission_worker = websocket
    print("🗺️ Mission worker connected", flush=True)
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text:
                # Forward status updates from Jetson → all browser clients
                dead = []
                for client in clients:
                    try:
                        await client.send_text(text)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    if c in clients:
                        clients.remove(c)
    except Exception as e:
        print(f"❌ Mission worker error: {e}", flush=True)
    finally:
        mission_worker = None
        print("🗺️ Mission worker disconnected", flush=True)
        # Tell all browsers the worker went offline
        dead = []
        for client in clients:
            try:
                await client.send_text(json.dumps({
                    "type": "mission_status", "status": "offline",
                    "msg": "Mission service disconnected from Jetson"
                }))
            except Exception:
                dead.append(client)
        for c in dead:
            if c in clients: clients.remove(c)


# ==============================
# AUTO PILOT  (Jetson autonomous mission runner)
# ==============================
@app.websocket("/ws/auto_pilot")
async def auto_pilot_ws(websocket: WebSocket):
    global auto_pilot
    await websocket.accept()
    auto_pilot = websocket
    print("🤖 Auto-pilot connected", flush=True)
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text:
                dead = []
                for client in clients:
                    try:
                        await client.send_text(text)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    if c in clients:
                        clients.remove(c)
    except Exception as e:
        print(f"❌ Auto-pilot error: {e}", flush=True)
    finally:
        auto_pilot = None
        print("🤖 Auto-pilot disconnected", flush=True)


# ==============================
# OBSTACLE DETECTOR  (Jetson SegFormer → browsers)
# ==============================
@app.websocket("/ws/obstacle")
async def obstacle_ws_handler(websocket: WebSocket):
    global obstacle_ws
    await websocket.accept()
    obstacle_ws = websocket
    print("👁️ Obstacle detector connected", flush=True)
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text:
                dead = []
                for client in clients:
                    try:
                        await client.send_text(text)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    if c in clients:
                        clients.remove(c)
    except Exception as e:
        print(f"❌ Obstacle detector error: {e}", flush=True)
    finally:
        obstacle_ws = None
        print("👁️ Obstacle detector disconnected", flush=True)


# ==============================
# STATIC FRONTEND (must be last)
# ==============================
_dist = os.path.join(os.path.dirname(__file__), "dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
