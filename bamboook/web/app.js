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
  const pageTitle = document.querySelector("#page-title");
  const pageDescription = document.querySelector("#page-description");
  let currentView = "search";
  let currentTracks = [];
  let toastTimer;

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

  function bookmarkIcon() {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4.8A1.8 1.8 0 0 1 7.8 3h8.4A1.8 1.8 0 0 1 18 4.8V21l-6-3.8L6 21V4.8Z"/></svg>';
  }

  function card(track, index, saved = false) {
    const links = track.links?.length ? track.links : [{ source: track.source || "Источник", url: track.url }];
    const linkMarkup = links.map((item) => {
      const href = safeLink(item.url);
      return href ? `<a class="source-link" href="${escapeHTML(href)}" data-open-link="${escapeHTML(href)}">${escapeHTML(item.source)}</a>` : "";
    }).join("");
    const art = safeLink(track.artwork);
    const action = saved
      ? `<button class="remove-button" data-remove="${Number(track.id)}" type="button" aria-label="Удалить ${escapeHTML(track.title)} из библиотеки"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 14h10l1-14M9 7V4h6v3"/></svg></button>`
      : `<button class="save-button" data-save="${index}" type="button" aria-label="Сохранить ${escapeHTML(track.title)}">${bookmarkIcon()}</button>`;
    return `<article class="track-card">
      <div class="cover" aria-hidden="true">♫${art ? `<img class="cover-art" src="${escapeHTML(art)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</div>
      <div class="track-info"><strong class="track-title">${escapeHTML(track.title)}</strong>
      <span class="track-meta">${escapeHTML(track.artist)}${track.album ? ` · ${escapeHTML(track.album)}` : ""}</span>
      <div class="track-extra">${track.duration ? `<span class="duration">${escapeHTML(track.duration)}</span>` : ""}<span class="source-links">${linkMarkup}</span></div></div>${action}</article>`;
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
      results.innerHTML = currentTracks.map((track, index) => card(track, index)).join("");
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
      libraryResults.innerHTML = tracks.map((track) => card(track, 0, true)).join("");
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
      importsList.innerHTML = (imported.imports || []).map((item) => {
        const href = safeLink(item.url);
        return href ? `<div class="import-item"><strong>${escapeHTML(item.service)}</strong><a href="${escapeHTML(href)}" data-open-link="${escapeHTML(href)}">Открыть ссылку ↗</a></div>` : "";
      }).join("");
    } catch (error) {
      renderEmpty(libraryResults, "Не удалось открыть коллекцию", error.message);
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
    navItems.forEach((item) => {
      const active = item.dataset.view === view;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    document.querySelector("#search-view").classList.toggle("hidden", library);
    document.querySelector("#library-view").classList.toggle("hidden", !library);
    searchArea.classList.toggle("hidden", library);
    pageTitle.innerHTML = library
      ? 'Библиотека<span class="title-period">.</span>'
      : 'Музыка<span class="title-period">.</span>';
    pageDescription.innerHTML = library
      ? "Всё, что ты сохранил,<br class=\"wide-only\" /> всегда под рукой."
      : "Ищи любимые треки и собирай<br class=\"wide-only\" /> свою коллекцию в одном месте.";
    if (library) loadLibrary();
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); search(queryInput.value); });
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
})();
