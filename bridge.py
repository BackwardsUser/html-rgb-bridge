#!/usr/bin/env python3
"""
html-rgb-bridge: run HTML canvas lighting effects on OpenRGB devices.

Pages connect over a WebSocket in one of two roles:
  renderer  runs the effect, samples its canvas at each LED and streams frames.
            With --render, the bridge keeps one running in a headless browser,
            so lighting never stalls when a window is hidden or closed.
  control   the settings UI (browser tab or control.py). It sends choices to
            the bridge, which saves them and forwards them to the renderer.
            If no renderer is connected, a control page renders by itself.
"""
import argparse
import asyncio
import json
import logging
import pathlib
import re
import shutil
import time

from aiohttp import WSCloseCode, WSMsgType, web
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor, ZoneType

ROOT = pathlib.Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
PROFILE_DIR = ROOT / ".renderer-profile"
MATRIX_TYPES = {ZoneType.MATRIX, ZoneType.MATRIX_LOOP_X, ZoneType.MATRIX_LOOP_Y}
BROWSERS = ["chromium", "google-chrome-stable", "google-chrome", "brave", "brave-browser", "microsoft-edge-stable"]
MIRROR_FPS = 20  # how often control pages get a copy of the frames for their preview

log = logging.getLogger("bridge")


def led_layout(device):
    """Return one {x, y, name} per device LED (device order), x/y normalized to 0..1."""
    leds = [None] * len(device.leds)
    for zone in device.zones:
        count = len(zone.leds)
        grid = zone.matrix_map if zone.type in MATRIX_TYPES else None
        if grid:
            rows = len(grid)
            cols = max((len(r) for r in grid), default=1) or 1
            for r, row in enumerate(grid):
                for c, idx in enumerate(row):
                    if idx is not None and 0 <= idx < count:
                        led = zone.leds[idx]
                        leds[led.id] = {"x": (c + 0.5) / cols, "y": (r + 0.5) / rows, "name": led.name}
        # LEDs not placed by a matrix (strips, single LEDs, unmapped keys)
        # are spread along a horizontal line through the middle of the canvas.
        for i, led in enumerate(zone.leds):
            if leds[led.id] is None:
                leds[led.id] = {"x": (i + 0.5) / count, "y": 0.5, "name": led.name}
    return [
        {"x": round(l["x"], 4), "y": round(l["y"], 4), "name": l["name"]} if l else {"x": 0.5, "y": 0.5, "name": ""}
        for l in leds
    ]


class Rig:
    """Owns the OpenRGB connection and pushes the newest frame to the devices."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.client = None
        self.devices = []  # [(Device, led_count)]
        self.layout = []  # sent to pages: [{name, leds: [...]}]
        self.latest = None
        self.frame_ready = asyncio.Event()
        self.on_layout = None  # async callback

    def _connect(self):
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.client = OpenRGBClient(self.host, self.port, name="html-rgb-bridge")
        devices, layout = [], []
        for dev in self.client.devices:
            try:
                dev.set_mode("direct")
            except Exception:
                log.warning("Skipping %s: it has no Direct mode", dev.name)
                continue
            leds = led_layout(dev)
            if leds:
                devices.append((dev, len(leds)))
                layout.append({"name": dev.name, "leds": leds})
                log.info("Driving %s (%d LEDs)", dev.name, len(leds))
        self.devices, self.layout = devices, layout

    async def connect_forever(self):
        self.devices, self.layout = [], []
        while True:
            try:
                await asyncio.to_thread(self._connect)
                break
            except Exception as exc:
                log.warning("OpenRGB not reachable at %s:%d (%s). Retrying in 3 s.", self.host, self.port, exc)
                await asyncio.sleep(3)
        if self.on_layout:
            await self.on_layout()

    def _apply(self, frame):
        pos = 0
        for dev, count in self.devices:
            end = pos + count * 3
            chunk = frame[pos:end]
            if len(chunk) < count * 3:
                return  # built for an older layout; wait for the next frame
            dev.set_colors([RGBColor(chunk[i], chunk[i + 1], chunk[i + 2]) for i in range(0, len(chunk), 3)], fast=True)
            pos = end

    async def pusher(self):
        """Always send the newest frame; drop stale ones instead of queueing them."""
        while True:
            await self.frame_ready.wait()
            self.frame_ready.clear()
            frame, self.latest = self.latest, None
            if frame is None or not self.devices:
                continue
            try:
                await asyncio.to_thread(self._apply, frame)
            except Exception as exc:
                log.error("Lost the OpenRGB connection (%s). Reconnecting.", exc)
                await self.connect_forever()


class Hub:
    """Tracks connected pages, the saved settings, and who is rendering."""

    def __init__(self, rig):
        self.rig = rig
        self.renderers = set()
        self.controls = set()
        self.state = {"effect": None, "props": {}, "brightness": 100}
        try:
            self.state.update(json.loads(STATE_FILE.read_text()))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        self._save_handle = None
        self._last_mirror = 0.0
        rig.on_layout = self.send_layout

    def save_soon(self):
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = asyncio.get_running_loop().call_later(
            0.5, lambda: STATE_FILE.write_text(json.dumps(self.state, indent=2))
        )

    async def send(self, sockets, payload):
        for ws in list(sockets):
            try:
                if isinstance(payload, (bytes, bytearray)):
                    await ws.send_bytes(payload)
                else:
                    await ws.send_json(payload)
            except Exception:
                sockets.discard(ws)

    async def send_layout(self):
        await self.send(self.renderers | self.controls, {"type": "layout", "devices": self.rig.layout})

    async def send_counts(self):
        # Controls need to know whether something renders in the background;
        # renderers only bother sending preview images while a control is open.
        await self.send(self.controls, {"type": "renderers", "count": len(self.renderers)})
        await self.send(self.renderers, {"type": "watchers", "count": len(self.controls)})

    async def on_frame(self, ws, data):
        # Frames from a control page only count when no renderer is connected.
        if ws in self.controls and self.renderers:
            return
        self.rig.latest = data
        self.rig.frame_ready.set()
        now = time.monotonic()
        if now - self._last_mirror >= 1 / MIRROR_FPS:
            self._last_mirror = now
            await self.send(self.controls - {ws}, data)

    async def on_command(self, msg):
        kind = msg.get("type")
        if kind == "preview":  # a snapshot of the renderer's canvas for the control window
            await self.send(self.controls, msg)
            return
        if kind == "select":
            self.state["effect"] = msg["effect"]
            self.state["props"][msg["effect"]] = msg.get("props") or self.state["props"].get(msg["effect"], {})
            forward = {"type": "select", "effect": msg["effect"], "props": self.state["props"][msg["effect"]]}
        elif kind == "set":
            self.state["props"].setdefault(msg["effect"], {})[msg["prop"]] = msg["value"]
            forward = msg
        elif kind == "brightness":
            self.state["brightness"] = msg["value"]
            forward = msg
        elif kind == "rescan":
            asyncio.create_task(self.rig.connect_forever())
            return
        else:
            return
        self.save_soon()
        await self.send(self.renderers, forward)


async def run_renderer(url, stopping):
    """Keep a headless Chromium-family browser running the effect page until shutdown."""
    exe = next((path for name in BROWSERS if (path := shutil.which(name))), None)
    if not exe:
        log.error("--render needs Chromium, Chrome, Brave or Edge installed (e.g. sudo pacman -S chromium).")
        return
    args = [
        exe, "--headless=new", "--no-first-run", "--no-default-browser-check", "--mute-audio",
        "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows", f"--user-data-dir={PROFILE_DIR}", url,
    ]
    while not stopping.is_set():
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        log.info("Headless renderer started (%s)", pathlib.Path(exe).name)
        try:
            code = await proc.wait()
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        if stopping.is_set():
            return
        log.warning("Headless renderer exited (code %s). Restarting in 3 s.", code)
        try:
            await asyncio.wait_for(stopping.wait(), 3)  # returns early if we're shutting down
        except asyncio.TimeoutError:
            pass


def read_meta(path):
    text = path.read_text(errors="replace")[:20000]
    title = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    desc = re.search(r"<meta\s+description\s*=\s*\"(.*?)\"", text, re.I | re.S)
    return {
        "file": path.name,
        "title": title.group(1).strip() if title else path.stem,
        "description": desc.group(1).strip() if desc else "",
    }


def make_app(args):
    rig = Rig(args.openrgb_host, args.openrgb_port)
    hub = Hub(rig)
    effects_dir = args.effects.resolve()
    effects_dir.mkdir(parents=True, exist_ok=True)

    async def index(_):
        return web.FileResponse(ROOT / "static" / "index.html")

    async def list_effects(_):
        files = sorted(effects_dir.glob("*.html"), key=lambda p: p.name.lower())
        return web.json_response([read_meta(p) for p in files])

    async def ws_handler(request):
        role = "renderer" if request.query.get("role") == "renderer" else "control"
        group = hub.renderers if role == "renderer" else hub.controls
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        group.add(ws)
        await ws.send_json({"type": "layout", "devices": rig.layout})
        await hub.send_counts()
        await ws.send_json({"type": "state", **hub.state})
        if role == "renderer":
            log.info("Renderer connected")
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    await hub.on_frame(ws, msg.data)
                elif msg.type == WSMsgType.TEXT:
                    await hub.on_command(json.loads(msg.data))
        finally:
            group.discard(ws)
            if role == "renderer":
                log.info("Renderer disconnected")
            await hub.send_counts()
        return ws

    stopping = asyncio.Event()

    async def start_background(app):
        app["tasks"] = [asyncio.create_task(rig.connect_forever()), asyncio.create_task(rig.pusher())]
        app["renderer"] = None
        if args.render:
            url = f"http://127.0.0.1:{args.port}/?role=renderer"
            app["renderer"] = asyncio.create_task(run_renderer(url, stopping))
        log.info("Control page: http://127.0.0.1:%d", args.port)
        log.info("Effects folder: %s", effects_dir)

    async def shutdown(app):
        # Runs first on SIGTERM/Ctrl+C: stop the headless browser for good,
        # then close every page's connection so the server isn't left waiting.
        log.info("Shutting down")
        stopping.set()
        if app["renderer"]:
            app["renderer"].cancel()
            await asyncio.gather(app["renderer"], return_exceptions=True)
        for ws in list(hub.renderers | hub.controls):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"bridge shutting down")

    async def stop_background(app):
        for task in app["tasks"]:
            task.cancel()
        await asyncio.gather(*app["tasks"], return_exceptions=True)

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/effects", list_effects)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/effects/", effects_dir)
    app.on_startup.append(start_background)
    app.on_shutdown.append(shutdown)
    app.on_cleanup.append(stop_background)
    return app


def main():
    ap = argparse.ArgumentParser(description="Run HTML canvas lighting effects on OpenRGB devices.")
    ap.add_argument("--openrgb-host", default="127.0.0.1")
    ap.add_argument("--openrgb-port", type=int, default=6742)
    ap.add_argument("--port", type=int, default=6743, help="port for the control page (default 6743)")
    ap.add_argument("--effects", type=pathlib.Path, default=ROOT / "effects", help="folder of .html effects")
    ap.add_argument("--render", action="store_true", help="render effects in a headless browser (recommended)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    web.run_app(make_app(args), host="127.0.0.1", port=args.port, print=None, access_log=None, shutdown_timeout=3)


if __name__ == "__main__":
    main()