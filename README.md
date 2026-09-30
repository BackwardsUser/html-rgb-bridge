# html-rgb-bridge

Run SignalRGB-style HTML lighting effects on Linux, through OpenRGB.

> **Heads up: this is a vibe-coded project.**
> I switched from Windows to Linux, lost SignalRGB, and just wanted my RGB back.
> I didn't much care how I got there, so this was thrown together in an afternoon
> with Claude (an AI assistant) doing most of the typing. That includes this
> README: Claude wrote it, and I asked for the changes I wanted. It works on my
> machine. It hasn't been carefully reviewed or tested anywhere else, so expect
> rough edges, and read the code before trusting it with anything important.

## Maintenance

- **I won't be updating this any further.** I will keep an eye on pull requests,
  though, and any are welcome.
- **The documentation is provided as is.** I've only run this on my own Arch
  setup and won't be testing it on other distros or operating systems. If you
  get it working somewhere else, a PR updating these docs is very welcome.

## What it does

SignalRGB effects are just web pages that draw on a canvas. OpenRGB can control
basically every RGB device but can't run those pages. This sits in between:

1. A headless Chromium renders the current effect in the background.
2. The canvas is sampled under each LED about 30 times a second.
3. Those colours are sent to your devices through OpenRGB's SDK server.

There's a small desktop window for picking effects and changing their settings,
with a live preview of what your keyboard is showing. Closing the window doesn't
stop the lighting.

## Requirements

- Linux (built and used on Arch with KDE on Wayland)
- [OpenRGB](https://openrgb.org), with your devices working in it
- Python 3.10 or newer
- Chromium, Chrome, Brave or Edge (used headless to render effects)
- WebKitGTK and PyGObject (for the desktop window)

On Arch:

```bash
sudo pacman -S openrgb chromium python-gobject webkit2gtk-4.1
```

## Install

```bash
git clone https://github.com/YOUR-NAME/html-rgb-bridge.git
cd html-rgb-bridge
python -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

`--system-site-packages` matters: the desktop window needs the GTK bindings
installed by your package manager.

## Run it once, by hand

```bash
openrgb --server &
.venv/bin/python bridge.py --render
.venv/bin/python control.py        # in another terminal
```

You can also open http://127.0.0.1:6743 in a browser instead of using `control.py`.

## Run it at login

The files in `linux/` set this up as systemd user services plus an app launcher
entry. **Edit the paths in them first**: they point at wherever the project lived
on my machine.

```bash
mkdir -p ~/.config/systemd/user ~/.local/share/applications
cp linux/*.service ~/.config/systemd/user/
cp linux/rgb-bridge.desktop ~/.local/share/applications/
systemctl --user daemon-reload
systemctl --user enable --now openrgb-server rgb-bridge
```

Then open **RGB Bridge** from your app launcher.

If you already run the OpenRGB app with its SDK server turned on, skip
`openrgb-server`. Only one OpenRGB instance can control the hardware at a time.
To look at the OpenRGB window while the service is running, connect it to the
service with `openrgb --client 127.0.0.1:6742` rather than starting a second copy.

Logs: `journalctl --user -u rgb-bridge -f`

## Effects

Effects live in `effects/` (or point the bridge elsewhere with `--effects some/folder`).
Three are included:

- **Tidepool**: slow layered waves, with a few palettes.
- **Solid colour**: one colour, optionally breathing.
- **Fireflies**: glowing orbs with trails and ripples, built with
  [ZIM](https://zimjs.com). It loads ZIM from ZIM's CDN, so it needs internet.

### Writing your own

The format follows SignalRGB's Lightscripts, so many existing effects should work
as-is:

```html
<head>
  <title>My effect</title>
  <meta description="Shown under the name in the effect list." />
  <meta property="speed" label="Speed" type="number" min="1" max="20" default="6" />
</head>
<body style="margin:0">
  <canvas id="exCanvas" width="320" height="200"></canvas>
  <script>
    // `speed` is a global, set from the settings panel.
    // Define onspeedChanged() if you want to react to changes immediately.
  </script>
</body>
```

Supported setting types: `number`, `hue`, `boolean`, `color`,
`combobox` (with `values="A,B,C"`) and `textfield`.

## Known limitations

- **No audio yet.** Audio-reactive effects load, but they receive silence.
- **SignalRGB extras aren't there.** Effects that depend on other SignalRGB-only
  features (game integrations, screen capture, etc.) won't work.
- **Every device OpenRGB exposes gets driven.** Keyboards map onto the canvas by
  their key grid. Everything else (RAM, GPUs, coolers, strips) is spread along a
  line across the middle of the canvas. There's no way to exclude or position
  devices yet.
- **After sleep,** a device may drop off and come back. If the lighting stops, use
  **Rescan devices** in the window.

## Troubleshooting

**The window opens and immediately closes, printing `Error 71 (Protocol error)
dispatching to Wayland display`.**
A WebKitGTK bug on some Wayland setups, mostly NVIDIA. `control.py` already sets
`WEBKIT_DISABLE_DMABUF_RENDERER=1` to avoid it. If it still happens, try launching
with `GDK_BACKEND=x11`.

**The window crashes with `No module named 'gi'`.**
The virtual environment was created without `--system-site-packages`. Delete
`.venv` and recreate it as shown above.

**OpenRGB logs `recv_select failed receiving magic, closing listener`.**
Harmless. It's OpenRGB noting that a client disconnected, usually the bridge
retrying while OpenRGB was still detecting devices.

**The lighting freezes when I switch browser tabs.**
You're running without `--render`, so the effect only runs while its page is
visible. Start the bridge with `--render` (the systemd service already does).

**My computer wakes itself from sleep.**
Not this project's fault, but it's how this project started. If you have a
keyboard running custom QMK firmware, that's a likely suspect: see
[qmk/qmk_firmware#25519](https://github.com/qmk/qmk_firmware/issues/25519).
Disabling USB wake for the keyboard fixes it, at the cost of not being able to
wake the PC by typing.

## Project layout

```
bridge.py            OpenRGB connection, web server, headless renderer
control.py           desktop window (pywebview)
static/index.html    the control panel and the renderer page
effects/             effect files
linux/               systemd user services and the app launcher entry
```

## Not affiliated

This isn't affiliated with or endorsed by SignalRGB, WhirlwindFX or OpenRGB. It
just borrows SignalRGB's effect format so existing effects are easy to reuse.

The Fireflies effect is built with ZIM. I'm an admin on the ZIM Discord, but this
is a personal project, not an official ZIM one.