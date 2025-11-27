
# EmbyContentWatchdog

**EmbyContentWatchdog** is a Python service that monitors Emby server log files for media playback errors (like EBML header parsing failures) and automatically triggers a metadata refresh for the affected content.  
It is rule-driven, easy to extend, and runs as a user service on Linux (Ubuntu 24.04 and later).

---

## Features

- Watches Emby log folder for new `.log` and `.txt` files
- Detects failure patterns using configurable rules (`rules.json`)
- Automatically refreshes metadata for affected items via Emby API
- Blocks repeated refresh requests for the same item during playback requests
- Logs all actions and errors to a daily log file with retention
- Easy to extend: add new error patterns or actions by editing `rules.json`

---

## Setup Instructions

### 1. **Clone the repository**

```
git clone https://github.com/yourusername/EmbyContentWatchdog.git
cd EmbyContentWatchdog
```

### 2. Install Python dependencies
```
apt install python3-watchdog
```

### 3. Edit configuration in the script
```
LOG_FOLDER = "/path/to/emby/logs"      # Emby log directory (e.g., /config/logs)
EMBY_SERVER = "http://127.0.0.1:8096"  # Emby server URL
EMBY_API_KEY = "YOUR_API_KEY"          # Your Emby API key
WATCH_SECONDS = 30                     # How long to tail each new file
RETENTION_DAYS = 7                     # How many days to keep logs
FILE_EXTS = (".log", ".txt")           # File extensions to watch
```

### 4. Configure detection rules
```

{
  "global": {
    "stop_on_first_action": true,
    "rule_reload_seconds": 60
  },
  "rules": [
    {
      "name": "EBMLHeaderParsingFailed",
      "pattern": "EBML header parsing failed",
      "action": "refresh_metadata",
      "rate_limit_seconds": 300,
      "level": "WARN"
    }
  ]
}
```

### 5. Set up as a user service (recommended)
#### a. Create a user systemd unit
```
mkdir -p ~/.config/systemd/user
nano ~/.config/systemd/user/emby-content-watchdog.service
```
##### paste:
```
[Unit]
Description=EmbyContentWatchdog (user unit)
After=network-online.target

[Service]
WorkingDirectory=/path/to/EmbyContentWatchdog
ExecStart=/usr/bin/python3 -u /path/to/EmbyContentWatchdog/emby_content_watchdog.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

#### b. Enable lingering (optional, for auto-start at boot)
```
sudo loginctl enable-linger $USER
```

#### c. Enable and start the service
```
systemctl --user daemon-reload
systemctl --user enable --now emby-content-watchdog.service
```

