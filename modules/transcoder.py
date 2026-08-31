import json
import os
import shutil
import subprocess
import threading
import zipfile
import platform
from pathlib import Path
import requests

FFMPEG_DIR = Path(__file__).resolve().parent.parent / "ffmpeg"
GITHUB_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"


def get_ffmpeg_path() -> str:
    for p in [FFMPEG_DIR / "ffmpeg.exe", FFMPEG_DIR / "ffmpeg"]:
        if p.exists():
            return str(p)
    found = shutil.which("ffmpeg")
    return found or ""


def get_ffprobe_path() -> str:
    for p in [FFMPEG_DIR / "ffprobe.exe", FFMPEG_DIR / "ffprobe"]:
        if p.exists():
            return str(p)
    found = shutil.which("ffprobe")
    return found or ""


def ensure_ffmpeg() -> str:
    path = get_ffmpeg_path()
    if path and get_ffprobe_path():
        return path

    # Linux/Render: do NOT plant Windows binaries here — rely on the system
    # ffmpeg/ffprobe (install via apt, see apt.txt). Without it, transcoding is
    # paused and the library skips non-playable files (safe, no crash).
    if platform.system() != "Windows":
        print("[ffmpeg] system ffmpeg/ffprobe missing — install via apt (add 'ffmpeg' to apt.txt)", flush=True)
        return ""

    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = FFMPEG_DIR / "ffmpeg.zip"
    try:
        resp = requests.get(GITHUB_URL, timeout=60, stream=True)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(256 * 1024):
                f.write(chunk)
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                name = member.rsplit("/", 1)[-1] if "/" in member else member
                if name in ("ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"):
                    data = z.read(member)
                    out = FFMPEG_DIR / name
                    with open(out, "wb") as f:
                        f.write(data)
        zip_path.unlink(missing_ok=True)
        return get_ffmpeg_path()
    except Exception as e:
        print(f"[ffmpeg] Download failed: {e}")
        zip_path.unlink(missing_ok=True)
        return ""


def _ffprobe_json(file_path: str, args: list[str]) -> dict:
    ffprobe = get_ffprobe_path()
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", *args, file_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {}
        import json
        return json.loads(result.stdout or "{}")
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Persistent on-disk probe cache.
#
# The library scan calls get_video_duration()/probe_streams() on EVERY video
# file on EVERY page load. Launching ffprobe (separate process) on dozens of
# MKV/HEVC files every time made the library take ~20s+ to load. We cache the
# ffprobe result on disk keyed by path+size+mtime so it's computed once and
# reused until the file actually changes.
# ---------------------------------------------------------------------------
_PROBE_STORE = Path(__file__).resolve().parent.parent / "data" / "probe_cache.json"
_probe_mem: dict = {}
_probe_mem_lock = threading.Lock()


def _load_probe_store() -> dict:
    global _probe_mem
    if _probe_mem:
        return _probe_mem
    try:
        if _PROBE_STORE.exists():
            _probe_mem = json.loads(_PROBE_STORE.read_text(encoding="utf-8"))
    except Exception:
        _probe_mem = {}
    return _probe_mem


def _save_probe_store():
    try:
        _PROBE_STORE.parent.mkdir(parents=True, exist_ok=True)
        _PROBE_STORE.write_text(json.dumps(_probe_mem, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _probe_key(file_path: str) -> tuple:
    try:
        st = os.stat(file_path)
        return file_path.lower(), st.st_size, int(st.st_mtime)
    except OSError:
        return file_path.lower(), -1, -1


def cached_probe(file_path: str) -> dict:
    """Return {'duration': float, 'video': codec or None, 'audio': [..]} using
    a persistent disk cache keyed by path+size+mtime."""
    key = _probe_key(file_path)
    store = _load_probe_store()
    with _probe_mem_lock:
        hit = store.get(str(key))
        if hit and "duration" in hit:
            return dict(hit)

    data = _ffprobe_json(file_path, ["-show_format", "-show_streams"])
    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except Exception:
        duration = 0.0
    video = None
    audio = []
    for st in data.get("streams", []):
        ct = st.get("codec_type")
        if ct == "video" and video is None:
            video = st.get("codec_name", "unknown")
        elif ct == "audio":
            audio.append(st.get("codec_name", "unknown"))

    entry = {"duration": duration, "video": video, "audio": audio}
    with _probe_mem_lock:
        store[str(key)] = entry
        _save_probe_store()
    return entry


def get_video_duration(file_path: str) -> float:
    if not get_ffprobe_path():
        return -1
    return float(cached_probe(file_path).get("duration", 0))


def detect_video_codec(file_path: str) -> str:
    ffprobe = get_ffprobe_path()
    if not ffprobe:
        return "unknown"
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "csv=p=0", file_path],
            capture_output=True, text=True, timeout=10,
        )
        codec = result.stdout.strip().lower()
        return codec or "unknown"
    except Exception:
        return "unknown"


NON_PLAYABLE = {".mkv", ".avi", ".mov", ".flv", ".wmv", ".ts", ".m2ts", ".vob"}


def needs_transcode(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in NON_PLAYABLE


def build_transcode_cmd(ffmpeg_path: str, input_path: str, seek_seconds: float = 0) -> list[str]:
    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error"]
    if seek_seconds > 0:
        cmd += ["-ss", str(seek_seconds)]
    cmd += [
        "-i", input_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+faststart",
        "-max_muxing_queue_size", "4096",
        "-f", "mp4", "-y", "pipe:1",
    ]
    return cmd


# ---------------------------------------------------------------------------
# Browser compatibility detection + centralized background transcode manager
#
# Browsers cannot play: HEVC (x265) video, and EAC3/DTS/AC3 audio in some
# browsers. They can normally play H.264+MP3/AAC in .mp4/.webm. Instead of
# asking the user to wait for a transcode on every play, we detect this the
# moment a download finishes and transcode to .playable.mp4 in the background
# so it is ready before the user opens the library.
# ---------------------------------------------------------------------------

BROWSER_OK_CODECS = {"h264", "avc1", "hev1", "hvc1"}  # hevc listed here only to be explicit below
AUDIO_OK = {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le"}
AUDIO_BAD = {"ac3", "eac3", "dts", "truehd", "mlp", "pcm_dvd"}

# Container formats that are never natively playable in a browser tag
NON_BROWSER_EXT = {".mkv", ".avi", ".mov", ".flv", ".wmv", ".ts", ".m2ts", ".vob", ".rmvb", ".webm"}


def probe_streams(file_path: str) -> dict:
    """Return {'video': codec_name or None, 'audio': [codec_names]} via ffprobe."""
    cached = cached_probe(file_path)
    return {"video": cached.get("video") or "unknown", "audio": cached.get("audio") or []}


def detect_audio_codecs(file_path: str) -> list:
    return probe_streams(file_path).get("audio", [])


def read_chunk_is_playable(file_path: str) -> bool:
    """True if the file can be played by a browser without transcoding."""
    streams = probe_streams(file_path)
    video = streams.get("video") or "unknown"
    # mkv/avi/mov containers: always transcode regardless of inner codec
    if Path(file_path).suffix.lower() in NON_BROWSER_EXT:
        return False
    # HEVC regardless of container (even in mp4) -> transcode
    if video in ("hevc", "h265", "x265", "hev1", "hvc1"):
        return False
    # any bad audio codec -> transcode (safe, also normalizes)
    for a in streams.get("audio", []):
        if a in AUDIO_BAD:
            return False
    # unknown video codec -> assume needs transcode to be safe
    if video == "unknown":
        return False
    return True


def video_codec_browser_ok(file_path: str) -> bool:
    """True if the video codec itself is browser-playable (H.264/AVC), regardless
    of container or audio. Used to decide between a fast remux (stream copy the
    video, maybe re-encode only audio) vs a slow full re-encode."""
    streams = probe_streams(file_path)
    video = streams.get("video") or "unknown"
    if video in ("hevc", "h265", "x265", "hev1", "hvc1"):
        return False
    if video not in ("h264", "avc1"):
        return False
    return True


def needs_remux(file_path: str) -> bool:
    """True when only the CONTAINER is non-browser (e.g. MKV) but the video
    codec is already browser-compatible (H.264) and audio is OK. In that case a
    fast stream-copy remux to .mp4 (~seconds) is enough — a full re-encode would
    be wasted (e.g., a 2GB x264 MKV). HEVC/x265 or bad-audio files still need a
    real transcode."""
    if Path(file_path).suffix.lower() not in NON_BROWSER_EXT:
        return False
    if not video_codec_browser_ok(file_path):
        return False
    for a in probe_streams(file_path).get("audio", []):
        if a in AUDIO_BAD:
            return False
    return True


def validate_video(file_path: str) -> dict:
    """Robustly check that a video file is REAL and structurally complete.

    Returns:
        {"ok": bool, "video": codec or None, "audio": [...], "duration": seconds,
         "error": reason or None}
    """
    ffprobe = get_ffprobe_path()
    if not ffprobe:
        return {"ok": False, "error": "ffprobe unavailable"}
    fpath = Path(file_path)
    if not fpath.exists() or fpath.stat().st_size < 1024:
        return {"ok": False, "error": "file missing or too small"}
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(fpath)],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return {"ok": False, "error": "ffprobe could not read file"}
        import json
        data = json.loads(result.stdout)
    except Exception:
        return {"ok": False, "error": "ffprobe failed"}
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video_codec = None
    audio_codecs = []
    has_video = False
    for st in streams:
        ct = st.get("codec_type")
        if ct == "video":
            has_video = True
            if video_codec is None:
                video_codec = st.get("codec_name", "unknown")
        elif ct == "audio":
            audio_codecs.append(st.get("codec_name", "unknown"))
    try:
        duration = float(fmt.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0
    if not has_video:
        return {"ok": False, "error": "no video stream", "video": video_codec,
                "audio": audio_codecs, "duration": duration}
    if duration <= 0:
        return {"ok": False, "error": "unreadable duration (corrupt)",
                "video": video_codec, "audio": audio_codecs, "duration": duration}
    return {"ok": True, "video": video_codec, "audio": audio_codecs,
            "duration": duration, "error": None}


def needs_transcode(file_path: str) -> bool:
    """True if this file is not directly browser-playable."""
    if Path(file_path).suffix.lower() in NON_BROWSER_EXT:
        return True
    return not read_chunk_is_playable(file_path)


# Cache of "already checked" files so we don't re-probe on every library scan
_probe_cache: dict[str, bool] = {}
_probe_cache_lock = threading.Lock()


def is_browser_playable(file_path: str) -> bool:
    """Cached version of needing-transcode check (True = playable directly)."""
    with _probe_cache_lock:
        if file_path in _probe_cache:
            return _probe_cache[file_path]
    try:
        if Path(file_path).suffix.lower() in NON_BROWSER_EXT:
            ok = False
        else:
            ok = read_chunk_is_playable(file_path)
    except Exception:
        ok = False
    with _probe_cache_lock:
        _probe_cache[file_path] = ok
    return ok


class TranscodeManager:
    """Serializes background transcodes. Only one ffmpeg at a time.

    - start(path): kicks a worker if needed; safe to call repeatedly.
    - is_running(path): True while that file is transcoding.
    - get_playable(path): returns the .playable.mp4 Path if present.
    - ready(path): True if a completed .playable.mp4 exists.
    """

    def __init__(self):
        self._queue: list[str] = []
        self._queue_lock = threading.Lock()
        self._running: dict[str, bool] = {}
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._bootstrap_done = False

    def _bootstrap(self):
        """On first use, remove clearly-invalid cache files (tiny/empty) left
        from a previous run. Full validation is done lazily by ready() so we
        never delete a genuinely-good playable just because it lacks a marker."""
        if self._bootstrap_done:
            return
        self._bootstrap_done = True
        import os
        roots = [
            str(Path.home() / "Downloads" / "movies"),
            str(Path(__file__).resolve().parent.parent),
        ]
        try:
            for root in roots:
                if not os.path.isdir(root):
                    continue
                for cp in Path(root).rglob("*.playable.mp4"):
                    try:
                        if cp.stat().st_size < 1024:
                            cp.unlink(missing_ok=True)
                            print(f"[transcode] removed tiny/broken cache: {cp.name}", flush=True)
                    except OSError:
                        pass
        except Exception as e:
            print(f"[transcode] bootstrap cleanup error: {e}", flush=True)

    @staticmethod
    def playable_path(file_path) -> Path:
        return Path(file_path).with_suffix(".playable.mp4")

    @staticmethod
    def _done_marker(file_path) -> Path:
        return Path(file_path).with_suffix(".playable.done")

    def ready(self, file_path) -> bool:
        """True only if a COMPLETE, VERIFIED playable cache exists.

        We do a real ffprobe validation of the .playable.mp4 (has a video
        stream, readable duration) and cache the result so repeated checks are
        fast. A file served to the browser is therefore guaranteed valid.
        """
        if self.is_running(file_path):
            return False
        cp = self.playable_path(file_path)
        key = str(cp)
        with _probe_cache_lock:
            if key in _probe_cache:
                return _probe_cache[key]
        try:
            ok = cp.exists() and cp.stat().st_size > 1024
            result = validate_video(str(cp)) if ok else {"ok": False}
            ok = bool(result.get("ok"))
        except Exception:
            ok = False
        with _probe_cache_lock:
            _probe_cache[key] = ok
        return ok

    def is_running(self, file_path) -> bool:
        return self._running.get(str(file_path), False)

    def status(self, file_path) -> dict:
        if self.ready(file_path):
            return {"status": "ready", "message": "Video is ready."}
        if self.is_running(file_path):
            return {"status": "preparing", "message": "Video is being prepared, please wait..."}
        return {"status": "not_started", "message": "Not started."}

    def start(self, file_path, on_done=None):
        self._bootstrap()
        fp = str(Path(file_path))
        with self._queue_lock:
            if fp in self._queue:
                return False
            if self.is_running(fp):
                return False
            self._queue.append(fp)
        self._ensure_worker(on_done)
        return True

    def _ensure_worker(self, on_done):
        with self._worker_lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_main, args=(on_done,), daemon=True
            )
            self._worker.start()

    def _worker_main(self, on_done):
        while True:
            with self._queue_lock:
                if not self._queue:
                    return
                fp = self._queue.pop(0)
            if self.ready(fp):
                continue
            self._running[fp] = True
            try:
                self._transcode_one(fp)
                if on_done:
                    try:
                        on_done(fp)
                    except Exception:
                        pass
            finally:
                self._running.pop(fp, None)

    def _transcode_one(self, fp):
        src = Path(fp)
        cached = self.playable_path(fp)
        ffmpeg = ensure_ffmpeg()
        if not ffmpeg:
            print(f"[transcode] no ffmpeg, cannot process {src.name}", flush=True)
            return
        try:
            if src.stat().st_size < 1024:
                print(f"[transcode] skip too small: {src.name}", flush=True)
                return
        except OSError:
            return
        if needs_remux(str(src)):
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", str(src),
                "-c", "copy",
                "-movflags", "frag_keyframe+empty_moov",
                "-max_muxing_queue_size", "4096",
                "-f", "mp4", "-y", str(cached),
            ]
            print(f"[transcode] remuxing (fast copy): {src.name} -> {cached.name}", flush=True)
        elif video_codec_browser_ok(str(src)):
            # Video is already H.264 (browser-OK) but audio is not (e.g. AC3/DTS):
            # stream-copy the video, re-encode only the audio -> much faster than a
            # full re-encode.
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", str(src),
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "160k", "-ac", "2",
                "-movflags", "frag_keyframe+empty_moov+faststart",
                "-max_muxing_queue_size", "4096",
                "-f", "mp4", "-y", str(cached),
            ]
            print(f"[transcode] remux + audio re-encode: {src.name} -> {cached.name}", flush=True)
        else:
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", str(src),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                "-movflags", "frag_keyframe+empty_moov+faststart",
                "-max_muxing_queue_size", "4096",
                "-f", "mp4", "-y", str(cached),
            ]
            print(f"[transcode] starting (full re-encode): {src.name} -> {cached.name}", flush=True)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            stderr_data, _ = proc.communicate(timeout=3600)
        except Exception as e:
            print(f"[transcode] ERROR: {e}", flush=True)
            cached.unlink(missing_ok=True)
            return
        if proc.returncode != 0:
            err = stderr_data.decode(errors="replace")[-300:] if stderr_data else "unknown"
            print(f"[transcode] FAILED: {err}", flush=True)
            cached.unlink(missing_ok=True)
            return

        # REAL validation of the output: ffmpeg exiting 0 is not enough — the
        # cache must actually be a playable mp4 (has video stream + duration).
        out_check = validate_video(str(cached))
        if not out_check.get("ok"):
            print(f"[transcode] OUTPUT INVALID: {out_check.get('error')} -> deleting", flush=True)
            cached.unlink(missing_ok=True)
            return
        with _probe_cache_lock:
            _probe_cache.pop(str(cached), None)
        print(f"[transcode] DONE: {cached.name} ({cached.stat().st_size/1024/1024:.0f}MB, {out_check.get('duration',0):.0f}s)", flush=True)

        # Final step: convert the fragmented MP4 (empty_moov+frag_keyframe, which
        # allowed reading the file WHILE it was being transcoded) into a STANDARD
        # MP4 with the moov atom moved to the front (faststart). Browsers start
        # playing standard-faststart files almost instantly, whereas fragmented
        # files cause a long buffering spinner before the first frame appears.
        self._finalize_standard(cached)

        # Once a verified playable exists, drop the original heavy source to save
        # disk space and keep the folder clean (library serves the playable).
        self._delete_original(cached, src)

    def _delete_original(self, cached: Path, src: Path) -> None:
        """Remove the original heavy source file once a validated .playable.mp4
        exists. The library streams the .playable.mp4, so the source (e.g. HEVC
        .mkv) is redundant and only wastes disk space."""
        if cached == src or not cached.exists():
            return
        if not src.exists():
            return
        try:
            if not validate_video(str(cached)).get("ok"):
                return
        except Exception:
            return
        try:
            size_mb = 0
            try:
                size_mb = src.stat().st_size / 1024 / 1024
            except OSError:
                pass
            src.unlink(missing_ok=True)
            with _probe_cache_lock:
                _probe_cache.pop(str(src), None)
                _probe_cache.pop(str(cached), None)
            print(f"[transcode] deleted original source to save space: {src.name} ({size_mb:.0f}MB)", flush=True)
        except OSError as e:
            print(f"[transcode] could not delete original {src.name}: {e}", flush=True)

    def _finalize_standard(self, cached) -> None:
        """Remux a fragmented playable into a standard faststart MP4 (stream copy,
        no re-encode) so the browser starts playback instantly. Fast copy, but can
        add a few seconds per GB."""
        try:
            ffmpeg = ensure_ffmpeg()
            if not ffmpeg or not cached.exists():
                return
            if cached.suffix.lower() != ".mp4":
                return
            tmp = cached.with_suffix(".mp4.final.tmp")
            tmp.unlink(missing_ok=True)
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", str(cached),
                "-c", "copy",
                "-movflags", "+faststart",
                "-max_muxing_queue_size", "4096",
                "-f", "mp4", "-y", str(tmp),
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            _, _ = proc.communicate(timeout=3600)
            if proc.returncode != 0 or not tmp.exists():
                print("[transcode] finalize-remux failed; keeping fragmented playable", flush=True)
                tmp.unlink(missing_ok=True)
                return
            os.replace(tmp, cached)
            with _probe_cache_lock:
                _probe_cache.pop(str(cached), None)
            print(f"[transcode] finalized to faststart standard: {cached.name} ({cached.stat().st_size/1024/1024:.0f}MB)", flush=True)
        except Exception as e:
            print(f"[transcode] finalize-remux error: {e}", flush=True)
            try:
                cached.with_suffix(".mp4.final.tmp").unlink(missing_ok=True)
            except Exception:
                pass


# Shared singleton used across the app (app.py, downloader.py, library.py)
transcode_manager = TranscodeManager()
