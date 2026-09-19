// Music panel — Phase 1: sidebar tab switching only.
// Live now-playing/queue/controls wiring (polling or WebSocket against the
// bot's /api/music/* endpoints) lands in Phase 2 — this file intentionally
// does nothing beyond local UI state for now.
(() => {
  const links = document.querySelectorAll(".music-sidebar-link");
  if (!links.length) return;

  links.forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      if (link.classList.contains("is-soon")) return; // not built yet

      links.forEach((l) => l.classList.remove("active"));
      link.classList.add("active");

      const target = link.dataset.panel;
      document.querySelectorAll(".music-view").forEach((view) => {
        view.hidden = view.id !== `panel-${target}`;
      });
    });
  });
})();