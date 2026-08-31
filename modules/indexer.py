import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from urllib.parse import quote_plus

SAMPLE_KEYWORDS = re.compile(r'sample|trailer|reel|teaser|promo|clip|behind.?the.?scenes|making.?of|featurette|preview', re.IGNORECASE)
MIN_RELEASE_MB = 200

TRACKERS = [
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


def parse_size_bytes(size_str: str) -> int:
    m = re.search(r'([\d\.]+)\s*([MGT]B)', size_str, re.I)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).upper()
    multipliers = {"MB": 1024 * 1024, "GB": 1024 ** 3, "TB": 1024 ** 4}
    return int(num * multipliers.get(unit, 0))


def build_magnet(info_hash: str, title: str) -> str:
    tr = "&".join([f"tr={quote_plus(t)}" for t in TRACKERS])
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote_plus(title)}&{tr}"


class CustomIndexer:
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        self._cache_lock = threading.Lock()
        self._cache = {}

    def _cache_get(self, key: str):
        with self._cache_lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            created, data = entry
            if time.time() - created > 300:
                del self._cache[key]
                return None
            return data

    def _cache_set(self, key: str, data):
        with self._cache_lock:
            if len(self._cache) > 128:
                self._cache.clear()
            self._cache[key] = (time.time(), data)

    def _fetch(self, url: str, timeout: int = 6, tries: int = 2, tag: str = "") -> requests.Response | None:
        connect_timeout = max(3.0, timeout - 3)
        timeout_tuple = (connect_timeout, timeout)
        for attempt in range(tries):
            try:
                resp = requests.get(url, headers=self.headers, timeout=timeout_tuple)
                print(f"[indexer] {tag or url} -> HTTP {resp.status_code}", flush=True)
                if resp.status_code == 200:
                    return resp
            except requests.RequestException as e:
                print(f"[indexer] {tag or url} error: {type(e).__name__}: {e}", flush=True)
            if attempt < tries - 1:
                time.sleep(0.5 * (attempt + 1))
        return None

    def _is_valid_quality(self, title: str) -> bool:
        bad = ["2160p", "2160", "4k", "uhd", "3d", "cam", "hdcam", "telesync", "hdts"]
        return not any(re.search(rf"\b{b}\b", title.lower()) for b in bad)

    def _is_valid_release(self, title: str, size_str: str = "") -> bool:
        if SAMPLE_KEYWORDS.search(title):
            return False
        if size_str:
            size_bytes = parse_size_bytes(size_str)
            if 0 < size_bytes < MIN_RELEASE_MB * 1024 * 1024:
                return False
        return True

    def search_piratebay(self, title: str, year: str, media_type: str = "movie", season: int = 0, episode: int = 0) -> list[dict]:
        if media_type == "tv" and season and episode:
            term = f"{title} S{season:02d}E{episode:02d}"
        elif media_type == "tv" and season:
            term = f"{title} Season {season}"
        elif media_type == "tv":
            term = title
        else:
            term = f"{title} {year}".strip()
        # Multiple TPB API mirrors for resilience (some get blocked per-region).
        tpb_hosts = [
            "https://apibay.org",
            "https://apibay.unblockit.cam",
            "https://apibay.org",
        ]
        try:
            for host in tpb_hosts:
                resp = self._fetch(f"{host}/q.php?q={quote_plus(term)}", tag="apibay")
                if not resp:
                    continue
                results = []
                for t in resp.json():
                    name = t.get("name", "")
                    info_hash = t.get("info_hash", "")
                    seeders = int(t.get("seeders", 0))
                    size_bytes = int(t.get("size", 0))
                    if not info_hash or info_hash == "0" * 40 or seeders <= 0:
                        continue
                    size_gb = size_bytes / (1024 ** 3)
                    if size_gb > 14.0:
                        continue
                    if self._is_valid_quality(name):
                        results.append({"title": name, "seeders": seeders, "size": f"{size_gb:.2f} GB", "indexer": "ThePirateBay", "magnet": build_magnet(info_hash, name)})
                if results:
                    return results
            print(f"[indexer] apibay returned nothing for '{term}'", flush=True)
            return []
        except Exception as e:
            print(f"[indexer] apibay error: {type(e).__name__}: {e}", flush=True)
            return []

    def search_torrentio(self, imdb_id: str, media_type: str = "movie", season: int = 0, episode: int = 0) -> list[dict]:
        # Multiple mirrors for resilience: some become unreachable from some
        # regions/datacenters, so try the next one before giving up.
        if media_type == "tv" and season and episode:
            path = f"/stream/{media_type}/{imdb_id}:{season}:{episode}.json"
        else:
            path = f"/stream/{media_type}/{imdb_id}.json"
        hosts = [
            "https://torrentio.strem.fun",
            "https://torrentio.strem.fun",
            "https://torrentio.biaky.workers.dev",
            "https://torrentio.netsc.datasabbir.workers.dev",
            "https://torrentio.run",
        ]
        for host in hosts:
            url = host + path
            try:
                resp = self._fetch(url, tag="torrentio")
                if not resp:
                    continue
                results = []
                for stream in resp.json().get("streams", []):
                    info_hash = stream.get("infoHash")
                    title_raw = stream.get("title", "")
                    if not info_hash:
                        continue
                    lines = title_raw.split("\n")
                    release_name = lines[0] if lines else "Unknown"
                    seeders = 0
                    size_str = "Unknown"
                    indexer = "Torrentio"
                    if len(lines) > 1:
                        details = lines[1]
                        sm = re.search(r'(\d+)', details)
                        if sm:
                            seeders = int(sm.group(1))
                        zm = re.search(r'([\d\.]+\s*[MGT]B)', details, re.I)
                        if zm:
                            size_str = zm.group(1)
                        im = re.search(r'⚙️\s*([^\n]+)', details)
                        if im:
                            indexer = im.group(1).strip()
                    if seeders <= 0:
                        continue
                    if self._is_valid_quality(release_name):
                        results.append({"title": release_name, "seeders": seeders, "size": size_str, "indexer": indexer, "magnet": build_magnet(info_hash, release_name)})
                if results:
                    return results
            except Exception as e:
                print(f"[indexer] torrentio {host} failed: {type(e).__name__}: {e}", flush=True)
                continue
        return []

    def search_yts(self, imdb_id: str) -> list[dict]:
        try:
            resp = self._fetch(f"https://yts.mx/api/v2/list_movies.json?query_term={imdb_id}")
            if not resp:
                return []
            movies = resp.json().get("data", {}).get("movies", [])
            if not movies:
                return []
            results = []
            for t in movies[0].get("torrents", []):
                quality = t.get("quality", "")
                if quality not in ("720p", "1080p"):
                    continue
                info_hash = t.get("hash")
                seeds = t.get("seeds", 0)
                title = f"{movies[0].get('title')} ({movies[0].get('year')}) [{quality}] [YTS]"
                if seeds <= 0 or not info_hash:
                    continue
                results.append({"title": title, "seeders": seeds, "size": t.get("size", "N/A"), "indexer": "YTS", "magnet": build_magnet(info_hash, title)})
            return results
        except Exception:
            return []

    def get_tv_seasons(self, imdb_id: str) -> list[dict]:
        urls = [
            f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json",
            f"https://torrentio.strem.fun/meta/{imdb_id}.json",
        ]
        for url in urls:
            try:
                resp = requests.get(url, headers=self.headers, timeout=10)
                if resp.status_code != 200:
                    continue
                episodes = resp.json().get("meta", {}).get("videos", [])
                if not episodes:
                    continue
                seasons = {}
                for ep in episodes:
                    s = ep.get("season", 0)
                    if s <= 0:
                        continue
                    if s not in seasons:
                        seasons[s] = {"season": s, "episode_count": 0, "title": ep.get("name", f"Season {s}")}
                    seasons[s]["episode_count"] = max(seasons[s]["episode_count"], ep.get("episode", 0))
                if seasons:
                    return sorted(seasons.values(), key=lambda x: x["season"])
            except Exception:
                continue
        return []

    def search_all(self, imdb_id: str, title: str, year: str, search_word: str, media_type: str = "movie", season: int = 0, episode: int = 0) -> list[dict]:
        cache_key = f"{imdb_id}|{media_type}|{season}|{episode}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        jobs = {
            "torrentio": lambda: self.search_torrentio(imdb_id, media_type, season, episode),
            "piratebay": lambda: self.search_piratebay(title, year, media_type, season, episode),
        }
        if media_type == "movie":
            jobs["yts"] = lambda: self.search_yts(imdb_id)

        releases = []
        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futures = {ex.submit(fn): name for name, fn in jobs.items()}
            for fut in as_completed(futures):
                try:
                    releases.extend(fut.result())
                except Exception as e:
                    print(f"[indexer] {fut} failed: {type(e).__name__}: {e}", flush=True)
                    continue
        print(f"[indexer] search_all({media_type} s={season} e={episode}) raw releases={len(releases)}", flush=True)

        # Build a forgiving match pattern. Torrentio already filters by the
        # exact episode when season/episode are given, so we must NOT re-filter
        # too strictly or we drop everything that has a loose title format.
        if media_type == "tv" and season and episode:
            pattern = re.compile(
                rf"(?:S{season:02d}E{episode:02d}|{season}x{episode}|"
                rf"S{season}E{episode}|Season\s*{season}\s*E(?:p\.?\s*)?{episode}|"
                rf"episode\s*{episode}\s*\(season\s*{season}\))",
                re.IGNORECASE,
            )
        elif media_type == "tv" and season:
            pattern = re.compile(rf"(?:S{season:02d}|Season\s*{season}|{season}x\d+)", re.IGNORECASE)
        else:
            pattern = re.compile(rf"\b{re.escape(search_word)}\b", re.IGNORECASE)

        seen = set()
        filtered = []
        for r in releases:
            if r["magnet"] in seen or r.get("seeders", 0) <= 0:
                continue
            normalized = re.sub(r'[\._\-]', ' ', r["title"])
            if pattern.search(normalized) and self._is_valid_quality(r["title"]) and self._is_valid_release(r["title"], r.get("size", "")):
                seen.add(r["magnet"])
                filtered.append(r)

        filtered.sort(key=lambda x: x["seeders"], reverse=True)

        # Graceful fallback: if strict matching dropped everything but we did
        # receive releases, return a de-duplicated top list so the user is never
        # stuck on a dead-end "No matching releases" screen just because the
        # titles happened to not match the pattern exactly.
        if not filtered and releases:
            seen = set()
            for r in sorted(releases, key=lambda x: x.get("seeders", 0), reverse=True):
                if r["magnet"] in seen or r.get("seeders", 0) <= 0:
                    continue
                if not self._is_valid_quality(r["title"]) or not self._is_valid_release(r["title"], r.get("size", "")):
                    continue
                seen.add(r["magnet"])
                filtered.append(r)
            filtered = filtered[:30]

        # Never cache an empty result: an indexer hiccup on the first (cold)
        # search would otherwise lock in "no releases" for minutes.
        if filtered:
            self._cache_set(cache_key, filtered)
        return filtered
