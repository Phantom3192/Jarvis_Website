// Jarvis site — live stats polling + docs scrollspy.

const REFRESH_MS = 15000;

function fmtNumber(n) {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  if (n >= 1073741824) return `${(n / 1073741824).toFixed(1)} GB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

function applyUsage(usage) {
  const panel = document.getElementById("telemetry-usage");
  if (!panel) return;

  if (!usage || !usage.available) {
    panel.classList.add("unavailable");
    return;
  }
  panel.classList.remove("unavailable");

  document.getElementById("t-cpu").textContent =
    usage.cpu_percent !== null && usage.cpu_percent !== undefined ? `${usage.cpu_percent}%` : "—";
  document.getElementById("t-ram").textContent =
    `${fmtBytes(usage.ram_used_bytes)} / ${fmtBytes(usage.ram_total_bytes)} (${usage.ram_percent}%)`;
  document.getElementById("t-storage").textContent =
    `${fmtBytes(usage.storage_used_bytes)} / ${fmtBytes(usage.storage_limit_bytes)} (${usage.storage_percent}%)`;
  document.getElementById("t-threads").textContent = fmtNumber(usage.threads);
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

    document.getElementById("t-status").textContent = data.online ? "Online" : "Reconnecting";
    document.getElementById("t-guilds").textContent = fmtNumber(data.guilds);
    document.getElementById("t-users").textContent = fmtNumber(data.users);
    document.getElementById("t-uptime").textContent = data.uptime_human || "—";
    document.getElementById("t-latency").textContent =
      data.latency_ms !== null && data.latency_ms !== undefined ? `${data.latency_ms} ms` : "—";

    applyUsage(data.usage);
  } catch (err) {
    document.getElementById("t-status").textContent = "Unreachable";
    const coreLabel = document.getElementById("core-online-label");
    if (coreLabel) coreLabel.textContent = "Unreachable";
    const coreDot = document.querySelector(".core-readout .status-dot");
    if (coreDot) coreDot.classList.add("offline");
    applyUsage(null);
  }
}

refreshStats();
setInterval(refreshStats, REFRESH_MS);

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