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
device: Optional[WebSocket] = None
clients: List[WebSocket] = []
_mission: dict = {}   # last uploaded mission, re-sent to new browser clients


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

    # Send any already-uploaded mission immediately so the browser sees it on load
    if _mission:
        try:
            await websocket.send_text(json.dumps({"type": "mission", **_mission}))
        except Exception:
            pass

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            text = msg.get("text")

            if text is not None and device:
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
# STATIC FRONTEND (must be last)
# ==============================
_dist = os.path.join(os.path.dirname(__file__), "dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
