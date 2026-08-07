const state = {
  range: "year",
  start: "",
  end: "",
  habit: "",
  list: "",
  period: "",
  data: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseDate = (s) => new Date(`${s}T12:00:00`);
const fmtNumber = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 1 });
const months = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];
const weekdays = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"];
const plural = (n, one, few, many) =>
  n === 1 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? few : many;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[ch]));
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.className = "toast", 3200);
}

function dateRange(kind, bounds) {
  const end = bounds?.max_date ? parseDate(bounds.max_date) : new Date();
  let start = new Date(end);
  if (kind === "month") start = new Date(end.getFullYear(), end.getMonth(), 1, 12);
  if (kind === "quarter") start.setMonth(start.getMonth() - 3, start.getDate() + 1);
  if (kind === "half") start.setMonth(start.getMonth() - 6, start.getDate() + 1);
  if (kind === "year") start.setFullYear(start.getFullYear() - 1, start.getDate() + 1);
  if (kind === "all") start = bounds?.min_date ? parseDate(bounds.min_date) : start;
  if (kind === "custom") return { start: state.start, end: state.end };
  return { start: iso(start), end: iso(end) };
}

function queryString(includeHabit = true) {
  const params = new URLSearchParams();
  if (state.start) params.set("start", state.start);
  if (state.end) params.set("end", state.end);
  if (includeHabit && state.habit) params.set("habit", state.habit);
  if (state.list) params.set("list", state.list);
  if (state.period) params.set("period", state.period);
  return params.toString();
}

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Nie udało się pobrać danych");
  return payload;
}

async function load(initial = false) {
  $("#syncState").innerHTML = "<i></i> Aktualizuję…";
  try {
    let data = await api(`/api/dashboard?${queryString()}`);
    if (initial) {
      const selected = dateRange(state.range, data.bounds);
      state.start = selected.start;
      state.end = selected.end;
      data = await api(`/api/dashboard?${queryString()}`);
      populateOptions(data.options);
    }
    state.data = data;
    render(data);
    $("#syncState").innerHTML = "<i></i> Dane aktualne";
  } catch (error) {
    $("#syncState").textContent = "Błąd danych";
    toast(error.message, true);
  }
}

function populateOptions(options) {
  function fill(selector, values, fallback) {
    const select = $(selector);
    const current = select.value;
    select.innerHTML = `<option value="">${fallback}</option>`;
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.append(option);
    });
    select.value = current;
  }
  fill("#habitFilter", options.habits, "Wszystkie nawyki");
  fill("#listFilter", options.lists, "Wszystkie listy");
  fill("#periodFilter", options.periods, "Dzienny i tygodniowy");
}

function render(data) {
  const s = data.summary;
  $("#metricRate").textContent = `${fmtNumber.format(s.rate)}%`;
  $("#metricDone").textContent = fmtNumber.format(s.done);
  $("#metricMissed").textContent = fmtNumber.format(s.missed);
  $("#metricPerfect").textContent = fmtNumber.format(s.perfect_days);
  $("#metricRateSub").textContent = `${s.records} zapisanych okresów`;
  $("#dataRange").textContent = state.start && state.end ? `${state.start} — ${state.end}` : "Cały okres";
  renderToday(data.analytics?.today);
  renderHeatmap(data.heatmap);
  renderHabits(data.habits);
  renderAnalytics(data.analytics);
}

function signed(value, suffix = " pp") {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${fmtNumber.format(value)}${suffix}`;
}

function renderAnalytics(analytics) {
  if (!analytics) return;
  const comparison = analytics.comparison;
  $("#comparisonValue").textContent = signed(comparison.delta);
  $("#comparisonValue").className = comparison.delta > 0 ? "positive" : comparison.delta < 0 ? "negative" : "";
  $("#comparisonMeta").textContent = comparison.previous_rate === null
    ? "Brak danych w poprzednim okresie"
    : `${fmtNumber.format(comparison.previous_rate)}% poprzednio · ${comparison.previous_records} zapisów`;
  $("#momentumValue").textContent = signed(analytics.trends.momentum);
  $("#momentumValue").className = analytics.trends.momentum > 0 ? "positive" : analytics.trends.momentum < 0 ? "negative" : "";
  $("#bestWeekday").textContent = analytics.best_weekday ? `${weekdays[analytics.best_weekday.day]} · ${fmtNumber.format(analytics.best_weekday.rate)}%` : "—";
  $("#worstWeekday").textContent = analytics.worst_weekday ? `Najsłabiej: ${weekdays[analytics.worst_weekday.day]} · ${fmtNumber.format(analytics.worst_weekday.rate)}%` : "Za mało danych";
  const sd = analytics.regularity.weekly_stddev;
  $("#regularityValue").textContent = sd === null ? "Za mało danych" : sd <= 5 ? "Bardzo stabilnie" : sd <= 15 ? "Stabilnie" : "Nierówny rytm";
  $("#regularityValue").title = sd === null ? "Potrzebne co najmniej dwa tygodnie" : `Odchylenie tygodniowe: ${sd} pp`;

  renderTrend(analytics.trends.daily.length ? analytics.trends.daily : analytics.trends.weekly);
  renderBars("#weekdayBars", analytics.weekdays.filter((x) => x.records).map((x) => ({ name: weekdays[x.day], rate: x.rate, meta: `${x.records}` })));
  renderBars("#listBars", analytics.lists.map((x) => ({ name: x.name, rate: x.rate, meta: `${x.done}/${x.total}` })));
  renderMonthly(analytics.monthly);
  renderHabitInsights(analytics);
  renderRecords(analytics.goal_metrics);
  renderCorrelations(analytics.correlations);
  renderQuality(analytics.data_quality);
}

function renderBars(selector, items) {
  $(selector).innerHTML = items.length ? items.map((item) => `
    <div class="bar-row"><span>${escapeHtml(item.name)}</span><div class="bar-track"><i style="width:${item.rate || 0}%"></i></div><strong title="${escapeHtml(item.meta)} zapisów">${item.rate === null ? "—" : `${fmtNumber.format(item.rate)}%`}</strong></div>`).join("") : "<p class='hint'>Brak danych.</p>";
}

function renderMonthly(items) {
  $("#monthlyGrid").innerHTML = items.length ? items.map((item) => {
    const [year, month] = item.month.split("-").map(Number);
    return `<article class="month-card"><span>${months[month - 1]} ${year}</span><strong>${fmtNumber.format(item.rate)}%</strong><small>${item.perfect_days} idealnych dni · ${item.records} zapisów</small><div class="mini-progress"><i style="width:${item.rate}%"></i></div></article>`;
  }).join("") : "<p class='hint'>Brak miesięcy z danymi dziennymi.</p>";
}

function renderHabitInsights(analytics) {
  const items = [];
  if (analytics.most_improved) items.push({
    title: "Największa poprawa", name: analytics.most_improved.name,
    value: signed(analytics.most_improved.delta), reliable: analytics.most_improved.reliable,
  });
  if (analytics.most_regressed) items.push({
    title: "Największy spadek", name: analytics.most_regressed.name,
    value: signed(analytics.most_regressed.delta), reliable: analytics.most_regressed.reliable,
  });
  const best = [...analytics.behaviors].filter((x) => x.median_recovery !== null).sort((a, b) => a.median_recovery - b.median_recovery)[0];
  if (best) items.push({ title: "Najszybszy powrót", name: best.name, value: `${fmtNumber.format(best.median_recovery)} okresu`, reliable: best.recoveries >= 3 });
  const longest = [...analytics.behaviors].sort((a, b) => b.longest_break - a.longest_break)[0];
  if (longest) items.push({ title: "Najdłuższa przerwa", name: longest.name, value: `${longest.longest_break} okresów`, reliable: true });
  $("#habitInsights").innerHTML = items.length ? items.map((item) => `
    <div class="insight-item"><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.name)}${item.reliable ? "" : " · mała próba"}</small></span><span class="insight-value ${String(item.value).startsWith("+") ? "positive" : String(item.value).startsWith("-") ? "negative" : ""}">${escapeHtml(item.value)}</span></div>`).join("") : "<p class='hint'>Potrzeba danych z co najmniej dwóch okresów porównawczych.</p>";
}

function renderToday(today) {
  if (!today?.pending) {
    $("#todayVerdict").textContent = "Brak danych o dziś";
    $("#todayMeta").textContent = "Odśwież stronę — backend i interfejs mogą być z różnych wersji.";
    return;
  }
  const left = today.pending.length;
  $("#todayVerdict").textContent = !today.total
    ? "Brak nawyków na dziś"
    : left === 0
      ? "Komplet — wszystko odhaczone"
      : `${left} ${plural(left, "nawyk został", "nawyki zostały", "nawyków zostało")} do zrobienia`;
  $("#todayMeta").textContent = today.total ? `${today.done} z ${today.total} wykonane · ${today.date}` : "";
  $("#todayList").innerHTML = left ? today.pending.map((item) => {
    const periods = (n) => item.unit === "week"
      ? plural(n, "tydzień", "tygodnie", "tygodni")
      : plural(n, "dzień", "dni", "dni");
    const stake = item.streak > 0
      ? `tracisz serię ${item.streak} ${periods(item.streak)}`
      : item.missed > 0
        ? `${item.missed} ${periods(item.missed)} z rzędu bez wykonania`
        : "jeszcze nie zaczęte";
    const progress = item.goal > 0
      ? `${fmtNumber.format(item.quantity)} / ${fmtNumber.format(item.goal)} ${escapeHtml(item.value_unit)}`
      : `limit 0 · odnotowano ${fmtNumber.format(item.quantity)} ${escapeHtml(item.value_unit)}`;
    const tone = item.streak > 0 ? " streak" : item.missed > 0 ? " cold" : "";
    return `<div class="today-row${tone}"><span><strong>${escapeHtml(item.name)}</strong><small>${progress}</small></span><span class="stake">${stake}</span></div>`;
  }).join("") : `<p class="today-clear">${today.total ? "Wszystkie dzisiejsze cele zaliczone." : "Habitify nie zwrócił nawyków zaplanowanych na dziś."}</p>`;
}

function renderRecords(items) {
  const useful = items.filter((item) => item.personal_best && (item.personal_best.unit || item.average_ratio !== null || item.zero_goal_successes !== null));
  $("#recordsGrid").innerHTML = useful.length ? useful.map((item) => {
    const record = item.personal_best;
    const targetText = item.average_ratio !== null
      ? `Średnio ${fmtNumber.format(item.average_ratio)}% celu · margines ${signed(item.average_margin, ` ${record.unit || ""}`)}`
      : `${item.zero_goal_successes} okresów bez naruszenia · ${item.zero_goal_violations} naruszeń`;
    return `<article class="record-card"><span>${escapeHtml(item.name)}</span><strong>${fmtNumber.format(record.value)} ${escapeHtml(record.unit)}</strong><small>Rekord: ${record.date}<br>${escapeHtml(targetText)}</small></article>`;
  }).join("") : "<p class='hint'>Brak wartości ilościowych w wybranym okresie.</p>";
}

function renderCorrelations(items) {
  $("#correlationList").innerHTML = items.length ? items.slice(0, 6).map((item) => {
    const score = item.correlation === null ? "—" : signed(item.correlation * 100, "");
    return `<div class="insight-item"><span><strong>${escapeHtml(item.first)} + ${escapeHtml(item.second)}</strong><small>${item.observations} wspólnych dni · razem wykonane ${fmtNumber.format(item.both_complete)}%${item.reliable ? "" : " · zbieram dane"}</small></span><span class="insight-value">${score}</span></div>`;
  }).join("") : "<p class='hint'>Potrzeba co najmniej dwóch nawyków dziennych ze wspólnymi datami.</p>";
}

function renderQuality(quality) {
  const latest = quality.latest_sync;
  const warning = quality.coverage_warning;
  $("#qualitySummary").innerHTML = `<div class="quality-callout ${warning ? "" : "quality-good"}">${warning || "Pokrycie porównywanych okresów nie zgłasza ostrzeżeń."}<br>${latest ? `Ostatnia synchronizacja: ${new Date(latest.completed_at).toLocaleString("pl-PL")} · ${latest.total_rows} okresów` : "Brak synchronizacji"}</div>`;
  $("#qualityList").innerHTML = quality.habits.map((item) => `
    <div class="quality-row"><strong>${escapeHtml(item.name)}</strong><span>${fmtNumber.format(item.coverage)}% pokrycia · ${item.gaps} luk<br>${item.first} — ${item.last}</span></div>`).join("");
}

function renderTrend(points) {
  const canvas = $("#trendChart");
  $("#trendEmpty").classList.toggle("hidden", points.length > 0);
  canvas.classList.toggle("hidden", points.length === 0);
  if (!points.length) return;
  requestAnimationFrame(() => {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, rect.width * dpr); canvas.height = Math.max(1, rect.height * dpr);
    const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
    const w = rect.width, h = rect.height, left = 34, right = 14, top = 15, bottom = 24;
    const x = (i) => left + (points.length <= 1 ? (w-left-right)/2 : i/(points.length-1)*(w-left-right));
    const y = (v) => top + (100-v)/100*(h-top-bottom);
    ctx.font = "9px sans-serif"; ctx.fillStyle = "#73776f"; ctx.strokeStyle = "#dcddd7"; ctx.lineWidth = 1;
    [0, 25, 50, 75, 100].forEach((v) => { ctx.beginPath(); ctx.moveTo(left, y(v)); ctx.lineTo(w-right, y(v)); ctx.stroke(); ctx.fillText(`${v}%`, 4, y(v)+3); });
    const line = (key, color, width) => { ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(x(i), y(p[key])) : ctx.moveTo(x(i), y(p[key]))); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = "round"; ctx.stroke(); };
    line("rate", "#b8bcb4", 1); line("avg30", "#d78b58", 2); line("avg7", "#355f4b", 2.5);
    points.forEach((p, i) => { ctx.beginPath(); ctx.arc(x(i), y(p.rate), 2.5, 0, Math.PI*2); ctx.fillStyle = "#355f4b"; ctx.fill(); });
    ctx.fillStyle = "#73776f"; ctx.fillText(points[0].date, left, h-5); if (points.length > 1) { const label = points.at(-1).date; ctx.fillText(label, w-right-ctx.measureText(label).width, h-5); }
  });
}

function startOfWeek(d) {
  const result = new Date(d);
  result.setDate(result.getDate() - ((result.getDay() + 6) % 7));
  return result;
}

function renderHeatmap(values) {
  const container = $("#heatmap");
  const labels = $("#monthLabels");
  container.innerHTML = "";
  labels.innerHTML = "";
  if (!state.start || !state.end) return;
  const lookup = new Map(values.map((item) => [item.date, item]));
  const actualStart = parseDate(state.start);
  const actualEnd = parseDate(state.end);
  const gridStart = startOfWeek(actualStart);
  const gridEnd = new Date(startOfWeek(actualEnd));
  gridEnd.setDate(gridEnd.getDate() + 6);
  const totalDays = Math.round((gridEnd - gridStart) / 86400000) + 1;
  const weeks = Math.ceil(totalDays / 7);
  container.style.gridTemplateColumns = `repeat(${weeks}, 13px)`;

  let lastMonth = -1;
  for (let i = 0; i < totalDays; i++) {
    const day = new Date(gridStart);
    day.setDate(day.getDate() + i);
    const dayIso = iso(day);
    const item = lookup.get(dayIso);
    const rate = item?.rate ?? 0;
    const level = rate === 0 ? 0 : rate < 40 ? 1 : rate < 70 ? 2 : rate < 100 ? 3 : 4;
    const cell = document.createElement("button");
    cell.className = `heat-cell${day < actualStart || day > actualEnd ? " outside" : ""}`;
    cell.dataset.level = level;
    cell.type = "button";
    cell.title = item
      ? `${dayIso}: ${item.done}/${item.total} wykonane (${item.rate}%)`
      : `${dayIso}: brak danych`;
    cell.setAttribute("aria-label", cell.title);
    container.append(cell);
  }

  for (let week = 0; week < weeks; week++) {
    const day = new Date(gridStart);
    day.setDate(day.getDate() + week * 7);
    const label = document.createElement("span");
    label.style.width = "17px";
    if (day.getMonth() !== lastMonth) {
      label.textContent = months[day.getMonth()];
      lastMonth = day.getMonth();
    }
    labels.append(label);
  }
  labels.style.width = `${weeks * 17 + 30}px`;
  $("#heatmapCaption").textContent = `${values.length} dni z danymi · kolor pokazuje udział wykonanych nawyków dziennych`;
}

function renderHabits(habits) {
  const body = $("#habitRows");
  $("#emptyState").classList.toggle("hidden", habits.length > 0);
  body.innerHTML = habits.map((habit) => {
    const unit = habit.streak_unit === "week" ? "tyg." : "dni";
    const average = habit.unit ? `${fmtNumber.format(habit.average)} ${escapeHtml(habit.unit)}` : fmtNumber.format(habit.average);
    return `<tr>
      <td><div class="habit-name"><span class="habit-dot">${escapeHtml(habit.name[0] || "H")}</span><span><strong>${escapeHtml(habit.name)}</strong><small>${escapeHtml(habit.list || habit.type)} · ${escapeHtml(habit.period)}</small></span></div></td>
      <td class="rate-cell"><div class="rate-top"><strong>${fmtNumber.format(habit.rate)}%</strong><span>${habit.rate >= 80 ? "dobry rytm" : "do poprawy"}</span></div><div class="progress"><i style="width:${habit.rate}%"></i></div></td>
      <td class="positive">${habit.done}</td><td class="${habit.missed ? "negative" : ""}">${habit.missed}</td>
      <td><strong>${habit.current_streak}</strong> ${unit}<br><small>rekord ${habit.longest_streak}</small></td>
      <td>${average}</td>
      <td><button class="row-open" data-habit="${encodeURIComponent(habit.name)}" aria-label="Otwórz ${escapeHtml(habit.name)}">›</button></td>
    </tr>`;
  }).join("");
  $$(".row-open").forEach((button) => button.addEventListener("click", () => openDetail(decodeURIComponent(button.dataset.habit))));
}

async function openDetail(name) {
  try {
    const detail = await api(`/api/habits/${encodeURIComponent(name)}?${queryString(false)}`);
    const unit = detail.streak_unit === "week" ? "tyg." : "dni";
    $("#detailContent").innerHTML = `
      <div class="detail-head"><p class="eyebrow">${escapeHtml(detail.list || detail.type)} · ${escapeHtml(detail.period)}</p><h2>${escapeHtml(detail.name)}</h2><p class="dialog-intro">Cel: ${fmtNumber.format(detail.goal)} ${escapeHtml(detail.unit)} · ${escapeHtml(detail.type)}</p></div>
      <div class="detail-kpis">
        <div class="detail-kpi"><span>Skuteczność</span><strong>${fmtNumber.format(detail.rate)}%</strong></div>
        <div class="detail-kpi"><span>Aktualny streak</span><strong>${detail.current_streak} ${unit}</strong></div>
        <div class="detail-kpi"><span>Rekord</span><strong>${detail.longest_streak} ${unit}</strong></div>
        <div class="detail-kpi"><span>Średnia</span><strong>${fmtNumber.format(detail.average)} ${escapeHtml(detail.unit)}</strong></div>
      </div>
      <div class="chart-wrap"><p class="chart-title">Wartość w czasie</p><canvas id="detailChart"></canvas></div>`;
    $("#detailDialog").showModal();
    requestAnimationFrame(() => drawChart(detail));
  } catch (error) { toast(error.message, true); }
}

function drawChart(detail) {
  const canvas = $("#detailChart");
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height, p = 26;
  const values = detail.records.map((r) => r.quantity);
  const max = Math.max(detail.goal, ...values, 1) * 1.12;
  const x = (i) => p + (values.length <= 1 ? (w - p * 2) / 2 : i / (values.length - 1) * (w - p * 2));
  const y = (v) => h - p - v / max * (h - p * 2);
  ctx.strokeStyle = "#d7d9d2"; ctx.lineWidth = 1;
  [0, .5, 1].forEach((r) => { ctx.beginPath(); ctx.moveTo(p, p + r * (h - p * 2)); ctx.lineTo(w - p, p + r * (h - p * 2)); ctx.stroke(); });
  ctx.setLineDash([5, 5]); ctx.strokeStyle = "#d78b58"; ctx.beginPath(); ctx.moveTo(p, y(detail.goal)); ctx.lineTo(w - p, y(detail.goal)); ctx.stroke(); ctx.setLineDash([]);
  if (values.length) {
    ctx.strokeStyle = "#355f4b"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.beginPath();
    values.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))); ctx.stroke();
    values.forEach((v, i) => { ctx.beginPath(); ctx.fillStyle = detail.records[i].complete ? "#355f4b" : "#bd6557"; ctx.arc(x(i), y(v), 3, 0, Math.PI * 2); ctx.fill(); });
  }
  ctx.fillStyle = "#73776f"; ctx.font = "10px sans-serif"; ctx.fillText(`cel ${fmtNumber.format(detail.goal)}`, p + 4, Math.max(10, y(detail.goal) - 6));
}

async function syncHabitify(full = false) {
  const buttons = [$("#syncButton"), $("#dialogSyncButton"), $("#fullSyncButton")].filter(Boolean);
  buttons.forEach((button) => button.disabled = true);
  $("#syncState").innerHTML = "<i></i> Synchronizuję…";
  try {
    const result = await api(`/api/sync${full ? "?full=1" : ""}`, { method: "POST" });
    toast(`Synchronizacja zakończona: ${result.inserted_rows} nowych, ${result.updated_rows} zaktualizowanych.`);
    await load(true);
    if ($("#settingsDialog").open) await loadSyncSettings();
  } catch (error) {
    $("#syncState").textContent = "Błąd synchronizacji";
    toast(error.message, true);
  } finally {
    buttons.forEach((button) => button.disabled = false);
  }
}

async function loadSyncSettings() {
  try {
    const [config, syncs] = await Promise.all([api("/api/config"), api("/api/syncs")]);
    const latest = config.latest_sync;
    $("#connectionStatus").className = `quality-callout ${config.habitify_configured && latest?.status !== "failed" ? "quality-good" : ""}`;
    $("#connectionStatus").textContent = !config.habitify_configured
      ? "Brak HABITIFY_API_KEY w konfiguracji."
      : config.sync_in_progress
        ? "Synchronizacja właśnie trwa."
        : latest?.status === "failed"
          ? `Ostatnia synchronizacja nie powiodła się: ${latest.error || "nieznany błąd"}`
          : config.sync_interval_minutes
            ? `Połączenie skonfigurowane · synchronizacja co ${config.sync_interval_minutes} min.`
            : "Połączenie skonfigurowane · automatyczna synchronizacja wyłączona.";
    $("#syncHistory").innerHTML = syncs.length ? syncs.map((item) => `
      <div class="import-row"><span><strong>${item.status === "success" ? "Zakończona" : item.status === "failed" ? "Błąd" : "W toku"}</strong><br>${new Date(item.completed_at || item.started_at).toLocaleString("pl-PL")}${item.full_sync ? " · pełna" : ""}</span><span>${item.habit_count} nawyków · ${item.total_rows} okresów<br>+${item.inserted_rows} / ↻${item.updated_rows}</span></div>`).join("") : "<p class='hint'>Brak synchronizacji.</p>";
  } catch (error) { toast(error.message, true); }
}

$$('[data-close]').forEach((button) => button.addEventListener("click", () => $(`#${button.dataset.close}`).close()));
$$("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); }));
$("#syncButton").addEventListener("click", () => syncHabitify());
$("#dialogSyncButton").addEventListener("click", () => syncHabitify());
$("#fullSyncButton").addEventListener("click", () => syncHabitify(true));
$("#settingsButton").addEventListener("click", async () => { await loadSyncSettings(); $("#settingsDialog").showModal(); });

$$('[data-range]').forEach((button) => button.addEventListener("click", async () => {
  state.range = button.dataset.range;
  $$('[data-range]').forEach((b) => b.classList.toggle("active", b === button));
  $("#customRange").classList.toggle("hidden", state.range !== "custom");
  if (state.range !== "custom") {
    const range = dateRange(state.range, state.data?.bounds);
    state.start = range.start; state.end = range.end;
    await load();
  }
}));

$("#applyRange").addEventListener("click", () => {
  state.start = $("#startDate").value; state.end = $("#endDate").value;
  if (!state.start || !state.end || state.start > state.end) return toast("Wybierz poprawny zakres dat.", true);
  load();
});

[["#habitFilter", "habit"], ["#listFilter", "list"], ["#periodFilter", "period"]].forEach(([selector, key]) => {
  $(selector).addEventListener("change", (event) => { state[key] = event.target.value; load(); });
});
$("#clearFilters").addEventListener("click", () => {
  state.habit = state.list = state.period = "";
  $("#habitFilter").value = $("#listFilter").value = $("#periodFilter").value = "";
  load();
});

load(true);
