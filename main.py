from typing import List, Optional
import json
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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


# ==============================
# ROOT (IMPORTANT)
# ==============================
@app.get("/")
def root():
    return {"status": "ASV backend running"}


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

            # Disconnect case
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

                # Heartbeat
                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    continue

                # Broadcast to all clients
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
# CLIENT (FRONTEND)
# ==============================
@app.websocket("/ws/client")
async def client_ws(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)

    print(f"🌐 Client connected ({len(clients)})", flush=True)

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            text = msg.get("text")

            # Forward commands → device
            if text is not None and device:
                try:
                    await device.send_text(text)
                except Exception:
                    print("⚠️ Failed to send to device", flush=True)

    except WebSocketDisconnect:
        print("Client disconnected", flush=True)

    except Exception as e:
        print(f"❌ Client error: {e}", flush=True)

    finally:
        if websocket in clients:
            clients.remove(websocket)

        print(f"🔌 Client removed ({len(clients)} left)", flush=True)
