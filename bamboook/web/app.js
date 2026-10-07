(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();

  const initData = tg?.initData || "";
  const user = tg?.initDataUnsafe?.user;
  const form = document.querySelector("#search-form");
  const queryInput = document.querySelector("#query");
  const results = document.querySelector("#results");
  const libraryResults = document.querySelector("#library-results");
  const toast = document.querySelector("#toast");
  const navItems = [...document.querySelectorAll(".nav-item")];
  const searchArea = document.querySelector(".search-area");
  const popularList = document.querySelector("#popular-list");
  const playlistList = document.querySelector("#playlists-list");
  const playlistDetail = document.querySelector("#playlist-detail");
  const playlistTracksView = document.querySelector("#playlist-tracks");
  const miniPlayer = document.querySelector("#mini-player");
  const audioPlayer = document.querySelector("#audio-player");
  const playerOverlay = document.querySelector("#player-overlay");
  const pageTitle = document.querySelector("#page-title");
  const pageDescription = document.querySelector("#page-description");
  let currentView = "search";
  let currentTracks = [];
  let libraryTracks = [];
  let currentPlaylistTracks = [];
  let playlists = [];
  let selectedPlaylistId = null;
  let accountState = null;
  let playQueue = [];
  let queueIndex = -1;
  let shuffleEnabled = false;
  let toastTimer;
  let currentAudioObjectUrl = "";
  let currentPlayingTrack = null;
  let activePlayerSource = "";
  let youtubePlayer = null;
  let pendingYoutubeId = "";
  let youtubeApiReady = Boolean(window.YT?.Player);
  let progressTimer = 0;

  function applyTheme() {
    const theme = tg?.colorScheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.body.dataset.theme = theme;
    const color = theme === "dark" ? "#202820" : "#e9dfc7";
    document.querySelector('meta[name="theme-color"]').content = color;
    tg?.setHeaderColor?.(color);
    tg?.setBackgroundColor?.(color);
  }

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
  }

  async function api(action, extra = {}) {
    let response;
    try {
      response = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, initData, ...extra }),
      });
    } catch {
      throw new Error("Не удалось связаться с BamBook. Проверь подключение и попробуй ещё раз.");
    }
    let payload;
    try { payload = await response.json(); }
    catch { throw new Error("Сервер BamBook вернул неожиданный ответ."); }
    if (!response.ok) throw new Error(payload.error || "Не удалось выполнить запрос");
    return payload;
  }

  function renderEmpty(container, title, description) {
    container.innerHTML = `<div class="empty-state"><span class="empty-note" aria-hidden="true">♫</span><h3>${escapeHTML(title)}</h3><p>${escapeHTML(description)}</p></div>`;
  }

  function safeLink(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" ? url.href : "";
    } catch { return ""; }
  }

  function bookmarkIcon() {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4.8A1.8 1.8 0 0 1 7.8 3h8.4A1.8 1.8 0 0 1 18 4.8V21l-6-3.8L6 21V4.8Z"/></svg>';
  }

  function providerLinks(track) {
    const spotifyId = /^[A-Za-z0-9]{22}$/.test(track?.spotify_id || "") ? track.spotify_id : "";
    const youtubeId = /^[A-Za-z0-9_-]{6,20}$/.test(track?.youtube_id || "") ? track.youtube_id : "";
    const query = encodeURIComponent(`${track?.artist || ""} ${track?.title || ""}`.trim());
    const links = [
      { label: spotifyId ? "Spotify" : "Искать в Spotify", url: spotifyId ? `https://open.spotify.com/track/${spotifyId}` : `https://open.spotify.com/search/${query}`, service: "spotify" },
      { label: "YouTube", url: youtubeId ? `https://www.youtube.com/watch?v=${youtubeId}` : `https://www.youtube.com/results?search_query=${query}`, service: "youtube" },
      { label: "YouTube Music", url: youtubeId ? `https://music.youtube.com/watch?v=${youtubeId}` : `https://music.youtube.com/search?q=${query}`, service: "youtube-music" },
    ];
    return links.map(({ label, url, service }) => `<button class="provider-link provider-${service}" data-open-source="${escapeHTML(url)}" type="button">${label} ↗</button>`).join("");
  }

  function card(track, index, { kind = "search", playlistId = null } = {}) {
    const art = safeLink(track.artwork);
    const hasFullAudio = /^\d{1,12}$/.test(String(track.jamendo_id || ""));
    const hasYoutube = /^[A-Za-z0-9_-]{6,20}$/.test(track.youtube_id || "");
    const canPlay = hasFullAudio || hasYoutube;
    const playLabel = hasYoutube || hasFullAudio ? "▶ Слушать" : "В BamBook нет аудио";
    const sources = (Array.isArray(track.sources) ? track.sources : [track.source]).filter(Boolean).slice(0, 3);
    const play = `<button class="play-track-button" data-play-kind="${kind}" data-play-index="${index}" type="button" ${canPlay ? "" : "disabled"}>${playLabel}</button>`;
    let action = "";
    if (kind === "search") {
      const options = playlists.map((item) => `<option value="${Number(item.id)}">${escapeHTML(item.name)}</option>`).join("");
      action = `<button class="save-button" data-save="${index}" type="button" aria-label="Сохранить ${escapeHTML(track.title)}">${bookmarkIcon()}</button><button class="queue-add" data-queue-index="${index}" type="button" aria-label="Добавить ${escapeHTML(track.title)} в очередь" ${canPlay ? "" : 'disabled title="В BamBook нет аудиопотока для этого результата"'}>+ Очередь</button><select class="playlist-add-select" data-add-index="${index}" aria-label="Добавить в плейлист" ${playlists.length ? "" : "disabled"}><option value="">+ В плейлист</option>${options}</select>`;
    } else if (kind === "playlist") {
      action = `<button class="playlist-track-remove" data-remove-playlist-track="${Number(track.id)}" type="button">Убрать</button>`;
    } else {
      action = `<button class="remove-button" data-remove="${Number(track.id)}" type="button" aria-label="Удалить ${escapeHTML(track.title)} из библиотеки"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3"/></svg></button>`;
    }
    action += `<div class="provider-actions">${providerLinks(track)}</div><button class="send-chat-button" data-send-chat-kind="${kind}" data-send-chat-index="${index}" type="button" ${hasFullAudio ? "" : 'disabled title="Файл доступен только для разрешённых Jamendo CC0-треков"'}>Отправить в чат</button>`;
    return `<article class="track-card">
      <div class="cover" aria-hidden="true">♫${art ? `<img class="cover-art" src="${escapeHTML(art)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</div>
      <div class="track-info"><strong class="track-title">${escapeHTML(track.title)}</strong>
      <span class="track-meta">${escapeHTML(track.artist)}${track.album ? ` · ${escapeHTML(track.album)}` : ""}</span>
      <div class="track-extra">${track.duration ? `<span class="duration">${escapeHTML(track.duration)}</span>` : ""}${sources.map((source) => `<span class="source-badge">${escapeHTML(source)}</span>`).join("")}</div><div class="track-tools">${play}${action}</div></div></article>`;
  }

  function pluralTracks(count) {
    const n = Math.abs(count) % 100;
    const last = n % 10;
    const noun = n > 10 && n < 20 ? "треков" : last > 1 && last < 5 ? "трека" : last === 1 ? "трек" : "треков";
    return `${count} ${noun}`;
  }

  async function search(query) {
    query = query.trim();
    if (!query) { queryInput.focus(); return; }
    const heading = document.querySelector("#results-heading");
    const source = document.querySelector("#result-source");
    const searchButton = document.querySelector("#search-button");
    popularList.classList.add("hidden");
    heading.textContent = "Ищу совпадения…";
    source.textContent = "Сверяю каталоги";
    results.innerHTML = "";
    searchButton.disabled = true;
    searchButton.querySelector("span").textContent = "Ищу…";
    try {
      const payload = await api("search", { query });
      currentTracks = payload.tracks || [];
      results.innerHTML = currentTracks.map((track, index) => card(track, index, { kind: "search" })).join("");
      if (!currentTracks.length) renderEmpty(results, "Пока пусто", "Попробуй другое название или имя исполнителя.");
      heading.textContent = currentTracks.length ? `Нашлось: ${currentTracks.length}` : "Ничего не нашлось";
      source.textContent = currentTracks.length ? "Сохранить в библиотеку" : "Попробуй другой запрос";
    } catch (error) {
      renderEmpty(results, "Не получилось найти", error.message);
      heading.textContent = "Поиск не удался";
      source.textContent = "Попробуй ещё раз";
    } finally {
      searchButton.disabled = false;
      searchButton.querySelector("span").textContent = "Найти";
    }
  }

  async function loadLibrary() {
    try {
      const payload = await api("library");
      const tracks = payload.tracks || [];
      libraryTracks = tracks;
      libraryResults.innerHTML = tracks.map((track, index) => card(track, index, { kind: "library" })).join("");
      const count = tracks.length;
      document.querySelector("#library-count").textContent = count;
      const navCount = document.querySelector("#nav-library-count");
      navCount.textContent = count || "";
      navCount.dataset.empty = count ? "false" : "true";
      document.querySelector("#library-caption").textContent = count
        ? `${pluralTracks(count)} уже в твоей коллекции.`
        : "Треки, которые хочется оставить рядом.";
      if (!count) renderEmpty(libraryResults, "Здесь будет твоя музыка", "Находи треки и сохраняй их одним нажатием.");

      const imported = await api("imports");
      const importsList = document.querySelector("#imports-list");
      importsList.innerHTML = (imported.imports || []).map((item) => `<div class="import-item"><strong>${escapeHTML(item.service)} · ссылка сохранена</strong></div>`).join("");
      await loadPlaylists();
    } catch (error) {
      renderEmpty(libraryResults, "Не удалось открыть коллекцию", error.message);
    }
  }

  async function loadPlaylists() {
    const payload = await api("playlists");
    playlists = payload.playlists || [];
    playlistList.innerHTML = playlists.map((item) => `<div class="playlist-row"><button class="playlist-open ${Number(item.id) === Number(selectedPlaylistId) ? "active" : ""}" data-open-playlist="${Number(item.id)}" type="button"><strong>${escapeHTML(item.name)}</strong><span>${item.count} ${pluralTracks(item.count).replace(/^\d+ /, "")}</span></button><button class="playlist-delete" data-delete-playlist="${Number(item.id)}" type="button" aria-label="Удалить плейлист ${escapeHTML(item.name)}">×</button></div>`).join("");
    if (!playlists.some((item) => Number(item.id) === Number(selectedPlaylistId))) {
      selectedPlaylistId = null;
      playlistDetail.classList.add("hidden");
    }
    if (selectedPlaylistId) await openPlaylist(selectedPlaylistId);
  }

  async function loadAccount() {
    const account = await api("account");
    accountState = account;
    const plus = account.plan === "plus";
    document.querySelector("#plan-name").textContent = plus ? "BamBook Plus" : "BamBook Free";
    document.querySelector("#plan-description").textContent = plus
      ? `Plus активен до ${new Date(account.premiumUntil * 1000).toLocaleDateString("ru-RU")}. Каналы без лимита, архив новостей 90 дней.`
      : "Музыка и плейлисты без лимита, до 3 каналов и архив новостей за 7 дней.";
    const premiumButton = document.querySelector("#premium-button");
    premiumButton.textContent = plus ? "Plus активен" : `Подключить · ${account.premiumPriceStars} ⭐/мес`;
    premiumButton.disabled = plus;
    document.querySelector("#news-limit").textContent = plus
      ? `BamBook Plus · ${account.newsChannels.length} каналов без лимита · архив ${account.newsHistoryDays} дней.`
      : `Бесплатно · ${account.newsChannels.length}/3 каналов · архив ${account.newsHistoryDays} дней.`;
    document.querySelector("#news-channels-list").innerHTML = account.newsChannels.length
      ? account.newsChannels.map((channel) => `<div class="import-item"><strong>${escapeHTML(channel.name)}</strong><button class="playlist-delete" data-remove-channel="${escapeHTML(channel.key)}" type="button">Убрать</button></div>`).join("")
      : '<div class="empty-state"><h3>Каналов пока нет</h3><p>Добавь публичный Telegram-канал, чтобы сохранить его в источники.</p></div>';
    const feed = account.newsFeed || [];
    document.querySelector("#news-feed").innerHTML = feed.length
      ? feed.map((post) => {
        const link = safeLink(post.url);
        const preview = post.text || "Медиа-публикация";
        const timestamp = new Date(post.publishedAt * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
        return `<article class="news-post"><div><strong>${escapeHTML(post.channel)}</strong><time>${timestamp}</time></div><p>${escapeHTML(preview)}</p>`
          + (link ? '<a href="' + escapeHTML(link) + '" target="_blank" rel="noopener noreferrer">Открыть пост</a>' : "")
          + "</article>";
      }).join("")
      : '<p class="news-empty">Новые публикации появятся здесь после того, как добавишь BamBook в каналы. История до подключения недоступна.</p>';
    document.querySelector("#connected-imports").innerHTML = account.imports.map((item) =>
      `<div class="import-item"><strong>${escapeHTML(item.service)} · подборка сохранена</strong></div>`).join("");
  }

  async function purchasePremium() {
    try {
      const invoice = await api("premium_invoice");
      if (tg?.openInvoice) {
        tg.openInvoice(invoice.invoiceLink, (status) => {
          if (status === "paid") {
            showToast("Платёж принят. Обновляю BamBook Plus…");
            setTimeout(() => loadAccount().catch((error) => showToast(error.message)), 1500);
          } else if (status === "failed") showToast("Не удалось завершить оплату");
        });
      } else {
        openLink(invoice.invoiceLink);
      }
    } catch (error) { showToast(error.message); }
  }

  async function openPlaylist(playlistId) {
    const playlist = playlists.find((item) => Number(item.id) === Number(playlistId));
    if (!playlist) return;
    selectedPlaylistId = Number(playlistId);
    playlistDetail.classList.remove("hidden");
    document.querySelector("#playlist-detail-title").textContent = playlist.name;
    document.querySelector("#playlist-delete").dataset.deletePlaylist = String(playlist.id);
    const payload = await api("playlist_tracks", { playlistId: playlist.id });
    currentPlaylistTracks = payload.tracks || [];
    playlistTracksView.innerHTML = currentPlaylistTracks.length
      ? currentPlaylistTracks.map((track, index) => card(track, index, { kind: "playlist", playlistId: playlist.id })).join("")
      : '<div class="empty-state"><span class="empty-note" aria-hidden="true">♫</span><h3>Плейлист пока пуст</h3><p>Найди песню и выбери этот плейлист в меню добавления.</p></div>';
    playlistList.querySelectorAll("[data-open-playlist]").forEach((button) => button.classList.toggle("active", Number(button.dataset.openPlaylist) === selectedPlaylistId));
  }

  async function createPlaylist(event) {
    event.preventDefault();
    const input = document.querySelector("#playlist-name");
    const name = input.value.trim();
    try {
      const result = await api("create_playlist", { name });
      input.value = "";
      selectedPlaylistId = result.playlistId;
      await loadPlaylists();
      showToast("Плейлист создан");
    } catch (error) { showToast(error.message); }
  }

  function openPlayer() {
    if (!currentPlayingTrack) return;
    playerOverlay.classList.remove("hidden");
    document.body.classList.add("player-expanded");
    if (activePlayerSource === "youtube") youtubePlayer?.playVideo?.();
  }

  function closePlayer() {
    playerOverlay.classList.add("hidden");
    document.body.classList.remove("player-expanded");
    // The official YouTube video remains visible while it plays. Closing the
    // full player pauses it instead of leaving a hidden video running.
    if (activePlayerSource === "youtube") youtubePlayer?.pauseVideo?.();
  }

  function showPlayerArtwork(track, video = false) {
    const art = safeLink(track?.artwork);
    const cover = document.querySelector("#player-artwork");
    const miniCover = document.querySelector("#mini-artwork");
    const backdrop = document.querySelector("#player-backdrop");
    for (const image of [cover, miniCover, backdrop]) {
      if (art) image.src = art;
      else image.removeAttribute("src");
      image.classList.toggle("has-image", Boolean(art));
    }
    document.querySelector("#player-visual").classList.toggle("youtube-active", video);
  }

  function togglePlayback() {
    if (activePlayerSource === "youtube" && youtubePlayer) {
      const state = youtubePlayer.getPlayerState?.();
      if (state === window.YT?.PlayerState?.PLAYING) youtubePlayer.pauseVideo();
      else { openPlayer(); youtubePlayer.playVideo(); }
      return;
    }
    if (audioPlayer.paused) audioPlayer.play().catch(() => showToast("Нажми воспроизведение, чтобы начать"));
    else audioPlayer.pause();
  }

  function updatePlayButtons(playing) {
    const symbol = playing ? "Ⅱ" : "▶";
    document.querySelector("#player-toggle").textContent = symbol;
    document.querySelector("#player-toggle").setAttribute("aria-label", playing ? "Приостановить" : "Воспроизвести");
    document.querySelector("#player-toggle-mini").textContent = symbol;
    document.querySelector("#player-toggle-mini").setAttribute("aria-label", playing ? "Приостановить" : "Воспроизвести");
  }

  function formatTime(value) {
    const seconds = Math.max(0, Math.floor(Number(value) || 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function updateProgress() {
    let current = 0, duration = 0;
    if (activePlayerSource === "youtube" && youtubePlayer?.getDuration) {
      current = youtubePlayer.getCurrentTime?.() || 0;
      duration = youtubePlayer.getDuration?.() || 0;
    } else if (activePlayerSource === "audio") {
      current = audioPlayer.currentTime || 0;
      duration = audioPlayer.duration || 0;
    }
    const seek = document.querySelector("#player-seek");
    seek.value = String(duration ? Math.round(current / duration * 1000) : 0);
    document.querySelector("#player-elapsed").textContent = formatTime(current);
    document.querySelector("#player-remaining").textContent = `−${formatTime(Math.max(0, duration - current))}`;
    updateMediaPosition(current, duration);
  }

  function updateMediaSession(track = currentPlayingTrack, playing = !audioPlayer.paused) {
    if (!("mediaSession" in navigator) || !track || activePlayerSource !== "audio") return;
    if ("MediaMetadata" in window) {
      const artwork = safeLink(track.artwork);
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title || "BamBook",
        artist: track.artist || "",
        album: track.album || "",
        artwork: artwork ? [96, 128, 192, 256, 384, 512].map((size) => ({ src: artwork, sizes: `${size}x${size}` })) : [],
      });
    }
    navigator.mediaSession.playbackState = playing ? "playing" : "paused";
    updateMediaPosition(audioPlayer.currentTime || 0, audioPlayer.duration || 0);
  }

  function updateMediaPosition(current, duration) {
    if (!("mediaSession" in navigator) || activePlayerSource !== "audio" || !duration || !Number.isFinite(duration)) return;
    try {
      navigator.mediaSession.setPositionState({ duration, playbackRate: audioPlayer.playbackRate || 1, position: Math.min(current, duration) });
    } catch { /* Position state is optional in older Telegram WebViews. */ }
  }

  if ("mediaSession" in navigator) {
    for (const [action, handler] of Object.entries({
      play: () => togglePlayback(),
      pause: () => togglePlayback(),
      nexttrack: () => playNext(1),
      previoustrack: () => playNext(-1),
      seekto: (details) => { if (activePlayerSource === "audio" && Number.isFinite(details.seekTime)) audioPlayer.currentTime = details.seekTime; },
    })) {
      try { navigator.mediaSession.setActionHandler(action, handler); } catch { /* Unsupported action. */ }
    }
  }

  function loadYoutubePlayer(videoId) {
    pendingYoutubeId = videoId;
    if (!youtubeApiReady || !window.YT?.Player) return;
    if (youtubePlayer) {
      youtubePlayer.loadVideoById(videoId);
      return;
    }
    youtubePlayer = new window.YT.Player("youtube-player", {
      width: "100%", height: "100%", videoId,
      playerVars: { autoplay: 1, controls: 1, playsinline: 1, rel: 0, enablejsapi: 1 },
      events: {
        onReady: (event) => { event.target.setVolume(Number(document.querySelector("#player-volume").value)); if (playerOverlay.classList.contains("hidden")) event.target.pauseVideo(); else event.target.playVideo(); updateProgress(); },
        onStateChange: (event) => {
          const state = event.data;
          updatePlayButtons(state === window.YT.PlayerState.PLAYING);
          if (state === window.YT.PlayerState.ENDED && queueIndex >= 0) playNext(1);
        },
        onError: () => showToast("YouTube не разрешает встроенное воспроизведение этого видео. Выбери другой результат."),
      },
    });
  }

  window.addEventListener("bambook:youtube-ready", () => {
    youtubeApiReady = Boolean(window.YT?.Player);
    if (pendingYoutubeId && youtubeApiReady) loadYoutubePlayer(pendingYoutubeId);
  });

  async function playTrack(track) {
    const jamendoId = /^\d{1,12}$/.test(String(track?.jamendo_id || "")) ? String(track.jamendo_id) : "";
    const youtubeId = /^[A-Za-z0-9_-]{6,20}$/.test(track?.youtube_id || "") ? track.youtube_id : "";
    if (!jamendoId && !youtubeId) { showToast("Для этого результата нет доступного плеера"); return; }
    currentPlayingTrack = track;
    const video = Boolean(youtubeId);
    activePlayerSource = video ? "youtube" : "audio";
    document.querySelector("#player-title").textContent = track.title;
    document.querySelector("#player-artist").textContent = track.artist;
    document.querySelector("#player-title-full").textContent = track.title;
    document.querySelector("#player-artist-full").textContent = track.artist;
    const sources = Array.isArray(track.sources) ? track.sources : String(track.source || "").split(" · ");
    const provider = video ? (sources.includes("YouTube Music") ? "YouTube Music" : "YouTube") : "Jamendo";
    const attribution = sources.includes("Spotify") ? `Spotify · ${provider}` : provider;
    document.querySelector("#player-source-label").textContent = `СЕЙЧАС ИГРАЕТ · ${attribution}`;
    document.querySelector("#player-save").disabled = !track.url;
    document.querySelector("#player-footer-source").textContent = video
      ? "Фон: открой YouTube или Spotify в приложении"
      : "Полный трек · Jamendo · системное управление";
    document.querySelector("#player-provider-links").innerHTML = providerLinks(track);
    const sendButton = document.querySelector("#send-to-chat");
    sendButton.disabled = !/^\d{1,12}$/.test(String(track.jamendo_id || ""));
    sendButton.title = sendButton.disabled ? "Файл доступен только для разрешённых Jamendo CC0-треков" : "Отправить полный аудиофайл в личный чат BamBook";
    showPlayerArtwork(track, video);
    miniPlayer.classList.add("active");
    openPlayer();
    audioPlayer.pause();
    audioPlayer.removeAttribute("src");
    audioPlayer.load();
    updatePlayButtons(false);
    if (currentAudioObjectUrl) { URL.revokeObjectURL(currentAudioObjectUrl); currentAudioObjectUrl = ""; }
    clearInterval(progressTimer);
    if (video) {
      loadYoutubePlayer(youtubeId);
      if (!youtubeApiReady) showToast("Открываю официальный YouTube-плеер…");
      progressTimer = setInterval(updateProgress, 500);
      return;
    }
    if (youtubePlayer) youtubePlayer.pauseVideo();
    showToast("Загружаю полный трек…");
    try {
      const response = await fetch("/api/action", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "play_jamendo", jamendoId, initData }),
      });
      if (!response.ok) {
        let message = "Не удалось загрузить аудио";
        try { message = (await response.json()).error || message; } catch {}
        throw new Error(message);
      }
      currentAudioObjectUrl = URL.createObjectURL(await response.blob());
      audioPlayer.src = currentAudioObjectUrl;
      await audioPlayer.play();
    } catch (error) { showToast(error.message || "Не удалось загрузить полный трек"); }
  }

  async function sendTrackToChat(track, button) {
    const jamendoId = /^\d{1,12}$/.test(String(track?.jamendo_id || "")) ? String(track.jamendo_id) : "";
    if (!jamendoId) { showToast("Файл доступен только для разрешённых Jamendo CC0-треков"); return; }
    const originalText = button?.textContent || "Отправить в чат";
    if (button) { button.disabled = true; button.textContent = "Отправляю…"; }
    try {
      await api("send_jamendo_audio", { jamendoId });
      showToast("Аудиофайл отправлен в личный чат BamBook");
    } catch (error) {
      showToast(error.message || "Не удалось отправить аудио в чат");
    } finally {
      if (button) { button.disabled = false; button.textContent = originalText; }
    }
  }

  const queueStorageKey = `bambook-queue-${user?.id || "guest"}`;
  try { playQueue = JSON.parse(localStorage.getItem(queueStorageKey) || "[]"); if (!Array.isArray(playQueue)) playQueue = []; }
  catch { playQueue = []; }

  function persistQueue() {
    try { localStorage.setItem(queueStorageKey, JSON.stringify(playQueue)); } catch {}
    renderQueue();
  }

  function renderQueue() {
    const list = document.querySelector("#queue-list");
    const empty = document.querySelector("#queue-empty");
    if (!list || !empty) return;
    document.querySelector("#queue-count").textContent = String(playQueue.length);
    document.querySelector("#player-queue-count").textContent = String(playQueue.length);
    empty.classList.toggle("hidden", playQueue.length > 0);
    list.innerHTML = playQueue.map((track, index) => `<li class="queue-item ${index === queueIndex ? "is-current" : ""}"><button class="queue-play" data-queue-play="${index}" type="button"><span class="queue-number">${index === queueIndex ? "♫" : index + 1}</span><span><strong>${escapeHTML(track.title)}</strong><small>${escapeHTML(track.artist)}</small></span></button><span class="queue-item-actions"><button type="button" data-queue-move="${index}" data-direction="-1" aria-label="Переместить выше" ${index === 0 ? "disabled" : ""}>↑</button><button type="button" data-queue-move="${index}" data-direction="1" aria-label="Переместить ниже" ${index === playQueue.length - 1 ? "disabled" : ""}>↓</button><button type="button" data-queue-remove="${index}" aria-label="Убрать из очереди">×</button></span></li>`).join("");
    document.querySelector("#shuffle-button").setAttribute("aria-pressed", String(shuffleEnabled));
    document.querySelector("#player-shuffle").setAttribute("aria-pressed", String(shuffleEnabled));
    document.querySelector("#player-footer-count").textContent = `${playQueue.length} треков в очереди`;
  }

  function addToQueue(track, playNow = false) {
    if (!track) return;
    playQueue.push(track);
    persistQueue();
    if (playNow || queueIndex < 0) playQueueAt(playQueue.length - 1);
    else showToast("Добавлено в очередь");
  }

  function playQueueAt(index) {
    if (index < 0 || index >= playQueue.length) return;
    queueIndex = index;
    persistQueue();
    playTrack(playQueue[index]);
  }

  function playNext(direction = 1) {
    if (!playQueue.length) return;
    let next = queueIndex + direction;
    if (next < 0) next = playQueue.length - 1;
    if (next >= playQueue.length) next = 0;
    playQueueAt(next);
  }

  async function startRadio(prompt) {
    const text = String(prompt || "").trim();
    if (!text) { showToast("Опиши настроение или тему"); return; }
    showToast("Подбираю музыку под твой вайб…");
    const variants = [text, `${text} music`, `${text} indie electronic jazz`];
    try {
      const batches = await Promise.all(variants.map((query) => api("search", { query })));
      const seen = new Set();
      const tracks = batches.flatMap((batch) => batch.tracks || []).filter((track) => {
        const key = `${track.artist} ${track.title}`.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
        if (!key || seen.has(key) || !(track.jamendo_id || track.youtube_id)) return false;
        seen.add(key); return true;
      }).slice(0, 18);
      if (!tracks.length) { showToast("Не нашёл доступных для плеера треков. Попробуй другой вайб."); return; }
      playQueue = tracks;
      queueIndex = -1;
      persistQueue();
      setView("radio");
      playQueueAt(0);
      showToast(`Радио готово · ${tracks.length} треков в очереди`);
    } catch (error) { showToast(error.message); }
  }

  function shuffleQueue() {
    const start = queueIndex >= 0 ? queueIndex + 1 : 0;
    const rest = playQueue.slice(start);
    for (let i = rest.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [rest[i], rest[j]] = [rest[j], rest[i]]; }
    playQueue.splice(start, rest.length, ...rest);
    shuffleEnabled = !shuffleEnabled;
    persistQueue();
  }

  renderQueue();
  audioPlayer.volume = Number(document.querySelector("#player-volume").value) / 100;
  audioPlayer.addEventListener("ended", () => { if (queueIndex >= 0) playNext(1); });
  audioPlayer.addEventListener("timeupdate", updateProgress);
  audioPlayer.addEventListener("loadedmetadata", updateProgress);
  audioPlayer.addEventListener("play", () => { updatePlayButtons(true); updateMediaSession(currentPlayingTrack, true); });
  audioPlayer.addEventListener("pause", () => { updatePlayButtons(false); updateMediaSession(currentPlayingTrack, false); });
  document.querySelector("#player-next").addEventListener("click", () => playNext(1));
  document.querySelector("#player-prev").addEventListener("click", () => playNext(-1));
  document.querySelector("#player-queue").addEventListener("click", () => { if (activePlayerSource === "youtube") closePlayer(); setView("radio"); });
  document.querySelector("#shuffle-button").addEventListener("click", shuffleQueue);
  document.querySelector("#player-open").addEventListener("click", openPlayer);
  document.querySelector("#player-close").addEventListener("click", closePlayer);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !playerOverlay.classList.contains("hidden")) closePlayer(); });
  document.querySelector("#player-toggle").addEventListener("click", togglePlayback);
  document.querySelector("#player-toggle-mini").addEventListener("click", togglePlayback);
  document.querySelector("#player-next-full").addEventListener("click", () => playNext(1));
  document.querySelector("#player-prev-full").addEventListener("click", () => playNext(-1));
  document.querySelector("#player-shuffle").addEventListener("click", shuffleQueue);
  document.querySelector("#player-open-queue").addEventListener("click", () => { closePlayer(); setView("radio"); });
  document.querySelector("#player-queue-full").addEventListener("click", () => { closePlayer(); setView("radio"); });
  document.querySelector("#player-seek").addEventListener("change", (event) => {
    const position = Number(event.target.value) / 1000;
    if (activePlayerSource === "youtube" && youtubePlayer?.getDuration) youtubePlayer.seekTo(youtubePlayer.getDuration() * position, true);
    else if (activePlayerSource === "audio" && audioPlayer.duration) audioPlayer.currentTime = audioPlayer.duration * position;
  });
  document.querySelector("#player-volume").addEventListener("input", (event) => {
    const level = Number(event.target.value);
    audioPlayer.volume = level / 100;
    youtubePlayer?.setVolume?.(level);
  });
  document.querySelector("#send-to-chat").addEventListener("click", (event) => sendTrackToChat(currentPlayingTrack, event.currentTarget));
  document.querySelector("#player-save").addEventListener("click", async () => {
    if (!currentPlayingTrack) return;
    try {
      const result = await api("save", { track: currentPlayingTrack });
      showToast(result.saved ? "Трек добавлен в библиотеку" : "Уже сохранён");
      await loadLibrary();
    } catch (error) { showToast(error.message); }
  });
  document.querySelector("#clear-queue").addEventListener("click", () => { playQueue = []; queueIndex = -1; persistQueue(); audioPlayer.pause(); youtubePlayer?.pauseVideo?.(); closePlayer(); showToast("Очередь очищена"); });
  document.querySelectorAll("#radio-form, #radio-form-page").forEach((radioForm) => radioForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const prompt = radioForm.querySelector("input").value;
    document.querySelectorAll("#radio-prompt, #radio-prompt-page").forEach((input) => { input.value = prompt; });
    startRadio(prompt);
  }));
  document.body.addEventListener("click", (event) => {
    const mood = event.target.closest("[data-mood]");
    if (mood) { document.querySelectorAll("#radio-prompt, #radio-prompt-page").forEach((input) => { input.value = mood.dataset.mood; }); startRadio(mood.dataset.mood); }
  });

  function openLink(url) {
    const safe = safeLink(url);
    if (!safe) return;
    if (tg?.openLink) tg.openLink(safe);
    else window.open(safe, "_blank", "noopener,noreferrer");
  }

  async function clickAction(event) {
    const link = event.target.closest("[data-open-link]");
    if (link) { event.preventDefault(); openLink(link.dataset.openLink); return; }
    const provider = event.target.closest("[data-open-source]");
    if (provider) {
      event.preventDefault();
      const url = safeLink(provider.dataset.openSource);
      if (url) openLink(url);
      return;
    }
    const suggestion = event.target.closest("[data-query]");
    if (suggestion) {
      queryInput.value = suggestion.dataset.query;
      search(queryInput.value);
      return;
    }
    const playButton = event.target.closest("[data-play-kind]");
    if (playButton) {
      const groups = { search: currentTracks, library: libraryTracks, playlist: currentPlaylistTracks };
      const track = groups[playButton.dataset.playKind]?.[Number(playButton.dataset.playIndex)];
      playQueue = [track].filter(Boolean); queueIndex = 0; persistQueue(); playQueueAt(0);
      return;
    }
    const sendButton = event.target.closest("[data-send-chat-kind]");
    if (sendButton) {
      const groups = { search: currentTracks, library: libraryTracks, playlist: currentPlaylistTracks };
      const track = groups[sendButton.dataset.sendChatKind]?.[Number(sendButton.dataset.sendChatIndex)];
      await sendTrackToChat(track, sendButton);
      return;
    }
    const queuePlay = event.target.closest("[data-queue-play]");
    if (queuePlay) { playQueueAt(Number(queuePlay.dataset.queuePlay)); return; }
    const queueAdd = event.target.closest("[data-queue-index]");
    if (queueAdd) { addToQueue(currentTracks[Number(queueAdd.dataset.queueIndex)]); return; }
    const queueMove = event.target.closest("[data-queue-move]");
    if (queueMove) {
      const index = Number(queueMove.dataset.queueMove), next = index + Number(queueMove.dataset.direction);
      if (next >= 0 && next < playQueue.length) { [playQueue[index], playQueue[next]] = [playQueue[next], playQueue[index]]; if (queueIndex === index) queueIndex = next; else if (queueIndex === next) queueIndex = index; persistQueue(); }
      return;
    }
    const queueRemove = event.target.closest("[data-queue-remove]");
    if (queueRemove) {
      const index = Number(queueRemove.dataset.queueRemove); playQueue.splice(index, 1);
      if (index < queueIndex) queueIndex--; else if (index === queueIndex) { queueIndex = -1; if (playQueue.length) playQueueAt(Math.min(index, playQueue.length - 1)); else { audioPlayer.pause(); youtubePlayer?.pauseVideo?.(); } }
      persistQueue(); return;
    }
    const openPlaylistButton = event.target.closest("[data-open-playlist]");
    if (openPlaylistButton) {
      try { await openPlaylist(Number(openPlaylistButton.dataset.openPlaylist)); }
      catch (error) { showToast(error.message); }
      return;
    }
    if (event.target.closest("#playlist-play-all")) {
      if (!currentPlaylistTracks.length) { showToast("В этом плейлисте пока нет треков"); return; }
      playQueue = [...currentPlaylistTracks]; queueIndex = -1; persistQueue(); playQueueAt(0); return;
    }
    const deletePlaylistButton = event.target.closest("[data-delete-playlist]");
    if (deletePlaylistButton) {
      try {
        await api("delete_playlist", { playlistId: Number(deletePlaylistButton.dataset.deletePlaylist) });
        selectedPlaylistId = null;
        await loadPlaylists();
        showToast("Плейлист удалён");
      } catch (error) { showToast(error.message); }
      return;
    }
    const removePlaylistTrack = event.target.closest("[data-remove-playlist-track]");
    if (removePlaylistTrack && selectedPlaylistId) {
      try {
        await api("remove_from_playlist", { playlistId: selectedPlaylistId, trackId: Number(removePlaylistTrack.dataset.removePlaylistTrack) });
        await loadLibrary();
        showToast("Трек убран из плейлиста");
      } catch (error) { showToast(error.message); }
      return;
    }
    const removeChannel = event.target.closest("[data-remove-channel]");
    if (removeChannel) {
      try {
        await api("remove_news_channel", { channelKey: removeChannel.dataset.removeChannel });
        await loadAccount();
        showToast("Канал удалён из источников");
      } catch (error) { showToast(error.message); }
      return;
    }
    const saveButton = event.target.closest("[data-save]");
    if (saveButton) {
      const track = currentTracks[Number(saveButton.dataset.save)];
      if (!track) return;
      saveButton.disabled = true;
      try {
        const result = await api("save", { track });
        showToast(result.saved ? "Трек добавлен в твою музыку" : "Этот трек уже в библиотеке");
        await loadLibrary();
      } catch (error) { showToast(error.message); saveButton.disabled = false; }
      return;
    }
    const removeButton = event.target.closest("[data-remove]");
    if (removeButton) {
      try {
        await api("remove", { trackId: Number(removeButton.dataset.remove) });
        await loadLibrary();
        showToast("Трек удалён из библиотеки");
      } catch (error) { showToast(error.message); }
    }
  }

  function setView(view) {
    currentView = view;
    const library = view === "library";
    const account = view === "account";
    const radio = view === "radio";
    navItems.forEach((item) => {
      const active = item.dataset.view === view;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    document.querySelector("#search-view").classList.toggle("hidden", library || account || radio);
    document.querySelector("#radio-view").classList.toggle("hidden", !radio);
    document.querySelector("#library-view").classList.toggle("hidden", !library);
    document.querySelector("#account-view").classList.toggle("hidden", !account);
    searchArea.classList.toggle("hidden", library || account || radio);
    pageTitle.innerHTML = library
      ? 'Библиотека<span class="title-period">.</span>'
      : account ? 'Аккаунт<span class="title-period">.</span>' : radio ? 'Радио<span class="title-period">.</span>' : 'Музыка<span class="title-period">.</span>';
    pageDescription.innerHTML = library
      ? "Всё, что ты сохранил,<br class=\"wide-only\" /> всегда под рукой."
      : account ? "Подключай источники<br class=\"wide-only\" /> и управляй своим планом."
        : radio ? "Своя волна из музыки<br class=\"wide-only\" /> под настроение."
        : "Ищи любимые треки и собирай<br class=\"wide-only\" /> свою коллекцию в одном месте.";
    if (library) loadLibrary();
    if (account) loadAccount().catch((error) => showToast(error.message));
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); search(queryInput.value); });
  document.querySelector("#playlist-form").addEventListener("submit", createPlaylist);
  document.querySelector("#premium-button").addEventListener("click", purchasePremium);
  document.querySelector("#news-channel-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.querySelector("#news-channel");
    try {
      const result = await api("add_news_channel", { channel: input.value.trim() });
      input.value = "";
      await loadAccount();
      showToast(result.added ? "Канал сохранён" : "Этот канал уже добавлен");
    } catch (error) { showToast(error.message); }
  });
  document.body.addEventListener("change", async (event) => {
    const select = event.target.closest("[data-add-index]");
    if (!select || !select.value) return;
    const track = currentTracks[Number(select.dataset.addIndex)];
    const playlistId = Number(select.value);
    select.disabled = true;
    try {
      const result = await api("add_to_playlist", { playlistId, track });
      showToast(result.added ? "Добавлено в плейлист" : "Этот трек уже в плейлисте");
      selectedPlaylistId = playlistId;
      await loadLibrary();
    } catch (error) { showToast(error.message); }
    finally { select.disabled = false; select.value = ""; }
  });
  document.querySelector("#import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.querySelector("#import-url");
    const button = event.submitter;
    if (button) button.disabled = true;
    try {
      const result = await api("import", { url: input.value.trim() });
      showToast(result.saved ? `Ссылка ${result.service} сохранена` : "Эта ссылка уже в библиотеке");
      input.value = "";
      await loadLibrary();
      await loadAccount();
    } catch (error) { showToast(error.message); }
    finally { if (button) button.disabled = false; }
  });
  document.body.addEventListener("click", clickAction);
  document.body.addEventListener("error", (event) => {
    if (event.target.matches(".cover-art")) event.target.remove();
  }, true);
  navItems.forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));

  if (user?.first_name) document.querySelector("#user-chip span:last-child").textContent = `Привет, ${user.first_name}`;
  applyTheme();
  tg?.onEvent?.("themeChanged", applyTheme);
  tg?.MainButton?.hide?.();
  if (!initData) showToast("Открой приложение из чата с BamBook");
  else loadLibrary();
})();

