"use strict";

/* Gipfel-Finder – Frontend-Logik (Vanilla JS, keine Build-Pipeline). */

const zustand = {
  daten: null,
  sortSpalte: "hoehe",
  sortAuf: false,
  sortiert: false,
  filter: {
    suche: "",
    min_hoehe: null,
    max_fahrzeit: null,
    min_aufstieg: null,
  },
  karte: null,
  marker: [],
  jobTimer: null,
};

/* Statischer Modus (GitHub Pages): Ohne Flask-Backend lädt die Seite eine
   einmalig exportierte daten.json statt /api/daten; „Neu suchen erzwingen“
   ist dann nicht verfügbar. Gesetzt wird window.STATISCH vom Export-Skript. */
const STATISCH = window.STATISCH === true;

const EL = {
  status: document.getElementById("cache-status"),
  summe: document.getElementById("summe"),
  warnungen: document.getElementById("warnungen"),
  ladehinweis: document.getElementById("ladehinweis"),
  karte: document.getElementById("karte"),
  koerper: document.getElementById("koerper"),
  toggleKarte: document.getElementById("toggle-karte"),
  filterSuche: document.getElementById("filter-suche"),
  filterMinHoehe: document.getElementById("filter-min-hoehe"),
  filterMaxFahrzeit: document.getElementById("filter-max-fahrzeit"),
  filterMinAufstieg: document.getElementById("filter-min-aufstieg"),
  filterZuruecksetzen: document.getElementById("filter-zuruecksetzen"),
  btnNeusuchen: document.getElementById("btn-neusuchen"),
  modalschicht: document.getElementById("modalschicht"),
  btnAbbrechen: document.getElementById("btn-abbrechen"),
  btnBestaetigen: document.getElementById("btn-bestaetigen"),
  fortschrittBox: document.getElementById("fortschritt-box"),
  fortschrittFuellung: document.getElementById("fortschritt-fuellung"),
  fortschrittText: document.getElementById("fortschritt-text"),
  kopf: document.getElementById("kopf"),
  btnThema: document.getElementById("btn-thema"),
  filterZaehler: document.getElementById("filter-zaehler"),
  toast: document.getElementById("toast"),
};

async function ladeDaten() {
  const antwort = await fetch(STATISCH ? "daten.json" : "/api/daten");
  if (!antwort.ok) throw new Error(`Server antwortet mit ${antwort.status}`);
  zustand.daten = await antwort.json();
  rendernStatus();
  rendernWarnungen();
  rendernTabelle();
  rendernKarte();
}

/* HTML-Sonderzeichen escapen (für Popup-Inhalte der Karte) */
function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function rendernStatus() {
  const stufen = zustand.daten.stufen;
  const namen = { fahrplan: "Fahrplan", hoehen: "Höhen", gipfel: "Gipfel", touren: "Touren" };
  const iconen = { fahrplan: "🚆", hoehen: "📏", gipfel: "⛰️", touren: "🥾" };
  const chips = Object.entries(stufen).map(([schluessel, st]) => {
    const chip = document.createElement("span");
    chip.className = "chip " + (st.vorhanden ? "chip-ok" : "chip-leer");
    chip.title =
      `${namen[schluessel]}: ${st.vorhanden ? "Zuletzt aktualisiert " + st.zuletzt : "noch keine Daten"}`;
    chip.textContent =
      `${iconen[schluessel] || "•"} ${namen[schluessel]}: ${st.vorhanden ? "aktuell" : "noch keine Daten"}`;
    return chip;
  });
  EL.status.replaceChildren(...chips);
  const sum = zustand.daten.summen;
  const hoehen = sum.hoehen ? ` · 📏 ${sum.hoehen} Bahnhöfe mit Höhe` : "";
  EL.summe.textContent = `⚑ ${sum.gipfel} Gipfel · 🚉 ${sum.fahrplan} erreichbare Bahnhöfe${hoehen}`;
}

function rendernWarnungen() {
  const alle = Object.values(zustand.daten.stufen).flatMap((st) => st.warnungen || []);
  if (!alle.length) {
    EL.warnungen.hidden = true;
    return;
  }
  const gezaehlt = [...new Set(alle)].slice(0, 15);
  const liste = document.createElement("ul");
  for (const text of gezaehlt) {
    const li = document.createElement("li");
    li.textContent = text;
    liste.appendChild(li);
  }
  EL.warnungen.textContent = "";
  EL.warnungen.appendChild(liste);
  EL.warnungen.hidden = false;
}

function spaltenwert(gipfel, spalte) {
  if (spalte === "name") return gipfel.name.toLowerCase();
  if (spalte === "hoehe") return gipfel.hoehe_m == null ? -1 : gipfel.hoehe_m;
  if (spalte === "starthoehe") return gipfel.bahnhof_hoehe_m == null ? -1 : gipfel.bahnhof_hoehe_m;
  if (spalte === "aufstieg") return gipfel.aufstieg_m == null ? -1 : gipfel.aufstieg_m;
  if (spalte === "fahrzeit") return gipfel.fahrzeit_min == null ? -1 : gipfel.fahrzeit_min;
  if (spalte === "bahnhof") return (gipfel.bahnhof_name || "").toLowerCase();
  return gipfel.name;
}

/* Filter: Werte aus der Leiste lesen und Tabelle neu rendern */
function nummernWert(wert) {
  if (wert === "" || wert == null) return null;
  const n = Number(wert);
  return Number.isFinite(n) ? n : null;
}

function aktualisiereFilterZaehler() {
  const f = zustand.filter;
  const aktiv =
    Number(Boolean(f.suche)) +
    Number(f.min_hoehe != null) +
    Number(f.max_fahrzeit != null) +
    Number(f.min_aufstieg != null);
  EL.filterZaehler.textContent = aktiv === 1 ? "1 Filter aktiv" : `${aktiv} Filter aktiv`;
  EL.filterZaehler.hidden = aktiv === 0;
  EL.filterZuruecksetzen.disabled = aktiv === 0;
}

function filterAnwenden() {
  zustand.filter.suche = (EL.filterSuche.value || "").toLowerCase().trim();
  zustand.filter.min_hoehe = nummernWert(EL.filterMinHoehe.value);
  zustand.filter.max_fahrzeit = nummernWert(EL.filterMaxFahrzeit.value);
  zustand.filter.min_aufstieg = nummernWert(EL.filterMinAufstieg.value);
  rendernTabelle();
  aktualisiereFilterZaehler();
}

function zuruecksetzenFilter() {
  EL.filterSuche.value = "";
  EL.filterMinHoehe.value = "";
  EL.filterMaxFahrzeit.value = "";
  EL.filterMinAufstieg.value = "";
  filterAnwenden();
}

function gefilterteGipfel() {
  const alle = zustand.daten.gipfel || [];
  const f = zustand.filter;
  if (!f.suche && f.min_hoehe == null && f.max_fahrzeit == null && f.min_aufstieg == null) {
    return alle;
  }
  return alle.filter((g) => {
    if (f.suche) {
      const text = `${g.name} ${g.bahnhof_name || ""}`.toLowerCase();
      if (!text.includes(f.suche)) return false;
    }
    if (f.min_hoehe != null && (g.hoehe_m == null || g.hoehe_m < f.min_hoehe)) return false;
    if (f.max_fahrzeit != null && (g.fahrzeit_min == null || g.fahrzeit_min > f.max_fahrzeit)) return false;
    if (f.min_aufstieg != null && (g.aufstieg_m == null || g.aufstieg_m < f.min_aufstieg)) return false;
    return true;
  });
}

function rendernTabelle() {
  const gipfel = gefilterteGipfel();
  markiereSortierung();
  const auf = zustand.sortAuf ? -1 : 1;
  gipfel.sort((a, b) => {
    const va = spaltenwert(a, zustand.sortSpalte);
    const vb = spaltenwert(b, zustand.sortSpalte);
    if (va === vb) return a.name.localeCompare(b.name, "de");
    if (va === -1) return 1;   // fehlende Werte ans Ende
    if (vb === -1) return -1;
    return va < vb ? -auf : auf;
  });

  const maxAufstieg = Math.max(1, ...gipfel.map((g) => g.aufstieg_m || 0));

  EL.koerper.textContent = "";
  if (!gipfel.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "Keine Gipfel gefunden – Filter anpassen oder „Neu suchen erzwingen“.";
    tr.appendChild(td);
    EL.koerper.appendChild(tr);
    return;
  }

  for (const g of gipfel) {
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    tdName.dataset.label = "Gipfel";
    tdName.textContent = g.name;
    tr.appendChild(tdName);

    const tdHoehe = document.createElement("td");
    tdHoehe.className = "zahl";
    tdHoehe.dataset.label = "Höhe (m)";
    tdHoehe.textContent = g.hoehe_m != null ? g.hoehe_m.toLocaleString("de-DE") : "—";
    tr.appendChild(tdHoehe);

    const tdBahnhof = document.createElement("td");
    tdBahnhof.dataset.label = "Ausgangsbahnhof";
    tdBahnhof.textContent = g.bahnhof_name || "—";
    tr.appendChild(tdBahnhof);

    const tdStartHoehe = document.createElement("td");
    tdStartHoehe.className = "zahl";
    tdStartHoehe.dataset.label = "Start-Höhe (m)";
    tdStartHoehe.textContent = g.bahnhof_hoehe_m != null
      ? g.bahnhof_hoehe_m.toLocaleString("de-DE") : "—";
    tr.appendChild(tdStartHoehe);

    const tdAufstieg = document.createElement("td");
    tdAufstieg.className = "zahl";
    tdAufstieg.dataset.label = "Höhenmeter";
    const aufstiegZone = document.createElement("span");
    aufstiegZone.className = "aufstieg-zelle";
    const aufstiegText = document.createElement("span");
    aufstiegText.textContent = g.aufstieg_m != null
      ? `+${g.aufstieg_m.toLocaleString("de-DE")} m` : "—";
    aufstiegZone.appendChild(aufstiegText);
    if (g.aufstieg_m != null) {
      const balken = document.createElement("span");
      balken.className = "balken";
      balken.setAttribute("aria-hidden", "true");
      const fuellung = document.createElement("span");
      fuellung.className = "balken-fuellung";
      const anteil = Math.max(4, Math.min(100, Math.round((g.aufstieg_m / maxAufstieg) * 100)));
      fuellung.style.width = anteil + "%";
      balken.appendChild(fuellung);
      aufstiegZone.appendChild(balken);
    }
    tdAufstieg.appendChild(aufstiegZone);
    if (g.aufstieg_m != null && g.aufstieg_m >= 1000) tdAufstieg.classList.add("stark");
    tr.appendChild(tdAufstieg);

    const tdFahrt = document.createElement("td");
    tdFahrt.className = "zahl";
    tdFahrt.dataset.label = "Fahrtzeit";
    tdFahrt.textContent = g.fahrzeit_min != null ? `${g.fahrzeit_min} Min.` : "—";
    tr.appendChild(tdFahrt);

    const tdTouren = document.createElement("td");
    tdTouren.className = "touren";
    tdTouren.dataset.label = "Tourvorschläge";
    for (const t of g.touren || []) {
      const a = document.createElement("a");
      a.href = t.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.title = t.name;
      a.className = "link " + (t.quelle ? `link-${t.quelle}` : "");
      if (t.quelle === "anreise") a.textContent = "🚗";
      else if (t.quelle === "komoot" || t.quelle === "alpenvereinaktiv") a.textContent = "🥾";
      else if (t.quelle.startsWith("outdooractive")) a.textContent = "⛰️";
      else a.textContent = "↗";
      tdTouren.appendChild(a);
    }
    tr.appendChild(tdTouren);
    EL.koerper.appendChild(tr);
  }
}

/* Filtereingaben an der Leiste */
for (const el of [EL.filterSuche, EL.filterMinHoehe, EL.filterMaxFahrzeit, EL.filterMinAufstieg]) {
  el.addEventListener("input", filterAnwenden);
}
EL.filterZuruecksetzen.addEventListener("click", zuruecksetzenFilter);

/* Sortieren per Klick oder Tastatur (Enter/Leertaste) an den Tabellenköpfen */
function sortierenNach(spalte) {
  zustand.sortiert = true;
  if (zustand.sortSpalte === spalte) {
    zustand.sortAuf = !zustand.sortAuf;
  } else {
    zustand.sortSpalte = spalte;
    zustand.sortAuf = ["hoehe", "aufstieg", "starthoehe"].includes(spalte);
  }
  rendernTabelle();
}

function markiereSortierung() {
  if (!zustand.sortiert) return;
  document.querySelectorAll("th[data-spalte]").forEach((th) => {
    th.classList.toggle("sortiert-auf", th.dataset.spalte === zustand.sortSpalte && zustand.sortAuf);
    th.classList.toggle("sortiert-ab", th.dataset.spalte === zustand.sortSpalte && !zustand.sortAuf);
  });
}

document.querySelectorAll("th[data-spalte]").forEach((th) => {
  th.addEventListener("click", () => sortierenNach(th.dataset.spalte));
  th.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      sortierenNach(th.dataset.spalte);
    }
  });
});
/* ------------------------------------------------------------------ */
/* Karte (Leaflet)                                                    */
/* ------------------------------------------------------------------ */

function rendernKarte() {
  if (!window.L) return;
  const gipfel = zustand.daten.gipfel;
  if (!gipfel.length) return;

  if (!zustand.karte) {
    zustand.karte = L.map("karte").setView([gipfel[0].lat, gipfel[0].lon], 9);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(zustand.karte);
  }
  for (const m of zustand.marker) zustand.karte.removeLayer(m);
  zustand.marker = [];
  for (const g of gipfel) {
    const m = L.marker([g.lat, g.lon]).addTo(zustand.karte);
    const hoehe = g.hoehe_m != null ? esc(g.hoehe_m.toLocaleString("de-DE")) + " m" : "?";
    const start = g.bahnhof_hoehe_m != null
      ? esc(g.bahnhof_hoehe_m.toLocaleString("de-DE")) + " m" : "—";
    const aufstieg = g.aufstieg_m != null
      ? `+${g.aufstieg_m.toLocaleString("de-DE")} m` : "—";
    m.bindPopup(
      `<b>${esc(g.name)}</b><br>` +
      `Gipfel ${hoehe} · ab ${start}<br>` +
      `≈ ${aufstieg} Höhenmeter<br>` +
      `ab ${esc(g.bahnhof_name || "—")} (${g.fahrzeit_min != null ? g.fahrzeit_min + " Min." : "—"})`
    );
    zustand.marker.push(m);
  }
  const mitte = gipfel.reduce((acc, g) => [acc[0] + g.lat, acc[1] + g.lon], [0, 0]);
  zustand.karte.setView([mitte[0] / gipfel.length, mitte[1] / gipfel.length], 9);
  EL.karte.hidden = false;
  zustand.karte.invalidateSize();
}

EL.toggleKarte.addEventListener("change", () => {
  EL.karte.hidden = !EL.toggleKarte.checked;
  if (!EL.karte.hidden && zustand.karte) zustand.karte.invalidateSize();
});

/* ------------------------------------------------------------------ */
/* Sticky-Header (Schatten beim Scrollen) & Design hell/dunkel         */
/* ------------------------------------------------------------------ */

function kopfScrollEffekt() {
  if (!EL.kopf) return;
  EL.kopf.classList.toggle("kopf-gescrollt", window.scrollY > 4);
}

window.addEventListener("scroll", kopfScrollEffekt, { passive: true });
kopfScrollEffekt();

function aktuellesThema() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function themenAnwenden() {
  if (!EL.btnThema) return;
  const dunkel = aktuellesThema() === "dark";
  EL.btnThema.textContent = dunkel ? "☀️" : "🌙";
  EL.btnThema.setAttribute("aria-label",
    dunkel ? "Helles Design aktivieren" : "Dunkles Design aktivieren");
  EL.btnThema.title = dunkel ? "Helles Design aktivieren" : "Dunkles Design aktivieren";
  const meta = document.getElementById("theme-farbe");
  if (meta) meta.setAttribute("content", dunkel ? "#0a301f" : "#0f4630");
  if (zustand.karte) zustand.karte.invalidateSize();  // Leaflet-Overlays neu zeichnen
}

if (EL.btnThema) {
  EL.btnThema.addEventListener("click", () => {
    const neu = aktuellesThema() === "dark" ? "light" : "dark";
    if (neu === "dark") document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("gipfel-thema", neu); } catch (e) { /* Speicher blockiert */ }
    themenAnwenden();
  });
  themenAnwenden();
}

/* ------------------------------------------------------------------ */
/* Neusuche erzwingen (Bestätigung -> Job -> Fortschritt)             */
/* ------------------------------------------------------------------ */

function oeffneModalschicht() {
  EL.modalschicht.hidden = false;
  EL.fortschrittBox.hidden = true;
  EL.btnBestaetigen.disabled = false;
  EL.btnBestaetigen.textContent = "Suche starten";
}

function schliesseModalschicht() {
  EL.modalschicht.hidden = true;
  // Fortschritt zurücksetzen, damit ein erneutes Öffnen sauber startet
  EL.fortschrittBox.hidden = true;
  EL.fortschrittFuellung.style.width = "0%";
  if (zustand.jobTimer) {
    clearInterval(zustand.jobTimer);
    zustand.jobTimer = null;
  }
}

if (STATISCH) {
  EL.btnNeusuchen.hidden = true; // ohne Backend gibt es nichts Neu zu suchen
} else {
EL.btnNeusuchen.addEventListener("click", oeffneModalschicht);
EL.btnAbbrechen.addEventListener("click", schliesseModalschicht);

EL.btnBestaetigen.addEventListener("click", async () => {
  const stufe = document.querySelector("input[name=stufe]:checked").value;
  EL.btnBestaetigen.disabled = true;
  EL.btnBestaetigen.textContent = "Läuft …";
  EL.fortschrittBox.hidden = false;
  try {
    const antwort = await fetch("/api/neusuchen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stufe }),
    });
    const daten = await antwort.json();
    if (!antwort.ok) throw new Error(daten.fehler || "Neusuche fehlgeschlagen");
    pollJob(daten.job_id);
  } catch (fehler) {
    toast(fehler.message, true);
    schliesseModalschicht();
  }
  });
}

function pollJob(jobId) {
  zustand.jobTimer = setInterval(async () => {
    try {
      const antwort = await fetch(`/api/fortschritt/${jobId}`);
      const j = await antwort.json();
      if (j.zustand === "unbekannt") return;
      if (j.gesamt > 0) {
        const anteil = Math.min(100, Math.round((j.erledigt / j.gesamt) * 100));
        EL.fortschrittFuellung.style.width = anteil + "%";
      }
      EL.fortschrittText.textContent = j.text || j.meldung || j.zustand;
      if (j.zustand === "fertig") {
        schliesseModalschicht();
        await ladeDaten();
        toast("Daten aktualisiert ✔", false);
      } else if (j.zustand === "fehler") {
        schliesseModalschicht();
        toast(j.meldung || "Neusuche fehlgeschlagen", true);
      }
    } catch {
      /* Netzwerk kurz unterbrochen – nächster Poll-Zyklus versucht es erneut */
    }
  }, 1500);
}

/* ------------------------------------------------------------------ */
/* Toast & Start                                                       */
/* ------------------------------------------------------------------ */

let toastTimer = null;

function toast(text, istFehler) {
  EL.toast.innerHTML =
    `<span data-toast-icon aria-hidden="true">${istFehler ? "⚠️" : "✅"}</span>` +
    `<span>${esc(text)}</span>`;
  EL.toast.className = "toast" + (istFehler ? " toast-fehler" : " toast-ok");
  EL.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    EL.toast.hidden = true;
  }, 5000);
}

aktualisiereFilterZaehler();

ladeDaten().catch((fehler) => {
  EL.ladehinweis.textContent = `Fehler beim Laden der Daten: ${fehler.message}`;
  EL.ladehinweis.hidden = false;
});