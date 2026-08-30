import os
import re
import shutil
import zipfile
import subprocess
import json
import time
import platform
import requests as req
from pathlib import Path
from uuid import uuid4
from threading import Thread
from urllib.parse import quote_plus


DEFAULT_MOVIES_DIR = str(Path.home() / "Downloads" / "movies")
RPC_URL = "http://127.0.0.1:6800/jsonrpc"
STALL_TIMEOUT = 90
MAX_STALL_RESTARTS = 4
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}

# Windows has a default 260-char MAX_PATH limit (LongPathsEnabled off). Release
# names can be very long, and multi-file torrents add nested subfolders/filenames,
# so the auto-created download folder must be SHORT. We strip redundant quality
# tags and cap the length to keep total paths safely under the limit.
FOLDER_MAX_LEN = 60
_FOLDER_TAG_RE = re.compile(
    r'\b(IMAX|2160p|4k|1080p|1080|720p|480p|BluRay|Blu-Ray|BDRip|BRRip|WEB-DL|WEBRip|WEB'
    r'|HDRip|DVDRip|HDTV|x265|x264|HEVC|AVC|10bit|8bit|AAC|DDP|DTS|AC3|5\.1|7\.1|2\.0'
    r'|REMUX|PROPER|EXTENDED|UNRATED|REPACK|HDR|HLG|AMZN|HMAX|NF|DSNP|Atmos)\b',
    re.IGNORECASE,
)


def _short_folder_name(title: str) -> str:
    """Collapse a long release name into a short, safe folder name."""
    name = _FOLDER_TAG_RE.sub(" ", title)
    name = re.sub(r'[.\-_\[\]()]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip(' .-_')
    if not name or len(name) < 5:
        name = re.sub(r'[\\/:*?"<>|]+', '', title).strip()
    if len(name) > FOLDER_MAX_LEN:
        name = name[:FOLDER_MAX_LEN].rstrip(' .-_')
    return name or "download"


# Clean, "proper title" folder naming. Raw torrent titles are cluttered with
# codecs (x264/HEVC), resolutions (1080p), websites (www.UIindex.org, eztv,
# rarbg...) and release groups (Tigole, MeGusta, YIFY, GalaxyRG...). We strip
# all of that so the download folder is named just the movie/show title, e.g.
# "The Grand Budapest Hotel" or "Breaking Bad".
_CLEAN_TAG_RE = re.compile(
    r'\b(?:1080p|720p|2160p|480p|360p|4k|8k|blu-?ray|bluray|bdrip|brrip|web-?dl|'
    r'webrip|webrip\.?hd|hdtv|hdrip|hd|web|hdr10?|hdr|sdr|vhs|dvdscr|xvid|divx|'
    r'x\s?264|x\s?265|hevc|avc|av1|h264|h265|h\.?264|h\.?265|dts-?hd|dts|aac|ac3|'
    r'eac3|ddp?5\.?\s?1|dd\s?5\.?\s?1|5\.?\s?1|7\.?\s?1|2\.?\s?0|atmos|remux|proper|'
    r'extended|unrated|remaster(ed)?|imax|10bit|8bit|hi10p|complexx|complete|'
    r'repack|internal|scene|pimprg|prg|galaxyrg\d*|galaxy|yify|yts|tigole\d*|'
    r'qxr\d*|q\s?xr|megusta\d*|vppv|psa|portalgoods|bone|sujaidr|crazy4|4ad|'
    r'uindex|index|konami|fgt|groggy|batv|evo|dtv|cinedvdr|rarbg|1337x|magnetdl|'
    r'what|hone|playweb|webdl|webdvd|bloppy|juicy|highcal|d3g|ddg|smurf|'
    r'amazon|netflix|disney|hulo|hulu|hbo|hbomax|showtime|starz|apple|peacock|'
    r'paramount|prime|criterion|hallmark|hgtv|foodnetwork|tlc|bravo|cmt|tnt|'
    r'french|english|eng|engsub|multi|subbed|stereo|mono|2ch|\d?ch|iian|verk|joy|silence|'
    r'worldfree4u|filmyzilla|wayne|hz|george|nick|kicks|e_kicks|nova)\b',
    re.IGNORECASE,
)


def _clean_title_for_folder(title: str) -> str:
    """Produce a clean, proper title for the download folder from a messy
    release name (strip year, season/episode markers, codecs, resolutions,
    websites and release groups)."""
    s = title
    # Strip leading "www.someSite.org - " / "http(s)://..." website markers.
    s = re.sub(r'^(?:www\.[\w\.\-]+\s*-+\s*|https?://\S*\s*-+\s*)', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\(?\b(?:19|20)\d{2}\b\)?', ' ', s)
    s = re.sub(r'\bSeason\s*\.?\s*S?\d{1,2}(?:\s*-\s*S?\d{1,2})*\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bS\d{1,2}E\d{1,3}\b|\bS\d{1,2}\b|\bE\d{1,3}\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\b\d+(?:\.\d+)?\s*(?:gb|mb|gib|mib)\b', ' ', s, flags=re.IGNORECASE)
    s = _CLEAN_TAG_RE.sub(' ', s)
    s = re.sub(r'[-_.()\[\]/]', ' ', s)
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    # Drop leftover codec/release-group numbers that survived tag removal (e.g. "265").
    s = re.sub(r'\b(?:264|265|1080|720|2160|480)\b', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = s.title().strip()
    if len(s) > 3 and len(s) <= FOLDER_MAX_LEN:
        return s
    # Fallback: safe short name (still tag-stripped + length-capped)
    return _short_folder_name(title)

PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.dump.cl:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker1.bt.moack.co.kr:80/announce",
    "udp://tracker.lelux.fi:6969/announce",
    "udp://retracker.lanta-net.ru:2710/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://tracker.cyberia.is:6969/announce",
]
TRACKER_CSV = ",".join(PUBLIC_TRACKERS)


def ensure_aria2_engine() -> str:
    # Cross-platform (Windows + Linux/Render): pick the right executable name,
    # and on Linux rely on the system-installed `aria2c` available in PATH.
    is_windows = platform.system() == "Windows"
    exe_name = "aria2c.exe" if is_windows else "aria2c"

    # Resolve via PATH first (works on Linux and if aria2c.exe is on PATH on Windows).
    found = shutil.which(exe_name)
    if found:
        return found

    exe = Path(exe_name)
    if exe.exists():
        return str(exe)

    # Windows only: download the bundled Windows binary.
    if not is_windows:
        raise RuntimeError("aria2c not found on PATH. Please install aria2 on the server (e.g. `apt-get install aria2`)")

    url = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
    zip_path = Path("aria2_temp.zip")
    try:
        resp = req.get(url, timeout=30)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            f.write(resp.content)
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                if member.endswith("aria2c.exe"):
                    with z.open(member) as src, open(exe, "wb") as dst:
                        dst.write(src.read())
                    break
        zip_path.unlink(missing_ok=True)
        return str(exe)
    except Exception as e:
        zip_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to setup aria2: {e}")


def check_disk_space(target_dir: Path, estimated_size_str: str) -> dict:
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        stat = shutil.disk_usage(target_dir)
        free_gb = stat.free / (1024 ** 3)
        match = re.search(r'([\d\.]+)\s*([MGT]B)', estimated_size_str, re.I)
        required_gb = 2.0
        if match:
            num = float(match.group(1))
            unit = match.group(2).upper()
            if unit == "GB":
                required_gb = num
            elif unit == "MB":
                required_gb = num / 1024.0
        return {"ok": free_gb >= required_gb, "free_gb": round(free_gb, 2), "required_gb": round(required_gb, 2), "drive": target_dir.anchor}
    except Exception:
        return {"ok": True, "free_gb": 0, "required_gb": 0, "drive": "?"}


def _get_video_duration_secs(file_path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return -1.0


def organize_and_clean_movie(movie_folder: Path, clean_title: str, expected_size_str: str = "",
                             is_episode: bool = False, episode_label: str = ""):
    # Junk files that come with torrents. NOTE: image files (.jpg/.png/...) are
    # deliberately KEPT — they may be the auto-downloaded poster.
    JUNK_EXT = {".nfo", ".txt", ".url", ".lnk", ".xml", ".htm", ".html", ".torrent"}
    JUNK_SUFFIX = (".aria2", ".part", ".sample", ".url", ".final.tmp", ".tmp", ".bak", ".temp")
    IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
    SAMPLE_RE = re.compile(r'sample|trailer|reel|teaser|promo|behind', re.IGNORECASE)
    # web-shortcut / site-marker files with no real extension (ex: "www.UIindex.org")
    NOEXT_JUNK_RE = re.compile(r'^(www\.[\w\.\-]+|http(s)?://[\w\.\-]+|index|urls?|link)', re.IGNORECASE)

    if not movie_folder.exists():
        return None

    expected_bytes = 0
    if expected_size_str:
        m = re.search(r'([\d\.]+)\s*([MGT]B)', expected_size_str, re.I)
        if m:
            num = float(m.group(1))
            unit = m.group(2).upper()
            mults = {"MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
            expected_bytes = int(num * mults.get(unit, 0))

    for root, dirs, files in os.walk(movie_folder, topdown=False):
        for f in files:
            fp = Path(root) / f
            ext = fp.suffix.lower()
            if ext in VIDEO_EXTENSIONS or ext in IMAGE_EXT:
                # keep video and (potential poster) image files
                continue
            is_junk = ext in JUNK_EXT
            is_junk = is_junk or any(fp.name.lower().endswith(s) for s in JUNK_SUFFIX)
            # extensionless web-shortcut junk files, e.g. "www.UIindex.org"
            if not ext and not is_junk and NOEXT_JUNK_RE.match(fp.stem):
                is_junk = True
            if is_junk:
                try:
                    fp.unlink(missing_ok=True)
                except OSError:
                    pass
        if Path(root) != movie_folder:
            try:
                if not any(Path(root).iterdir()):
                    Path(root).rmdir()
            except OSError:
                pass

    all_videos = []
    for root, _, files in os.walk(movie_folder):
        for f in files:
            fp = Path(root) / f
            if fp.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if size == 0:
                try:
                    fp.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            is_sample = bool(SAMPLE_RE.search(fp.stem))
            all_videos.append((fp, size, is_sample))

    if not all_videos:
        return None

    all_videos.sort(key=lambda x: x[1], reverse=True)

    # Never collapse multi-episode folders (TV season packs) -- EXCEPT when we
    # are explicitly organizing a single series episode (is_episode), where we
    # still want to name just this file and keep the shared Season folder.
    ep_re = re.compile(r'\bS\d{1,2}\s*E\d{1,3}\b', re.IGNORECASE)
    if not is_episode and sum(1 for fp, _, _ in all_videos if ep_re.search(fp.stem)) >= 2:
        print(f"[organize] Season pack detected ({movie_folder.name}) - skipping cleanup")
        return str(all_videos[0][0])

    main_video = None
    if is_episode and episode_label:
        # In episode mode the season folder holds sibling episodes (already
        # present from prior downloads). Pick the video that actually matches
        # THIS episode download — the one whose filename contains the same
        # SxxExx marker as episode_label ("Breaking Bad - S01E02").
        m_label = re.search(r'\b(S\d{1,2}E\d{1,3})\b', episode_label, re.IGNORECASE)
        if m_label:
            want = m_label.group(1).upper()
            cands = [v for v in all_videos if re.search(r'\bS\d{1,2}\s*E\d{1,3}\b', v[0].stem, re.IGNORECASE)]
            for fp, size, is_sample in cands:
                stem_up = fp.stem.upper()
                if want in stem_up:
                    main_video = (fp, size)
                    break
    if main_video is None:
        for fp, size, is_sample in all_videos:
            if not is_sample:
                main_video = (fp, size)
                break
    if main_video is None:
        main_video = (all_videos[0][0], all_videos[0][1])

    main_path, main_size = main_video

    if expected_bytes > 0:
        ratio = main_size / expected_bytes
        if ratio < 0.15:
            print(f"[organize] {main_path.name} is {ratio:.0%} of expected — skipping cleanup")
            return str(main_path)

    dur = _get_video_duration_secs(str(main_path))
    if 0 < dur < 600 and main_size < 50 * 1024 * 1024:
        print(f"[organize] Main file too short ({dur:.0f}s): {main_path.name}")
        return None

    for fp, size, is_sample in all_videos:
        if fp != main_path:
            if is_episode:
                continue  # keep sibling episodes in the shared Season folder
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass

    if is_episode and episode_label:
        # Series episode: store it with a clean "<Show> - SxxExx" name inside the
        # Season folder, so the library groups it under the right series.
        safe_title = episode_label
        ext = main_path.suffix
        target = movie_folder / f"{safe_title}{ext}"
    else:
        safe_title = _clean_title_for_folder(clean_title) or main_path.stem
        ext = main_path.suffix
        target = movie_folder / f"{safe_title}{ext}"
    if main_path != target:
        if target.exists():
            target.unlink()
        shutil.move(str(main_path), str(target))

    # Remove leftover junk but KEEP video + poster image files. In episode
    # mode the folder is a shared "<Show>/Season N/" directory, so we must also
    # keep sibling episodes already present.
    for item in list(movie_folder.iterdir()):
        if item == target:
            continue
        if item.is_dir():
            try:
                shutil.rmtree(item, ignore_errors=True)
            except Exception:
                pass
        else:
            if item.suffix.lower() in IMAGE_EXT:
                continue  # keep poster
            if is_episode and item.suffix.lower() in VIDEO_EXTENSIONS:
                continue  # keep sibling episode videos in the Season folder
            try:
                item.unlink(missing_ok=True)
            except Exception:
                pass

    print(f"[organize] Kept: {target.name} ({target.stat().st_size / 1024 / 1024:.0f}MB)")

    # Optionally rename the folder to the clean "proper title" so the library
    # scans a tidy name (also helps IMDb poster lookup). Series episodes keep
    # their "<Show>/Season N" structure, so never rename the folder here.
    if not is_episode:
        clean_dir_name = safe_title
        if clean_dir_name and clean_dir_name != movie_folder.name:
            new_dir = movie_folder.parent / clean_dir_name
            if not new_dir.exists() and new_dir != movie_folder:
                try:
                    movie_folder.rename(new_dir)
                    movie_folder = new_dir
                    target = new_dir / target.name
                except OSError:
                    pass

    return str(target)


def _rpc_call(method: str, params=None) -> dict | None:
    payload = {"jsonrpc": "2.0", "id": "req", "method": method, "params": params or []}
    try:
        r = req.post(RPC_URL, json=payload, timeout=5)
        resp = r.json()
        if "error" in resp:
            print(f"[rpc] {method} -> error: {resp['error']}")
        return resp
    except req.ConnectionError:
        print(f"[rpc] {method} -> connection refused")
        return None
    except req.Timeout:
        print(f"[rpc] {method} -> timeout")
        return None
    except Exception as e:
        print(f"[rpc] {method} -> {e}")
        return None


def _format_speed(bps: int) -> str:
    if bps <= 0:
        return "0 B/s"
    val = float(bps)
    for unit in ["B/s", "KB/s", "MB/s", "GB/s"]:
        if val < 1024:
            return f"{val:.1f} {unit}" if unit != "B/s" else f"{int(val)} B/s"
        val /= 1024
    return f"{val:.1f} TB/s"


def _format_eta(seconds: int) -> str:
    if seconds < 0 or seconds == 8640000:
        return "Unknown"
    if seconds == 0:
        return "Done"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s" if m > 0 else f"{s}s"


def _find_big_active_gid(magnet: str) -> str | None:
    """Find the REAL (large) active download gid for a magnet.

    With magnet-only links aria2 sometimes exposes a tiny metadata pseudo-download
    while the actual torrent runs under another gid. We pick the active download
    with the largest totalLength (i.e. the real media file), ignoring the ~KB
    metadata artifact.
    """
    try:
        result = _rpc_call("aria2.tellActive", [["gid", "totalLength", "downloadSpeed"]])
        if not result or "result" not in result:
            return None
        real = None
        best_size = 0
        for d in result["result"]:
            total = int(d.get("totalLength", 0) or 0)
            speed = int(d.get("downloadSpeed", 0) or 0)
            # real downloads are >2MB with data; skip metadata artifacts
            if total > 2 * 1024 * 1024 and (total > best_size):
                best_size = total
                real = d.get("gid")
        return real or None
    except Exception:
        return None


class DownloadManager:
    def __init__(self):
        self.aria2_path = ensure_aria2_engine()
        self.tasks: dict[str, dict] = {}
        self._magnets: dict[str, str] = {}
        self._destinations: dict[str, str] = {}
        self._gids: dict[str, str] = {}
        self._active_magnets: set[str] = set()
        self._zero_speed_since: dict[str, float | None] = {}
        self._stall_restarts: dict[str, int] = {}
        self._progress_seen: dict[str, int] = {}
        self._tellstatus_fails: dict[str, int] = {}
        self._rpc_server = None
        self._start_rpc_server()
        self._monitor = Thread(target=self._monitor_loop, daemon=True)
        self._monitor.start()

    def _kill_stale_aria2(self):
        """Kill leftover aria2c processes from earlier runs so only ONE instance
        owns ports 6800 (RPC) and 6881 (DHT/listen). Several stalled downloads
        were caused by piled-up orphaned aria2 processes fighting over the ports."""
        import subprocess as _sp
        try:
            if platform.system() == "Windows":
                out = _sp.run(
                    ["taskkill", "/F", "/IM", "aria2c.exe", "/T"],
                    capture_output=True, text=True,
                )
            else:
                out = _sp.run(
                    ["pkill", "-f", "aria2c"],
                    capture_output=True, text=True,
                )
            print(f"[aria2] cleared stale processes: {out.stdout.strip()[:120]}", flush=True)
        except Exception as e:
            print(f"[aria2] stale cleanup error: {e}", flush=True)

    def _start_rpc_server(self):
        self._kill_stale_aria2()
        cmd = [
            self.aria2_path,
            "--enable-rpc",
            "--rpc-listen-all=false",
            "--rpc-listen-port=6800",
            "--rpc-allow-origin-all=true",
            "--seed-time=0",
            "--console-log-level=notice",
            "--summary-interval=0",
            "--bt-max-peers=120",
            "--max-connection-per-server=16",
            "--max-concurrent-downloads=5",
            "--split=16",
            "--min-split-size=1M",
            "--enable-dht=true",
            "--dht-listen-port=6881",
            "--listen-port=6881",
            "--bt-enable-lpd=true",
            "--enable-peer-exchange=true",
            "--peer-agent=aria2/1.37.0",
            "--peer-id-prefix=-AR1370-",
            "--bt-tracker=" + TRACKER_CSV,
            "--max-tries=10",
            "--retry-wait=5",
            "--continue=true",
            "--always-resume=true",
            "--auto-file-renaming=false",
            "--bt-save-metadata=true",
            "--bt-remove-unselected-file=true",
            "--bt-force-encryption=false",
            "--bt-stop-timeout=180",
            "--bt-tracker-timeout=30",
        ]
        try:
            self._rpc_server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
            health = _rpc_call("aria2.getVersion")
            if health and "result" in health:
                print(f"[aria2] RPC ready — version {health['result'].get('version', '?')}")
            else:
                print("[aria2] WARNING: health check failed")
        except Exception as e:
            print(f"[aria2] Failed to start: {e}")
            self._rpc_server = None

    def _ensure_aria2_alive(self) -> bool:
        health = _rpc_call("aria2.getVersion")
        if health and "result" in health:
            return True
        print("[aria2] RPC unreachable, restarting...")
        try:
            if self._rpc_server:
                self._rpc_server.kill()
                self._rpc_server.wait(timeout=5)
        except Exception:
            pass
        self._start_rpc_server()
        health = _rpc_call("aria2.getVersion")
        if health and "result" in health:
            print("[aria2] Restarted OK")
            return True
        print("[aria2] FATAL: restart failed")
        return False

    def start_download(self, magnet: str, destination: str, title: str, size: str = "",
                       target_folder: str | None = None, episode_label: str = "") -> str | None:
        if magnet in self._active_magnets:
            print(f"[dl] Magnet already downloading, skipping duplicate")
            return None

        task_id = str(uuid4())[:8]
        if target_folder:
            # Series episode: download straight into <dest>/<Show>/Season <n>/.
            movie_folder = Path(target_folder)
            movie_folder.mkdir(parents=True, exist_ok=True)
        else:
            # Movie: use a CLEAN "proper title" folder name (short + no release
            # tags) so the download folder is named just the movie title.
            clean_name = _clean_title_for_folder(title)
            movie_folder = Path(destination) / clean_name
            movie_folder.mkdir(parents=True, exist_ok=True)

        self.tasks[task_id] = {
            "status": "downloading",
            "title": title,
            "folder": str(movie_folder),
            "progress": 0,
            "speed": "0 B/s",
            "speed_bytes": 0,
            "peers": 0,
            "eta": "Connecting...",
            "error": None,
            "started_at": time.time(),
            "expected_size": size,
            "episode_label": episode_label,
        }

        self._magnets[task_id] = magnet
        self._destinations[task_id] = destination
        self._zero_speed_since[task_id] = None
        self._active_magnets.add(magnet)

        thread = Thread(target=self._run_download_rpc, args=(task_id, magnet, str(movie_folder)), daemon=True)
        thread.start()
        return task_id

    @staticmethod
    def _add_uri_options(destination: str) -> dict:
        return {
            "dir": destination,
            "seed-time": "0",
            "follow-torrent": "mem",
            "auto-file-renaming": "false",
            "allow-overwrite": "true",
            "continue": "true",
            "always-resume": "true",
            "bt-max-peers": "120",
            "max-connection-per-server": "16",
            "split": "16",
            "min-split-size": "1M",
            "enable-dht": "true",
            "bt-enable-lpd": "true",
            "enable-peer-exchange": "true",
            "bt-tracker": TRACKER_CSV,
            "bt-tracker-timeout": "30",
            "bt-stop-timeout": "180",
            "bt-save-metadata": "true",
        }

    def _run_download_rpc(self, task_id: str, magnet: str, destination: str):
        if not self._ensure_aria2_alive():
            task = self.tasks.get(task_id)
            if task:
                task["status"] = "error"
                task["error"] = "aria2 engine is not responding"
                task["speed"] = "Failed"
            return

        if "tr=" not in magnet:
            tracker_params = "&".join([f"tr={quote_plus(t)}" for t in PUBLIC_TRACKERS])
            sep = "&" if "?" in magnet else "?"
            magnet = magnet + sep + tracker_params
            print(f"[dl] {task_id} embedded {len(PUBLIC_TRACKERS)} trackers in magnet URI")

        result = _rpc_call("aria2.addUri", [[magnet], self._add_uri_options(destination)])

        if not result or "error" in result:
            task = self.tasks.get(task_id)
            err_msg = result.get("error", {}).get("message", "RPC failed") if result else "RPC unreachable"
            if "already registered" in err_msg.lower():
                print(f"[dl] {task_id} torrent already registered, treating as duplicate")
                if task:
                    task["status"] = "error"
                    task["error"] = "This torrent is already being downloaded."
                    task["speed"] = "Duplicate"
            elif task:
                task["status"] = "error"
                task["error"] = err_msg
                task["speed"] = "Failed"
            self._active_magnets.discard(magnet)
            return

        gid = result.get("result")
        if not gid:
            task = self.tasks.get(task_id)
            if task:
                task["status"] = "error"
                task["error"] = "No GID returned"
            self._active_magnets.discard(magnet)
            return

        self._gids[task_id] = gid
        print(f"[dl] {task_id} GID={gid}, monitoring...")

        while True:
            task = self.tasks.get(task_id)
            if not task or task["status"] != "downloading":
                break

            status = _rpc_call("aria2.tellStatus", [gid, [
                "totalLength", "completedLength", "downloadSpeed",
                "uploadSpeed", "connections", "status", "errorCode", "errorMessage",
            ]])

            if not status or "error" in status:
                # A dead/missing gid (aria2 no longer tracks it) must not loop
                # forever. Count consecutive failures and treat a persistently
                # missing gid the same as a stall (restart -> error).
                self._tellstatus_fails[task_id] = self._tellstatus_fails.get(task_id, 0) + 1
                if self._tellstatus_fails[task_id] >= 5:
                    print(f"[dl] {task_id} gid {gid} repeatedly unavailable, treating as stalled", flush=True)
                    task["status"] = "error"
                    task["error"] = "Download engine lost the torrent (stalled); partial files kept; try Resume."
                    task["speed"] = "Stalled"
                    task["eta"] = "--"
                    break
                time.sleep(1)
                continue

            s = status.get("result", {})

            if s.get("status") == "error":
                task["status"] = "error"
                task["error"] = s.get("errorMessage", "Download failed")
                task["speed"] = "Failed"
                task["eta"] = "--"
                break

            if s.get("status") == "complete":
                total_len = int(s.get("totalLength", 0) or 0)
                completed_len = int(s.get("completedLength", 0) or 0)

                # BUG FIX: aria2 can create a small "metadata-only" pseudo-download
                # (often ~46 KB, just the .torrent info) that "completes" instantly,
                # while the REAL torrent download runs under a DIFFERENT gid. If we
                # finalize on the tiny artifact we wrongly report "Corrupt"/"No
                # valid video". Detect a tiny completed length and re-sync to the
                # real active download for the same magnet instead.
                if completed_len < 2 * 1024 * 1024:
                    real = _find_big_active_gid(self._magnets.get(task_id, ""))
                    if real:
                        print(f"[dl] {task_id} metadata-artifact completed (old gid {gid}); re-syncing to real gid {real}", flush=True)
                        gid = real
                        self._gids[task_id] = gid
                        time.sleep(1)
                        continue

                task["progress"] = 100
                task["speed"] = "Verifying..."
                task["speed_bytes"] = 0
                task["peers"] = 0
                task["eta"] = "--"
                try:
                    # GUARD: aria2 can report "complete" on a magnet whose metadata
                    # never resolved (totalLength/completeLength == 0) or whose
                    # torrent is empty. Treat those as failures — there is no real
                    # file to play, so trying to "organize" an empty folder only
                    # yields a confusing "No valid video found" error.
                    if total_len == 0 or completed_len == 0:
                        task["status"] = "error"
                        task["error"] = ("Torrent has no downloadable data (metadata never resolved or empty torrent). "
                                         "This release likely has no seeders. Try another release.")
                        task["speed"] = "No data"
                        print(f"[dl] {task_id} aria2 'complete' but 0 bytes — invalid/metaless torrent", flush=True)
                        break

                    expected_size = task.get("expected_size", "")
                    result_path = organize_and_clean_movie(Path(destination), task["title"], expected_size,
                                                           is_episode=bool(task.get("episode_label")),
                                                           episode_label=task.get("episode_label", ""))
                    if result_path:
                        # REAL completeness check: probe the file with ffprobe.
                        # A "complete" download can still be corrupt/truncated.
                        from modules.transcoder import validate_video
                        check = validate_video(result_path)
                        if not check.get("ok"):
                            task["status"] = "error"
                            task["error"] = "Downloaded file is incomplete/corrupt: " + str(check.get("error", "invalid video"))
                            task["speed"] = "Corrupt"
                            print(f"[dl] {task_id} downloaded file INVALID: {check.get('error')}", flush=True)
                            break
                        task["status"] = "completed"
                        task["file"] = result_path
                        task["duration"] = round(check.get("duration", 0))
                        task["speed"] = "Done"
                        self._auto_transcode_after_download(result_path)
                    else:
                        task["status"] = "error"
                        task["error"] = "No valid video found"
                        task["speed"] = "Failed"
                except Exception as e:
                    task["status"] = "error"
                    task["error"] = f"Cleanup failed: {e}"
                    task["speed"] = "Failed"
                break

            total = int(s.get("totalLength", 0))
            completed = int(s.get("completedLength", 0))
            status = s.get("status", "active")
            # With magnet-only links aria2 can monitor the small metadata
            # pseudo-download while the REAL torrent data runs under another gid.
            # During active download re-sync to the real gid so the progress bar
            # reflects the actual transfer instead of sitting at 0%.
            if status == "active" and total < 2 * 1024 * 1024:
                real = _find_big_active_gid(self._magnets.get(task_id, ""))
                if real and real != gid:
                    print(f"[dl] {task_id} active metadata-gid {gid}; re-syncing to real gid {real}", flush=True)
                    gid = real
                    self._gids[task_id] = gid
                    time.sleep(1)
                    continue

            speed = int(s.get("downloadSpeed", 0))
            connections = int(s.get("connections", 0))

            if total > 0:
                task["progress"] = int((completed / total) * 100)
                if speed > 0:
                    task["eta"] = _format_eta((total - completed) // speed)
                else:
                    task["eta"] = "Waiting..."
            else:
                task["progress"] = 0
                task["eta"] = "Connecting..."

            task["speed"] = _format_speed(speed)
            task["speed_bytes"] = speed
            task["peers"] = connections

            now = time.time()
            # Progression is judged by ACTUAL bytes completed on disk, not the
            # instantaneous aria2 downloadSpeed. aria2 can report a non-zero
            # (phantom) downloadSpeed while writing nothing to disk, which would
            # otherwise keep `made_progress` true forever and defeat stall
            # detection. Peers that are active count toward activity only when
            # they actually deliver completed bytes.
            made_progress = completed > self._progress_seen.get(task_id, 0)
            if made_progress:
                self._progress_seen[task_id] = completed
                self._tellstatus_fails[task_id] = 0
                self._zero_speed_since[task_id] = None
                self._stall_restarts[task_id] = 0
            else:
                if self._zero_speed_since.get(task_id) is None:
                    self._zero_speed_since[task_id] = now
                    print(f"[dl] {task_id} zero speed, stall timer started ({STALL_TIMEOUT}s)")
                elapsed = now - self._zero_speed_since[task_id]
                if elapsed >= 30 and int(elapsed) % 30 == 0:
                    print(f"[dl] {task_id} zero speed {int(elapsed)}s, {connections} peers")

                if elapsed >= STALL_TIMEOUT:
                    restarts = self._stall_restarts.get(task_id, 0)
                    if restarts < MAX_STALL_RESTARTS:
                        # Internet likely dropped: re-add the SAME magnet into the SAME
                        # folder. aria2's .aria2 control file resumes from where it stopped.
                        print(f"[dl] {task_id} stalled - restarting torrent (attempt {restarts + 1}/{MAX_STALL_RESTARTS})")
                        _rpc_call("aria2.remove", [gid])
                        self._gids.pop(task_id, None)
                        time.sleep(3)
                        readd = _rpc_call("aria2.addUri", [[magnet], self._add_uri_options(destination)])
                        if readd and "result" in readd:
                            gid = readd["result"]
                            self._gids[task_id] = gid
                            self._stall_restarts[task_id] = restarts + 1
                            self._zero_speed_since[task_id] = None
                            task["speed"] = "0 B/s"
                            task["peers"] = 0
                            task["eta"] = "Reconnecting..."
                        else:
                            # aria2 not answering yet; retry again in 15s instead of giving up
                            print(f"[dl] {task_id} re-add failed, will retry shortly")
                            self._zero_speed_since[task_id] = now - STALL_TIMEOUT + 15
                    else:
                        print(f"[dl] {task_id} still stalled after {MAX_STALL_RESTARTS} restart attempts")
                        task["status"] = "error"
                        task["error"] = "Download stalled \u2014 partial files kept; try Resume."
                        task["speed"] = "Stalled"
                        task["peers"] = 0
                        task["eta"] = "--"
                        break

            time.sleep(1)

        self._gids.pop(task_id, None)
        self._zero_speed_since.pop(task_id, None)
        self._stall_restarts.pop(task_id, None)
        self._progress_seen.pop(task_id, None)
        self._tellstatus_fails.pop(task_id, None)
        self._active_magnets.discard(magnet)
        print(f"[dl] {task_id} ended, status: {task.get('status', 'unknown')}")

    def _auto_transcode_after_download(self, result_path):
        """After a download finishes, find all video files and transcode any that
        browsers cannot play (HEVC/MKV/AVI/EAC3...) into .playable.mp4 in the
        background. Works for single movies and full season packs."""
        try:
            from modules.transcoder import transcode_manager, is_browser_playable, needs_transcode

            root = Path(result_path)
            if root.is_file():
                files = [root]
            else:
                files = [
                    Path(r) / f
                    for r, _, fs in os.walk(root)
                    for f in fs
                    if Path(f).suffix.lower() in VIDEO_EXTENSIONS
                ]
            started = []
            for fp in files:
                try:
                    if fp.stat().st_size < 1024:
                        continue
                except OSError:
                    continue
                if is_browser_playable(str(fp)):
                    continue
                if transcode_manager.start(str(fp)):
                    started.append(fp.name)
            if started:
                print(f"[dl] auto-transcoding for browser play: {started}", flush=True)
        except Exception as e:
            print(f"[dl] auto-transcode error: {e}", flush=True)

    def cancel_download(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task["status"] not in ("downloading", "paused"):
            return False
        gid = self._gids.pop(task_id, None)
        if gid:
            _rpc_call("aria2.remove", [gid])
        magnet = self._magnets.get(task_id)
        if magnet:
            self._active_magnets.discard(magnet)
        task["status"] = "cancelled"
        task["speed"] = "Cancelled"
        task["peers"] = 0
        task["eta"] = "--"
        self._zero_speed_since.pop(task_id, None)
        self._stall_restarts.pop(task_id, None)
        self._progress_seen.pop(task_id, None)
        self._tellstatus_fails.pop(task_id, None)
        folder = Path(task["folder"])
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        return True

    def pause_download(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task["status"] != "downloading":
            return False
        gid = self._gids.get(task_id)
        if gid:
            _rpc_call("aria2.pause", [gid])
        task["status"] = "paused"
        task["speed"] = "Paused"
        task["peers"] = 0
        task["eta"] = "--"
        self._zero_speed_since.pop(task_id, None)
        return True

    def resume_download(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task["status"] != "paused":
            return False
        gid = self._gids.get(task_id)
        if gid:
            _rpc_call("aria2.unpause", [gid])
            task["status"] = "downloading"
            task["speed"] = "0 B/s"
            task["speed_bytes"] = 0
            task["peers"] = 0
            task["eta"] = "Resuming..."
            self._zero_speed_since[task_id] = None
            return True
        magnet = self._magnets.get(task_id)
        if not magnet:
            return False
        movie_folder = task["folder"]
        task["status"] = "downloading"
        task["speed"] = "0 B/s"
        task["speed_bytes"] = 0
        task["peers"] = 0
        task["eta"] = "Resuming..."
        self._zero_speed_since[task_id] = None
        thread = Thread(target=self._run_download_rpc, args=(task_id, magnet, movie_folder), daemon=True)
        thread.start()
        return True

    def _monitor_loop(self):
        while True:
            time.sleep(3)
            for tid in list(self.tasks.keys()):
                task = self.tasks.get(tid)
                if not task or task["status"] != "downloading":
                    continue
                gid = self._gids.get(tid)
                if not gid:
                    continue
                result = _rpc_call("aria2.tellStatus", [gid, [
                    "downloadSpeed", "connections", "totalLength", "completedLength"
                ]])
                if not result or "result" not in result:
                    continue
                s = result["result"]
                spd = int(s.get("downloadSpeed", 0))
                conns = int(s.get("connections", 0))
                total = int(s.get("totalLength", 0))
                completed = int(s.get("completedLength", 0))

                task["speed"] = _format_speed(spd)
                task["speed_bytes"] = spd
                task["peers"] = conns

                if total > 0:
                    task["progress"] = int((completed / total) * 100)
                    if spd > 0:
                        task["eta"] = _format_eta((total - completed) // spd)
                    else:
                        task["eta"] = "Waiting..."

                if spd > 0:
                    self._zero_speed_since[tid] = None

    def get_progress(self, task_id: str) -> dict | None:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> dict:
        return self.tasks
