const state = {
  range: "year",
  start: "",
  end: "",
  habit: "",
  list: "",
  period: "",
  data: null,
};
const historyState = { tab: "syncs", page: 1, perPage: 10, dateFrom: "", dateTo: "" };

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
  $("#metricRate").textContent = s.rate === null ? "—" : `${fmtNumber.format(s.rate)}%`;
  $("#metricDone").textContent = fmtNumber.format(s.done);
  $("#metricMissed").textContent = fmtNumber.format(s.missed);
  $("#metricProgress").textContent = fmtNumber.format(s.in_progress);
  $("#metricPerfect").textContent = fmtNumber.format(s.perfect_days);
  $("#metricRateSub").textContent = `${s.resolved} zakończonych okresów`;
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
  renderBars("#listBars", analytics.lists.map((x) => ({ name: x.name, rate: x.rate, meta: `${x.done}/${x.total}${x.in_progress ? ` · ${x.in_progress} w trakcie` : ""}` })));
  renderMonthly(analytics.monthly);
  renderHabitInsights(analytics);
  renderRecords(analytics.goal_metrics);
  renderQuality(analytics.data_quality);
}

function renderBars(selector, items) {
  $(selector).innerHTML = items.length ? items.map((item) => `
    <div class="bar-row"><span>${escapeHtml(item.name)}</span><div class="bar-track"><i style="width:${item.rate || 0}%"></i></div><strong title="${escapeHtml(item.meta)} zapisów">${item.rate === null ? "—" : `${fmtNumber.format(item.rate)}%`}</strong></div>`).join("") : "<p class='hint'>Brak danych.</p>";
}

function renderMonthly(items) {
  $("#monthlyGrid").innerHTML = items.length ? items.map((item) => {
    const [year, month] = item.month.split("-").map(Number);
    return `<article class="month-card"><span>${months[month - 1]} ${year}</span><strong>${item.rate === null ? "—" : `${fmtNumber.format(item.rate)}%`}</strong><small>${item.perfect_days} ${plural(item.perfect_days, "idealny dzień", "idealne dni", "idealnych dni")} · ${item.records} ${plural(item.records, "zapis", "zapisy", "zapisów")}</small><div class="mini-progress"><i style="width:${item.rate || 0}%"></i></div></article>`;
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
  if (best) items.push({ title: "Najszybszy powrót", name: best.name, value: `${fmtNumber.format(best.median_recovery)} ${plural(Math.round(best.median_recovery), "okres", "okresy", "okresów")}`, reliable: best.recoveries >= 3 });
  const longest = [...analytics.behaviors].sort((a, b) => b.longest_break - a.longest_break)[0];
  if (longest) items.push({ title: "Najdłuższa przerwa", name: longest.name, value: `${longest.longest_break} ${plural(longest.longest_break, "okres", "okresy", "okresów")}`, reliable: true });
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
      : `${left} ${plural(left, "cel jest", "cele są", "celów jest")} w trakcie`;
  $("#todayMeta").textContent = today.total ? `${today.done} z ${today.total} wykonane · ${today.date}` : "";
  $("#todayList").innerHTML = left ? today.pending.map((item) => {
    const progress = progressText(item);
    const streak = item.streak ? ` · seria ${item.streak} ${item.unit === "week" ? "tyg." : "dni"}` : "";
    return `<div class="today-row pending"><span><strong>${escapeHtml(item.name)}</strong><small>${progress}</small></span><span class="stake">W trakcie${streak}</span></div>`;
  }).join("") : `<p class="today-clear">${today.total ? "Wszystkie dzisiejsze cele zaliczone." : "Habitify nie zwrócił nawyków zaplanowanych na dziś."}</p>`;
}

function progressText(item) {
  const unit = escapeHtml(item.value_unit || "");
  if (item.type === "Breaking" && item.goal === 0) return item.quantity === 0 ? "cel zachowany do tej pory" : `${fmtNumber.format(item.quantity)} ${unit} · cel przekroczony`;
  if (item.type === "Breaking") return `${fmtNumber.format(item.quantity)} / ${fmtNumber.format(item.goal)} ${unit} · pozostało ${fmtNumber.format(Math.max(0, item.goal - item.quantity))} ${unit}`;
  const percent = item.goal > 0 ? Math.min(100, item.quantity / item.goal * 100) : 0;
  return `${fmtNumber.format(item.quantity)} / ${fmtNumber.format(item.goal)} ${unit} · ${fmtNumber.format(percent)}%`;
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
    const theme = getComputedStyle(document.documentElement), token = (name) => theme.getPropertyValue(name).trim();
    const narrow = w < 520; // na telefonie surowy wynik i kropki to sama gęstwina, zostają średnie
    const step = Math.max(1, Math.ceil(points.length / (w * 1.5))); // najwyżej ~1,5 punktu na piksel
    const data = step > 1 ? points.filter((p, i) => i % step === 0) : points;
    const x = (i) => left + (data.length <= 1 ? (w-left-right)/2 : i/(data.length-1)*(w-left-right));
    const y = (v) => top + (100-v)/100*(h-top-bottom);
    ctx.font = "9px sans-serif"; ctx.fillStyle = token("--muted"); ctx.strokeStyle = token("--line"); ctx.lineWidth = 1;
    [0, 25, 50, 75, 100].forEach((v) => { ctx.beginPath(); ctx.moveTo(left, y(v)); ctx.lineTo(w-right, y(v)); ctx.stroke(); ctx.fillText(`${v}%`, 4, y(v)+3); });
    const line = (key, color, width) => { ctx.beginPath(); data.forEach((p, i) => i ? ctx.lineTo(x(i), y(p[key])) : ctx.moveTo(x(i), y(p[key]))); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = "round"; ctx.stroke(); };
    if (!narrow) line("rate", token("--dot-raw"), 1);
    line("avg30", token("--orange"), 2); line("avg7", token("--green"), 2.5);
    if (!narrow) data.forEach((p, i) => { ctx.beginPath(); ctx.arc(x(i), y(p.rate), 2.5, 0, Math.PI*2); ctx.fillStyle = token("--green"); ctx.fill(); });
    ctx.fillStyle = token("--muted"); ctx.fillText(data[0].date, left, h-5); if (data.length > 1) { const label = data.at(-1).date; ctx.fillText(label, w-right-ctx.measureText(label).width, h-5); }
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
    cell.className = `heat-cell${day < actualStart || day > actualEnd ? " outside" : ""}${item?.in_progress ? " in-progress" : ""}`;
    cell.dataset.level = level;
    cell.type = "button";
    cell.title = item
      ? `${dayIso}: ${item.done}/${item.total} wykonane${item.in_progress ? ` · ${item.in_progress} w trakcie` : ""} (${item.rate}%)`
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
    const hasRate = habit.rate !== null;
    return `<tr>
      <td class="habit-cell"><div class="habit-name"><span class="habit-dot">${escapeHtml(habit.name[0] || "H")}</span><span><strong>${escapeHtml(habit.name)}</strong><small>${escapeHtml(habit.list || habit.type)} · ${escapeHtml(habit.period)}</small></span></div></td>
      <td class="rate-cell"><div class="rate-top"><strong>${hasRate ? `${fmtNumber.format(habit.rate)}%` : "—"}</strong><span>${hasRate ? (habit.rate >= 80 ? "dobry rytm" : "do poprawy") : "brak zamkniętych"}</span></div><div class="progress"><i style="width:${habit.rate || 0}%"></i></div></td>
      <td class="positive" data-label="Wykonane">${habit.done}</td><td class="${habit.missed ? "negative" : ""}" data-label="Niewykonane">${habit.missed}</td>
      <td class="pending-count" data-label="W trakcie">${habit.in_progress || "—"}</td>
      <td data-label="Streak"><strong>${habit.current_streak}</strong> ${unit}<br><small>rekord ${habit.longest_streak}</small></td>
      <td data-label="Średnia">${average}</td>
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
        <div class="detail-kpi"><span>Skuteczność</span><strong>${detail.rate === null ? "—" : `${fmtNumber.format(detail.rate)}%`}</strong></div>
        <div class="detail-kpi"><span>W trakcie</span><strong>${detail.in_progress}</strong></div>
        <div class="detail-kpi"><span>Aktualny streak</span><strong>${detail.current_streak} ${unit}</strong></div>
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
  const theme = getComputedStyle(document.documentElement), token = (name) => theme.getPropertyValue(name).trim();
  const values = detail.records.map((r) => r.quantity);
  const max = Math.max(detail.goal, ...values, 1) * 1.12;
  const x = (i) => p + (values.length <= 1 ? (w - p * 2) / 2 : i / (values.length - 1) * (w - p * 2));
  const y = (v) => h - p - v / max * (h - p * 2);
  ctx.strokeStyle = token("--line"); ctx.lineWidth = 1;
  [0, .5, 1].forEach((r) => { ctx.beginPath(); ctx.moveTo(p, p + r * (h - p * 2)); ctx.lineTo(w - p, p + r * (h - p * 2)); ctx.stroke(); });
  ctx.setLineDash([5, 5]); ctx.strokeStyle = token("--orange"); ctx.beginPath(); ctx.moveTo(p, y(detail.goal)); ctx.lineTo(w - p, y(detail.goal)); ctx.stroke(); ctx.setLineDash([]);
  if (values.length) {
    ctx.strokeStyle = token("--green"); ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.beginPath();
    values.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))); ctx.stroke();
    const dots = values.length <= (w - p * 2) / 4; // przy gęstych danych same linie czytają się lepiej
    if (dots) values.forEach((v, i) => { ctx.beginPath(); ctx.fillStyle = detail.records[i].state === "complete" ? token("--green") : detail.records[i].state === "in_progress" ? token("--pending") : token("--red"); ctx.arc(x(i), y(v), 3, 0, Math.PI * 2); ctx.fill(); });
  }
  ctx.fillStyle = token("--muted"); ctx.font = "10px sans-serif"; ctx.fillText(`cel ${fmtNumber.format(detail.goal)}`, p + 4, Math.max(10, y(detail.goal) - 6));
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

function renderBackupStatus(status) {
  const latest = status.latest;
  $("#backupStatus").innerHTML = latest
    ? `<strong>${status.healthy ? "Backup sprawdzony" : "Backup wymaga uwagi"}</strong><small>${escapeHtml(latest.file)} · ${latest.size_kb} KB · codziennie ${escapeHtml(status.backup_time)} · retencja ${status.keep}</small>`
    : `<strong>Brak backupu</strong><small>Pierwszy snapshot powstanie po ${escapeHtml(status.backup_time)}, gdy baza będzie zawierała dane.</small>`;
}

function historyQuery() {
  const params = new URLSearchParams({ page: historyState.page, per_page: historyState.perPage });
  if (historyState.dateFrom) params.set("date_from", historyState.dateFrom);
  if (historyState.dateTo) params.set("date_to", historyState.dateTo);
  return params.toString();
}

function renderHistoryPagination(pagination) {
  $("#historyCount").textContent = `${pagination.total} ${plural(pagination.total, "wpis", "wpisy", "wpisów")}`;
  $("#historyPage").textContent = `Strona ${pagination.page} z ${pagination.pages}`;
  $("#historyPrevious").disabled = !pagination.has_previous;
  $("#historyNext").disabled = !pagination.has_next;
}

async function loadHistory() {
  const backups = historyState.tab === "backups";
  const result = await api(`/${backups ? "api/backups" : "api/syncs"}?${historyQuery()}`);
  const items = backups ? result.backups : result.items;
  if (backups) renderBackupStatus(result);
  $("#historyList").innerHTML = items.length ? items.map((item) => backups ? `
    <div class="backup-row"><span><strong>${item.kind === "pre_restore" ? "Kopia przed przywróceniem" : item.kind === "manual" ? "Backup ręczny" : "Backup automatyczny"}</strong><small>${new Date(item.modified).toLocaleString("pl-PL")} · ${item.size_kb} KB</small></span><span><a href="/api/backups/${encodeURIComponent(item.file)}/download">Pobierz</a><button data-restore="${escapeHtml(item.file)}">Przywróć</button></span></div>` : `
    <div class="import-row"><span><strong>${item.status === "success" ? "Zakończona" : item.status === "failed" ? "Błąd" : "W toku"}</strong><br>${new Date(item.completed_at || item.started_at).toLocaleString("pl-PL")}${item.full_sync ? " · pełna" : ""}</span><span>${item.habit_count} nawyków · ${item.total_rows} okresów<br>${item.status === "failed" ? escapeHtml(item.error || "Nieznany błąd") : `+${item.inserted_rows} / ↻${item.updated_rows}`}</span></div>`).join("") : "<p class='hint'>Brak wpisów w wybranym zakresie.</p>";
  renderHistoryPagination(result.pagination);
  $$('[data-restore]').forEach((button) => button.addEventListener("click", () => restoreServerBackup(button.dataset.restore)));
}

async function loadBackupSummary() {
  renderBackupStatus(await api("/api/backups?page=1&per_page=1"));
}

async function restoreServerBackup(filename) {
  const confirmation = prompt(`Przywrócenie zastąpi lokalny snapshot Habitify. Wpisz PRZYWRÓĆ, aby użyć kopii:\n${filename}`);
  if (confirmation !== "PRZYWRÓĆ") return;
  try {
    const result = await api(`/api/backups/${encodeURIComponent(filename)}/restore`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation }),
    });
    toast(`Baza przywrócona. Kopia bezpieczeństwa: ${result.safety_backup}`);
    await Promise.all([load(true), loadSyncSettings()]);
  } catch (error) { toast(error.message, true); }
}

async function restoreUploadedBackup(file) {
  if (!file) return;
  const confirmation = prompt("Przywrócenie zastąpi lokalny snapshot Habitify. Wpisz PRZYWRÓĆ, aby kontynuować.");
  if (confirmation !== "PRZYWRÓĆ") { $("#backupFileInput").value = ""; return; }
  const form = new FormData(); form.append("file", file);
  try {
    const result = await api(`/api/backups/restore-upload?confirmation=${encodeURIComponent(confirmation)}`, { method: "POST", body: form });
    toast(`Baza przywrócona z pliku. Kopia bezpieczeństwa: ${result.safety_backup}`);
    await Promise.all([load(true), loadSyncSettings()]);
  } catch (error) { toast(error.message, true); }
  finally { $("#backupFileInput").value = ""; }
}

async function loadSyncSettings() {
  try {
    const [config] = await Promise.all([api("/api/config"), loadBackupSummary(), loadHistory()]);
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
  } catch (error) { toast(error.message, true); }
}

$$('[data-close]').forEach((button) => button.addEventListener("click", () => $(`#${button.dataset.close}`).close()));
$$("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); }));
$("#syncButton").addEventListener("click", () => syncHabitify());
$("#dialogSyncButton").addEventListener("click", () => syncHabitify());
$("#fullSyncButton").addEventListener("click", () => syncHabitify(true));
$("#settingsButton").addEventListener("click", async () => { await loadSyncSettings(); $("#settingsDialog").showModal(); });
$("#backupNow").addEventListener("click", async () => {
  try {
    const result = await api("/api/backup", { method: "POST" });
    toast(`Utworzono backup ${result.backup}`);
    historyState.tab = "backups"; historyState.page = 1;
    $$('[data-history-tab]').forEach((button) => button.classList.toggle("active", button.dataset.historyTab === "backups"));
    await Promise.all([loadBackupSummary(), loadHistory()]);
  } catch (error) { toast(error.message, true); }
});
$("#restoreUpload").addEventListener("click", () => $("#backupFileInput").click());
$("#backupFileInput").addEventListener("change", (event) => restoreUploadedBackup(event.target.files[0]));
$$('[data-history-tab]').forEach((button) => button.addEventListener("click", async () => {
  historyState.tab = button.dataset.historyTab; historyState.page = 1;
  $$('[data-history-tab]').forEach((item) => item.classList.toggle("active", item === button));
  await loadHistory();
}));
$("#historyApply").addEventListener("click", async () => { historyState.dateFrom = $("#historyFrom").value; historyState.dateTo = $("#historyTo").value; historyState.page = 1; await loadHistory(); });
$("#historyClear").addEventListener("click", async () => { $("#historyFrom").value = ""; $("#historyTo").value = ""; historyState.dateFrom = ""; historyState.dateTo = ""; historyState.page = 1; await loadHistory(); });
$("#historyPrevious").addEventListener("click", async () => { if (historyState.page > 1) { historyState.page -= 1; await loadHistory(); } });
$("#historyNext").addEventListener("click", async () => { historyState.page += 1; await loadHistory(); });

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

// dotyk nie pokazuje title, więc opis komórki trafia do toastu
$("#heatmap").addEventListener("click", (event) => {
  const cell = event.target.closest(".heat-cell");
  if (cell) toast(cell.title);
});

// canvas ma stałą szerokość w pikselach, więc obrót telefonu wymaga przerysowania;
// zmiana samej wysokości (pasek adresu na mobile) nic nie zmienia
let lastWidth = window.innerWidth;
const redraw = () => state.data && render(state.data);
window.addEventListener("resize", () => {
  if (window.innerWidth === lastWidth) return;
  lastWidth = window.innerWidth;
  clearTimeout(window.chartTimer);
  window.chartTimer = setTimeout(redraw, 150);
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);

load(true);
