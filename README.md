# 📡 SRT Decoder Dashboard — Raspberry Pi 4

A full-stack SRT stream decoder with a live web dashboard. Supports **Listener** and **Caller** modes, optional **AES passphrase encryption**, live HLS preview, and real-time stats.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 Raspberry Pi 4                       │
│                                                      │
│  SRT Input (UDP) ──► ffmpeg ──► HLS segments        │
│       ↑                              ↓               │
│  Network                    Flask web server         │
│                              ├── /           (UI)    │
│                              ├── /hls/       (HLS)   │
│                              ├── /api/       (REST)  │
│                              └── socket.io   (live)  │
└─────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Install on Raspberry Pi 4

```bash
git clone <repo> && cd srt-decoder
sudo bash scripts/install.sh
sudo systemctl start srt-decoder
```

### 2. Manual (development)

```bash
pip install -r requirements.txt
python app.py
```

Open `http://<pi-ip>:5000` in your browser.

---

## Features

| Feature | Details |
|---------|---------|
| **Listener mode** | Binds to a port, waits for SRT callers |
| **Caller mode** | Actively connects to a remote SRT source |
| **Passphrase** | AES-128 encryption via SRT protocol |
| **Latency control** | 20ms – 8000ms configurable |
| **Live preview** | HLS stream via hls.js in the browser |
| **Real-time stats** | Bitrate, RTT, packets lost, bytes received |
| **Bitrate chart** | 60-second rolling history |
| **System log** | Live WebSocket log feed |
| **Demo mode** | Works without ffmpeg (mock stats) |

---

## SRT URL Format

The dashboard generates the SRT URL automatically:

```
# Listener
srt://0.0.0.0:1935?mode=listener&latency=200

# Caller (with passphrase)
srt://192.168.1.50:1935?mode=caller&latency=200&passphrase=mysecret&pbkeylen=16
```

---

## Sending a Stream (OBS / FFmpeg)

### OBS Studio
1. Settings → Stream
2. Service: **Custom**
3. Server: `srt://192.168.x.x:1935?mode=caller`
4. If passphrase set: `srt://192.168.x.x:1935?mode=caller&passphrase=yourpassphrase`

### FFmpeg (test source)
```bash
# Send test pattern to the Pi (listener mode)
ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=440 \
  -c:v libx264 -preset ultrafast -b:v 3000k \
  -c:a aac -b:a 128k \
  -f mpegts "srt://192.168.x.x:1935?mode=caller&latency=200"

# With passphrase
ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=30 \
  -c:v libx264 -preset ultrafast -b:v 3000k \
  -f mpegts "srt://192.168.x.x:1935?mode=caller&latency=200&passphrase=mysecret&pbkeylen=16"
```

### srt-live-transmit (relay)
```bash
srt-live-transmit \
  "srt://:1935?mode=listener&passphrase=mysecret" \
  "srt://192.168.x.x:1935?mode=caller"
```

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web dashboard |
| `/api/status` | GET | Full decoder state + stats |
| `/api/config` | POST | Update settings (when stopped) |
| `/api/start` | POST | Start decoder |
| `/api/stop` | POST | Stop decoder |
| `/api/logs` | GET | Recent log entries |
| `/hls/stream.m3u8` | GET | HLS playlist |
| `/hls/seg*.ts` | GET | HLS segments |

### WebSocket Events
- `status_change` → `{ status: 'idle' | 'running' | 'error' }`
- `stats` → `{ bitrate, rtt, packets_lost, bytes_received, uptime }`
- `log` → `{ time, level, msg }`

---

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 5000 | TCP | Web dashboard |
| 1935 | UDP | SRT stream (default, configurable) |

---

## Dependencies

- **Python 3.9+**
- **ffmpeg** with SRT support (`ffmpeg -protocols | grep srt`)
- **Flask + Flask-SocketIO + Eventlet**

Install ffmpeg with SRT on Raspberry Pi:
```bash
sudo apt-get install ffmpeg libsrt-dev srt-tools
```

---

## Troubleshooting

**No video in browser?**
- Confirm ffmpeg is running: `ps aux | grep ffmpeg`
- Check HLS segments exist: `ls /tmp/srt_hls/`
- Allow up to 6 seconds for HLS to initialize

**SRT connection refused?**
- Check port is open: `sudo ufw allow 1935/udp`
- Verify no other process owns the port: `ss -ulnp | grep 1935`

**Passphrase mismatch?**
- Both sender and receiver must use identical passphrase
- Minimum 10 characters, maximum 79 characters

---

Designed By M Jafari for Live streaming Community 
