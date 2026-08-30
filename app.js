// ===== Language =====
let currentLang = localStorage.getItem("lang") || "en";
let translations = {};

async function loadLang(lang) {
    try {
        const resp = await fetch(`/static/lang/${lang}.json`);
        translations = await resp.json();
        currentLang = lang;
        localStorage.setItem("lang", lang);
        applyTranslations();
        document.documentElement.lang = lang;
        document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    } catch (e) {}
}

function t(key, params = {}) {
    let text = translations[key] || key;
    for (const [k, v] of Object.entries(params)) text = text.replace(`{${k}}`, v);
    return text;
}

function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.getAttribute("data-i18n");
        if (translations[key]) el.textContent = translations[key];
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
        const key = el.getAttribute("data-i18n-placeholder");
        if (translations[key]) el.placeholder = translations[key];
    });
}

// ===== Theme =====
let currentTheme = localStorage.getItem("theme") || "dark";
function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
    currentTheme = theme;
    document.getElementById("themeToggle").innerHTML = theme === "dark" ? "&#9788;" : "&#9790;";
}

// ===== DOM =====
const $ = id => document.getElementById(id);
const searchInput = $("searchInput"), searchBtn = $("searchBtn"), searchClearBtn = $("searchClearBtn"), loading = $("loading");
const errorBox = $("errorBox"), errorText = $("errorText");
const movieInfo = $("movieInfo"), movieTitle = $("movieTitle"), movieMeta = $("movieMeta"), moviePoster = $("moviePoster");
const releasesSection = $("releasesSection"), releasesBody = $("releasesBody"), releaseCount = $("releaseCount"), resultsCloseBtn = $("resultsCloseBtn");
const downloadsList = $("downloadsList");
const settingsBtn = $("settingsBtn"), settingsModal = $("settingsModal");
const modalOverlay = $("modalOverlay"), modalClose = $("modalClose"), modalCancel = $("modalCancel"), modalSave = $("modalSave");
const destInput = $("destInput"), currentDest = $("currentDest");
const themeToggle = $("themeToggle"), langSelect = $("langSelect"), homeBtn = $("homeBtn");
const loginBtn = $("loginBtn"), logoutBtn = $("logoutBtn"), userMenu = $("userMenu"), usernameDisplay = $("usernameDisplay");
const authModal = $("authModal"), authOverlay = $("authOverlay"), authClose = $("authClose");
const authModalTitle = $("authModalTitle"), authUsername = $("authUsername"), authPassword = $("authPassword");
const authSubmit = $("authSubmit"), authSwitch = $("authSwitch"), authError = $("authError"), authErrorText = $("authErrorText");
const tvSelectors = $("tvSelectors"), seasonSelect = $("seasonSelect"), episodeSelect = $("episodeSelect");
const batchDownloadBtn = $("batchDownloadBtn"), batchStatus = $("batchStatus"), batchProgressText = $("batchProgressText"), batchProgressFill = $("batchProgressFill");
const playerModal = $("playerModal"), playerOverlay = $("playerOverlay"), playerClose = $("playerClose");
const playerTitle = $("playerTitle"), videoPlayer = $("videoPlayer"), playerInfo = $("playerInfo");
const seriesDetail = $("seriesDetail"), seriesBackBtn = $("seriesBackBtn"), seriesHeader = $("seriesHeader");
const seasonTabs = $("seasonTabs"), episodeList = $("episodeList");

let currentReleases = [], activeTasks = {}, currentMediaType = "movie", tvSeasons = [];
let currentSeriesData = null, currentSeasonEpisodes = [], currentImdbId = "";
let _searchToken = 0;

function showError(msg) { errorText.textContent = msg; errorBox.classList.remove("hidden"); }

function showToast(message, type = "success", duration = 3200) {
    let container = document.querySelector(".toast-container");
    if (!container) { container = document.createElement("div"); container.className = "toast-container"; document.body.appendChild(container); }
    const toast = document.createElement("div");
    toast.className = "toast toast-" + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => { toast.classList.add("toast-out"); setTimeout(() => toast.remove(), 350); }, duration);
}
function hideError() { errorBox.classList.add("hidden"); }
function showLoading() { loading.classList.remove("hidden"); }
function hideLoading() { loading.classList.add("hidden"); }
function getIP() { return "web-" + Math.random().toString(36).slice(2, 10); }

// ===== Section Nav =====
let _currentSection = "search";
function switchSection(section) {
    _currentSection = section;
    document.querySelectorAll(".page-section").forEach(s => s.classList.remove("active"));
    document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
    if (section === "search") {
        $("searchPage").classList.add("active");
        document.querySelector('[data-section="search"]').classList.add("active");
    } else {
        $("libraryPage").classList.add("active");
        document.querySelector('[data-section="library"]').classList.add("active");
        seriesDetail.classList.add("hidden");
        loadLibrary();
    }
    updateHeroVisibility();
}

function updateSearchClear() {
    if (searchClearBtn) searchClearBtn.classList.toggle("hidden", (searchInput.value || "").trim() === "");
}

function resetSearch() {
    _searchToken++;
    switchSection("search");
    searchInput.value = "";
    updateSearchClear();
    hideError();
    hideBatchStatus();
    hideBatchButton();
    movieInfo.classList.add("hidden");
    releasesSection.classList.add("hidden");
    if (resultsCloseBtn) resultsCloseBtn.classList.add("hidden");
    tvSelectors.classList.add("hidden");
}

homeBtn.addEventListener("click", resetSearch);
searchClearBtn.addEventListener("click", () => { resetSearch(); searchInput.focus(); });
searchInput.addEventListener("input", updateSearchClear);

document.querySelectorAll(".nav-tab").forEach(tab => {
    tab.addEventListener("click", () => switchSection(tab.dataset.section));
});

// ===== Media Type =====
document.querySelectorAll(".type-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        _searchToken++;
        document.querySelectorAll(".type-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentMediaType = btn.dataset.type;
        movieInfo.classList.add("hidden");
        releasesSection.classList.add("hidden");
        if (currentMediaType === "tv") populateTVDropdowns();
        else tvSelectors.classList.add("hidden");
    });
});

function populateTVDropdowns() {
    seasonSelect.innerHTML = '<option value="0">--</option>';
    for (let i = 1; i <= 10; i++) {
        const opt = document.createElement("option");
        opt.value = i;
        opt.textContent = "S" + i;
        seasonSelect.appendChild(opt);
    }
    episodeSelect.innerHTML = '<option value="0">--</option>';
    for (let i = 1; i <= 30; i++) {
        const opt = document.createElement("option");
        opt.value = i;
        opt.textContent = "E" + i;
        episodeSelect.appendChild(opt);
    }
    tvSelectors.classList.remove("hidden");
}

seasonSelect.addEventListener("change", () => {
    const s = parseInt(seasonSelect.value);
    if (s > 0) {
        showBatchButton(s);
        searchMovie();
    } else {
        hideBatchButton();
    }
});

episodeSelect.addEventListener("change", () => {
    if (parseInt(episodeSelect.value) > 0) searchMovie();
});

// ===== Search =====
async function searchMovie() {
    const query = searchInput.value.trim();
    if (!query) return;
    const token = ++_searchToken;
    hideError(); showLoading();
    movieInfo.classList.add("hidden");
    releasesSection.classList.add("hidden");

    const season = currentMediaType === "tv" ? parseInt(seasonSelect.value) || 0 : 0;
    const episode = currentMediaType === "tv" ? parseInt(episodeSelect.value) || 0 : 0;

    try {
        const resp = await fetch("/api/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query, media_type: currentMediaType, season, episode, _ip: getIP() })
        });
        const data = await resp.json();
        if (token !== _searchToken) return;
        if (!resp.ok) { showError(data.error || "Search failed"); hideLoading(); return; }

        movieTitle.textContent = data.movie.title + " (" + data.movie.year + ")";
        movieMeta.textContent = "IMDb: " + data.movie.imdb_id + " | " + (data.movie.media_type === "tv" ? "TV Show" : "Movie");
        currentImdbId = data.movie.imdb_id || "";

        if (data.movie.poster) { moviePoster.src = data.movie.poster; moviePoster.style.display = "block"; }
        else { moviePoster.style.display = "none"; }
        movieInfo.classList.remove("hidden");

        if (data.tv_seasons && data.tv_seasons.length > 0) {
            tvSeasons = data.tv_seasons;
            showBatchButton(parseInt(seasonSelect.value) || 1);
        }

        currentReleases = data.releases;
        renderReleases(data.releases);
        releasesSection.classList.remove("hidden");
        if (resultsCloseBtn) resultsCloseBtn.classList.remove("hidden");
    } catch (e) { if (token === _searchToken) showError(t("network_error")); }
    if (token === _searchToken) hideLoading();
}

function renderReleases(releases) {
    releasesBody.innerHTML = "";
    if (releases.length === 0) { releaseCount.textContent = t("no_results"); return; }
    releaseCount.textContent = t("releases_found", { count: releases.length });

    releases.slice(0, 20).forEach((r, i) => {
        const tr = document.createElement("tr");
        const dt = r.title.length > 50 ? r.title.slice(0, 47) + "..." : r.title;
        const safeTitle = r.title.replace(/"/g, "&quot;");
        tr.innerHTML = '<td>' + i + '</td><td title="' + safeTitle + '">' + dt + '</td><td>' + (r.size || "N/A") + '</td><td class="seeds">' + r.seeders + '</td><td>' + r.indexer + '</td><td><button class="download-btn" data-index="' + i + '">' + t("download") + '</button></td>';
        releasesBody.appendChild(tr);
    });

    document.querySelectorAll(".download-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            const idx = parseInt(e.target.dataset.index);
            startDownload(currentReleases[idx]);
            e.target.disabled = true;
            e.target.textContent = t("downloading");
        });
    });
}

// ===== Download =====
async function startDownload(release) {
    try {
        const resp = await fetch("/api/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ magnet: release.magnet, title: release.title, size: release.size, imdb_id: currentImdbId })
        });
        const data = await resp.json();
        if (!resp.ok) { showError(data.error || "Download failed"); return; }
        addDownloadCard(data.task_id, release.title);
        listenProgress(data.task_id);
    } catch (e) { showError(t("network_error")); }
}

function addDownloadCard(taskId, title) {
    const emptyState = downloadsList.querySelector(".empty-state");
    if (emptyState) emptyState.remove();
    const card = document.createElement("div");
    card.className = "download-card";
    card.id = "task-" + taskId;
    card.innerHTML = '<div class="download-card-header"><span class="download-card-title">' + title + '</span><span class="download-status status-downloading">' + t("downloading") + '</span></div><div class="progress-bar"><div class="progress-fill"></div></div><div class="progress-label"><span class="progress-pct">0%</span><span class="progress-speed">0 B/s</span></div><div class="download-meta"><span class="peers">0 ' + t("peers") + '</span><span class="eta">' + t("eta") + ': --</span></div><div class="download-controls"><button class="ctrl-btn pause-btn" data-task="' + taskId + '">&#10074;&#10074; Pause</button><button class="ctrl-btn resume-btn hidden" data-task="' + taskId + '">&#9654; Resume</button><button class="ctrl-btn cancel-btn" data-task="' + taskId + '">&#10005; Cancel</button></div>';
    downloadsList.prepend(card);
    activeTasks[taskId] = card;

    card.querySelector(".pause-btn").addEventListener("click", () => pauseTask(taskId));
    card.querySelector(".resume-btn").addEventListener("click", () => resumeTask(taskId));
    card.querySelector(".cancel-btn").addEventListener("click", () => cancelTask(taskId));
    releasesSection.classList.add("hidden");
    if (resultsCloseBtn) resultsCloseBtn.classList.add("hidden");
    updateFloatDownloads();
}

function listenProgress(taskId) {
    const es = new EventSource("/api/progress/" + taskId);
    es.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const card = activeTasks[taskId];
        if (!card) return;
        const fill = card.querySelector(".progress-fill");
        const peers = card.querySelector(".peers");
        const eta = card.querySelector(".eta");
        const status = card.querySelector(".download-status");
        const labelPct = card.querySelector(".progress-pct");
        const labelSpeed = card.querySelector(".progress-speed");

        fill.style.width = data.progress + "%";
        peers.textContent = data.peers + " " + t("peers");
        eta.textContent = t("eta") + ": " + data.eta;

        // Show exact percentage + real-time speed together on the progress bar
        if (labelPct) labelPct.textContent = Math.round(data.progress) + "%";
        if (labelSpeed) labelSpeed.textContent = data.status === "downloading" ? data.speed : "";

        if (data.status === "completed") {
            status.textContent = "\u2713 " + t("completed");
            status.className = "download-status status-completed";
            fill.style.background = "var(--success)";
            fill.style.width = "100%";
            if (labelPct) labelPct.textContent = "100%";
            if (labelSpeed) labelSpeed.textContent = "Done";
            es.close();
            hideControls(card);
            addDismissButton(card, taskId);
            scheduleAutoHide(card, taskId, 4000);
        } else if (data.status === "error") {
            status.textContent = t("error") + ": " + (data.error || "Unknown");
            status.className = "download-status status-error";
            fill.style.background = "var(--error)";
            if (labelSpeed) labelSpeed.textContent = "Failed";
            es.close();
            hideControls(card);
            addDismissButton(card, taskId);
            scheduleAutoHide(card, taskId, 6000);
        } else if (data.status === "downloading") {
            status.textContent = t("downloading");
            status.className = "download-status status-downloading";
        } else if (data.status === "paused") {
            status.textContent = "Paused";
            status.className = "download-status status-paused";
            if (labelSpeed) labelSpeed.textContent = "Paused";
            es.close();
            const pauseBtn = card.querySelector(".pause-btn");
            const resumeBtn = card.querySelector(".resume-btn");
            if (pauseBtn) pauseBtn.classList.add("hidden");
            if (resumeBtn) resumeBtn.classList.remove("hidden");
        } else if (data.status === "cancelled") {
            status.textContent = "Cancelled";
            status.className = "download-status status-error";
            fill.style.background = "var(--error)";
            if (labelSpeed) labelSpeed.textContent = "Cancelled";
            es.close();
            hideControls(card);
            setTimeout(() => { card.classList.add("fading"); }, 2000);
            setTimeout(() => { card.remove(); delete activeTasks[taskId]; updateFloatDownloads(); }, 2500);
        }
    };
    let retryCount = 0;
    es.onerror = () => {
        es.close();
        const card = activeTasks[taskId];
        if (!card) return;
        const status = card.querySelector(".download-status");
        const currentStatus = status ? status.textContent : "";
        if (currentStatus.includes(t("completed")) || currentStatus.includes("Done") || currentStatus === "Cancelled") return;
        retryCount++;
        if (retryCount > 10) {
            status.textContent = "Connection lost";
            status.className = "download-status status-error";
            return;
        }
        setTimeout(() => {
            if (activeTasks[taskId]) {
                listenProgress(taskId);
            }
        }, 2000);
    };
}

function hideControls(card) {
    const ctrls = card.querySelector(".download-controls");
    if (ctrls) ctrls.style.display = "none";
}

function scheduleAutoHide(card, taskId, delay) {
    setTimeout(() => {
        if (!activeTasks[taskId] || !card.isConnected) return;
        card.classList.add("fading");
        setTimeout(() => { card.remove(); delete activeTasks[taskId]; updateFloatDownloads(); }, 500);
    }, delay || 4000);
}

function addDismissButton(card, taskId) {
    const ctrls = card.querySelector(".download-controls");
    if (!ctrls) return;
    ctrls.style.display = "flex";
    ctrls.innerHTML = "";
    const btn = document.createElement("button");
    btn.className = "ctrl-btn cancel-btn";
    btn.textContent = "\u2715 Dismiss";
    btn.addEventListener("click", () => {
        card.classList.add("fading");
        setTimeout(() => { card.remove(); delete activeTasks[taskId]; updateFloatDownloads(); }, 500);
    });
    ctrls.appendChild(btn);
}

// ===== Global Floating Downloads Panel =====
let _floatCollapsed = localStorage.getItem("floatCollapsed") === "1";

function updateFloatDownloads() {
    const panel = $("floatDownloads");
    if (!panel) return;
    const empty = Object.keys(activeTasks).length === 0;
    panel.classList.toggle("hidden", empty);
    document.body.classList.toggle("has-downloads-banner", !empty);
}

function toggleFloatDownloads(collapse) {
    const body = $("floatDownloadsBody");
    const toggle = $("floatDownloadsToggle");
    const closed = collapse !== undefined ? collapse : !_floatCollapsed;
    _floatCollapsed = closed;
    localStorage.setItem("floatCollapsed", closed ? "1" : "0");
    if (body) body.classList.toggle("collapsed", closed);
    if (toggle) toggle.innerHTML = closed ? "&#9650;" : "&#9660;";
}

document.addEventListener("click", (e) => {
    const hdr = e.target.closest("#floatDownloadsHeader");
    if (hdr) toggleFloatDownloads();
});

async function pauseTask(taskId) {
    try {
        const resp = await fetch("/api/download/" + taskId + "/pause", { method: "POST" });
        if (resp.ok) {
            const card = activeTasks[taskId];
            if (card) {
                const status = card.querySelector(".download-status");
                status.textContent = "Pausing...";
            }
        }
    } catch (e) {}
}

async function resumeTask(taskId) {
    try {
        const resp = await fetch("/api/download/" + taskId + "/resume", { method: "POST" });
        if (resp.ok) {
            const card = activeTasks[taskId];
            if (card) {
                const status = card.querySelector(".download-status");
                status.textContent = "Resuming...";
                status.className = "download-status status-downloading";
                const pauseBtn = card.querySelector(".pause-btn");
                const resumeBtn = card.querySelector(".resume-btn");
                if (pauseBtn) pauseBtn.classList.remove("hidden");
                if (resumeBtn) resumeBtn.classList.add("hidden");
                listenProgress(taskId);
            }
        }
    } catch (e) {}
}

async function cancelTask(taskId) {
    try {
        const resp = await fetch("/api/download/" + taskId + "/cancel", { method: "POST" });
        if (resp.ok) {
            const card = activeTasks[taskId];
            if (card) {
                const status = card.querySelector(".download-status");
                status.textContent = "Cancelling...";
            }
        }
    } catch (e) {}
}

// ===== Batch Download =====
function showBatchButton(season) {
    if (!batchDownloadBtn) return;
    batchDownloadBtn.classList.remove("hidden");
    batchDownloadBtn.textContent = t("download_season") || "Download Season";
    batchDownloadBtn.onclick = () => batchDownloadSeason(season);
}

function hideBatchButton() {
    if (batchDownloadBtn) batchDownloadBtn.classList.add("hidden");
}

function hideBatchStatus() {
    if (batchStatus) batchStatus.classList.add("hidden");
}

async function batchDownloadSeason(season) {
    const showTitle = movieTitle ? movieTitle.textContent.replace(/\s*\(\d{4}\)$/, "") : "";
    const imdbMeta = movieMeta ? movieMeta.textContent : "";
    const imdbMatch = imdbMeta.match(/IMDb:\s*(tt\d+)/);
    const imdbId = imdbMatch ? imdbMatch[1] : "";
    const seasonData = tvSeasons.find(x => x.season === season);
    if (!seasonData) return;

    batchDownloadBtn.disabled = true;
    batchDownloadBtn.textContent = "Searching...";
    batchStatus.classList.remove("hidden");
    batchProgressText.textContent = "Starting batch download...";
    batchProgressFill.style.width = "0%";

    try {
        const resp = await fetch("/api/download-season", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                imdb_id: imdbId,
                title: showTitle,
                season: season,
                episode_count: seasonData.episode_count,
                _ip: getIP()
            })
        });
        const data = await resp.json();
        if (!resp.ok) {
            showError(data.error || "Batch download failed");
            batchDownloadBtn.disabled = false;
            hideBatchStatus();
            return;
        }

        const tasks = data.tasks || [];
        let done = 0;

        for (const task of tasks) {
            if (task.task_id) {
                addDownloadCard(task.task_id, task.title);
                listenProgress(task.task_id);
            }
            done++;
            batchProgressText.textContent = `Queued ${done}/${tasks.length} episodes`;
            batchProgressFill.style.width = Math.round((done / tasks.length) * 100) + "%";
        }

        batchProgressText.textContent = `All ${tasks.length} episodes queued!`;
        batchProgressFill.style.width = "100%";
        batchDownloadBtn.textContent = "Downloaded " + tasks.length + " episodes";
        setTimeout(() => { hideBatchStatus(); batchDownloadBtn.disabled = false; }, 5000);
    } catch (e) {
        showError(t("network_error"));
        batchDownloadBtn.disabled = false;
        hideBatchStatus();
    }
}

// ===== Library =====
let _libraryData = { downloaded: [], series: [] };
let _activeFilter = "all";
let _activeGenre = "";

async function loadLibrary() {
    try {
        const resp = await fetch("/api/library");
        const data = await resp.json();
        _libraryData = { downloaded: data.downloaded || [], series: data.series || [] };
        buildGenrePills();
        renderLibraryViews();
        loadFeatured();
        loadStorage();
    } catch (e) {}
}

function renderLibraryViews() {
    renderDownloadedMovies(_libraryData.downloaded);
    renderSeries(_libraryData.series);
    applyLibraryFilters();
}

function buildGenrePills() {
    const container = $("genrePills");
    if (!container) return;
    if (_activeGenre && !genres().includes(_activeGenre)) _activeGenre = "";
    const codes = genres();
    container.innerHTML = codes.map(g =>
        '<button class="pill genre-pill' + (g === _activeGenre ? " active" : "") + '" data-genre="' + g + '">' + g + '</button>'
    ).join("");
    if (codes.length) container.classList.remove("hidden");
    else container.classList.add("hidden");
}

function genres() {
    const set = new Set();
    _libraryData.downloaded.forEach(m => (m.genres || []).forEach(g => set.add(g)));
    _libraryData.series.forEach(s => (s.genres || []).forEach(g => set.add(g)));
    return Array.from(set).sort();
}

function resetFilter() {
    _activeGenre = "";
    document.querySelectorAll("#genrePills .genre-pill").forEach(p => p.classList.remove("active"));
    applyLibraryFilters();
}

function makePoster(url, fallbackEmoji, onPlay) {
    const wrap = document.createElement("div");
    wrap.className = "library-card-poster-wrap";
    let poster;
    if (!url) {
        poster = document.createElement("div");
        poster.className = "library-card-poster";
        poster.textContent = fallbackEmoji;
    } else {
        poster = document.createElement("img");
        poster.className = "library-card-poster";
        poster.loading = "lazy";
        poster.alt = "";
        poster.src = url;
        poster.addEventListener("error", () => {
            const d = document.createElement("div");
            d.className = "library-card-poster";
            d.textContent = fallbackEmoji;
            poster.replaceWith(d);
        });
    }
    wrap.appendChild(poster);

    if (onPlay) {
        const overlay = document.createElement("div");
        overlay.className = "card-overlay";
        const play = document.createElement("button");
        play.className = "overlay-play";
        play.innerHTML = "&#9654;";
        play.setAttribute("aria-label", "Play");
        play.addEventListener("click", (e) => { e.stopPropagation(); onPlay(); });
        overlay.appendChild(play);
        wrap.appendChild(overlay);
    }
    return wrap;
}

function makeCardInfo(title, metaText) {
    const info = document.createElement("div");
    info.className = "library-card-info";
    const t = document.createElement("div");
    t.className = "library-card-title";
    t.textContent = title;
    t.title = title;
    const m = document.createElement("div");
    m.className = "library-card-meta";
    m.textContent = metaText;
    info.append(t, m);
    return info;
}

function makeBadge(text) {
    const b = document.createElement("span");
    b.className = "library-card-badge badge-downloaded";
    b.textContent = text;
    return b;
}

function makeDeleteButton(targetPath) {
    const btn = document.createElement("button");
    btn.className = "delete-btn";
    btn.title = "Delete";
    btn.innerHTML = "&#128465;";
    btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await performDelete(targetPath);
    });
    return btn;
}

async function performDelete(targetPath) {
    if (!confirm("Are you sure you want to delete this?")) return;
    try {
        const resp = await fetch("/api/library/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: targetPath })
        });
        const data = await resp.json();
        if (!resp.ok) { showToast(data.error || "Delete failed", "error"); return; }
        showToast("Deleted", "success");
        loadLibrary();
    } catch (err) { showToast(t("network_error"), "error"); }
}

// ===== Card "..." overflow menu (Netflix style) =====
let _openCardMenu = null;
function closeCardMenus() {
    if (_openCardMenu) { _openCardMenu.classList.remove("open"); _openCardMenu = null; }
}
document.addEventListener("click", () => closeCardMenus());

function makeCardMenu(items) {
    const btn = document.createElement("button");
    btn.className = "card-menu-btn";
    btn.title = "Menu";
    btn.setAttribute("aria-label", "Menu");
    btn.innerHTML = "&#8942;";
    const menu = document.createElement("div");
    menu.className = "card-menu";
    items.forEach(it => {
        const mi = document.createElement("button");
        mi.className = "card-menu-item" + (it.danger ? " danger" : "");
        mi.innerHTML = it.label;
        mi.addEventListener("click", (e) => {
            e.stopPropagation();
            closeCardMenus();
            it.action && it.action();
        });
        menu.appendChild(mi);
    });
    btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const wasOpen = menu.classList.contains("open");
        closeCardMenus();
        if (!wasOpen) { menu.classList.add("open"); _openCardMenu = menu; }
    });
    const host = document.createElement("div");
    host.className = "card-menu-wrap";
    host.appendChild(btn);
    host.appendChild(menu);
    return host;
}

function makeEmptyState(text, emoji, sub) {
    const box = document.createElement("div");
    box.className = "empty-state";
    box.style.width = "100%";
    box.innerHTML = '<div class="empty-icon">' + emoji + '</div><div class="empty-title">' + text + '</div>'
        + (sub ? '<div class="empty-sub">' + sub + '</div>' : "");
    return box;
}

function renderDownloadedMovies(movies) {
    const row = $("downloadedList");
    row.innerHTML = "";
    const list = movies || [];
    if (!list.length) { row.appendChild(makeEmptyState(t("no_downloaded"), "&#127916;", t("empty_movies_hint"))); return; }
    list.forEach(m => {
        const card = document.createElement("div");
        card.className = "library-card";
        card.dataset.title = (m.title || "").toLowerCase();
        card.dataset.genres = (m.genres || []).join(" ").toLowerCase();
        const metaParts = [];
        if (m.year) metaParts.push(m.year);
        metaParts.push(m.size);
        if (m.all_videos && m.all_videos.length > 1) metaParts.push(m.all_videos.length + " files");
        const onPlay = () => playVideoModal(m.video_path, m.title, m.all_videos);
        card.appendChild(makePoster(m.poster, "\u{1F3AC}", onPlay));
        card.appendChild(makeCardInfo(m.title, metaParts.join(" \u00B7 ")));
        card.appendChild(makeBadge(t("downloaded")));
        const delTarget = (m.folder && m.path) ? m.path : (m.original_path || m.video_path);
        const menuItems = [
            { label: "&#9654; " + t("play"), action: onPlay },
            { label: "&#128465; " + t("delete"), action: () => performDelete(delTarget), danger: true }
        ];
        card.appendChild(makeCardMenu(menuItems));
        row.appendChild(card);
    });
}

function renderSeries(series) {
    const row = $("seriesList");
    row.innerHTML = "";
    if (!series || series.length === 0) { row.appendChild(makeEmptyState(t("no_series"), "&#128250;", t("empty_series_hint"))); return; }
    series.forEach(s => {
        const card = document.createElement("div");
        card.className = "library-card";
        card.dataset.title = ((s.display_title || s.title) || "").toLowerCase();
        card.dataset.genres = (s.genres || []).join(" ").toLowerCase();
        const seasonText = s.season_count === 1 ? "1 Season" : s.season_count + " Seasons";
        const epText = s.total_episodes === 1 ? "1 Episode" : s.total_episodes + " Episodes";
        const onBrowse = () => openSeriesDetail(s);
        card.appendChild(makePoster(s.poster, "\u{1F4FA}", onBrowse));
        card.appendChild(makeCardInfo(s.display_title || s.title, seasonText + " \u00B7 " + epText));
        card.appendChild(makeBadge(t("downloaded")));
        const menuItems = [
            { label: "\u{1F4C4} " + t("browse"), action: onBrowse }
        ];
        if (s.path) menuItems.push({ label: "&#128465; " + t("delete"), action: () => performDelete(s.path), danger: true });
        card.appendChild(makeCardMenu(menuItems));
        row.appendChild(card);
    });
}

// ===== Storage =====
function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0, val = bytes;
    while (val >= 1024 && i < units.length - 1) { val /= 1024; i++; }
    const digits = val >= 100 ? 0 : val >= 10 ? 1 : 2;
    return val.toFixed(digits) + " " + units[i];
}

let _storageOpen = false;
let _storageData = { total: 0, used: 0, free: 0, percent: 0 };

async function loadStorage() {
    const card = $("storageCard");
    if (!card) return;
    try {
        if (!_storageData.total) {
            const resp = await fetch("/api/storage");
            const s = await resp.json();
            if (!s || !s.total) { card.classList.add("hidden"); return; }
            _storageData = { total: s.total, used: s.used, free: s.free, percent: Math.max(0, Math.min(100, s.percent || 0)) };
        }
        renderStorage();
        applyStorageVisibility();
    } catch (e) {}
}

function renderStorage() {
    const s = _storageData;
    $("storageCount").textContent = formatBytes(s.used) + " / " + formatBytes(s.total);
    $("storageFill").style.width = s.percent + "%";
    $("storagePercent").textContent = Math.round(s.percent) + "%";
    $("storageFree").textContent = t("free", { free: formatBytes(s.free) });
    $("storageFill").classList.toggle("warn", s.percent >= 80);
    $("storageFill").classList.toggle("danger", s.percent >= 92);
}

function applyStorageVisibility() {
    const card = $("storageCard");
    if (!card) return;
    card.classList.toggle("hidden", !_storageOpen);
}

const storageToggleBtn = $("storageToggle");
if (storageToggleBtn) storageToggleBtn.addEventListener("click", () => {
    _storageOpen = !_storageOpen;
    storageToggleBtn.classList.toggle("open", _storageOpen);
    loadStorage();
    applyStorageVisibility();
});

// ===== Hero Banner =====
let _heroItem = null;
let _featuredItems = [];
let _featuredIdx = 0;

async function loadFeatured() {
    try {
        const resp = await fetch("/api/featured");
        const data = await resp.json();
        _featuredItems = data.featured || [];
    } catch (e) {
        _featuredItems = [];
    }
    renderHero();
}

function renderHero() {
    const banner = $("heroBanner");
    if (!banner) return;
    if (!_featuredItems.length) { _heroItem = null; updateHeroVisibility(); return; }
    if (_featuredIdx >= _featuredItems.length) _featuredIdx = 0;
    const item = _featuredItems[_featuredIdx];
    _heroItem = item;

    const isDiscovery = item.kind === "discovery" || !item.downloaded;
    const isSeries = !!(item.seasons && item.total_episodes) || item.kind === "series";
    const title = (isSeries ? (item.display_title || item.title) : item.title) || item.title || "";

    $("heroBadge").textContent = isDiscovery ? t("trending") : (isSeries ? t("series") : t("featured"));
    $("heroTitle").textContent = title;
    let meta = [];
    if (item.year) meta.push(item.year);
    if (isDiscovery) {
        meta.push(item.media_type === "tv" ? t("type_tv") : t("type_movie"));
    } else {
        meta.push(isSeries
            ? (item.season_count === 1 ? "1 Season" : item.season_count + " Seasons") + " · " + item.total_episodes + " Episodes"
            : item.size);
    }
    $("heroMeta").textContent = meta.join(" · ");
    $("heroDesc").textContent = (item.year ? title + " (" + item.year + "). " : title + ". ")
        + (isDiscovery ? t("trending_hint") : (isSeries ? t("empty_series_hint") : t("empty_movies_hint")));

    $("heroBackdrop").style.backgroundImage = item.poster ? 'url("' + item.poster + '")' : "";

    const playBtn = $("heroPlayBtn");
    const downloadBtn = $("heroDownloadBtn");
    if (!playBtn || !downloadBtn) { updateHeroVisibility(); return; }
    if (isDiscovery) {
        playBtn.classList.add("hidden");
        downloadBtn.classList.remove("hidden");
        downloadBtn.onclick = () => {
            currentMediaType = (item.media_type === "tv") ? "tv" : "movie";
            searchInput.value = item.title;
            switchSection("search");
            searchMovie();
        };
    } else {
        downloadBtn.classList.add("hidden");
        playBtn.classList.remove("hidden");
        playBtn.onclick = () => {
            if (isSeries) { switchSection("library"); openSeriesDetail(item); }
            else if (item.video_path) playVideoModal(item.video_path, item.title, item.all_videos);
        };
    }

    const prevBtn = $("heroPrev");
    const nextBtn = $("heroNext");
    if (prevBtn) prevBtn.onclick = () => { _featuredIdx = (_featuredIdx - 1 + _featuredItems.length) % _featuredItems.length; renderHero(); };
    if (nextBtn) nextBtn.onclick = () => { _featuredIdx = (_featuredIdx + 1) % _featuredItems.length; renderHero(); };

    updateHeroVisibility();
}

function updateHeroVisibility() {
    const banner = $("heroBanner");
    if (!banner) return;
    const onHome = _currentSection === "search";
    banner.classList.toggle("hidden", !(onHome && _heroItem));
}

// ===== Filter Pills =====
function applyLibraryFilters() {
    const q = ($("librarySearch").value || "").trim().toLowerCase();
    const showMovies = _activeFilter === "all" || _activeFilter === "movies";
    const showSeries = _activeFilter === "all" || _activeFilter === "series";

    const movieList = $("downloadedList");
    const seriesList = $("seriesList");

    const matches = card => {
        if (q && !(card.dataset.title || "").includes(q)) return false;
        if (_activeGenre && !(card.dataset.genres || "").includes(_activeGenre.toLowerCase())) return false;
        return true;
    };

    movieList.querySelectorAll(".library-card").forEach(card => {
        card.style.display = (showMovies && matches(card)) ? "" : "none";
    });
    seriesList.querySelectorAll(".library-card").forEach(card => {
        card.style.display = (showSeries && matches(card)) ? "" : "none";
    });
}

// ===== Series Detail =====
function openSeriesDetail(seriesData) {
    currentSeriesData = seriesData;
    $("libraryPage").querySelector(".library-header").classList.add("hidden");
    $("libraryPage").querySelectorAll(".library-subsection").forEach(el => el.classList.add("hidden"));
    seriesDetail.classList.remove("hidden");

    seriesHeader.innerHTML = '<h2>' + seriesData.display_title + '</h2><p class="series-meta">' + seriesData.season_count + ' Season' + (seriesData.season_count !== 1 ? 's' : '') + ' &middot; ' + seriesData.total_episodes + ' Episodes</p>';

    seasonTabs.innerHTML = "";
    seriesData.seasons.forEach((s, idx) => {
        const btn = document.createElement("button");
        btn.className = "season-tab" + (idx === 0 ? " active" : "");
        btn.textContent = "Season " + s.season;
        btn.addEventListener("click", () => {
            document.querySelectorAll(".season-tab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            renderEpisodes(s);
        });
        seasonTabs.appendChild(btn);
    });

    if (seriesData.seasons.length > 0) {
        renderEpisodes(seriesData.seasons[0]);
    }
}

function renderEpisodes(seasonData) {
    currentSeasonEpisodes = seasonData.episodes;
    episodeList.innerHTML = "";
    seasonData.episodes.forEach((ep, idx) => {
        const item = document.createElement("div");
        item.className = "episode-item";
        const epTitle = "S" + String(ep.season).padStart(2, "0") + "E" + String(ep.episode).padStart(2, "0");
        item.innerHTML = '<div class="episode-info"><span class="episode-number">' + epTitle + '</span><span class="episode-name">' + ep.filename + '</span><span class="episode-size">' + ep.size + '</span></div>';
        const playBtn = document.createElement("button");
        playBtn.className = "episode-play-btn";
        playBtn.innerHTML = "&#9654; Play";
        playBtn.addEventListener("click", () => {
            const allEps = seasonData.episodes.map(e => ({
                name: e.filename,
                path: e.path,
                size: e.size
            }));
            playVideoModal(ep.path, epTitle + " - " + ep.filename, allEps);
        });
        item.appendChild(playBtn);
        episodeList.appendChild(item);
    });
}

seriesBackBtn.addEventListener("click", () => {
    seriesDetail.classList.add("hidden");
    $("libraryPage").querySelector(".library-header").classList.remove("hidden");
    $("libraryPage").querySelectorAll(".library-subsection").forEach(el => el.classList.remove("hidden"));
    loadLibrary();
});

// ===== Video Player Modal =====
const PLAYABLE = ["mp4", "webm", "ogg"];
const TRANSCODE = ["mkv", "avi", "mov", "flv", "wmv", "ts", "m2ts", "vob"];

let _currentPlayPath = null;
let _transcodeFailed = false;

async function playVideoModal(path, title, allVideos) {
    if (!path) return;
    playerTitle.textContent = title || "Playing...";
    playerInfo.innerHTML = "";
    videoPlayer.style.display = "none";
    videoPlayer.pause();
    videoPlayer.removeAttribute("src");
    videoPlayer.load();
    _currentPlayPath = path;
    _transcodeFailed = false;

    const ext = path.split(".").pop().split("?")[0].toLowerCase();
    const streamUrl = "/api/stream?path=" + encodeURIComponent(path);

    playerModal.classList.remove("hidden");
    document.body.style.overflow = "hidden";

    const waitMsg = document.createElement("div");
    waitMsg.className = "player-unsupported";
    waitMsg.innerHTML = '<p id="transcodeMsg">V\u00E9rification du fichier...</p>';
    playerInfo.appendChild(waitMsg);
    const msgEl = waitMsg.querySelector("#transcodeMsg");

    const tryPlay = async (attempt) => {
        let resp;
        try {
            resp = await fetch("/api/transcode?path=" + encodeURIComponent(path),
                { headers: { "Range": "bytes=0-0" }, signal: AbortSignal.timeout(15000) });
        } catch (e) {
            msgEl.textContent = "Erreur serveur. R\u00E9essai...";
            if (attempt < 10) setTimeout(() => tryPlay(attempt + 1), 3000);
            return;
        }
        let data = {};
        try { data = await resp.json(); } catch (e) {}

        if (data.status === "ready") {
            playerInfo.innerHTML = "";
            attachVideoErrorHandler(viewAllVideos(allVideos, path, title));
            videoPlayer.src = streamUrl;
            videoPlayer.style.display = "block";
            try {
                const p = videoPlayer.play();
                if (p) p.catch(() => {});
            } catch (e) {}
            showFileList(allVideos, path, title);
            return;
        }

        if (data.status === "preparing" || data.message) {
            const pct = data.progress != null ? " (" + Math.round(data.progress) + "%)" : "";
            msgEl.textContent = "Pr\u00E9paration de la lecture en cours..." + pct;
            if (attempt < 600) setTimeout(() => tryPlay(attempt + 1), 2500);
            return;
        }

        msgEl.textContent = "Impossible de lire ce fichier pour le moment.";
    };

    tryPlay(0);
}

function viewAllVideos(allVideos, path, title) {
    return { allVideos, path, title };
}

function attachVideoErrorHandler(playCtx) {
    videoPlayer.onerror = () => {
        showTranscodeOption(playCtx.path, playCtx.title, playCtx.allVideos);
    };
}

function showTranscodeOption(path, title, allVideos) {
    const transcodeUrl = "/api/transcode?path=" + encodeURIComponent(path);
    const wrap = document.createElement("div");
    wrap.className = "player-unsupported";

    const msg = document.createElement("p");
    msg.textContent = "This format needs preparation for browser playback.";
    wrap.appendChild(msg);

    const btn = document.createElement("button");
    btn.className = "btn-primary";
    btn.textContent = "Prepare for playback";
    btn.addEventListener("click", () => startTranscodePoll(transcodeUrl, path, title, allVideos, msg, btn));
    wrap.appendChild(btn);

    const dlLink = document.createElement("a");
    dlLink.href = "/api/stream?path=" + encodeURIComponent(path);
    dlLink.download = "";
    dlLink.textContent = "Or download file";
    dlLink.style.display = "block";
    dlLink.style.marginTop = "0.5rem";
    dlLink.style.color = "#888";
    dlLink.style.fontSize = "0.85rem";
    wrap.appendChild(dlLink);

    playerInfo.appendChild(wrap);
}

async function startTranscodePoll(transcodeUrl, path, title, allVideos, msgEl, btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = "Starting...";
    let attempts = 0;
    const maxAttempts = 300;

    while (attempts < maxAttempts) {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            const resp = await fetch(transcodeUrl, { headers: { "Range": "bytes=0-0" }, signal: controller.signal });
            clearTimeout(timeout);

            const ct = resp.headers.get("content-type") || "";
            if (ct.includes("video/") && (resp.ok || resp.status === 206)) {
                videoPlayer.src = transcodeUrl;
                videoPlayer.style.display = "block";
                showFileList(allVideos, path, title);
                return;
            }

            if (resp.ok) {
                const data = await resp.json().catch(() => ({}));
                if (data.status === "ready") {
                    // Transcode finished -> stream the now-playable file
                    const streamUrl = "/api/stream?path=" + encodeURIComponent(path);
                    videoPlayer.src = streamUrl;
                    videoPlayer.style.display = "block";
                    showFileList(allVideos, path, title);
                    return;
                }
                if (data.error) {
                    msgEl.textContent = data.error;
                    msgEl.style.color = "#e74c3c";
                    btnEl.disabled = false;
                    btnEl.textContent = "Retry";
                    return;
                }
            }
            msgEl.textContent = "Preparing video... " + (attempts > 0 ? "(" + attempts + "s)" : "");
        } catch (e) {
            msgEl.textContent = "Reconnecting...";
        }
        await new Promise(r => setTimeout(r, 2000));
        attempts++;
    }
    msgEl.textContent = "Timed out. Try again later.";
    msgEl.style.color = "#e74c3c";
    btnEl.disabled = false;
    btnEl.textContent = "Retry";
}

function showFileList(allVideos, path, title) {
    if (!allVideos || allVideos.length <= 1) return;
    const otherFiles = allVideos.filter(v => v.path !== path);
    if (otherFiles.length === 0) return;
    const listDiv = document.createElement("div");
    listDiv.className = "player-file-list";
    listDiv.innerHTML = '<h4>All files:</h4>';
    const currentItem = document.createElement("div");
    currentItem.className = "player-file-item current";
    currentItem.innerHTML = '<span>' + getShortName(allVideos.find(v => v.path === path)?.name || title) + '</span><span class="player-file-playing">Playing</span>';
    listDiv.appendChild(currentItem);
    otherFiles.forEach(v => {
        const fItem = document.createElement("div");
        fItem.className = "player-file-item";
        fItem.innerHTML = '<span>' + getShortName(v.name) + ' (' + v.size + ')</span>';
        const fBtn = document.createElement("button");
        fBtn.textContent = "Play";
        fBtn.addEventListener("click", () => playVideoModal(v.path, v.name, allVideos));
        fItem.appendChild(fBtn);
        listDiv.appendChild(fItem);
    });
    playerInfo.appendChild(listDiv);
}

function getShortName(name) {
    return name.length > 50 ? name.slice(0, 47) + "..." : name;
}

function closePlayer() {
    videoPlayer.pause();
    videoPlayer.src = "";
    playerModal.classList.add("hidden");
    document.body.style.overflow = "";
}

videoPlayer.addEventListener("error", function() {
    if (!videoPlayer.src || videoPlayer.src === window.location.href) return;
    const err = videoPlayer.error;
    if (!err) return;
    if (_transcodeFailed) return;
    _transcodeFailed = true;
    const src = videoPlayer.src;
    const ext = _currentPlayPath ? _currentPlayPath.split(".").pop().split("?")[0].toLowerCase() : "";
    videoPlayer.style.display = "none";
    videoPlayer.src = "";

    const msgs = { 1: "Aborted", 2: "Network error", 3: "Decode error", 4: "Format not supported" };
    const msg = msgs[err.code] || "Unknown error";

    if (TRANSCODE.includes(ext)) {
        const errDiv = document.createElement("div");
        errDiv.className = "player-unsupported";
        errDiv.innerHTML = '<p>Playback error: ' + msg + '</p><p style="color:#888;font-size:0.85rem">The file needs to be converted for browser playback.</p>';
        playerInfo.appendChild(errDiv);
        showTranscodeOption(_currentPlayPath, playerTitle.textContent, null);
    } else {
        const errDiv = document.createElement("div");
        errDiv.className = "player-unsupported";
        errDiv.innerHTML = '<p>Video error: ' + msg + '</p><a href="' + src + '" download class="btn-primary">Download File</a>';
        playerInfo.appendChild(errDiv);
    }
});

playerClose.addEventListener("click", closePlayer);
playerOverlay.addEventListener("click", closePlayer);
document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !playerModal.classList.contains("hidden")) closePlayer();
});

// ===== Settings =====
async function loadSettings() {
    try {
        const resp = await fetch("/api/settings");
        const data = await resp.json();
        currentDest.textContent = t("current") + ": " + data.destination;
        destInput.value = data.destination;
    } catch (e) {}
}

async function saveSettings() {
    const dest = destInput.value.trim();
    if (!dest) return;
    try {
        await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ destination: dest })
        });
        currentDest.textContent = t("current") + ": " + dest;
        settingsModal.classList.add("hidden");
    } catch (e) {}
}

// ===== Auth =====
async function checkAuth() {
    try {
        const resp = await fetch("/api/me");
        if (resp.ok) { const d = await resp.json(); if (d.logged_in) showLoggedIn(d.username); }
    } catch (e) {}
}

function showLoggedIn(u) { loginBtn.classList.add("hidden"); userMenu.classList.remove("hidden"); usernameDisplay.textContent = u; }
function showLoggedOut() { loginBtn.classList.remove("hidden"); userMenu.classList.add("hidden"); usernameDisplay.textContent = ""; }

function openAuthModal(mode) {
    authMode = mode; authError.classList.add("hidden");
    authUsername.value = ""; authPassword.value = "";
    if (mode === "login") {
        authModalTitle.textContent = t("login_title"); authSubmit.textContent = t("login");
        authSwitch.innerHTML = t("no_account") + ' <a href="#" id="switchToRegister">' + t("register") + "</a>";
    } else {
        authModalTitle.textContent = t("register_title"); authSubmit.textContent = t("register");
        authSwitch.innerHTML = t("has_account") + ' <a href="#" id="switchToLogin">' + t("login") + "</a>";
    }
    authModal.classList.remove("hidden");
    const sr = document.getElementById("switchToRegister");
    const sl = document.getElementById("switchToLogin");
    if (sr) sr.addEventListener("click", e => { e.preventDefault(); openAuthModal("register"); });
    if (sl) sl.addEventListener("click", e => { e.preventDefault(); openAuthModal("login"); });
}

let authMode = "login";
async function submitAuth() {
    const u = authUsername.value.trim(), p = authPassword.value;
    if (!u || !p) { authErrorText.textContent = t("field_required"); authError.classList.remove("hidden"); return; }
    const ep = authMode === "login" ? "/api/login" : "/api/register";
    try {
        const resp = await fetch(ep, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: u, password: p, _ip: getIP() })
        });
        const data = await resp.json();
        if (!resp.ok) { authErrorText.textContent = data.error; authError.classList.remove("hidden"); return; }
        authModal.classList.add("hidden"); showLoggedIn(data.username);
    } catch (e) { authErrorText.textContent = t("network_error"); authError.classList.remove("hidden"); }
}

async function logout() { await fetch("/api/logout", { method: "POST" }); showLoggedOut(); }

// ===== Events =====
searchBtn.addEventListener("click", searchMovie);
searchInput.addEventListener("keydown", e => { if (e.key === "Enter") searchMovie(); });
themeToggle.addEventListener("click", () => setTheme(currentTheme === "dark" ? "light" : "dark"));
langSelect.addEventListener("change", e => loadLang(e.target.value));
settingsBtn.addEventListener("click", () => { loadSettings(); settingsModal.classList.remove("hidden"); });
modalOverlay.addEventListener("click", () => settingsModal.classList.add("hidden"));
modalClose.addEventListener("click", () => settingsModal.classList.add("hidden"));
modalCancel.addEventListener("click", () => settingsModal.classList.add("hidden"));
modalSave.addEventListener("click", saveSettings);
loginBtn.addEventListener("click", () => openAuthModal("login"));
logoutBtn.addEventListener("click", logout);
authOverlay.addEventListener("click", () => authModal.classList.add("hidden"));
authClose.addEventListener("click", () => authModal.classList.add("hidden"));
authSubmit.addEventListener("click", submitAuth);
authPassword.addEventListener("keydown", e => { if (e.key === "Enter") submitAuth(); });
authUsername.addEventListener("keydown", e => { if (e.key === "Enter") authPassword.focus(); });

// ===== Library Filters =====
document.querySelectorAll("#filterPills .pill").forEach(pill => {
    pill.addEventListener("click", () => {
        document.querySelectorAll("#filterPills .pill").forEach(p => p.classList.remove("active"));
        pill.classList.add("active");
        _activeFilter = pill.dataset.filter;
        applyLibraryFilters();
    });
});
$("librarySearch").addEventListener("input", () => applyLibraryFilters());

$("genrePills").addEventListener("click", (e) => {
    const pill = e.target.closest(".genre-pill");
    if (!pill) return;
    const g = pill.dataset.genre;
    _activeGenre = (_activeGenre === g) ? "" : g;
    document.querySelectorAll("#genrePills .genre-pill").forEach(p => p.classList.toggle("active", p === pill && !!_activeGenre));
    applyLibraryFilters();
});

// ===== Init =====
setTheme(currentTheme);
langSelect.value = currentLang;
loadLang(currentLang);
checkAuth();
loadLibrary();
toggleFloatDownloads(_floatCollapsed);
updateFloatDownloads();

// Recover active downloads on page load
async function recoverDownloads() {
    try {
        const resp = await fetch("/api/tasks");
        const tasks = await resp.json();
        for (const [taskId, task] of Object.entries(tasks)) {
            if (["downloading", "paused"].includes(task.status)) {
                addDownloadCard(taskId, task.title);
                const card = activeTasks[taskId];
                if (card) {
                    const fill = card.querySelector(".progress-fill");
                    const labelPct = card.querySelector(".progress-pct");
                    const labelSpeed = card.querySelector(".progress-speed");
                    const peers = card.querySelector(".peers");
                    const status = card.querySelector(".download-status");
                    fill.style.width = task.progress + "%";
                    if (labelPct) labelPct.textContent = Math.round(task.progress) + "%";
                    if (labelSpeed) labelSpeed.textContent = task.status === "downloading" ? task.speed : "Paused";
                    peers.textContent = task.peers + " " + t("peers");
                    if (task.status === "paused") {
                        status.textContent = "Paused";
                        status.className = "download-status status-paused";
                        const pauseBtn = card.querySelector(".pause-btn");
                        const resumeBtn = card.querySelector(".resume-btn");
                        if (pauseBtn) pauseBtn.classList.add("hidden");
                        if (resumeBtn) resumeBtn.classList.remove("hidden");
                    }
                }
                listenProgress(taskId);
            } else if (task.status === "completed" || task.status === "error") {
                addDownloadCard(taskId, task.title);
                const card = activeTasks[taskId];
                if (card) {
                    const fill = card.querySelector(".progress-fill");
                    const status = card.querySelector(".download-status");
                    const labelPct = card.querySelector(".progress-pct");
                    const labelSpeed = card.querySelector(".progress-speed");
                    if (task.status === "completed") {
                        fill.style.width = "100%";
                        fill.style.background = "var(--success)";
                        status.textContent = "\u2713 " + t("completed");
                        status.className = "download-status status-completed";
                        if (labelPct) labelPct.textContent = "100%";
                        if (labelSpeed) labelSpeed.textContent = "Done";
                    } else {
                        fill.style.width = "100%";
                        fill.style.background = "var(--error)";
                        status.textContent = t("error") + ": " + (task.error || "");
                        status.className = "download-status status-error";
                        if (labelSpeed) labelSpeed.textContent = "Failed";
                    }
                    hideControls(card);
                    addDismissButton(card, taskId);
                    scheduleAutoHide(card, taskId, task.status === "completed" ? 4000 : 6000);
                }
            }
        }
    } catch (e) {}
}
recoverDownloads();
