document.querySelectorAll("table.sortable").forEach((t) => {
  t.querySelectorAll("th").forEach((th, i) => th.querySelector("button").addEventListener("click", () => {
    const dir = th.getAttribute("aria-sort") === "descending" ? "ascending" : "descending";
    t.querySelectorAll("th").forEach((x) => x.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", dir);
    const key = (tr) => { const td = tr.children[i]; const v = td && td.dataset.v;
      if (v === undefined) return null; const n = Number(v); return Number.isNaN(n) ? v : n; };
    const rows = [...t.tBodies[0].rows].sort((a, b) => {
      const x = key(a), y = key(b);
      if (x === null) return 1; if (y === null) return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return dir === "ascending" ? c : -c; });
    t.tBodies[0].append(...rows);
  }));
});
