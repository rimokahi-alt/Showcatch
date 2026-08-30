import re
import requests
from urllib.parse import quote_plus


class IMDbResolver:
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        self.omdb_key = "trilogy"
        self.timeout = 8

    def resolve_movie(self, raw_query: str, media_type: str = "movie") -> tuple[str, str, str] | None:
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', raw_query)
        target_year = year_match.group(1) if year_match else None
        clean_title = re.sub(r'\b(19\d{2}|20\d{2})\b|\(|\)', '', raw_query).strip()
        if not clean_title:
            return None

        result = self._resolve_omdb(clean_title, target_year, media_type)
        if result:
            return result
        return self._resolve_imdb(clean_title, target_year)

    def _resolve_omdb(self, title: str, year: str | None, media_type: str = "movie") -> tuple[str, str, str] | None:
        omdb_type = "series" if media_type == "tv" else "movie"
        try:
            params = {"s": title, "type": omdb_type, "apikey": self.omdb_key}
            resp = requests.get("https://www.omdbapi.com/", params=params, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("Response") == "True":
                    results = data.get("Search", [])
                    if year:
                        for item in results:
                            if item.get("Year", "").startswith(year):
                                return item["imdbID"], item.get("Title", title), item.get("Year", "")
                    if results:
                        best = results[0]
                        return best["imdbID"], best.get("Title", title), best.get("Year", "")

            params2 = {"t": title, "type": omdb_type, "apikey": self.omdb_key}
            if year:
                params2["y"] = year
            resp2 = requests.get("https://www.omdbapi.com/", params=params2, headers=self.headers, timeout=self.timeout)
            if resp2.status_code == 200:
                data2 = resp2.json()
                if data2.get("Response") == "True" and data2.get("imdbID"):
                    return data2["imdbID"], data2.get("Title", title), data2.get("Year", "")
        except Exception:
            pass
        return None

    def _resolve_imdb(self, title: str, year: str | None) -> tuple[str, str, str] | None:
        first_char = title.lower().replace(" ", "_")[0]
        url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{quote_plus(title.lower())}.json"
        try:
            resp = requests.get(url, headers=self.headers, timeout=5)
            if resp.status_code != 200:
                return None
            items = resp.json().get("d", [])
            pattern = re.compile(rf"\b{re.escape(title)}\b", re.IGNORECASE)
            candidates = []
            for item in items:
                imdb_id = item.get("id", "")
                t = item.get("l", "")
                y = str(item.get("y", ""))
                if imdb_id.startswith("tt") and t and pattern.search(t):
                    candidates.append((imdb_id, t, y))
            if candidates:
                if year:
                    for c in candidates:
                        if c[2] == year:
                            return c
                return candidates[0]
            for item in items:
                if item.get("id", "").startswith("tt"):
                    return item["id"], item.get("l"), str(item.get("y", ""))
        except Exception:
            pass
        return None
