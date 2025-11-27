
# EmbyContentWatchdog

**EmbyContentWatchdog** is a Python service that monitors Emby server log files for media playback errors (like EBML header parsing failures) and automatically triggers a metadata refresh for the affected content.  
It is rule-driven, easy to extend, and runs as a user service on Linux (Ubuntu 24.04 and later).

---

## Features

- Watches Emby log folder for new `.log` and `.txt` files
- Detects failure patterns using configurable rules (`rules.json`)
- Automatically refreshes metadata for affected items via Emby API
- Rate-limits repeated refreshes for the same item
- Logs all actions and errors to a daily log file with retention
- Easy to extend: add new error patterns or actions by editing `rules.json`

---

## Setup Instructions

### 1. **Clone the repository**

```
git clone https://github.com/yourusername/EmbyContentWatchdog.git
cd EmbyContentWatchdog
```
### 2 Install Python dependencies
