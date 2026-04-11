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

device: WebSocket = None
clients: list[WebSocket] = []


@app.websocket("/ws/device")
async def device_ws(websocket: WebSocket):
    global device
    await websocket.accept()
    device = websocket
    print("Device connected")

    try:
        while True:
            msg = await websocket.receive()

            if "text" in msg:
                # GPS/telemetry — broadcast to all clients
                dead = []
                for client in clients:
                    try:
                        await client.send_text(msg["text"])
                    except Exception:
                        dead.append(client)
                for c in dead:
                    clients.remove(c)

            elif "bytes" in msg:
                # Camera frame (binary, first byte = 0x01) — broadcast to clients
                dead = []
                for client in clients:
                    try:
                        await client.send_bytes(msg["bytes"])
                    except Exception:
                        dead.append(client)
                for c in dead:
                    clients.remove(c)

    except WebSocketDisconnect:
        print("Device disconnected")
    except Exception as e:
        print(f"Device error: {e}")
    finally:
        device = None


@app.websocket("/ws/client")
async def client_ws(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    print(f"Client connected  ({len(clients)} total)")

    try:
        while True:
            msg = await websocket.receive()

            # Joystick commands from browser → forward to device
            if "text" in msg and device:
                try:
                    await device.send_text(msg["text"])
                except Exception:
                    pass

    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"Client error: {e}")
    finally:
        if websocket in clients:
            clients.remove(websocket)
