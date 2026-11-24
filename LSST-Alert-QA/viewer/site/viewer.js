// The transient monitor's report viewer: the overview of reports/ without ?file=, one report with it.
// The rules it follows are viewer/SPEC.md's; its tests are written from them, not from this file.
"use strict";

(() => {
  const REPORTS = "reports/";
  // A report's file name, live or init. The footprint is any text; / and \ are refused before this.
  const REPORT_NAME = /^transient_(.+)_(\d{8})_(\d{6})(_init)?\.json$/;
  // The one form of run_utc a report may have; the digits are not checked as a calendar date.
  const RUN_UTC = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\+00:00$/;
  const READING = "Reading reports ...";
  const MISSING = "-";
  // first: the direction of a column's first click ("asc" is ▲: values ascend down the table).
  const COLUMNS = [
    { key: "report", label: "Report", first: "asc" },
    { key: "date", label: "Date", first: "desc" },
    { key: "mode", label: "Mode", first: "asc" },
    { key: "exit", label: "Exit", first: "desc" },
    { key: "alerts", label: "Alerts", first: "desc" },
    { key: "earlier", label: "Reported Earlier" },
    { key: "message", label: "Message" },
  ];
  const DEFAULT_SORT = { key: "date", dir: "desc" };
  const MODE_RANK = { init: 0, live: 1 }; // any other mode ranks after these, then by its text

  const app = document.getElementById("app");
  let sort = { ...DEFAULT_SORT };
  let view = 0; // bumped by every view drawn: a read that finishes for an older view is dropped
  let rows = []; // the overview's good reports, kept so a sort redraws without reading again
  let table = null;

  // ---- values ----------------------------------------------------------------------------------

  const byCode = (a, b) => (a < b ? -1 : a > b ? 1 : 0); // character-code order, the same in every browser

  function isObject(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  // A key's value, or undefined when the report lacks it (null counts as lacking it).
  function valueOf(obj, key) {
    return isObject(obj) && Object.hasOwn(obj, key) && obj[key] !== null ? obj[key] : undefined;
  }

  function text(v) {
    if (v === undefined || v === null) return MISSING;
    return typeof v === "object" ? JSON.stringify(v) : String(v);
  }

  function dateText(runUtc) {
    const [, y, mo, d, h, mi, s] = RUN_UTC.exec(runUtc);
    return `${y}-${mo}-${d} ${h}:${mi}:${s} UTC`;
  }

  function entries(v) {
    if (Array.isArray(v)) return v.length;
    if (isObject(v)) return Object.keys(v).length;
    return undefined;
  }

  // Exactly one of the defined texts, nothing from the report's own wording added.
  function message(report) {
    const exit = valueOf(report, "exit_code");
    if (exit === 0) return "-";
    if (exit !== 1) return "ERROR: undefined error and exit code";
    const listings = isObject(valueOf(report, "listings")) ? report.listings : {};
    const failed = Object.keys(listings)
      .filter((k) => valueOf(listings[k], "status") === "failed")
      .map((k) => k.toUpperCase())
      .sort(byCode);
    if (failed.length === 0) return "ERROR: Network failed";
    if (failed.length === 1) return `ERROR: ${failed[0]} listing failed`;
    return `ERROR: ${failed.slice(0, -1).join(", ")} and ${failed.at(-1)} listings failed`;
  }

  function rowOf(name, report) {
    const mode = valueOf(report, "mode");
    return {
      name,
      report: valueOf(report, "footprint"),
      date: report.run_utc,
      mode,
      exit: valueOf(report, "exit_code"),
      alerts: mode === "init" ? undefined : entries(valueOf(report, "alerts")),
      earlier: valueOf(valueOf(report, "state_before"), "reported"),
      message: message(report),
    };
  }

  // ---- sorting ---------------------------------------------------------------------------------

  function sortKey(row, key) {
    if (key === "mode") return row.mode === undefined ? undefined : [MODE_RANK[row.mode] ?? 2, text(row.mode)];
    return row[key];
  }

  function compareValues(a, b) {
    if (Array.isArray(a) && Array.isArray(b)) {
      for (let i = 0; i < Math.min(a.length, b.length); i++) {
        const c = compareValues(a[i], b[i]);
        if (c) return c;
      }
      return a.length - b.length;
    }
    const an = typeof a === "number";
    const bn = typeof b === "number";
    if (an && bn) return a - b;
    if (an !== bn) return an ? -1 : 1; // numbers before text
    return byCode(text(a), text(b));
  }

  // "-" goes last in both directions; ties go by Date, newest first, which a flip never turns.
  function sorted(list, { key, dir }) {
    const sign = dir === "asc" ? 1 : -1;
    return list.slice().sort((x, y) => {
      const a = sortKey(x, key);
      const b = sortKey(y, key);
      if (a === undefined || b === undefined) {
        if (a !== b) return a === undefined ? 1 : -1;
      } else {
        const c = sign * compareValues(a, b);
        if (c) return c;
      }
      return byCode(y.date, x.date) || byCode(x.name, y.name);
    });
  }

  function sortFrom(state) {
    const s = isObject(state) ? state.sort : undefined;
    const known = isObject(s) && COLUMNS.some((c) => c.first && c.key === s.key);
    return known && (s.dir === "asc" || s.dir === "desc") ? { key: s.key, dir: s.dir } : null;
  }

  // ---- reading ---------------------------------------------------------------------------------

  // The entries of reports/, from the server's directory index; null when it cannot be listed.
  async function listFolder() {
    let html;
    try {
      const response = await fetch(REPORTS, { cache: "no-store" });
      if (!response.ok) return null;
      html = await response.text();
    } catch {
      return null;
    }
    const names = [];
    for (const a of new DOMParser().parseFromString(html, "text/html").querySelectorAll("a[href]")) {
      const href = a.getAttribute("href");
      if (/^[a-z][a-z0-9+.-]*:|^[/?#]|^\.\.?\/?$/i.test(href)) continue; // elsewhere, not an entry
      try {
        names.push(decodeURIComponent(href));
      } catch {
        // an href that is not percent-encoding: no name to read
      }
    }
    return names;
  }

  // One report file: { report } or { error } with the message that names it.
  async function readReport(name) {
    let body;
    try {
      const response = await fetch(REPORTS + encodeURIComponent(name), { cache: "no-store" });
      if (!response.ok) return { error: `Report not found: ${name}` };
      body = await response.text();
    } catch {
      return { error: `Report not found: ${name}` };
    }
    let report;
    try {
      report = JSON.parse(body);
    } catch {
      return { error: `Not a valid report: ${name}` };
    }
    const runUtc = valueOf(report, "run_utc");
    if (typeof runUtc !== "string" || !RUN_UTC.test(runUtc)) return { error: `Not a valid report: ${name}` };
    return { report };
  }

  // ---- drawing ---------------------------------------------------------------------------------

  // Every string goes in as text, never as HTML.
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    node.append(...children);
    return node;
  }

  const show = (...nodes) => app.replaceChildren(...nodes);
  const line = (words, cls = "message") => el("p", { class: cls }, words);
  const plainClick = (e) => e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey;

  function drawTable() {
    const head = el("tr");
    for (const col of COLUMNS) {
      if (!col.first) {
        head.append(el("th", { scope: "col" }, col.label));
        continue;
      }
      const active = sort.key === col.key;
      const mark = active ? (sort.dir === "asc" ? "▲" : "▼") : "↕";
      const ariaSort = active ? (sort.dir === "asc" ? "ascending" : "descending") : "none";
      const th = el("th", { scope: "col", class: "sortable", "aria-sort": ariaSort },
        el("button", { type: "button" }, col.label, el("span", { "aria-hidden": "true" }, ` ${mark}`)));
      th.addEventListener("click", () => sortBy(col));
      head.append(th);
    }
    const body = el("tbody");
    for (const row of sorted(rows, sort)) {
      const tr = el("tr", {},
        el("td", {}, el("a", { href: "?file=" + encodeURIComponent(row.name) }, text(row.report))),
        el("td", {}, dateText(row.date)),
        el("td", {}, text(row.mode)),
        el("td", { class: "num" }, text(row.exit)),
        el("td", { class: "num" }, text(row.alerts)),
        el("td", { class: "num" }, text(row.earlier)),
        el("td", {}, row.message));
      // The whole row opens the report; a modified click on its link is left to the browser (new tab).
      tr.addEventListener("click", (e) => {
        if (!plainClick(e)) return;
        e.preventDefault();
        openReport(row.name);
      });
      body.append(tr);
    }
    return el("table", {}, el("thead", {}, head), body);
  }

  function sortBy(col) {
    sort = sort.key === col.key
      ? { key: col.key, dir: sort.dir === "asc" ? "desc" : "asc" }
      : { key: col.key, dir: col.first };
    history.replaceState({ view: "overview", sort }, "", location.href);
    const next = drawTable();
    table.replaceWith(next);
    table = next;
  }

  function coneLine(report) {
    const cone = Array.isArray(report.cone) ? report.cone : [];
    // As the report wrote it: JSON drops a whole number's ".0", so it is put back; the minus is "-".
    const part = (label, v) => {
      if (typeof v !== "number") return `${label} ${v === undefined || v === null ? MISSING : text(v)}`;
      const written = Object.is(v, -0) ? "-0.0" : Number.isInteger(v) ? v.toFixed(1) : String(v);
      return `${label} ${written}°`;
    };
    return el("p", {},
      el("span", {}, "Cone"), ": ",
      el("span", {}, part("RA", cone[0])), ", ",
      el("span", {}, part("Dec", cone[1])), ", ",
      el("span", {}, part("Radius", cone[2])));
  }

  function overviewLink() {
    const a = el("a", { href: location.pathname }, "Overview");
    a.addEventListener("click", (e) => {
      if (!plainClick(e)) return;
      e.preventDefault();
      history.pushState({ view: "overview", sort }, "", location.pathname);
      route();
    });
    return a;
  }

  // ---- views -----------------------------------------------------------------------------------

  async function drawOverview() {
    const id = ++view;
    history.replaceState({ view: "overview", sort }, "", location.href);
    document.title = "Transient monitor reports";
    show(line(READING, "reading"));
    const names = await listFolder();
    if (id !== view) return;
    if (names === null) return show(line("Report folder not found"));
    if (names.length === 0) return show(line("Report folder empty"));
    const reports = names.filter((n) => !/[/\\]/.test(n) && REPORT_NAME.test(n)).sort(byCode);
    if (reports.length === 0) return show(line("No reports found in the folder"));
    const results = await Promise.all(reports.map(readReport));
    if (id !== view) return;
    const bad = [];
    rows = [];
    results.forEach((r, i) => (r.error ? bad.push(line(r.error)) : rows.push(rowOf(reports[i], r.report))));
    table = rows.length ? drawTable() : null;
    show(...(bad.length ? [el("div", {}, ...bad)] : []), ...(table ? [table] : []));
  }

  async function drawReport(name) {
    const id = ++view;
    history.replaceState({ view: "report", sort }, "", location.href);
    document.title = name;
    const nav = el("nav", {}, overviewLink());
    if (/[/\\]/.test(name)) return show(nav, line("Invalid file name"));
    if (!REPORT_NAME.test(name)) return show(nav, line(`Invalid file name: ${name}`));
    show(nav, line(READING, "reading"));
    const result = await readReport(name);
    if (id !== view) return;
    if (result.error) return show(nav, line(result.error));
    const report = result.report;
    nav.append(el("a", { href: REPORTS + encodeURIComponent(name), target: "_blank", rel: "noopener" }, "Raw JSON"));
    show(nav, el("section", { "aria-label": "Report" },
      el("h1", {}, `${dateText(report.run_utc)} - ${text(valueOf(report, "footprint"))} - ${text(valueOf(report, "mode"))}`),
      coneLine(report)));
  }

  function openReport(name) {
    history.pushState({ view: "report", sort }, "", "?file=" + encodeURIComponent(name));
    route();
  }

  // ?file= as written in the address: percent-decoded only, so a "+" stays a "+".
  function fileParam() {
    const decode = (s) => {
      try {
        return decodeURIComponent(s);
      } catch {
        return s;
      }
    };
    for (const part of location.search.replace(/^\?/, "").split("&")) {
      const eq = part.indexOf("=");
      if (decode(eq < 0 ? part : part.slice(0, eq)) === "file") return eq < 0 ? "" : decode(part.slice(eq + 1));
    }
    return null;
  }

  function route() {
    const name = fileParam();
    if (name === null) drawOverview();
    else drawReport(name);
  }

  // The sort lives in page memory and in every history entry the page makes; a report's entry
  // carries the sort of the overview it came from. A reload or a new tab starts the overview at
  // the default; a reloaded report page keeps its entry's sort for the Overview link and Back.
  window.addEventListener("popstate", (e) => {
    sort = sortFrom(e.state) ?? sort;
    route();
  });
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) route(); // restored from the back-forward cache: read the folder again
  });
  const navigation = performance.getEntriesByType("navigation")[0];
  const reloadedOverview = navigation?.type === "reload" && fileParam() === null;
  sort = (!reloadedOverview && sortFrom(history.state)) || { ...DEFAULT_SORT };
  route();
})();
