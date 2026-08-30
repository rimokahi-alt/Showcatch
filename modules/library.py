import json
import re
import os
import requests
from pathlib import Path
from urllib.parse import quote_plus
from datetime import datetime, timezone

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}
SEASON_PATTERN = re.compile(r'[Ss](\d{1,2})', re.IGNORECASE)
EPISODE_PATTERN = re.compile(r'[Ee](\d{1,3})', re.IGNORECASE)
SKIP_PATTERNS = re.compile(r'sample|trailer|extras|behind.the.scenes|making.of|featurette', re.IGNORECASE)
MIN_DURATION_SECONDS = 600


def _load_history() -> list:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def _save_history(history: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def add_to_history(title: str, year: str, imdb_id: str, poster_url: str = "", media_type: str = "movie"):
    history = _load_history()
    for entry in history:
        if entry.get("imdb_id") == imdb_id:
            entry["last_searched"] = datetime.now(timezone.utc).isoformat()
            entry["search_count"] = entry.get("search_count", 0) + 1
            _save_history(history)
            return
    history.insert(0, {
        "title": title, "year": year, "imdb_id": imdb_id,
        "poster_url": poster_url, "media_type": media_type,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "last_searched": datetime.now(timezone.utc).isoformat(),
        "search_count": 1, "downloaded": False,
    })
    if len(history) > 100:
        history = history[:100]
    _save_history(history)


def mark_downloaded(imdb_id: str):
    history = _load_history()
    for entry in history:
        if entry.get("imdb_id") == imdb_id:
            entry["downloaded"] = True
            entry["downloaded_at"] = datetime.now(timezone.utc).isoformat()
            break
    _save_history(history)


def get_history() -> list:
    return _load_history()


def clear_history():
    _save_history([])


def clean_history():
    history = _load_history()
    from modules.indexer import SAMPLE_KEYWORDS
    cleaned = [e for e in history if not SAMPLE_KEYWORDS.search(e.get("title", ""))]
    if len(cleaned) != len(history):
        _save_history(cleaned)


def get_poster_url(imdb_id: str) -> str:
    try:
        first_char = imdb_id[2:3].lower() if len(imdb_id) > 2 else "t"
        url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{imdb_id}.json"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if resp.status_code == 200:
            for item in resp.json().get("d", []):
                if item.get("id") == imdb_id:
                    img = item.get("i", {})
                    if img and "imageUrl" in img:
                        return img["imageUrl"]
    except Exception:
        pass
    return ""


def _norm_title(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or "").lower())


def fetch_poster_by_title(title: str, year: str = "") -> str:
    """Look up a poster image via IMDb's public suggestion API (no key needed)."""
    query = f"{title} {year}".strip().lower()
    first_char = re.sub(r'[^a-z0-9]', '', query)[:1] or "a"
    url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{quote_plus(query)}.json"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if resp.status_code == 200:
            for item in resp.json().get("d", []):
                if item.get("id", "").startswith("tt"):
                    img = item.get("i", {})
                    if img and img.get("imageUrl"):
                        return img["imageUrl"]
    except Exception:
        pass
    return ""


def attach_posters(movies: list[dict], series: list[dict], history: list[dict]) -> None:
    """Decorate library items with poster URLs (history cache first, IMDb fallback)."""
    poster_map: dict[str, str] = {}
    for h in history:
        if h.get("poster_url"):
            poster_map[_norm_title(h.get("title", ""))] = h["poster_url"]

    # Persistent on-disk poster cache so we only hit IMDb once per title.
    poster_cache_file = Path(__file__).resolve().parent.parent / "data" / "poster_cache.json"
    disk_cache: dict[str, str] = {}
    try:
        if poster_cache_file.exists():
            disk_cache = json.loads(poster_cache_file.read_text(encoding="utf-8"))
    except Exception:
        disk_cache = {}

    def save_disk():
        try:
            poster_cache_file.parent.mkdir(parents=True, exist_ok=True)
            poster_cache_file.write_text(json.dumps(disk_cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def lookup(title: str, year: str) -> str:
        key = _norm_title(title)
        if not key:
            return ""
        if key in poster_map:
            return poster_map[key]
        if key in disk_cache:
            poster_map[key] = disk_cache[key]
            return disk_cache[key]
        clean = _imdb_query_title(title)
        if not clean or len(clean) < 2:
            return ""
        url = fetch_poster_by_title(clean, year)
        if url:
            poster_map[key] = url
            disk_cache[key] = url
            save_disk()
        return url

    for m in movies:
        m["poster"] = lookup(m.get("title", ""), m.get("year", ""))
    for s in series:
        s["poster"] = lookup(s.get("display_title") or s.get("title", ""), s.get("year", ""))


_GENRE_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "genre_cache.json"


def _load_genre_cache() -> dict:
    try:
        if _GENRE_CACHE_FILE.exists():
            return json.loads(_GENRE_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def fetch_genres(title: str, year: str = "") -> list:
    """Fetch genres for a title via IMDb/OMDB (no API key needed). Cached on disk."""
    if not title:
        return []
    clean = _imdb_query_title(title)
    if not clean or len(clean) < 2:
        return []
    key = _norm_title(clean)
    cache = _load_genre_cache()
    if key in cache:
        return cache[key]

    genres = []
    try:
        params = {"t": clean, "apikey": "trilogy", "r": "json"}
        if year:
            params["y"] = year
        resp = requests.get("https://www.omdbapi.com/", params=params,
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("Response") == "True" and data.get("Genre"):
                genres = [g.strip() for g in data["Genre"].split(",") if g.strip()]
    except Exception:
        pass

    try:
        cache[key] = genres
        _GENRE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GENRE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return genres


def attach_genres(movies: list[dict], series: list[dict]) -> None:
    """Decorate library items with an ordered list of genres (best-effort, cached)."""
    for m in movies:
        m["genres"] = fetch_genres(m.get("title", ""), m.get("year", ""))
    for s in series:
        s["genres"] = fetch_genres(s.get("display_title") or s.get("title", ""), s.get("year", ""))



def _get_video_duration(file_path: str) -> float:
    from modules.transcoder import get_video_duration
    return get_video_duration(file_path)


def _is_valid_video(file_path: Path) -> bool:
    if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    if SKIP_PATTERNS.search(file_path.stem):
        return False
    try:
        return file_path.stat().st_size > 0
    except OSError:
        return False


def _prefer_playable(fp: Path) -> Path:
    """If a .playable.mp4 cache exists, point playback at that (browser-ready)."""
    cp = fp.with_suffix(".playable.mp4")
    try:
        if cp.exists() and cp.stat().st_size > 1024:
            return cp
    except OSError:
        pass
    return fp


def _usable_playable(fp: Path):
    """Return the path to serve for browser playback, or None if the file is not
    yet playable.

    - A valid .playable.mp4 (H.264) -> that path.
    - A plain .mp4 (or already browser-compatible) -> the file itself.
    - Any other container/codec with no ready .playable.mp4 -> None (scheduled
      for transcode) so the library never surfaces an unwatchable file.
    """
    try:
        if fp.stat().st_size < 1024:
            return None
    except OSError:
        return None
    cp = fp.with_suffix(".playable.mp4")
    try:
        if cp.exists() and cp.stat().st_size > 1024:
            return cp
    except OSError:
        pass
    try:
        from modules.transcoder import read_chunk_is_playable, transcode_manager
    except Exception:
        return None
    if read_chunk_is_playable(str(fp)):
        return fp
    # Not playable yet -> schedule background transcode/remux, return None.
    try:
        if fp.suffix.lower() in VIDEO_EXTENSIONS:
            transcode_manager.start(str(fp))
    except Exception:
        pass
    return None



def _check_duration(file_path: Path) -> bool:
    duration = _get_video_duration(str(file_path))
    if duration <= 0:
        print(f"[library] Excluding corrupt/unreadable video ({duration}s): {file_path.name}")
        return False
    if duration < MIN_DURATION_SECONDS:
        print(f"[library] Excluding short video ({duration:.0f}s): {file_path.name}")
        return False
    return True


def _format_size(size_bytes: int) -> str:
    size_mb = size_bytes / (1024 * 1024)
    return f"{size_mb:.1f} MB" if size_mb < 1024 else f"{size_mb / 1024:.2f} GB"


def _extract_title_year(folder_name: str) -> tuple[str, str]:
    year_match = re.search(r'\((\d{4})\)', folder_name)
    year = year_match.group(1) if year_match else ""
    name_match = re.match(r'^(.+?)(?:\s*\(\d{4}\))?$', folder_name)
    title = name_match.group(1) if name_match else folder_name
    return title.strip(), year


def _clean_show_name(name: str) -> str:
    name = re.sub(r'[\.\-]?(1080p|720p|2160p|4k|BluRay|WEB\-?DL|HDRip|x264|x265|HEVC|AVC|DDP?\.?5\.?1|AAC|DTS|REMUX|PROPER|EXTENDED|UNRATED).*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[\.\-]?\[.*?\]', '', name)
    name = name.replace('.', ' ').replace('_', ' ').replace('-', ' ')
    return re.sub(r'\s+', ' ', name).strip()


_IMDB_TAG_RE = re.compile(
    r'\b(?:1080p|720p|2160p|480p|4k|8k|bluray|web-?dl|webrip|web|hdr10?|hdr|'
    r'x264|x265|x\s?264|x\s?265|hevc|avc|av1|h264|h265|dts-?hd|dts|aac|ac3|eac3|'
    r'ddp5\.?1|dd5\.?1|dd\s?5\.?1|5\.1|7\.1|2\.0|atmos|remux|proper|extended|'
    r'unrated|remastered|remaster|imax|10bit|8bit|hi10p|galaxyrg|galaxy|rg|yify|'
    r'tigole|q\s?xr|qxr|megusta|vppv|psa|portalgoods|bone|sujaidr|brrip|bdrip|'
    r'hdtv|2ch|stereo|mono|complete|french|eng|engsub|multi|amzn|amznweb|hdrip|'
    r'hd|repack|internal|scene|fgt|groggy|batv|evo|dtv|amazon|netflix|ddp|dd|h)\b',
    re.IGNORECASE,
)


def _imdb_query_title(name: str) -> str:
    """Aggressively clean a title into a bare movie/show name suitable for an
    IMDb suggestion lookup (drop year, episode markers, resolution/codec tags,
    release groups and any digits/symbols)."""
    s = name
    s = re.sub(r'\(?\b(?:19|20)\d{2}\b\)?', ' ', s)
    s = re.sub(r'\b\d+(?:\.\d+)?\s*(?:gb|mb|gib|mib)\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bS\d{1,2}E\d{1,3}\b|\bS\d{1,2}\b|\bSeason\s*\d+\b|\bE\d{1,3}\b', ' ', s, flags=re.IGNORECASE)
    s = _IMDB_TAG_RE.sub(' ', s)
    s = re.sub(r'[-_\.\/()\[\]]', ' ', s)
    s = re.sub(r'[^a-zA-Z\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.title()



def _detect_series_from_files(folder_path: Path) -> bool:
    for root, _, files in os.walk(folder_path):
        for f in files:
            if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                if SEASON_PATTERN.search(f) and EPISODE_PATTERN.search(f):
                    return True
    return False


def _detect_series_from_structure(folder_path: Path) -> bool:
    season_dir_pattern = re.compile(r'^[Ss]eason\s*\d+|^[Ss]\d{1,2}$', re.IGNORECASE)
    for item in folder_path.iterdir():
        if item.is_dir() and season_dir_pattern.match(item.name):
            return True
    return False


def _parse_episode_info(filename: str) -> tuple[int, int]:
    s_match = SEASON_PATTERN.search(filename)
    e_match = EPISODE_PATTERN.search(filename)
    if s_match and e_match:
        return int(s_match.group(1)), int(e_match.group(1))
    return 0, 0


def scan_series_from_folder(folder_path: Path) -> dict | None:
    episodes = []
    seasons_found = set()

    # Collect every video file plus the set of still-existing "original" stems.
    # A .playable.mp4 is only the transcode cache of an original (skip it to avoid
    # double-counting) -- but once the original heavy source has been deleted the
    # playable is the ONLY copy, so it must be scanned as the episode itself.
    all_files = []
    original_stems = set()
    for root, _, files in os.walk(folder_path):
        for f in files:
            fp = Path(root) / f
            if fp.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            all_files.append(fp)
            if not fp.stem.endswith(".playable"):
                original_stems.add(fp.stem.lower())

    for fp in all_files:
        stem = fp.stem
        if stem.endswith(".playable"):
            base = re.sub(r'\.playable$', '', stem)
            if base.lower() in original_stems:
                continue  # original still on disk -> cache copy
        if SKIP_PATTERNS.search(fp.stem):
            continue
        try:
            if fp.stat().st_size <= 0:
                continue
        except OSError:
            continue

        s_num, e_num = _parse_episode_info(fp.name)
        rel_path = fp.relative_to(folder_path)
        season_dir_match = re.match(r'[Ss]eason\s*(\d+)|[Ss](\d{1,2})', str(rel_path.parent), re.IGNORECASE)
        if season_dir_match:
            s_num = int(season_dir_match.group(1) or season_dir_match.group(2))

        if s_num > 0 and e_num > 0:
            if not _check_duration(fp):
                continue
            playable = _usable_playable(fp)
            if playable is None:
                continue
            seasons_found.add(s_num)
            try:
                size = fp.stat().st_size
            except OSError:
                size = 0
            episodes.append({
                "season": s_num, "episode": e_num,
                "name": fp.stem, "filename": fp.name,
                "path": str(playable), "relative_path": str(rel_path),
                "original_path": str(fp),
                "size": _format_size(size), "size_bytes": size,
            })

    if not episodes:
        return None

    episodes.sort(key=lambda x: (x["season"], x["episode"]))
    seasons = {}
    for ep in episodes:
        s = ep["season"]
        if s not in seasons:
            seasons[s] = []
        seasons[s].append(ep)

    title, year = _extract_title_year(folder_path.name)
    return {
        "title": _clean_show_name(folder_path.name),
        "display_title": title,
        "year": year,
        "folder": folder_path.name,
        "path": str(folder_path),
        "total_episodes": len(episodes),
        "seasons": [
            {"season": s, "episode_count": len(eps), "episodes": eps}
            for s, eps in sorted(seasons.items())
        ],
        "season_count": len(seasons_found),
    }


def scan_downloaded_movies(download_dir: str = "", active_folders: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    movies = []
    series_list = []
    active_folders = active_folders or set()

    dir_path = Path(download_dir) if download_dir else Path.home() / "Downloads" / "movies"
    if not dir_path.exists():
        return movies, series_list

    for item in dir_path.iterdir():
        if item.is_dir():
            aria2_control_files = [
                Path(root) / f
                for root, _, files in os.walk(item)
                for f in files
                if f.endswith(".aria2")
            ]
            if aria2_control_files:
                if str(item) in active_folders or item.name in active_folders:
                    continue
                # Stale control files from a finished/dead download -> clean them up
                print(f"[library] Removing {len(aria2_control_files)} stale .aria2 file(s) in: {item.name}")
                for cf in aria2_control_files:
                    try:
                        cf.unlink(missing_ok=True)
                    except OSError:
                        pass

            if _detect_series_from_files(item) or _detect_series_from_structure(item):
                series_data = scan_series_from_folder(item)
                if series_data:
                    series_list.append(series_data)
                continue

            video_files = []
            for root, _, files in os.walk(item):
                for f in files:
                    fp = Path(root) / f
                    if _is_valid_video(fp):
                        video_files.append(fp)

            if not video_files:
                continue

            video_files.sort(key=lambda x: x.stat().st_size, reverse=True)

            main_video = video_files[0]
            if not _check_duration(main_video):
                continue

            valid_videos = [v for v in video_files if _check_duration(v)]
            if not valid_videos:
                continue

            # Only surface videos that BOTH have a valid duration AND are
            # browser-playable (ready .playable.mp4 or plain browser .mp4).
            # Unusable ones are skipped here and still auto-queued for
            # background transcode/remux by _usable_playable().
            usable = [(v, _usable_playable(v)) for v in valid_videos]
            usable = [(v, p) for v, p in usable if p is not None]
            if not usable:
                continue

            valid_videos = [v for v, _ in usable]
            main_video = valid_videos[0]
            title, year = _extract_title_year(item.name)

            all_videos_data = [
                {
                    "name": v.name,
                    "path": str(p),
                    "original_path": str(v),
                    "size": _format_size(v.stat().st_size),
                }
                for v, p in usable
            ]

            playable_main = dict(usable).get(main_video, main_video)
            movies.append({
                "title": title, "year": year,
                "folder": item.name,
                "video_file": main_video.name,
                "video_path": str(playable_main),
                "original_path": str(main_video),
                "size": _format_size(main_video.stat().st_size),
                "path": str(item),
                "all_videos": all_videos_data,
            })

        elif item.is_file() and _is_valid_video(item):
            if not _check_duration(item):
                continue
            playable = _usable_playable(item)
            if playable is None:
                continue
            title, year = _extract_title_year(item.stem)
            size_str = _format_size(item.stat().st_size)
            movies.append({
                "title": title, "year": year, "folder": "",
                "video_file": item.name, "video_path": str(playable),
                "original_path": str(item),
                "size": size_str, "path": str(item.parent),
                "all_videos": [{"name": item.name, "path": str(playable), "original_path": str(item), "size": size_str}],
            })

    movies.sort(key=lambda x: x["title"])
    series_list.sort(key=lambda x: x["title"])
    return movies, series_list
