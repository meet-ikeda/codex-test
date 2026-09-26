/* Line icons: one thin stroke, no fill, no color. Placed wherever an element has data-icon="name".
   Built with createElementNS (no innerHTML). The hippocampus is drawn as what its name means: a seahorse. */
"use strict";

const ICONS = {
  // 01 Brain — in profile, front to the right: lateral fissure, central sulcus, a fold, cerebellum, brainstem
  brain: ["M4 12.5C3.2 8.2 6.5 4.5 11.5 4.3 16.8 4.1 20.8 7 20.7 11c-.1 2.6-1.9 4.4-4.5 4.6-1.4 1.7-4.3 2-5.9.4-1.6 0-2.7-.3-3.7-1-1.4-.1-2.3-1.1-2.6-2.5z",
    "M18.4 12.6c-2.5-1.6-5.3-1.7-7.8-.4", "M13.3 4.3c-.8 1.5-.2 2.9-1.2 4.6-.4.7-.4 1.4 0 2", "M8.4 5.1c.4 1.2-.4 2.4.3 3.7",
    "M18.2 6.6c-1.2.6-1.7 1.8-1.3 3", "M15.9 14.2c-1.3-.5-2.7-.4-3.9.3",
    "M6.8 15.1c-.6 1.8.5 3.3 2.2 3.3 1 0 1.7-.6 1.9-1.4", "M11.8 16.9l.5 3.3", "M13.6 16.8l.3 3.4"],
  // 02 Cortex — memories joined by links
  cortex: ["M5 6.5a1.5 1.5 0 1 0 0 .01", "M18.5 5a1.5 1.5 0 1 0 0 .01", "M12 12a2 2 0 1 0 0 .01", "M6 18.5a1.5 1.5 0 1 0 0 .01",
    "M19 17.5a1.5 1.5 0 1 0 0 .01", "M6.3 7.3 10.4 10.8", "M17.3 5.9 13.5 10.6", "M7.2 17.6 10.4 13.3", "M17.6 16.8 13.8 13.1", "M6.5 6.4 17 5.1"],
  // 03 Bookshelf — books standing on a shelf, one leaning
  shelf: ["M3 20.5h18", "M5 20.5V6h2.8v14.5", "M7.8 20.5V8.5h2.6v12", "M11.8 20.5 13.9 7.3l2.6.4-2.1 13.2", "M17.5 20.5V9.5H20v11"],
  // 04 Sleep — a crescent and a small star
  sleep: ["M15.5 4.2A8.2 8.2 0 1 0 19.8 16a6.6 6.6 0 0 1-4.3-11.8z", "M18.5 5v2.6", "M17.2 6.3h2.6"],
  // 05 Safety — a shield with a check
  safety: ["M12 3.2 5 5.8v5.4c0 4.4 3 8.2 7 9.6 4-1.4 7-5.2 7-9.6V5.8z", "M9.2 12.1l2 2 3.8-4"],
  // A. Receiving box — an open tray with something dropping in
  inbox: ["M3.5 13.5 6 6.5h12l2.5 7", "M3.5 13.5v5h17v-5", "M3.5 13.5h5l1.2 2.2h4.6l1.2-2.2h5", "M12 2.8v6.4", "M9.8 7l2.2 2.2L14.2 7"],
  // B. Hippocampus — the seahorse it is named after: snout to the left, crown, dorsal fin, curled tail
  hippocampus: ["M5.5 6.6 9.2 5.6c.4-1.6 1.8-2.6 3.4-2.6 2 0 3.2 1.4 3 3.2-.2 1.4-1 2.3-.7 3.6.4 1.8 1.9 3 1.7 5.2-.2 2.4-2.2 3.9-4.2 3.6-1.8-.3-2.4-1.9-1.4-2.8.8-.7 2-.2 1.8.8",
    "M5.5 7.4 9.6 7.2c.8 1 .7 2 .3 3-.7 1.8-.5 3.6.9 4.8", "M12.6 5.2h.01", "M13.2 3.1l.6-1.3.9 1.1", "M16.2 11.4l2.2-.6-.4 2.6", "M10.1 11.2h1.6", "M9.9 13h1.7"],
};

function iconSvg(name, size = 18) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  for (const d of ICONS[name] || []) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(path);
  }
  return svg;
}

document.querySelectorAll("[data-icon]").forEach((el) => {
  el.prepend(iconSvg(el.dataset.icon, Number(el.dataset.iconSize) || 18));
});
