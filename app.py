import json
import re
import os
import shutil
import asyncio
import subprocess
import threading
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from modules.resolver import IMDbResolver
from modules.indexer import CustomIndexer, SAMPLE_KEYWORDS, parse_size_bytes, MIN_RELEASE_MB
from modules.downloader import DownloadManager, DEFAULT_MOVIES_DIR, check_disk_space, _clean_title_for_folder
from modules.auth import register_user, authenticate_user, create_token, verify_token
from modules.security import (
    SecurityHeadersMiddleware, general_limiter, auth_limiter,
    sanitize_search_input, sanitize_path_input,
)
from modules.library import (
    add_to_history, get_history, clear_history, mark_downloaded,
    scan_downloaded_movies, get_poster_url, clean_history, attach_posters,
    attach_genres,
)
from modules.transcoder import ensure_ffmpeg, build_transcode_cmd, detect_video_codec

app = FastAPI(title="ShowCatch")
app.add_middleware(SecurityHeadersMiddleware)


@app.on_event("startup")
async def startup_cleanup():
    clean_history()


_APP_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))

resolver = IMDbResolver()
indexer = CustomIndexer()
download_manager = DownloadManager()
settings = {"destination": DEFAULT_MOVIES_DIR}


def get_current_user(request: Request):
    token = request.cookies.get("auth_token")
    if not token:
        return None
    return verify_token(token)


def _safe_path(raw_path: str):
    try:
        p = Path(raw_path)
        if not p.is_absolute():
            return None
        if p.exists() and p.is_file():
            return p
        return None
    except Exception:
        return None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    return templates.TemplateResponse(request, "index.html", {"user": user})


@app.post("/api/register")
async def api_register(payload: dict):
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    if auth_limiter.is_limited(payload.get("_ip", "unknown")):
        return JSONResponse({"error": "Too many attempts."}, status_code=429)
    ok, msg = register_user(username, password)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    token = create_token(username)
    resp = JSONResponse({"message": "Account created", "username": username})
    resp.set_cookie(key="auth_token", value=token, httponly=True, samesite="strict", max_age=86400)
    return resp


@app.post("/api/login")
async def api_login(payload: dict):
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    if auth_limiter.is_limited(payload.get("_ip", "unknown")):
        return JSONResponse({"error": "Too many attempts."}, status_code=429)
    ok, msg = authenticate_user(username, password)
    if not ok:
        return JSONResponse({"error": msg}, status_code=401)
    token = create_token(username)
    resp = JSONResponse({"message": "Logged in", "username": username})
    resp.set_cookie(key="auth_token", value=token, httponly=True, samesite="strict", max_age=86400)
    return resp


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"message": "Logged out"})
    resp.delete_cookie("auth_token")
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"logged_in": False}, status_code=401)
    return {"logged_in": True, "username": user}


@app.post("/api/search")
async def search(payload: dict):
    raw_query = payload.get("query", "").strip()
    media_type = payload.get("media_type", "movie")
    season = payload.get("season", 0)
    episode = payload.get("episode", 0)

    if not raw_query:
        return JSONResponse({"error": "Empty query"}, status_code=400)
    if general_limiter.is_limited(payload.get("_ip", "unknown")):
        return JSONResponse({"error": "Rate limit exceeded."}, status_code=429)

    raw_query = sanitize_search_input(raw_query)
    if not raw_query:
        return JSONResponse({"error": "Invalid input"}, status_code=400)

    loop = asyncio.get_event_loop()
    resolved = await loop.run_in_executor(None, lambda: resolver.resolve_movie(raw_query, media_type))
    if not resolved:
        return JSONResponse({"error": "Movie not found on IMDb"}, status_code=404)

    imdb_id, title, year = resolved
    poster_url = get_poster_url(imdb_id)
    add_to_history(title, year, imdb_id, poster_url, media_type)

    async def _run_search():
        return await loop.run_in_executor(
            None, lambda: indexer.search_all(imdb_id, title, year, title, media_type, season, episode)
        )

    async def _run_seasons():
        if media_type != "tv":
            return []
        return await loop.run_in_executor(None, indexer.get_tv_seasons, imdb_id)

    releases, tv_seasons = await asyncio.gather(_run_search(), _run_seasons())

    return {
        "movie": {"title": title, "year": year, "imdb_id": imdb_id, "poster": poster_url, "media_type": media_type},
        "releases": releases,
        "tv_seasons": tv_seasons,
    }


@app.post("/api/download")
async def download(payload: dict):
    magnet = payload.get("magnet", "")
    title = payload.get("title", "Unknown")
    size = payload.get("size", "2.0 GB")
    imdb_id = payload.get("imdb_id", "")
    dest = settings.get("destination", DEFAULT_MOVIES_DIR)

    title = sanitize_path_input(title)
    if not magnet:
        return JSONResponse({"error": "Invalid magnet link"}, status_code=400)
    if SAMPLE_KEYWORDS.search(title):
        return JSONResponse({"error": "Blocked: sample/trailer/clip."}, status_code=400)

    size_bytes = parse_size_bytes(size)
    if 0 < size_bytes < MIN_RELEASE_MB * 1024 * 1024:
        return JSONResponse({"error": f"Blocked: too small ({size})."}, status_code=400)

    folder_name = title or "Unknown"
    movie_folder = Path(dest) / folder_name

    space = check_disk_space(movie_folder, size)
    if not space["ok"]:
        return JSONResponse({"error": f"Not enough space. Need {space['required_gb']} GB, have {space['free_gb']} GB."}, status_code=400)

    # If this release is a series episode, auto-place it into the proper
    # library series structure (<Show>/Season <n>/) so it lands in the Series
    # section automatically instead of becoming a standalone movie.
    m_ep = re.search(r'\bS(\d{1,2})\s*E(\d{1,3})\b', title, re.IGNORECASE)
    if m_ep:
        show_dir = _clean_title_for_folder(title) or (sanitize_path_input(title) or "Unknown")
        season_dir = Path(dest) / show_dir / "Season " + str(int(m_ep.group(1)))
        episode_label = f"{show_dir} - S{int(m_ep.group(1)):02d}E{int(m_ep.group(2)):02d}"
        task_id = download_manager.start_download(magnet, dest, folder_name, size,
                                                  target_folder=str(season_dir),
                                                  episode_label=episode_label)
    else:
        task_id = download_manager.start_download(magnet, dest, folder_name, size)
    if not task_id:
        return JSONResponse({"error": "This torrent is already being downloaded."}, status_code=409)
    if imdb_id:
        mark_downloaded(imdb_id)
    return {"task_id": task_id, "status": "started"}


@app.post("/api/download-season")
async def download_season(payload: dict):
    imdb_id = payload.get("imdb_id", "")
    title = payload.get("title", "")
    season = payload.get("season", 0)
    episode_count = payload.get("episode_count", 0)
    dest = settings.get("destination", DEFAULT_MOVIES_DIR)

    if not title or not season or not episode_count:
        return JSONResponse({"error": "Missing parameters"}, status_code=400)

    tasks = []
    # Organization: every downloaded episode goes straight into the proper
    # library series structure "<Show>/Season <n>/" so the library groups them
    # under the right show (movies vs series sections).
    show_dir = _clean_title_for_folder(title) or (sanitize_path_input(title) or "Unknown")
    season_dir = Path(dest) / show_dir / "Season " + str(season)
    for ep in range(1, episode_count + 1):
        releases = indexer.search_all(imdb_id, title, "", title, "tv", season, ep)
        if not releases:
            tasks.append({"title": f"{title} S{season:02d}E{ep:02d}", "error": "No releases"})
            continue

        best = releases[0]
        ep_title = sanitize_path_input(best["title"])
        size = best.get("size", "2.0 GB")
        episode_label = f"{show_dir} - S{season:02d}E{ep:02d}"
        task_id = download_manager.start_download(best["magnet"], dest, ep_title, size,
                                                  target_folder=str(season_dir),
                                                  episode_label=episode_label)
        tasks.append({"task_id": task_id, "title": ep_title})

    return {"tasks": tasks}


@app.get("/api/progress/{task_id}")
async def progress(task_id: str):
    async def event_generator():
        while True:
            data = download_manager.get_progress(task_id)
            if data is None:
                yield f"data: {json.dumps({'error': 'Task not found'})}\n\n"
                break
            yield f"data: {json.dumps(data)}\n\n"
            if data["status"] in ("completed", "error", "cancelled"):
                break
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/download/{task_id}/pause")
async def pause_download(task_id: str):
    if download_manager.pause_download(task_id):
        return {"message": "Paused", "status": "paused"}
    return JSONResponse({"error": "Cannot pause"}, status_code=400)


@app.post("/api/download/{task_id}/resume")
async def resume_download(task_id: str):
    if download_manager.resume_download(task_id):
        return {"message": "Resumed", "status": "resumed"}
    return JSONResponse({"error": "Cannot resume"}, status_code=400)


@app.post("/api/download/{task_id}/cancel")
async def cancel_download(task_id: str):
    if download_manager.cancel_download(task_id):
        return {"message": "Cancelled", "status": "cancelled"}
    return JSONResponse({"error": "Cannot cancel"}, status_code=400)


@app.get("/api/stream")
async def stream_file(request: Request, path: str):
    p = _safe_path(path)
    if not p or not p.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)

    # If the requested file is not browser-playable but a completed transcode
    # cache exists, serve that instead (library already prefers it, but be safe).
    from modules.transcoder import transcode_manager
    if transcode_manager.ready(path):
        p = transcode_manager.playable_path(path)

    file_size = p.stat().st_size
    ext = p.suffix.lower()
    mime_map = {
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
        ".mov": "video/quicktime", ".webm": "video/webm", ".m4v": "video/x-m4v",
    }
    content_type = mime_map.get(ext, "application/octet-stream")

    range_header = request.headers.get("range", "")
    start = 0
    end = file_size - 1
    status_code = 200

    if range_header.startswith("bytes="):
        parts = range_header[6:].split("-")
        try:
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
        except ValueError:
            start, end = 0, file_size - 1
        status_code = 206

    content_length = end - start + 1

    async def ranged_generator():
        CHUNK = 1024 * 1024
        with open(p, "rb") as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk_size = min(CHUNK, remaining)
                data = f.read(chunk_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    headers["Content-Length"] = str(content_length)
    return StreamingResponse(ranged_generator(), media_type=content_type, headers=headers, status_code=status_code)


@app.get("/api/transcode")
async def transcode_file(request: Request, path: str):
    from modules.transcoder import transcode_manager, needs_transcode

    p = _safe_path(path)
    if not p or not p.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)

    # If it's already browser-playable and no cache needed, stream as-is
    if not needs_transcode(path):
        return JSONResponse({"status": "ready", "message": "File is already playable in browser."})

    status = transcode_manager.status(path)
    if status["status"] == "ready":
        return JSONResponse({"status": "ready", "message": "Video is ready."})

    if status["status"] == "preparing":
        return JSONResponse(status)

    if transcode_manager.start(path):
        return JSONResponse({"status": "preparing", "message": "Video is being prepared, please wait..."})

    return JSONResponse(status)


def _get_duration(ffmpeg_path, input_path):
    try:
        result = subprocess.run(
            [ffmpeg_path, "-i", input_path],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stderr.split("\n"):
            if "Duration:" in line:
                parts = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = parts.split(":")
                return float(h) * 3600 + float(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


@app.get("/api/settings")
async def get_settings():
    return settings


@app.post("/api/settings")
async def update_settings(payload: dict):
    dest = payload.get("destination", "").strip()
    if dest:
        dest = sanitize_path_input(dest)
        if dest:
            Path(dest).mkdir(parents=True, exist_ok=True)
            settings["destination"] = dest
    return settings


@app.get("/api/tasks")
async def get_tasks():
    return download_manager.get_all_tasks()


@app.get("/api/storage")
async def get_storage():
    dest = settings.get("destination", DEFAULT_MOVIES_DIR)
    try:
        usage = shutil.disk_usage(dest)
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0, "drive": dest}
    total, used, free = usage.total, usage.used, usage.free
    percent = round((used / total) * 100, 1) if total else 0
    return {
        "total": total, "used": used, "free": free,
        "percent": percent, "drive": dest,
    }


@app.get("/api/library")
async def get_library():
    dest = settings.get("destination", DEFAULT_MOVIES_DIR)
    active = {
        task["folder"]
        for task in download_manager.get_all_tasks().values()
        if task.get("status") in ("downloading", "paused") and task.get("folder")
    }
    movies, series = scan_downloaded_movies(dest, active_folders=active)
    history = get_history()
    attach_posters(movies, series, history)
    attach_genres(movies, series)
    return {"downloaded": movies, "series": series, "history": history}


@app.get("/api/featured")
async def get_featured():
    """Featured banner items: local library (downloaded, playable) first, then
    discovered titles from search history (not yet downloaded) so the banner can
    showcase exciting/trending titles and offer a direct download action."""
    dest = settings.get("destination", DEFAULT_MOVIES_DIR)
    active = {
        task["folder"]
        for task in download_manager.get_all_tasks().values()
        if task.get("status") in ("downloading", "paused") and task.get("folder")
    }
    movies, series = scan_downloaded_movies(dest, active_folders=active)
    history = get_history()
    attach_posters(movies, series, history)
    attach_genres(movies, series)

    downloaded_ids = set()
    for m in movies:
        if m.get("imdb_id"):
            downloaded_ids.add(m["imdb_id"])
    for s in series:
        if s.get("imdb_id"):
            downloaded_ids.add(s["imdb_id"])

    featured = []
    for m in movies:
        featured.append({**m, "downloaded": True, "kind": "movie"})
    for s in series:
        featured.append({**s, "downloaded": True, "kind": "series"})

    seen = set(downloaded_ids)
    for h in sorted(history, key=lambda x: (x.get("search_count", 0), x.get("last_searched", "")), reverse=True):
        imdb = h.get("imdb_id", "")
        if not imdb or imdb in seen:
            continue
        # A history entry marked as downloaded (by mark_downloaded) is already
        # in the library banner — never re-surface it as a discovery duplicate.
        if h.get("downloaded"):
            seen.add(imdb)
            continue
        seen.add(imdb)
        featured.append({
            "title": h.get("title", ""),
            "year": h.get("year", ""),
            "imdb_id": imdb,
            "poster": h.get("poster_url", ""),
            "media_type": h.get("media_type", "movie"),
            "downloaded": False,
            "kind": "discovery",
        })
    return {"featured": featured}


@app.get("/api/discover")
async def get_discover():
    """Suggestions for an empty search: trending/popular discovered titles from
    search history that are not yet in the local library, each with a download
    action. Reuses the same source as the featured banner projections."""
    dest = settings.get("destination", DEFAULT_MOVIES_DIR)
    active = {
        task["folder"]
        for task in download_manager.get_all_tasks().values()
        if task.get("status") in ("downloading", "paused") and task.get("folder")
    }
    movies, series = scan_downloaded_movies(dest, active_folders=active)
    history = get_history()

    downloaded_ids = set()
    for m in movies:
        if m.get("imdb_id"):
            downloaded_ids.add(m["imdb_id"])
    for s in series:
        if s.get("imdb_id"):
            downloaded_ids.add(s["imdb_id"])

    seen = set(downloaded_ids)
    discover = []
    for h in sorted(history, key=lambda x: (x.get("search_count", 0), x.get("last_searched", "")), reverse=True):
        imdb = h.get("imdb_id", "")
        if not imdb or imdb in seen:
            continue
        # Never re-surface a title that has already been downloaded.
        if h.get("downloaded"):
            seen.add(imdb)
            continue
        seen.add(imdb)
        discover.append({
            "title": h.get("title", ""),
            "year": h.get("year", ""),
            "imdb_id": imdb,
            "media_type": h.get("media_type", "movie"),
            "downloaded": False,
        })
    return {"discover": discover}


@app.post("/api/library/delete")
async def api_delete_item(payload: dict):
    dest = Path(settings.get("destination", DEFAULT_MOVIES_DIR)).resolve()
    target = payload.get("path", "")
    if not target:
        return JSONResponse({"error": "Missing path"}, status_code=400)

    try:
        p = Path(target).resolve()
    except Exception:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    # SECURITY: never allow deleting anything outside the library folder.
    try:
        p.relative_to(dest)
    except ValueError:
        return JSONResponse({"error": "Path outside library"}, status_code=400)

    # Never delete the library root itself or a file directly at the root's
    # parent (folder-only movies come as a folder; root files come as the file).
    if p == dest:
        return JSONResponse({"error": "Cannot delete library root"}, status_code=400)
    if not p.exists():
        return JSONResponse({"error": "Item not found"}, status_code=404)

    # Do NOT delete a path that is inside or equal to an active download folder.
    active = [
        Path(task["folder"]).resolve()
        for task in download_manager.get_all_tasks().values()
        if task.get("status") in ("downloading", "paused") and task.get("folder")
    ]
    for act in active:
        try:
            p.relative_to(act)
            return JSONResponse({"error": "Item is currently being downloaded"}, status_code=409)
        except ValueError:
            pass

    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except Exception as e:
        return JSONResponse({"error": f"Delete failed: {e}"}, status_code=500)

    if p.exists():
        return JSONResponse({"error": "Could not fully remove item"}, status_code=500)
    return {"message": "Deleted", "deleted": str(p)}


@app.get("/api/series/{series_path:path}/seasons")
async def get_series_seasons(series_path: str):
    from modules.library import scan_series_from_folder
    p = _safe_path(series_path)
    if not p or not p.is_dir():
        return JSONResponse({"error": "Series folder not found"}, status_code=404)
    data = scan_series_from_folder(p)
    if not data:
        return JSONResponse({"error": "No episodes found"}, status_code=404)
    return data


@app.post("/api/library/clear-history")
async def api_clear_history():
    clear_history()
    return {"message": "History cleared"}


def _get_lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    import socket
    import uvicorn

    # Anti-duplicate guard: refuse to start if another instance is already
    # listening on the server port, to avoid the process chaos that previously
    # made the app unreachable with repeated "Network error".
    def _port_in_use(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                return s.connect_ex((host, port)) == 0
        except Exception:
            return False

    ip = _get_lan_ip()
    if _port_in_use("127.0.0.1", 8000):
        print("\n[guard] Port 8000 is already in use - another instance appears to be running.")
        print("[guard] Refusing to start a duplicate server. Exiting.")
        print("[guard] If the running instance is stale/unreachable, stop it first, then retry.\n")
        raise SystemExit(0)

    print("\n" + "=" * 50)
    print(" ShowCatch server")
    print(f"   On this PC:  http://localhost:8000")
    print(f"   On phone:    http://{ip}:8000   (same Wi-Fi)")
    print("=" * 50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
