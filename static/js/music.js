// Music panel — Phase 2: live control wiring.
// Talks only to this website's own /api/panel/* routes (never the bot
// directly, and never sees any shared secret) — see app.py for the proxy.
(() => {
  // ── Sidebar tab switching (unchanged from Phase 1) ─────────────────────
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
    });
  });

  const guildSelect = document.getElementById("guildSelect");
  if (!guildSelect) return; // not logged in / no controllable guilds — nothing to wire up

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
  let currentGuildId = guildSelect.value;

  async function api(path, method = "GET", body = null) {
    const opts = { method, headers: {} };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    try {
      const res = await fetch(`/api/panel/${currentGuildId}${path}`, opts);
      return await res.json();
    } catch {
      return { error: "network_error" };
    }
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
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
    const state = await api("/state");
    renderState(state);
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    refresh();
    pollTimer = setInterval(refresh, 3000); // simple polling for now — can move to WebSocket/SSE later
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

  btnPlayPause.addEventListener("click", async () => {
    await api("/pause", "POST");
    refresh();
  });

  btnSkip.addEventListener("click", async () => {
    await api("/skip", "POST");
    refresh();
  });

  btnPlay.addEventListener("click", async () => {
    const query = playInput.value.trim();
    if (!query) return;
    btnPlay.disabled = true;
    await api("/play", "POST", { query });
    playInput.value = "";
    btnPlay.disabled = false;
    refresh();
  });
  playInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") btnPlay.click();
  });

  startPolling();
})();   