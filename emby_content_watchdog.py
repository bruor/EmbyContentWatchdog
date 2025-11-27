import os
import re
import time
import json
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# =================== USER CONFIG ===================
LOG_FOLDER = "/opt/docker/emby-server/programdata/logs"      # e.g., /config/logs
EMBY_SERVER = "http://127.0.0.1:8096"  # Emby base URL
EMBY_API_KEY = "api_key"

WATCH_SECONDS = 5          # How long to tail each newly discovered file
RETENTION_DAYS = 7          # Service logs retention under ./logs/
FILE_EXTS = (".log", ".txt")

# Regex to parse item info from lines (works with plain or escaped JSON lines)
ITEMID_RE = re.compile(r'"ItemId"\s*:\s*"?(?P<id>\d+)"?')
NAME_RE   = re.compile(r'"Name"\s*:\s*"(?P<name>[^"]+)"')
# ====================================================

#Exclude some files from being scanned when created
EXCLUDE_PATTERNS = [
    "graph.txt",   # Exclude files ending with _graph.txt
    #"test_",        # Exclude files containing 'test_'
    # Add more patterns as needed
]

# ===== Internal paths & state =====
SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_LOG_DIR = SCRIPT_DIR / "logs"
RULES_PATH = SCRIPT_DIR / "rules.json"    # hot-reloaded config
SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)

Rule = Dict[str, Any]
CompiledRule = Dict[str, Any]

# Rate-limit cache keyed by (item_id, rule_name)
recent_refresh: Dict[Tuple[str, str], float] = {}


def is_excluded(filename):
    return any(pat in filename for pat in EXCLUDE_PATTERNS)

def base(file_path: str) -> str:
    #Return only the filename portion of a path.
    return os.path.basename(file_path)

def service_log_path_for_today() -> Path:
    return SERVICE_LOG_DIR / f"emby-ebml-tail-{datetime.now().strftime('%Y%m%d')}.log"

def write_log(event: str, details: Optional[dict] = None):
    now = datetime.now().isoformat(timespec='seconds')
    level = (details or {}).pop("_level", "INFO")
    kv = ""
    if details:
        kv = " | " + " ".join(f"{k}={repr(v)}" for k, v in details.items())
    line = f"{now} | {level} | {event}{kv}\n"
    try:
        with service_log_path_for_today().open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        print("[LOG-ERR]", line)

def cleanup_service_logs():
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    for p in SERVICE_LOG_DIR.glob("emby-ebml-tail-*.log"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                p.unlink(missing_ok=True)
                write_log("LogRetentionDelete", {"path": str(p)})
        except Exception as e:
            write_log("LogRetentionError", {"path": str(p), "error": str(e), "_level": "ERROR"})

def load_rules() -> Tuple[List[CompiledRule], Dict[str, Any]]:
    try:
        with RULES_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        write_log("RulesNotFound", {"path": str(RULES_PATH), "_level": "ERROR"})
        return [], {"stop_on_first_action": True, "rule_reload_seconds": 60}
    except Exception as e:
        write_log("RulesLoadError", {"error": str(e), "trace": traceback.format_exc(), "_level": "ERROR"})
        return [], {"stop_on_first_action": True, "rule_reload_seconds": 60}

    rules = cfg.get("rules", [])
    global_cfg = cfg.get("global", {"stop_on_first_action": True, "rule_reload_seconds": 60})

    compiled: List[CompiledRule] = []
    for r in rules:
        try:
            compiled.append({
                "name": r["name"],
                "pattern": re.compile(r["pattern"]),
                "action": r.get("action", "refresh_metadata"),
                "rate_limit_seconds": int(r.get("rate_limit_seconds", 300)),
                "level": r.get("level", "WARN"),
            })
        except Exception as e:
            write_log("RuleCompileError", {"rule": r, "error": str(e), "_level": "ERROR"})
    write_log("RulesLoaded", {"count": len(compiled)})
    return compiled, global_cfg

def call_emby_refresh(item_id: str) -> int:
    url = (
        f"{EMBY_SERVER}/Items/{item_id}/Refresh"
        f"?api_key={EMBY_API_KEY}&MetadataRefreshMode=FullRefresh&ReplaceAllMetadata=true&ReplaceAllImages=false"
    )
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        write_log("ApiTransportError", {"item_id": item_id, "error": str(e), "_level": "ERROR"})
        return 0

def perform_action(action: str, item_id: str, name: Optional[str]) -> int:
    if action == "refresh_metadata":
        return call_emby_refresh(item_id)
    write_log("UnknownAction", {"action": action, "_level": "ERROR"})
    return 0

def cleanup_cache(now: float, rules: List[CompiledRule]):
    expired = []
    for key, ts in recent_refresh.items():
        item_id, rule_name = key
        ttl = next((r["rate_limit_seconds"] for r in rules if r["name"] == rule_name), 300)
        if (now - ts) >= ttl:
            expired.append(key)
    for key in expired:
        recent_refresh.pop(key, None)

def can_fire(item_id: str, rule: CompiledRule, now: float) -> bool:
    last_ts = recent_refresh.get((item_id, rule["name"]))
    return last_ts is None or (now - last_ts) >= rule["rate_limit_seconds"]

def mark_fired(item_id: str, rule: CompiledRule, now: float):
    recent_refresh[(item_id, rule["name"])] = now

def tail_file(filepath: str, timeout: int, compiled_rules: List[CompiledRule], global_cfg: Dict[str, Any]):
    write_log("WatchStart", {"file": base(filepath), "timeout_s": timeout})
    start = time.time()
    item_id: Optional[str] = None
    name: Optional[str] = None
    stop_on_first_action = bool(global_cfg.get("stop_on_first_action", True))

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, os.SEEK_SET)
            while True:
                now = time.time()
                cleanup_cache(now, compiled_rules)
                cleanup_service_logs()

                if (now - start) >= timeout:
                    write_log("WatchTimeout", {"file": filepath})
                    return

                line = f.readline()
                if not line:
                    time.sleep(0.25)
                    continue

                m_id = ITEMID_RE.search(line)
                if m_id:
                    item_id = m_id.group("id")

                m_name = NAME_RE.search(line)
                if m_name:
                    name = m_name.group("name")

                for rule in compiled_rules:
                    if rule["pattern"].search(line):
                        lvl = rule["level"]
                        write_log("RuleMatched", {
                            "file": filepath, "rule": rule["name"], "level": lvl,
                            "item_id": item_id, "name": name,
                            "line_snippet": line.strip()[:200]
                        })
                        if not item_id:
                            write_log("ActionSkippedNoItemId", {
                                "rule": rule["name"], "file": filepath, "_level": "WARN"
                            })
                            continue

                        if can_fire(item_id, rule, now):
                            status = perform_action(rule["action"], item_id, name)
                            mark_fired(item_id, rule, now)
                            write_log("ActionCalled", {
                                "rule": rule["name"], "action": rule["action"],
                                "file": base(filepath), "item_id": item_id, "name": name,
                                "status_code": status
                            })
                        else:
                            ttl = rule["rate_limit_seconds"]
                            wait_left = int(ttl - (now - recent_refresh[(item_id, rule["name"])]))
                            write_log("ActionSkippedTTL", {
                                "rule": rule["name"], "file": filepath,
                                "item_id": item_id, "name": name, "wait_left_s": wait_left
                            })

                        if stop_on_first_action:
                            return
    except FileNotFoundError:
        write_log("WatchFileNotFound", {"file": base(filepath), "_level": "ERROR"})
    except Exception as e:
        write_log("WatchUnhandledError", {
            "file": base(filepath), "error": str(e), "trace": traceback.format_exc(), "_level": "ERROR"
        })

class NewLogFileHandler(FileSystemEventHandler):
    def __init__(self, compiled_rules, global_cfg):
        super().__init__()
        self.compiled_rules = compiled_rules
        self.global_cfg = global_cfg
    def on_created(self, event):
        if event.is_directory:
            return
        filename = base(event.src_path)
        if is_excluded(filename):
            #write_log("FileExcluded", {"file": base(event.src_path)})
            return
        if event.src_path.lower().endswith(FILE_EXTS):
            write_log("NewFileDetected", {"file": base(event.src_path)})
            tail_file(event.src_path, WATCH_SECONDS, self.compiled_rules, self.global_cfg)

def main():
    compiled_rules, global_cfg = load_rules()
    event_handler = NewLogFileHandler(compiled_rules, global_cfg)
    observer = Observer()
    observer.schedule(event_handler, LOG_FOLDER, recursive=False)
    observer.start()
    write_log("ServiceStart", {"log_folder": LOG_FOLDER})

    try:
        while True:
            cleanup_service_logs()
            # Hot-reload rules every minute
            time.sleep(60)
            compiled_rules, global_cfg = load_rules()
            event_handler.compiled_rules = compiled_rules
            event_handler.global_cfg = global_cfg
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
