// Jarvis site — live stats polling + docs scrollspy.

const REFRESH_MS = 3000;

function fmtNumber(n) {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

async function refreshStats() {
  try {
    const res = await fetch("/api/stats", { cache: "no-store" });
    if (!res.ok) throw new Error("bad response");
    const data = await res.json();

    document.getElementById("core-guilds")?.replaceChildren(fmtNumber(data.guilds));
    document.getElementById("core-users")?.replaceChildren(fmtNumber(data.users));
    const coreLabel = document.getElementById("core-online-label");
    if (coreLabel) coreLabel.textContent = data.online ? "Online" : "Reconnecting";
    const coreDot = document.querySelector(".core-readout .status-dot");
    if (coreDot) coreDot.classList.toggle("offline", !data.online);

    const tStatus = document.getElementById("t-status");
    if (tStatus) tStatus.textContent = data.online ? "Online" : "Reconnecting";
    document.getElementById("t-guilds")?.replaceChildren(fmtNumber(data.guilds));
    document.getElementById("t-users")?.replaceChildren(fmtNumber(data.users));
    const tUptime = document.getElementById("t-uptime");
    if (tUptime) tUptime.textContent = data.uptime_human || "—";
    const tLatency = document.getElementById("t-latency");
    if (tLatency) {
      tLatency.textContent =
        data.latency_ms !== null && data.latency_ms !== undefined ? `${data.latency_ms} ms` : "—";
    }
  } catch (err) {
    const tStatus = document.getElementById("t-status");
    if (tStatus) tStatus.textContent = "Unreachable";
    const coreLabel = document.getElementById("core-online-label");
    if (coreLabel) coreLabel.textContent = "Unreachable";
    const coreDot = document.querySelector(".core-readout .status-dot");
    if (coreDot) coreDot.classList.add("offline");
  }
}

let statsInterval = null;

function startStatsPolling() {
  if (statsInterval) return; // already running
  refreshStats();
  statsInterval = setInterval(refreshStats, REFRESH_MS);
}

function stopStatsPolling() {
  if (!statsInterval) return;
  clearInterval(statsInterval);
  statsInterval = null;
}

// Pause polling while the tab is hidden/backgrounded — no point hammering
// the bot's API every 15s for a tab nobody is looking at. Resumes (with an
// immediate refresh so the numbers aren't stale) the moment it's visible
// again.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopStatsPolling();
  } else {
    startStatsPolling();
  }
});

startStatsPolling();

// ── Mobile nav menu ─────────────────────────────────────────────────────
const navToggle = document.getElementById("navToggle");
const navMobile = document.getElementById("navMobile");

if (navToggle && navMobile) {
  // Clone the desktop links + CTAs into the mobile panel once.
  const desktopLinks = document.querySelector(".nav-links");
  const desktopCtas = document.querySelector(".nav-ctas");
  if (desktopLinks) {
    desktopLinks.querySelectorAll("a").forEach((a) => {
      const clone = a.cloneNode(true);
      navMobile.appendChild(clone);
    });
  }
  if (desktopCtas) {
    desktopCtas.querySelectorAll("a").forEach((a) => {
      const clone = a.cloneNode(true);
      clone.className = "nav-mobile-cta";
      navMobile.appendChild(clone);
    });
  }

  const closeMenu = () => {
    navMobile.classList.remove("open");
    navToggle.setAttribute("aria-expanded", "false");
  };

  navToggle.addEventListener("click", () => {
    const isOpen = navMobile.classList.toggle("open");
    navToggle.setAttribute("aria-expanded", String(isOpen));
  });

  navMobile.addEventListener("click", (e) => {
    if (e.target.tagName === "A") closeMenu();
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 720) closeMenu();
  });
}

// ── Docs scrollspy: highlight the active category in the sidebar ──────────
const navItems = document.querySelectorAll(".docs-nav-item");
const docSections = document.querySelectorAll(".docs-cat");

if ("IntersectionObserver" in window && navItems.length && docSections.length) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const id = entry.target.id;
          navItems.forEach((item) => {
            item.classList.toggle("active", item.getAttribute("href") === `#${id}`);
          });
        }
      });
    },
    { rootMargin: "-15% 0px -70% 0px" }
  );
  docSections.forEach((section) => observer.observe(section));
}