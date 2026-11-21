const $ = (id) => document.getElementById(id);
const charts = {};
const t = (kg) => kg / 1000;
const nf = (n, d = 1) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }).format(n);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PALETTE = ["#2f6f4f", "#2c7be5", "#e5a42c", "#c2410c", "#7c3aed", "#0e9aa7", "#9ca3af"];
let meta = null;
let currentRecord = null;

async function api(url, options) {
  const r = await fetch(url, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = body.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || r.statusText);
  }
  return body;
}

function post(url, data, method = "POST") {
  return api(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

function draw(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($(id), config);
}

const legend = { position: "bottom", labels: { boxWidth: 12, color: css("--ink") } };
const axis = () => ({ ticks: { color: css("--muted") }, grid: { color: css("--line") } });

function setMsg(id, text, ok) {
  const el = $(id);
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
}

function renderYoy(d) {
  $("yoyNote").textContent = d.like_for_like ? `compared over ${d.comparison_months}` : "";
  draw("yoyChart", {
    type: "bar",
    data: {
      labels: [String(d.previous_year), String(d.current_year)],
      datasets: [
        { label: "Scope 1", data: [t(d.previous.scope1), t(d.current.scope1)], backgroundColor: css("--s1"), stack: "s" },
        { label: "Scope 2", data: [t(d.previous.scope2), t(d.current.scope2)], backgroundColor: css("--s2"), stack: "s" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${nf(c.parsed.y)} tCO₂e` } } },
      scales: { x: { stacked: true, ...axis() }, y: { stacked: true, title: { display: true, text: "tCO₂e", color: css("--muted") }, ...axis() } },
    },
  });
  const pct = (v) => (v === null ? "no prior data" : `${v > 0 ? "+" : ""}${v}% vs ${d.previous_year}`);
  $("kTotal").textContent = nf(t(d.current.total), 0) + " t";
  $("kTotalSub").textContent = pct(d.change_pct.total);
  $("kS1").textContent = nf(t(d.current.scope1), 0) + " t";
  $("kS1Sub").textContent = pct(d.change_pct.scope1);
  $("kS2").textContent = nf(t(d.current.scope2), 0) + " t";
  $("kS2Sub").textContent = pct(d.change_pct.scope2);
}

function renderHotspots(d) {
  draw("hotspotChart", {
    type: "doughnut",
    data: {
      labels: d.sources.map((s) => `${s.source} (S${s.scope})`),
      datasets: [{ data: d.sources.map((s) => t(s.kgco2e)), backgroundColor: PALETTE, borderColor: css("--card"), borderWidth: 2 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "58%",
      plugins: {
        legend: { ...legend, position: "right" },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${nf(c.parsed)} tCO₂e (${d.sources[c.dataIndex].share_pct}%)` } },
      },
    },
  });
}

function renderIntensity(d) {
  $("kInt").textContent = nf(d.intensity, 1);
  $("kIntSub").textContent = `${d.intensity_unit} · ${d.start} to ${d.end}`;
}

function renderMonthly(d) {
  const filled = d.months.filter((m) => m.total > 0).length;
  $("monthlyNote").textContent = `${d.year}, ${filled} month${filled === 1 ? "" : "s"} with data`;
  draw("monthlyChart", {
    type: "line",
    data: {
      labels: MONTHS,
      datasets: [{
        label: "Total emissions (tCO₂e)", data: d.months.map((m) => (m.total > 0 ? t(m.total) : null)),
        borderColor: css("--accent"), backgroundColor: "rgba(47,111,79,0.15)", fill: true, tension: 0.25, pointRadius: 4, spanGaps: false,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend, tooltip: { callbacks: { label: (c) => `${nf(c.parsed.y)} tCO₂e` } } },
      scales: { x: axis(), y: { title: { display: true, text: "tCO₂e", color: css("--muted") }, ...axis() } },
    },
  });
}

function renderRecords(rows) {
  $("recordRows").innerHTML = rows.map((r) => `
    <tr>
      <td>${r.id}</td><td>${r.activity_date}</td><td>S${r.scope}</td><td>${r.activity_name}</td>
      <td class="num">${nf(r.quantity, 2)} ${r.unit}</td><td class="num">${r.factor_value} (v${r.factor_version})</td>
      <td class="num">${nf(r.effective_kgco2e, 1)} ${r.is_overridden ? '<span class="tag over">overridden</span>' : ""}</td>
      <td><button class="small ghost" data-id="${r.id}">Override / audit</button></td>
    </tr>`).join("");
  $("recordRows").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => openOverride(Number(b.dataset.id))));
}

async function refresh() {
  const year = Number($("year").value);
  const metric = $("metric").value;
  const start = `${year}-01-01`;
  const end = `${year}-12-31`;
  $("notice").classList.add("hidden");
  try {
    const [yoy, hot, mon, recs] = await Promise.all([
      api(`/api/analytics/yoy?year=${year}`),
      api(`/api/analytics/hotspots?start=${start}&end=${end}`),
      api(`/api/analytics/monthly?year=${year}`),
      api("/api/records?limit=15"),
    ]);
    renderYoy(yoy);
    renderHotspots(hot);
    renderMonthly(mon);
    renderRecords(recs);
    try {
      renderIntensity(await api(`/api/analytics/intensity?start=${start}&end=${end}&metric_name=${encodeURIComponent(metric)}`));
    } catch (e) {
      $("kInt").textContent = "–";
      $("kIntSub").textContent = e.message;
    }
  } catch (e) {
    $("notice").textContent = e.message;
    $("notice").classList.remove("hidden");
  }
}

function fillActivities() {
  const scope = Number($("rScope").value);
  const list = meta.activities.filter((a) => a.scope === scope);
  $("rActivity").innerHTML = list.map((a) => `<option value="${a.activity_key}" data-unit="${a.unit}">${a.name}</option>`).join("");
  syncUnit();
}

function syncUnit() {
  const opt = $("rActivity").selectedOptions[0];
  $("rUnit").textContent = opt ? `(${opt.dataset.unit})` : "";
}

async function loadMeta() {
  meta = await api("/api/meta");
  $("year").innerHTML = meta.years.map((y) => `<option ${y === meta.default_year ? "selected" : ""}>${y}</option>`).join("") || `<option>${meta.default_year}</option>`;
  $("metric").innerHTML = meta.metrics.map((m) => `<option>${m.metric_name}</option>`).join("");
  $("metricNames").innerHTML = meta.metrics.map((m) => `<option value="${m.metric_name}">`).join("");
  fillActivities();
}

async function openOverride(id) {
  currentRecord = await api(`/api/records/${id}`);
  $("ovTitle").textContent = `Record ${id}: ${currentRecord.activity_name}`;
  $("ovCurrent").textContent = `Calculated ${nf(currentRecord.calculated_kgco2e, 2)} kgCO₂e` + (currentRecord.is_overridden ? `, currently overridden to ${nf(currentRecord.override_kgco2e, 2)}` : "");
  $("ovValue").value = currentRecord.effective_kgco2e;
  $("ovRevert").style.display = currentRecord.is_overridden ? "" : "none";
  $("ovMsg").textContent = "";
  await loadAudit(id);
  $("overrideDialog").showModal();
}

async function loadAudit(id) {
  const rows = await api(`/api/records/${id}/audit`);
  $("auditRows").innerHTML = rows.length
    ? rows.map((a) => `<tr><td>${a.changed_at.replace("T", " ").slice(0, 16)}</td><td>${a.action}</td><td class="num">${a.old_value === null ? "–" : nf(a.old_value, 2)}</td><td class="num">${a.new_value === null ? "–" : nf(a.new_value, 2)}</td><td>${a.changed_by}</td><td>${a.reason}</td></tr>`).join("")
    : `<tr><td colspan="6">No changes recorded</td></tr>`;
}

$("rScope").addEventListener("change", fillActivities);
$("rActivity").addEventListener("change", syncUnit);
$("year").addEventListener("change", refresh);
$("metric").addEventListener("change", refresh);
$("ovCancel").addEventListener("click", () => $("overrideDialog").close());

$("recordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const scope = $("rScope").value;
  try {
    const r = await post(`/api/records/scope${scope}`, {
      activity_key: $("rActivity").value, quantity: Number($("rQty").value), activity_date: $("rDate").value,
      location: $("rLoc").value || null, notes: $("rNotes").value || null,
    });
    setMsg("recordMsg", `Saved: ${nf(r.calculated_kgco2e, 1)} kgCO₂e using factor v${r.factor_version} (${r.factor_value} per ${r.unit})`, true);
    $("rQty").value = "";
    await refresh();
  } catch (err) {
    setMsg("recordMsg", err.message, false);
  }
});

$("metricForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const m = await post("/api/metrics", { date: $("mDate").value, metric_name: $("mName").value, value: Number($("mValue").value), unit: $("mUnit").value, aggregation: $("mAgg").value });
    setMsg("metricMsg", `Saved ${m.metric_name} = ${nf(m.value, 2)} for ${m.date}`, true);
    await loadMeta();
    await refresh();
  } catch (err) {
    setMsg("metricMsg", err.message, false);
  }
});

$("overrideForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await post(`/api/records/${currentRecord.id}/override`, { override_kgco2e: Number($("ovValue").value), reason: $("ovReason").value, changed_by: $("ovUser").value }, "PATCH");
    setMsg("ovMsg", "Override applied", true);
    await loadAudit(currentRecord.id);
    await refresh();
  } catch (err) {
    setMsg("ovMsg", err.message, false);
  }
});

$("ovRevert").addEventListener("click", async () => {
  try {
    await post(`/api/records/${currentRecord.id}/override/revert`, { reason: $("ovReason").value, changed_by: $("ovUser").value });
    setMsg("ovMsg", "Reverted to calculated value", true);
    $("ovRevert").style.display = "none";
    await loadAudit(currentRecord.id);
    await refresh();
  } catch (err) {
    setMsg("ovMsg", err.message, false);
  }
});

const now = new Date();
const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
$("rDate").value = today;
$("mDate").value = today;
$("rDate").max = today;
loadMeta().then(refresh);
