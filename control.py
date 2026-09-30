#!/usr/bin/env python3
"""Desktop window for the RGB bridge control panel (pywebview + WebKitGTK)."""
import argparse
import os
import urllib.request

# WebKitGTK's DMA-BUF renderer crashes on some Wayland setups (notably NVIDIA)
# with "Error 71 (Protocol error) dispatching to Wayland display".
# Turning it off costs nothing noticeable for a control panel.
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

import webview  # noqa: E402  (must come after the environment tweak)

NOT_RUNNING = """<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;
background:#22262b;color:#ebe7df;font:16px/1.5 system-ui,sans-serif">
<div style="max-width:32rem;padding:2rem">
<h1 style="font-size:1.4rem;margin:0 0 .5rem">The bridge isn't running</h1>
<p style="color:#a0a6ad;margin:0 0 1rem">Start it, and this window will open the control panel on its own:</p>
<pre style="background:#2b3036;padding:.75rem 1rem;border-radius:8px">systemctl --user start rgb-bridge</pre>
</div></body></html>"""


def bridge_up(url):
    try:
        urllib.request.urlopen(url + "api/effects", timeout=1)
        return True
    except OSError:
        return False


def wait_for_bridge(window, url):
    """Show a help screen until the bridge answers, then load the panel."""
    import time
    while not bridge_up(url):
        time.sleep(2)
    window.load_url(url)


def main():
    ap = argparse.ArgumentParser(description="Open the RGB bridge control panel.")
    ap.add_argument("--port", type=int, default=6743)
    ap.add_argument("--debug", action="store_true", help="enable the web inspector (right-click > Inspect Element)")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"

    if bridge_up(url):
        window = webview.create_window("RGB bridge", url, width=1280, height=820,
                                       min_size=(720, 520), background_color="#22262b")
        webview.start(gui="gtk", debug=args.debug)
    else:
        window = webview.create_window("RGB bridge", html=NOT_RUNNING, width=1280, height=820,
                                       min_size=(720, 520), background_color="#22262b")
        webview.start(wait_for_bridge, (window, url), gui="gtk", debug=args.debug)


if __name__ == "__main__":
    main()