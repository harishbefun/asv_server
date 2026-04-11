from typing import List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

device: Optional[WebSocket] = None
clients: List[WebSocket] = []


@app.websocket("/ws/device")
async def device_ws(websocket: WebSocket):
    global device
    await websocket.accept()
    device = websocket
    print("Device connected", flush=True)

    try:
        while True:
            msg = await websocket.receive()
            text = msg.get("text")
            data = msg.get("bytes")

            if text is not None:
                # Telemetry / camera (base64 JSON) — broadcast to all clients
                dead = []
                for client in clients:
                    try:
                        await client.send_text(text)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    clients.remove(c)

            elif data is not None:
                # Binary camera frame — broadcast to all clients
                dead = []
                for client in clients:
                    try:
                        await client.send_bytes(data)
                    except Exception:
                        dead.append(client)
                for c in dead:
                    clients.remove(c)

    except WebSocketDisconnect:
        print("Device disconnected", flush=True)
    except Exception as e:
        print(f"Device error: {e}", flush=True)
    finally:
        device = None


@app.websocket("/ws/client")
async def client_ws(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    print(f"Client connected ({len(clients)} total)", flush=True)

    try:
        while True:
            msg = await websocket.receive()
            text = msg.get("text")

            # Joystick commands from browser → forward to device
            if text is not None and device:
                try:
                    await device.send_text(text)
                except Exception:
                    pass

    except WebSocketDisconnect:
        print("Client disconnected", flush=True)
    except Exception as e:
        print(f"Client error: {e}", flush=True)
    finally:
        if websocket in clients:
            clients.remove(websocket)
