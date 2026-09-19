// Music panel — Phase 2/3: live control wiring + playlists.
// Talks only to this website's own /api/panel/* routes (never the bot
// directly, and never sees any shared secret) — see app.py for the proxy.
(() => {
  // ── Sidebar tab switching ───────────────────────────────────────────────
  const links = document.querySelectorAll(".music-sidebar-link");
  links.forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      if (link.classList.contains("is-soon")) return;
      links.forEach((l) => l.classList.remove("active"));
      link.classList.add("active");
      const target = link.dataset.panel;
      document.querySelectorAll(".music-view").forEach((view) => {
        view.hidden = view.id !== `panel-${target}`;
      });
      if (target === "playlists") loadPlaylists();
      if (target === "home") loadHome();
      if (target === "liked") loadLikedSongs();
    });
  });

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
  }

  async function callApi(path, method = "GET", body = null) {
    const opts = { method, headers: {} };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    try {
      const res = await fetch(path, opts);
      return await res.json();
    } catch {
      return { error: "network_error" };
    }
  }

  // ── Shared toast (brief feedback for actions taken outside Live Control,
  // e.g. the "⋮" menu on a recommendations/search row) ──────────────────────
  const liveToast = document.getElementById("liveToast");
  let toastTimer = null;
  function showToast(text, isError = false) {
    if (!liveToast) return;
    clearTimeout(toastTimer);
    liveToast.textContent = text;
    liveToast.classList.toggle("is-error", isError);
    liveToast.hidden = false;
    toastTimer = setTimeout(() => { liveToast.hidden = true; }, 4000);
  }

  const TRACK_ERRORS = {
    not_connected: "Jarvis isn't in a voice channel yet — join one in Live Control first.",
    no_results: "Couldn't find anything playable for that.",
    lavalink_error: "Playback error — try again.",
    bot_unreachable: "Jarvis didn't respond in time — try again in a moment.",
    network_error: "Network error — try again.",
    not_configured: "Music panel isn't fully configured yet.",
  };

  // ── Live control (guild-dependent — only if the user has a controllable server) ──

  const guildSelect = document.getElementById("guildSelect");
  let currentGuildId = guildSelect ? guildSelect.value : null;

  if (guildSelect) {
    const npArt = document.getElementById("npArt");
    const npTitle = document.getElementById("npTitle");
    const npSub = document.getElementById("npSub");
    const npProgressFill = document.getElementById("npProgressFill");
    const btnPlayPause = document.getElementById("btnPlayPause");
    const btnSkip = document.getElementById("btnSkip");
    const queueList = document.getElementById("queueList");
    const joinCard = document.getElementById("joinCard");
    const channelSelect = document.getElementById("channelSelect");
    const btnJoin = document.getElementById("btnJoin");
    const playInput = document.getElementById("playInput");
    const btnPlay = document.getElementById("btnPlay");
    const btnLike = document.getElementById("btnLike");

    let pollTimer = null;

    async function api(path, method = "GET", body = null) {
      return callApi(`/api/panel/${currentGuildId}${path}`, method, body);
    }

    function renderState(state) {
      if (!state || state.error) {
        npTitle.textContent = "Couldn't load state";
        npSub.textContent = state && state.error ? state.error : "";
        return;
      }

      if (!state.connected) {
        joinCard.hidden = false;
        npTitle.textContent = "Not connected";
        npSub.textContent = "Join a voice channel below to get started";
        npProgressFill.style.width = "0%";
        btnPlayPause.disabled = true;
        btnSkip.disabled = true;
        btnLike.disabled = true;
        btnLike.textContent = "🤍";
        playInput.disabled = true;
        btnPlay.disabled = true;
        queueList.innerHTML = '<li class="music-queue-empty">Not connected to a voice channel.</li>';
        loadChannels();
        return;
      }

      joinCard.hidden = true;
      playInput.disabled = false;
      btnPlay.disabled = false;

      if (state.current) {
        npTitle.textContent = state.current.title;
        npSub.textContent = `${state.current.author || ""} · ${state.channel_name || ""}`.trim();
        npArt.style.backgroundImage = state.current.artwork ? `url(${state.current.artwork})` : "none";
        const pct = state.current.length ? Math.min(100, (state.position_ms / state.current.length) * 100) : 0;
        npProgressFill.style.width = `${pct}%`;
        btnPlayPause.textContent = state.paused ? "▶" : "⏸";
        btnPlayPause.disabled = false;
        btnSkip.disabled = false;
        btnLike.disabled = false;
        btnLike.textContent = state.current.liked ? "❤️" : "🤍";
      } else {
        npTitle.textContent = "Nothing playing";
        npSub.textContent = state.channel_name ? `Connected to ${state.channel_name}` : "";
        npArt.style.backgroundImage = "none";
        npProgressFill.style.width = "0%";
        btnPlayPause.disabled = true;
        btnSkip.disabled = true;
        btnLike.disabled = true;
        btnLike.textContent = "🤍";
      }

      if (state.queue && state.queue.length) {
        queueList.innerHTML = "";
        state.queue.forEach((t, i) => {
          const li = document.createElement("li");
          li.className = "music-queue-item";
          li.innerHTML = `
            <span><span class="qi-title">${escapeHtml(t.title)}</span> <span class="qi-author">${escapeHtml(t.author)}</span></span>
            <button class="music-queue-remove" data-index="${i}" title="Remove">✕</button>
          `;
          queueList.appendChild(li);
        });
        queueList.querySelectorAll(".music-queue-remove").forEach((btn) => {
          btn.addEventListener("click", async () => {
            await api("/queue/remove", "POST", { index: parseInt(btn.dataset.index, 10) });
            refresh();
          });
        });
      } else {
        queueList.innerHTML = '<li class="music-queue-empty">Queue is empty.</li>';
      }
    }

    async function loadChannels() {
      const data = await api("/channels");
      if (!data.channels) return;
      channelSelect.innerHTML = data.channels
        .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
        .join("");
    }

    async function refresh() {
      renderState(await api("/state"));
    }

    function startPolling() {
      if (pollTimer) clearInterval(pollTimer);
      refresh();
      pollTimer = setInterval(refresh, 3000);
    }

    guildSelect.addEventListener("change", () => {
      currentGuildId = guildSelect.value;
      startPolling();
    });

    const joinToast = document.getElementById("joinToast");
    const JOIN_ERRORS = {
      channel_not_found: "That channel couldn't be found — try picking it again.",
      connect_failed: "Couldn't connect to that channel (check the bot has permission to join/speak there).",
      not_configured: "Music panel isn't fully configured yet.",
      bot_unreachable: "Jarvis didn't respond in time — try again in a moment.",
      network_error: "Network error — try again.",
    };

    btnJoin.addEventListener("click", async () => {
      const channelId = channelSelect.value;
      if (!channelId) return;
      btnJoin.disabled = true;
      joinToast.hidden = true;
      // IMPORTANT: channelId is a Discord snowflake — a 17-19 digit integer.
      // JS numbers only keep full precision up to 2^53, so running this
      // through parseInt()/Number() silently corrupts it into the wrong ID
      // (that was the actual bug behind "Join channel" doing nothing: the
      // backend received a mangled ID, found no matching channel, and the
      // UI never showed the resulting error). Send it as the string it
      // already is — the backend parses it with Python's int(), which has
      // no such precision limit.
      const result = await api("/join", "POST", { channel_id: channelId });
      btnJoin.disabled = false;
      if (!result || !result.ok) {
        const errKey = result && result.error;
        joinToast.textContent = JOIN_ERRORS[errKey] || "Couldn't join that channel. Try again.";
        joinToast.classList.add("is-error");
        joinToast.hidden = false;
      }
      refresh();
    });

    btnPlayPause.addEventListener("click", async () => { await api("/pause", "POST"); refresh(); });
    btnSkip.addEventListener("click", async () => { await api("/skip", "POST"); refresh(); });
    btnLike.addEventListener("click", async () => {
      btnLike.disabled = true;
      await api("/like", "POST");
      refresh();
    });

    btnPlay.addEventListener("click", async () => {
      const query = playInput.value.trim();
      if (!query) return;
      hideSuggestions();
      btnPlay.disabled = true;
      const result = await api("/play", "POST", { query });
      playInput.value = "";
      btnPlay.disabled = false;
      if (result && result.ok) {
        showToast(result.action === "queued" ? `Queued "${result.track?.title || query}".` : `Now playing "${result.track?.title || query}".`);
      } else {
        showToast(TRACK_ERRORS[result && result.error] || "Couldn't play that. Try again.", true);
      }
      refresh();
    });
    playInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !suggestionsList.hidden && activeSuggestionIndex >= 0) {
        e.preventDefault();
        chooseSuggestion(activeSuggestionIndex);
        return;
      }
      if (e.key === "Enter") { hideSuggestions(); btnPlay.click(); return; }
      if (e.key === "Escape") { hideSuggestions(); return; }
      if (e.key === "ArrowDown" && !suggestionsList.hidden) {
        e.preventDefault();
        moveSuggestionActive(1);
      }
      if (e.key === "ArrowUp" && !suggestionsList.hidden) {
        e.preventDefault();
        moveSuggestionActive(-1);
      }
    });

    // ── Search-as-you-type suggestions ───────────────────────────────────
    const suggestionsList = document.getElementById("searchSuggestions");
    let suggestionsAbortTimer = null;
    let suggestionsSeq = 0;
    let activeSuggestionIndex = -1;

    function hideSuggestions() {
      suggestionsList.hidden = true;
      suggestionsList.innerHTML = "";
      activeSuggestionIndex = -1;
    }

    function moveSuggestionActive(delta) {
      const items = suggestionsList.querySelectorAll(".music-search-suggestion");
      if (!items.length) return;
      activeSuggestionIndex = (activeSuggestionIndex + delta + items.length) % items.length;
      items.forEach((el, i) => el.classList.toggle("is-active", i === activeSuggestionIndex));
    }

    function chooseSuggestion(index) {
      const items = suggestionsList.querySelectorAll(".music-search-suggestion");
      const el = items[index];
      if (!el) return;
      playInput.value = el.dataset.query;
      hideSuggestions();
      btnPlay.click();
    }

    async function fetchSuggestions(query) {
      const seq = ++suggestionsSeq;
      const data = await callApi(`/api/panel/${currentGuildId}/search?q=${encodeURIComponent(query)}`);
      if (seq !== suggestionsSeq) return; // a newer keystroke already superseded this request
      if (!data.ok || !data.results || !data.results.length) {
        hideSuggestions();
        return;
      }
      suggestionsList.innerHTML = "";
      data.results.forEach((t) => {
        const li = document.createElement("li");
        li.className = "music-search-suggestion";
        li.dataset.query = t.uri || `${t.author || ""} ${t.title || ""}`.trim();
        li.innerHTML = `
          <span class="qi-title">${escapeHtml(t.title)}</span>
          <span class="qi-author">${escapeHtml(t.author || "")}</span>
        `;
        li.addEventListener("mousedown", (e) => {
          // mousedown (not click) fires before the input's blur handler
          // hides the list, so the click actually lands on the item.
          e.preventDefault();
          playInput.value = t.uri || `${t.author || ""} ${t.title || ""}`.trim();
          hideSuggestions();
          btnPlay.click();
        });
        suggestionsList.appendChild(li);
      });
      activeSuggestionIndex = -1;
      suggestionsList.hidden = false;
    }

    playInput.addEventListener("input", () => {
      clearTimeout(suggestionsAbortTimer);
      const query = playInput.value.trim();
      if (!currentGuildId || query.length < 2) {
        hideSuggestions();
        return;
      }
      // Debounce — wait for a pause in typing before hitting the search
      // endpoint, same idea as a Google search box.
      suggestionsAbortTimer = setTimeout(() => fetchSuggestions(query), 300);
    });
    playInput.addEventListener("blur", () => {
      // Small delay so a suggestion's mousedown can still register first.
      setTimeout(hideSuggestions, 150);
    });

    startPolling();
  }

  // ── Home (mood browse + "based on your likes") ─────────────────────────

  async function playTrackAction(t, mode) {
    if (!currentGuildId) {
      showToast("Pick a server in Live Control first.", true);
      return;
    }
    const query = t.uri || `${t.author || ""} ${t.title || ""}`.trim();
    const result = await callApi(`/api/panel/${currentGuildId}/play`, "POST", { query, mode });
    if (result && result.ok) {
      showToast(
        mode === "now" ? `Playing "${result.track?.title || t.title}" now.`
        : `Queued "${result.track?.title || t.title}".`
      );
    } else {
      showToast(TRACK_ERRORS[result && result.error] || "Couldn't do that. Try again.", true);
    }
    // Refreshes whatever Live Control is showing, if that tab happens to
    // be open elsewhere on the page — harmless no-op otherwise.
    const refreshBtn = document.getElementById("guildSelect");
    if (refreshBtn) refreshBtn.dispatchEvent(new Event("change", { bubbles: false }));
  }
  
  async function likeTrackAction(t) {
    const query = t.uri || `${t.author || ""} ${t.title || ""}`.trim();
    const result = await callApi("/api/panel/like", "POST", { query });
    if (result && result.ok) {
      showToast(result.already ? `Already liked "${t.title}".` : `Liked "${t.title}".`);
    } else {
      showToast("Couldn't like that. Try again.", true);
    }
  }

  async function addTrackToPlaylistAction(t) {
    const name = prompt(`Add "${t.title}" to which playlist? (existing name, or a new one)`);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    const query = t.uri || `${t.author || ""} ${t.title || ""}`.trim();
    const result = await callApi("/api/panel/playlists/add", "POST", { name: trimmed, query });
    if (result && result.ok) {
      showToast(`Added "${t.title}" to “${result.name || trimmed}”.`);
    } else {
      showToast("Couldn't add that to the playlist. Try again.", true);
    }
  }

  // Only one "⋮" dropdown is ever open at a time.
  let openTrackMenu = null;
  function closeOpenTrackMenu() {
    if (!openTrackMenu) return;
    openTrackMenu.dropdown.hidden = true;
    openTrackMenu.btn.classList.remove("is-open");
    openTrackMenu = null;
  }
  document.addEventListener("click", (e) => {
    if (openTrackMenu && !openTrackMenu.wrap.contains(e.target)) closeOpenTrackMenu();
  });

  function trackRow(t, _unusedOnAdd, extraButtons = "") {
    const li = document.createElement("li");
    li.className = "music-queue-item";
    li.innerHTML = `
      <span><span class="qi-title">${escapeHtml(t.title)}</span> <span class="qi-author">${escapeHtml(t.author)}</span></span>
      <span class="music-liked-actions">
        <span class="music-track-menu">
          <button type="button" class="music-track-menu-btn" title="More options" aria-haspopup="true" aria-expanded="false">⋮</button>
          <div class="music-track-menu-dropdown" hidden>
            <button type="button" class="music-track-menu-item" data-action="now">▶️ Play now</button>
            <button type="button" class="music-track-menu-item" data-action="queue">➕ Add to queue</button>
            <button type="button" class="music-track-menu-item" data-action="playlist">📁 Add to playlist</button>
            <button type="button" class="music-track-menu-item" data-action="like">🤍 Like</button>
          </div>
        </span>
        ${extraButtons}
      </span>
    `;
    const wrap = li.querySelector(".music-track-menu");
    const btn = wrap.querySelector(".music-track-menu-btn");
    const dropdown = wrap.querySelector(".music-track-menu-dropdown");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = !dropdown.hidden;
      closeOpenTrackMenu();
      if (isOpen) return; // was already open — this click just closes it
      dropdown.hidden = false;
      btn.classList.add("is-open");
      btn.setAttribute("aria-expanded", "true");
      openTrackMenu = { wrap, btn, dropdown };
    });
    dropdown.querySelectorAll(".music-track-menu-item").forEach((item) => {
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        closeOpenTrackMenu();
        const action = item.dataset.action;
        if (action === "now") playTrackAction(t, "now");
        else if (action === "queue") playTrackAction(t, "queue");
        else if (action === "playlist") addTrackToPlaylistAction(t);
        else if (action === "like") likeTrackAction(t);
      });
    });
    return li;
  }

  async function loadMood(id, label) {
    const resultsWrap = document.getElementById("moodResults");
    const resultsTitle = document.getElementById("moodResultsTitle");
    const resultsList = document.getElementById("moodResultsList");
    if (!resultsWrap) return;
    resultsWrap.hidden = false;
    resultsTitle.textContent = label;
    resultsList.innerHTML = '<li class="music-queue-empty">Loading…</li>';
    const data = await callApi(`/api/panel/home/mood/${encodeURIComponent(id)}`);
    if (!data.ok) {
      resultsList.innerHTML = '<li class="music-queue-empty">Couldn\u2019t load that mood right now.</li>';
      return;
    }
    resultsList.innerHTML = "";
    (data.tracks || []).forEach((t) => resultsList.appendChild(trackRow(t)));
  }

  async function loadHome() {
    const moodGrid = document.getElementById("moodGrid");
    const homeBasedOn = document.getElementById("homeBasedOn");
    if (!moodGrid) return;
    moodGrid.innerHTML = '<span class="music-queue-empty">Loading…</span>';
    homeBasedOn.innerHTML = "";
    const data = await callApi("/api/panel/home");
    if (!data.moods) {
      moodGrid.innerHTML = '<span class="music-queue-empty">Couldn\u2019t load recommendations right now.</span>';
      return;
    }

    moodGrid.innerHTML = "";
    data.moods.forEach((m) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "music-mood-chip";
      btn.textContent = m.label;
      btn.addEventListener("click", () => loadMood(m.id, m.label));
      moodGrid.appendChild(btn);
    });

    if (data.based_on && data.based_on.length) {
      data.based_on.forEach((section) => {
        const wrap = document.createElement("div");
        wrap.className = "music-home-section";
        const heading = document.createElement("h3");
        heading.className = "music-inbox-heading";
        heading.textContent = `Because you like ${section.seed}`;
        const ul = document.createElement("ul");
        ul.className = "music-queue-list";
        section.tracks.forEach((t) => ul.appendChild(trackRow(t)));
        wrap.appendChild(heading);
        wrap.appendChild(ul);
        homeBasedOn.appendChild(wrap);
      });
    } else {
      const note = document.createElement("p");
      note.className = "music-phase-note";
      note.textContent = "Like a few songs or play some music and we'll start recommending more like it here.";
      homeBasedOn.appendChild(note);
    }
  }

  // ── Liked Songs ──────────────────────────────────────────────────────────

  async function loadLikedSongs() {
    const likedList = document.getElementById("likedList");
    if (!likedList) return;
    likedList.innerHTML = '<li class="music-queue-empty">Loading your liked songs…</li>';
    const data = await callApi("/api/panel/liked");
    if (!data.liked) {
      likedList.innerHTML = '<li class="music-queue-empty">Couldn\u2019t load liked songs.</li>';
      return;
    }
    if (!data.liked.length) {
      likedList.innerHTML = '<li class="music-queue-empty">No liked songs yet — hit 🤍 on a track playing in Live Control.</li>';
      return;
    }
    likedList.innerHTML = "";
    data.liked.forEach((t, i) => {
      const row = trackRow(
        t, null,
        '<button class="music-queue-remove" title="Unlike">✕</button>'
      );
      row.querySelector('[title="Unlike"]').addEventListener("click", async () => {
        await callApi("/api/panel/liked/remove", "POST", { index: i });
        loadLikedSongs();
      });
      likedList.appendChild(row);
    });
  }

  // ── Playlists ────────────────────────────────────────────────────────────

  const playlistNamesEl = document.getElementById("playlistNames");
  if (!playlistNamesEl) return; // playlists UI not present on this page for some reason

  const playlistInboxEl = document.getElementById("playlistInbox");
  const newPlaylistInput = document.getElementById("newPlaylistInput");
  const newPlaylistName = document.getElementById("newPlaylistName");
  const btnCreatePlaylist = document.getElementById("btnCreatePlaylist");
  const playlistAddStatus = document.getElementById("playlistAddStatus");
  const playlistDetailEmpty = document.getElementById("playlistDetailEmpty");
  const playlistDetail = document.getElementById("playlistDetail");
  const playlistDetailName = document.getElementById("playlistDetailName");
  const playlistPermissionBadge = document.getElementById("playlistPermissionBadge");
  const playlistOwnerControls = document.getElementById("playlistOwnerControls");
  const playlistShareControls = document.getElementById("playlistShareControls");
  const playlistTracksEl = document.getElementById("playlistTracks");
  const renamePlaylistInput = document.getElementById("renamePlaylistInput");
  const btnRenamePlaylist = document.getElementById("btnRenamePlaylist");
  const btnDeletePlaylist = document.getElementById("btnDeletePlaylist");
  const btnPlayPlaylist = document.getElementById("btnPlayPlaylist");
  const shareTargetId = document.getElementById("shareTargetId");
  const sharePermission = document.getElementById("sharePermission");
  const btnSharePlaylist = document.getElementById("btnSharePlaylist");

  let currentPlaylistName = null;

  async function loadPlaylists() {
    const data = await callApi("/api/panel/playlists");
    if (data.playlists) {
      playlistNamesEl.innerHTML = data.playlists.length
        ? data.playlists.map((p) => `
            <li class="music-playlist-item" data-name="${escapeHtml(p.name)}">
              <span>${escapeHtml(p.name)}</span>
              <span class="pl-count">${p.track_count}</span>
            </li>`).join("")
        : '<li class="music-queue-empty">No playlists yet — add a song below.</li>';
      playlistNamesEl.querySelectorAll(".music-playlist-item").forEach((el) => {
        el.addEventListener("click", () => selectPlaylist(el.dataset.name));
      });
    }

    const inbox = await callApi("/api/panel/playlists/inbox");
    if (inbox.inbox) {
      playlistInboxEl.innerHTML = inbox.inbox.length
        ? inbox.inbox.map((e) => `
            <li class="music-playlist-item" data-name="${escapeHtml(e.name)}">
              <span>${escapeHtml(e.name)}</span>
              <span class="pl-count">${e.permission}</span>
            </li>`).join("")
        : '<li class="music-queue-empty">Nothing shared with you yet.</li>';
      playlistInboxEl.querySelectorAll(".music-playlist-item").forEach((el) => {
        el.addEventListener("click", () => selectPlaylist(el.dataset.name));
      });
    }
  }

  async function selectPlaylist(name) {
    currentPlaylistName = name;
    playlistNamesEl.querySelectorAll(".music-playlist-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.name === name);
    });

    const data = await callApi(`/api/panel/playlists/${encodeURIComponent(name)}`);
    if (!data.ok) {
      playlistDetailEmpty.hidden = false;
      playlistDetailEmpty.textContent = "Couldn't load that playlist.";
      playlistDetail.hidden = true;
      return;
    }

    playlistDetailEmpty.hidden = true;
    playlistDetail.hidden = false;
    playlistDetailName.textContent = data.name;
    playlistPermissionBadge.textContent = data.permission;

    const canEdit = data.permission === "owner" || data.permission === "write";
    const isOwner = data.permission === "owner";
    playlistOwnerControls.style.display = canEdit ? "flex" : "none";
    playlistShareControls.style.display = isOwner ? "flex" : "none";
    // Rename/delete only make sense for the actual owner, not a
    // write-collaborator — narrow further within the owner controls block.
    btnRenamePlaylist.style.display = isOwner ? "inline-flex" : "none";
    btnDeletePlaylist.style.display = isOwner ? "inline-flex" : "none";
    renamePlaylistInput.style.display = isOwner ? "inline-block" : "none";

    playlistTracksEl.innerHTML = data.tracks.length
      ? data.tracks.map((t, i) => `
          <li class="music-queue-item">
            <span><span class="qi-title">${escapeHtml(t.title)}</span> <span class="qi-author">${escapeHtml(t.author)}</span></span>
            ${canEdit ? `<button class="music-queue-remove" data-index="${i + 1}" title="Remove">✕</button>` : ""}
          </li>`).join("")
      : '<li class="music-queue-empty">This playlist is empty.</li>';

    playlistTracksEl.querySelectorAll(".music-queue-remove").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await callApi(`/api/panel/playlists/${encodeURIComponent(name)}/remove`, "POST", {
          index: parseInt(btn.dataset.index, 10),
        });
        selectPlaylist(name);
        loadPlaylists();
      });
    });
  }

  const ADD_ERRORS = {
    spotify_unavailable: "Couldn't read that Spotify link. Make sure the playlist or album is public and the link is correct.",
    no_results: "Nothing playable was found for that.",
    name_required: "Give the playlist a name when adding a single song.",
    not_logged_in: "Your session expired — log in with Discord again.",
    bot_unreachable: "Jarvis didn't respond in time. If it was a big playlist, check whether it appeared before retrying.",
    network_error: "Network error — try again.",
  };

  function showAddStatus(text, isError) {
    playlistAddStatus.textContent = text;
    playlistAddStatus.classList.toggle("is-error", !!isError);
    playlistAddStatus.hidden = !text;
  }

  btnCreatePlaylist.addEventListener("click", async () => {
    const query = newPlaylistInput.value.trim();
    if (!query) return;
    const name = newPlaylistName.value.trim() || null;
    const isLink = /^https?:\/\/|^spotify:/i.test(query);
    btnCreatePlaylist.disabled = true;
    showAddStatus(isLink ? "Importing…" : "Adding…", false);
    const result = await callApi("/api/panel/playlists/add", "POST", { name, query });
    btnCreatePlaylist.disabled = false;

    if (!result.ok) {
      showAddStatus(ADD_ERRORS[result.error] || "Couldn't add that. Try again.", true);
      return;
    }
    newPlaylistInput.value = "";
    newPlaylistName.value = "";
    showAddStatus(
      result.added
        ? `Added ${result.added} songs to “${result.name}” (${result.total} total).`
        : `Added to “${result.name}”.`,
      false
    );
    await loadPlaylists();
    if (result.name) selectPlaylist(result.name);
  });

  btnRenamePlaylist.addEventListener("click", async () => {
    const newName = renamePlaylistInput.value.trim();
    if (!newName || !currentPlaylistName) return;
    const result = await callApi(`/api/panel/playlists/${encodeURIComponent(currentPlaylistName)}/rename`, "POST", {
      new_name: newName,
    });
    renamePlaylistInput.value = "";
    await loadPlaylists();
    if (result.ok) selectPlaylist(newName);
  });

  btnDeletePlaylist.addEventListener("click", async () => {
    if (!currentPlaylistName) return;
    if (!confirm(`Delete playlist "${currentPlaylistName}"? This can't be undone.`)) return;
    await callApi(`/api/panel/playlists/${encodeURIComponent(currentPlaylistName)}/delete`, "POST");
    currentPlaylistName = null;
    playlistDetail.hidden = true;
    playlistDetailEmpty.hidden = false;
    playlistDetailEmpty.textContent = "Select a playlist on the left to view its songs.";
    loadPlaylists();
  });

  btnSharePlaylist.addEventListener("click", async () => {
    const targetId = shareTargetId.value.trim();
    if (!targetId || !currentPlaylistName) return;
    await callApi(`/api/panel/playlists/${encodeURIComponent(currentPlaylistName)}/share`, "POST", {
      target_id: parseInt(targetId, 10),
      permission: sharePermission.value,
    });
    shareTargetId.value = "";
  });

  btnPlayPlaylist.addEventListener("click", async () => {
    if (!currentPlaylistName) return;
    if (!currentGuildId) {
      alert("Pick a server in Live Control first.");
      return;
    }
    btnPlayPlaylist.disabled = true;
    await callApi(`/api/panel/${currentGuildId}/playlists/${encodeURIComponent(currentPlaylistName)}/play`, "POST");
    btnPlayPlaylist.disabled = false;
  });

  loadPlaylists();
})();