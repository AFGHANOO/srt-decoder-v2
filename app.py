#!/usr/bin/env python3
"""
SRT Decoder Dashboard - Raspberry Pi 4
Supports listener/caller modes, passphrase encryption, HLS web preview,
and HDMI output via ffplay/mpv.
"""

import os
import subprocess
import threading
import time
import re
import shutil
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'srt-decoder-rpi4-secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ── State ─────────────────────────────────────────────────────────────────────
state = {
    "mode": "listener",
    "host": "0.0.0.0",
    "port": 1935,
    "passphrase": "",
    "latency": 200,
    "status": "idle",           # idle | running | error
    "started_at": None,
    "bytes_received": 0,
    "bitrate": 0,
    "packets_lost": 0,
    "rtt": 0,
    "hls_active": False,
    "hdmi_output": False,       # whether to send to HDMI display
    "hdmi_player": "ffplay",    # ffplay | mpv | vlc
    "hdmi_status": "off",       # off | active | error
    "log": [],
}

hls_proc   = None
hdmi_proc  = None
log_lock   = threading.Lock()

HLS_DIR      = "/tmp/srt_hls"
HLS_PLAYLIST = f"{HLS_DIR}/stream.m3u8"

# ── Helpers ───────────────────────────────────────────────────────────────────

def add_log(level, message):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": message}
    with log_lock:
        state["log"].append(entry)
        if len(state["log"]) > 300:
            state["log"] = state["log"][-300:]
    socketio.emit("log", entry)


def build_srt_url():
    host = state["host"] if state["mode"] == "caller" else "0.0.0.0"
    params = [f"mode={state['mode']}", f"latency={state['latency']}"]
    if state["passphrase"]:
        params.append(f"passphrase={state['passphrase']}")
        params.append("pbkeylen=16")
    return f"srt://{host}:{state['port']}?{'&'.join(params)}"


def check_bin(name):
    return shutil.which(name) is not None


def parse_ffmpeg_stats(line):
    m = re.search(r"bitrate=\s*([\d.]+)kbits/s", line)
    if m:
        state["bitrate"] = float(m.group(1))
    return m is not None


# ── HLS pipeline (SRT → ffmpeg → HLS segments → web player) ──────────────────

def start_hls_pipeline():
    global hls_proc
    os.makedirs(HLS_DIR, exist_ok=True)
    srt_url = build_srt_url()
    cmd = [
        "ffmpeg", "-y",
        "-i", srt_url,
        "-c:v", "copy",
        "-c:a", "aac",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_filename", f"{HLS_DIR}/seg%05d.ts",
        HLS_PLAYLIST
    ]
    add_log("info", f"HLS pipeline → {srt_url}")
    try:
        hls_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        state["hls_active"] = True

        def _read():
            for line in hls_proc.stderr:
                line = line.strip()
                if not line:
                    continue
                parse_ffmpeg_stats(line)
                if "error" in line.lower() or "warning" in line.lower():
                    add_log("warn", line[:130])
                socketio.emit("stats", {
                    "bitrate": state["bitrate"],
                    "bytes_received": state["bytes_received"],
                })
            state["hls_active"] = False
            add_log("info", "HLS pipeline stopped")

        threading.Thread(target=_read, daemon=True).start()
        return True
    except Exception as e:
        add_log("error", f"HLS ffmpeg failed: {e}")
        return False


def stop_hls_pipeline():
    global hls_proc
    if hls_proc and hls_proc.poll() is None:
        hls_proc.terminate()
        try: hls_proc.wait(timeout=5)
        except subprocess.TimeoutExpired: hls_proc.kill()
        hls_proc = None
    state["hls_active"] = False


# ── HDMI output player ────────────────────────────────────────────────────────

def start_hdmi_player():
    """
    Launch a player process that decodes the SRT stream and outputs
    audio+video to the Pi's HDMI port.

    Three player options:
      ffplay  — part of ffmpeg, always available if ffmpeg is installed
      mpv     — lower latency, GPU-accelerated on RPi4
      vlc     — familiar UI, good subtitle support
    """
    global hdmi_proc
    srt_url = build_srt_url()
    player  = state["hdmi_player"]

    if player == "mpv" and check_bin("mpv"):
        cmd = [
            "mpv",
            "--no-cache",
            "--untimed",
            "--no-border",
            "--fullscreen",
            "--audio-device=auto",
            f"--network-timeout=10",
            srt_url,
        ]
    elif player == "vlc" and check_bin("vlc"):
        cmd = [
            "cvlc",          # command-line VLC (no GUI chrome)
            "--fullscreen",
            "--no-video-title-show",
            f"--network-caching={state['latency']}",
            srt_url,
        ]
    else:
        # ffplay fallback — always available alongside ffmpeg
        player = "ffplay"
        cmd = [
            "ffplay",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-framedrop",
            "-infbuf",
            "-vf", "setpts=0",
            "-fs",           # fullscreen
            "-autoexit",
            srt_url,
        ]

    # ffplay/mpv need a display — set DISPLAY if not set
    env = os.environ.copy()
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"

    add_log("info", f"HDMI output via {player} → {srt_url}")
    try:
        hdmi_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        state["hdmi_status"] = "active"
        socketio.emit("hdmi_status", {"hdmi_status": "active", "player": player})

        def _watch():
            for line in hdmi_proc.stderr:
                line = line.strip()
                if line and ("error" in line.lower() or "failed" in line.lower()):
                    add_log("warn", f"[{player}] {line[:120]}")
            rc = hdmi_proc.wait()
            state["hdmi_status"] = "off"
            socketio.emit("hdmi_status", {"hdmi_status": "off"})
            if rc and rc != -15:   # -15 = SIGTERM (normal stop)
                add_log("error", f"HDMI player exited with code {rc}")

        threading.Thread(target=_watch, daemon=True).start()
        return True
    except FileNotFoundError:
        add_log("error", f"Player '{player}' not found — install it or choose another")
        state["hdmi_status"] = "error"
        socketio.emit("hdmi_status", {"hdmi_status": "error"})
        return False
    except Exception as e:
        add_log("error", f"HDMI player launch failed: {e}")
        state["hdmi_status"] = "error"
        return False


def stop_hdmi_player():
    global hdmi_proc
    if hdmi_proc and hdmi_proc.poll() is None:
        hdmi_proc.terminate()
        try: hdmi_proc.wait(timeout=5)
        except subprocess.TimeoutExpired: hdmi_proc.kill()
        hdmi_proc = None
    state["hdmi_status"] = "off"
    socketio.emit("hdmi_status", {"hdmi_status": "off"})
    add_log("info", "HDMI player stopped")


def mock_stats_generator():
    import random
    while state["status"] == "running":
        state["bitrate"]        = round(random.uniform(1800, 5200), 1)
        state["rtt"]            = round(random.uniform(10, 60), 1)
        state["packets_lost"]   = random.randint(0, 3)
        state["bytes_received"] += random.randint(50000, 200000)
        socketio.emit("stats", {
            "bitrate":        state["bitrate"],
            "rtt":            state["rtt"],
            "packets_lost":   state["packets_lost"],
            "bytes_received": state["bytes_received"],
            "uptime": int(time.time() - state["started_at"]) if state["started_at"] else 0,
        })
        time.sleep(1)


# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    uptime = int(time.time() - state["started_at"]) if state["started_at"] else 0
    players = {
        "ffplay": check_bin("ffplay"),
        "mpv":    check_bin("mpv"),
        "vlc":    check_bin("cvlc") or check_bin("vlc"),
    }
    return jsonify({
        **state,
        "uptime":          uptime,
        "ffmpeg_available": check_bin("ffmpeg"),
        "available_players": players,
        "srt_url":         build_srt_url() if state["status"] == "running" else "",
    })


@app.route("/api/config", methods=["POST"])
def api_config():
    if state["status"] == "running":
        return jsonify({"error": "Stop stream before changing config"}), 400
    data = request.json
    for key in ["mode", "host", "port", "passphrase", "latency", "hdmi_output", "hdmi_player"]:
        if key in data:
            state[key] = data[key]
    add_log("info", f"Config — mode={state['mode']} port={state['port']} "
                    f"latency={state['latency']}ms hdmi={state['hdmi_output']} player={state['hdmi_player']}")
    return jsonify({"ok": True})


@app.route("/api/start", methods=["POST"])
def api_start():
    if state["status"] == "running":
        return jsonify({"error": "Already running"}), 400

    state.update(status="running", started_at=time.time(),
                 bytes_received=0, bitrate=0, packets_lost=0, rtt=0)

    srt_url = build_srt_url()
    add_log("info", f"Starting — {state['mode'].upper()} mode  port={state['port']}")
    add_log("info", f"SRT URL: {srt_url}")
    if state["passphrase"]:
        add_log("info", "AES-128 passphrase encryption enabled")

    if check_bin("ffmpeg"):
        if not start_hls_pipeline():
            state["status"] = "error"
            return jsonify({"error": "ffmpeg HLS pipeline failed"}), 500
    else:
        add_log("warn", "ffmpeg not found — DEMO mode (mock stats, no real stream)")
        threading.Thread(target=mock_stats_generator, daemon=True).start()

    if state["hdmi_output"]:
        # Small delay so ffmpeg can negotiate the SRT connection first
        def _deferred_hdmi():
            time.sleep(2)
            start_hdmi_player()
        threading.Thread(target=_deferred_hdmi, daemon=True).start()

    socketio.emit("status_change", {"status": "running"})
    return jsonify({"ok": True, "srt_url": srt_url})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if state["status"] != "running":
        return jsonify({"error": "Not running"}), 400

    stop_hls_pipeline()
    stop_hdmi_player()
    state.update(status="idle", started_at=None)
    add_log("info", "Decoder stopped")
    socketio.emit("status_change", {"status": "idle"})
    return jsonify({"ok": True})


@app.route("/api/hdmi/start", methods=["POST"])
def api_hdmi_start():
    """Start/restart HDMI output independently while stream is running."""
    if state["status"] != "running":
        return jsonify({"error": "Decoder not running"}), 400
    stop_hdmi_player()
    ok = start_hdmi_player()
    return jsonify({"ok": ok, "hdmi_status": state["hdmi_status"]})


@app.route("/api/hdmi/stop", methods=["POST"])
def api_hdmi_stop():
    stop_hdmi_player()
    return jsonify({"ok": True})


@app.route("/api/logs")
def api_logs():
    return jsonify(state["log"])


@app.route("/hls/<path:filename>")
def hls_serve(filename):
    filepath = os.path.join(HLS_DIR, filename)
    if not os.path.exists(filepath):
        return "Not found", 404
    mime = ("application/vnd.apple.mpegurl" if filename.endswith(".m3u8")
            else "video/mp2t" if filename.endswith(".ts")
            else "application/octet-stream")

    def _gen():
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return Response(stream_with_context(_gen()), mimetype=mime)


# ── Socket.IO ─────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    uptime = int(time.time() - state["started_at"]) if state["started_at"] else 0
    emit("status_change", {"status": state["status"]})
    emit("hdmi_status",   {"hdmi_status": state["hdmi_status"]})
    emit("stats", {
        "bitrate": state["bitrate"], "rtt": state["rtt"],
        "packets_lost": state["packets_lost"],
        "bytes_received": state["bytes_received"],
        "uptime": uptime,
    })


if __name__ == "__main__":
    add_log("info", "SRT Decoder Dashboard v2 — port 5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
