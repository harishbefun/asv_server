from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

clients = []
device = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws/device")
async def device_ws(ws: WebSocket):
    global device
    await ws.accept()
    device = ws
    print("Device connected")

    try:
        while True:
            data = await ws.receive_text()
            for client in clients:
                await client.send_text(data)

    except WebSocketDisconnect:
        print("Device disconnected")
        device = None


@app.websocket("/ws/client")
async def client_ws(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    print("Client connected")

    try:
        while True:
            data = await ws.receive_text()
            if device:
                await device.send_text(data)

    except WebSocketDisconnect:
        print("Client disconnected")
        clients.remove(ws)
