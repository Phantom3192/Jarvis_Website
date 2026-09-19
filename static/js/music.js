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
      } else {
        npTitle.textContent = "Nothing playing";
        npSub.textContent = state.channel_name ? `Connected to ${state.channel_name}` : "";
        npArt.style.backgroundImage = "none";
        npProgressFill.style.width = "0%";
        btnPlayPause.disabled = true;
        btnSkip.disabled = true;
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

    btnJoin.addEventListener("click", async () => {
      const channelId = channelSelect.value;
      if (!channelId) return;
      btnJoin.disabled = true;
      await api("/join", "POST", { channel_id: parseInt(channelId, 10) });
      btnJoin.disabled = false;
      refresh();
    });

    btnPlayPause.addEventListener("click", async () => { await api("/pause", "POST"); refresh(); });
    btnSkip.addEventListener("click", async () => { await api("/skip", "POST"); refresh(); });

    btnPlay.addEventListener("click", async () => {
      const query = playInput.value.trim();
      if (!query) return;
      btnPlay.disabled = true;
      await api("/play", "POST", { query });
      playInput.value = "";
      btnPlay.disabled = false;
      refresh();
    });
    playInput.addEventListener("keydown", (e) => { if (e.key === "Enter") btnPlay.click(); });

    startPolling();
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