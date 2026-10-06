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
  const pageTitle = document.querySelector("#page-title");
  const pageDescription = document.querySelector("#page-description");
  let currentView = "search";
  let currentTracks = [];
  let libraryTracks = [];
  let currentPlaylistTracks = [];
  let playlists = [];
  let selectedPlaylistId = null;
  let accountState = null;
  let toastTimer;
  let currentAudioObjectUrl = "";

  function applyTheme() {
    const theme = tg?.colorScheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.body.dataset.theme = theme;
    const color = theme === "dark" ? "#101810" : "#d9f39e";
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

  function safePreview(value) {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase();
      return url.protocol === "https:" && (host.endsWith(".itunes.apple.com") || host.endsWith(".mzstatic.com")) ? url.href : "";
    } catch { return ""; }
  }

  function bookmarkIcon() {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4.8A1.8 1.8 0 0 1 7.8 3h8.4A1.8 1.8 0 0 1 18 4.8V21l-6-3.8L6 21V4.8Z"/></svg>';
  }

  function card(track, index, { kind = "search", playlistId = null } = {}) {
    const art = safeLink(track.artwork);
    const hasPreview = Boolean(safePreview(track.preview_url));
    const hasFullAudio = /^\d{1,12}$/.test(String(track.jamendo_id || ""));
    const canPlay = hasFullAudio || hasPreview;
    const playLabel = hasFullAudio ? "▶ Полный трек" : hasPreview ? "▶ Превью" : "Нет аудио";
    const play = `<button class="preview-button" data-play-kind="${kind}" data-play-index="${index}" type="button" ${canPlay ? "" : "disabled"}>${playLabel}</button>`;
    let action = "";
    if (kind === "search") {
      const options = playlists.map((item) => `<option value="${Number(item.id)}">${escapeHTML(item.name)}</option>`).join("");
      action = `<button class="save-button" data-save="${index}" type="button" aria-label="Сохранить ${escapeHTML(track.title)}">${bookmarkIcon()}</button><select class="playlist-add-select" data-add-index="${index}" aria-label="Добавить в плейлист" ${playlists.length ? "" : "disabled"}><option value="">+ В плейлист</option>${options}</select>`;
    } else if (kind === "playlist") {
      action = `<button class="playlist-track-remove" data-remove-playlist-track="${Number(track.id)}" type="button">Убрать</button>`;
    } else {
      action = `<button class="remove-button" data-remove="${Number(track.id)}" type="button" aria-label="Удалить ${escapeHTML(track.title)} из библиотеки"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3"/></svg></button>`;
    }
    return `<article class="track-card">
      <div class="cover" aria-hidden="true">♫${art ? `<img class="cover-art" src="${escapeHTML(art)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</div>
      <div class="track-info"><strong class="track-title">${escapeHTML(track.title)}</strong>
      <span class="track-meta">${escapeHTML(track.artist)}${track.album ? ` · ${escapeHTML(track.album)}` : ""}</span>
      <div class="track-extra">${track.duration ? `<span class="duration">${escapeHTML(track.duration)}</span>` : ""}</div><div class="track-tools">${play}${action}</div></div></article>`;
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

  async function playTrack(track) {
    const preview = safePreview(track?.preview_url);
    const jamendoId = /^\d{1,12}$/.test(String(track?.jamendo_id || "")) ? String(track.jamendo_id) : "";
    if (!preview && !jamendoId) { showToast("Встроенное аудио для этого результата недоступно"); return; }
    document.querySelector("#player-title").textContent = track.title;
    document.querySelector("#player-artist").textContent = track.artist;
    miniPlayer.classList.add("active");
    audioPlayer.pause();
    if (currentAudioObjectUrl) {
      URL.revokeObjectURL(currentAudioObjectUrl);
      currentAudioObjectUrl = "";
    }
    audioPlayer.classList.remove("hidden");
    if (jamendoId) {
      audioPlayer.removeAttribute("src");
      audioPlayer.load();
      showToast("Загружаю полный трек…");
      try {
        const response = await fetch("/api/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
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
      } catch (error) {
        showToast(error.message || "Не удалось загрузить полный трек");
      }
    } else {
      audioPlayer.src = preview;
      audioPlayer.play().catch(() => showToast("Нажми ▶ в плеере, чтобы начать прослушивание"));
    }
  }

  function openLink(url) {
    const safe = safeLink(url);
    if (!safe) return;
    if (tg?.openLink) tg.openLink(safe);
    else window.open(safe, "_blank", "noopener,noreferrer");
  }

  async function clickAction(event) {
    const link = event.target.closest("[data-open-link]");
    if (link) { event.preventDefault(); openLink(link.dataset.openLink); return; }
    const suggestion = event.target.closest("[data-query]");
    if (suggestion) {
      queryInput.value = suggestion.dataset.query;
      search(queryInput.value);
      return;
    }
    const playButton = event.target.closest("[data-play-kind]");
    if (playButton) {
      const groups = { search: currentTracks, library: libraryTracks, playlist: currentPlaylistTracks };
      playTrack(groups[playButton.dataset.playKind]?.[Number(playButton.dataset.playIndex)]);
      return;
    }
    const openPlaylistButton = event.target.closest("[data-open-playlist]");
    if (openPlaylistButton) {
      try { await openPlaylist(Number(openPlaylistButton.dataset.openPlaylist)); }
      catch (error) { showToast(error.message); }
      return;
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
    navItems.forEach((item) => {
      const active = item.dataset.view === view;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    document.querySelector("#search-view").classList.toggle("hidden", library || account);
    document.querySelector("#library-view").classList.toggle("hidden", !library);
    document.querySelector("#account-view").classList.toggle("hidden", !account);
    searchArea.classList.toggle("hidden", library || account);
    pageTitle.innerHTML = library
      ? 'Библиотека<span class="title-period">.</span>'
      : account ? 'Аккаунт<span class="title-period">.</span>' : 'Музыка<span class="title-period">.</span>';
    pageDescription.innerHTML = library
      ? "Всё, что ты сохранил,<br class=\"wide-only\" /> всегда под рукой."
      : account ? "Подключай источники<br class=\"wide-only\" /> и управляй своим планом."
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
