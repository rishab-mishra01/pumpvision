(function () {
  'use strict';

  var config = JSON.parse(document.getElementById('ops-config').textContent);
  var root = document.documentElement;
  var prefsKey = 'pumpvision.ops.preferences.v1';
  var defaults = {text:'standard', contrast:'standard', density:'comfortable', reduceMotion:false, live:true, announce:true};
  var prefs = loadPrefs();
  var selectedDate = document.getElementById('ops-cycle-date').value || '';
  var snapshotEtag = null;
  var snapshotController = null;
  var lastEventId = 0;
  var lastSuccess = null;
  var retryMs = 10000;
  var timer = null;
  var activity = new Map();
  var visibleActivityKeys = new Set();
  var pendingActivityKeys = new Set();
  var initializedActivity = false;
  var auditLoaded = false;

  function loadPrefs() {
    try { return Object.assign({}, defaults, JSON.parse(localStorage.getItem(prefsKey) || '{}')); }
    catch (_) { return Object.assign({}, defaults); }
  }
  function savePrefs() { localStorage.setItem(prefsKey, JSON.stringify(prefs)); }
  function applyPrefs() {
    root.dataset.opsText = prefs.text;
    root.dataset.opsContrast = prefs.contrast;
    root.dataset.opsDensity = prefs.density;
    root.dataset.opsReduceMotion = String(prefs.reduceMotion);
    document.getElementById('ops-pref-motion').checked = prefs.reduceMotion;
    document.getElementById('ops-pref-live').checked = prefs.live;
    document.getElementById('ops-pref-announce').checked = prefs.announce;
    document.querySelectorAll('input[name="text-size"]').forEach(function (el) { el.checked = el.value === prefs.text; });
    document.querySelectorAll('input[name="contrast"]').forEach(function (el) { el.checked = el.value === prefs.contrast; });
    document.querySelectorAll('input[name="density"]').forEach(function (el) { el.checked = el.value === prefs.density; });
    var pause = document.getElementById('ops-pause');
    pause.setAttribute('aria-pressed', String(!prefs.live));
    pause.querySelector('span:last-child').textContent = prefs.live ? 'Pause live' : 'Resume live';
  }
  function e(value) {
    return String(value == null ? '' : value).replace(/[&<>'"]/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];
    });
  }
  function money(value) {
    return value == null ? '—' : '₹' + new Intl.NumberFormat('en-IN', {maximumFractionDigits:0}).format(value);
  }
  function number(value, unit) {
    return value == null ? '—' : new Intl.NumberFormat('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}).format(value) + (unit ? ' ' + unit : '');
  }
  function dt(value, includeDate) {
    if (!value) return 'Not available';
    var parsed = new Date(value);
    if (isNaN(parsed.getTime())) return 'Not available';
    var options = {timeZone:'Asia/Kolkata', hour:'2-digit', minute:'2-digit', hour12:false};
    if (includeDate) Object.assign(options, {day:'numeric', month:'short'});
    return new Intl.DateTimeFormat('en-IN', options).format(parsed) + ' IST';
  }
  function age(minutes) {
    if (minutes == null) return 'No reading';
    if (minutes < 1) return 'Just now';
    if (minutes < 60) return minutes + ' min ago';
    var hours = Math.floor(minutes / 60), mins = minutes % 60;
    if (hours < 24) return hours + 'h' + (mins ? ' ' + mins + 'm' : '') + ' ago';
    return Math.floor(hours / 24) + 'd ago';
  }
  function label(value) {
    var labels = {
      healthy:'Healthy', pending:'Pending', stale:'Stale', failed:'Failed', open:'Open',
      provisional:'Provisional', submitted:'Submitted', final:'Final', reconciled:'Reconciled',
      review:'Review', manual_submitted:'Manual submitted', manual_draft:'Manual draft',
      iras_only:'IRAS only', sdms_only:'SDMS only', awaiting_sdms:'Awaiting SDMS'
    };
    return labels[value] || String(value || 'Unknown').replace(/_/g, ' ');
  }
  function status(value) {
    return '<span class="ops-status ops-status--' + e(value) + '">' + e(label(value)) + '</span>';
  }
  function setText(id, value) {
    var node = document.getElementById(id);
    var next = String(value);
    if (node.textContent !== next) {
      node.textContent = next;
      node.classList.remove('ops-updated');
      void node.offsetWidth;
      node.classList.add('ops-updated');
      node.dataset.noHighlight = String(prefs.reduceMotion);
    }
  }
  function query(url, extra) {
    var params = new URLSearchParams(extra || {});
    if (selectedDate) params.set('cycle_date', selectedDate);
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + params.toString();
  }
  function connection(state, text) {
    var el = document.getElementById('ops-connection');
    el.classList.toggle('is-live', state === 'live');
    el.classList.toggle('is-error', state === 'error');
    document.getElementById('ops-connection-text').textContent = text;
  }
  function preserveFocus(render) {
    var active = document.activeElement;
    var id = active && active.id;
    render();
    if (id) {
      var replacement = document.getElementById(id);
      if (replacement && active !== replacement) replacement.focus({preventScroll:true});
    }
  }

  function renderSnapshot(data) {
    var c = data.cycle, shift = data.shift, f = data.financial;
    if (!selectedDate) {
      selectedDate = c.op_date;
      document.getElementById('ops-cycle-date').value = selectedDate;
    }
    setText('ops-cycle-window', c.op_date + ' · ' + dt(c.start, true) + ' → ' + dt(c.end, true));
    var stateNode = document.getElementById('ops-cycle-state');
    stateNode.className = 'ops-status ops-status--' + shift.state;
    stateNode.textContent = label(shift.state);
    setText('ops-shift-summary', shift.liquid_locked + '/6 liquid nozzles locked · ' + shift.cng_entered + '/2 CNG entered · ' + shift.iras_received + '/6 IRAS received');
    setText('ops-last-check', dt(data.generated_at, false));

    setText('kpi-cash', money(f.expected_sales_cash));
    setText('kpi-cash-note', f.expected_sales_cash == null ? 'Waiting for ' + (f.missing.join(', ') || 'source data') : (f.state === 'final' ? 'Final source set' : 'Provisional'));
    setText('kpi-gross', money(f.gross_fuel_sales));
    setText('kpi-gross-note', f.gross_fuel_sales == null ? 'Incomplete liquid/CNG close' : 'Fuel only · ' + label(f.state));
    setText('kpi-expenses', money(f.expenses));
    setText('kpi-expenses-note', f.expense_count + ' entr' + (f.expense_count === 1 ? 'y' : 'ies') + ' for this cycle');
    setText('kpi-collections', money(f.account_collections));
    setText('kpi-collections-note', f.collection_count + ' collection' + (f.collection_count === 1 ? '' : 's') + ' · separate from POS');

    preserveFocus(function () {
      renderAlerts(data.alerts);
      renderHealth(data.sources);
      renderPrices(data.prices);
      renderNozzles(data.nozzles);
      renderCng(data.cng);
      renderSources(data.sources);
    });
    mergeActivities(data.activity, false);
  }

  function renderAlerts(rows) {
    document.getElementById('ops-alert-count').textContent = rows.length;
    document.getElementById('ops-alerts').innerHTML = rows.length ? rows.map(function (row) {
      return '<article class="ops-list-item ops-list-item--' + e(row.severity) + '"><span class="ops-list-item__icon" aria-hidden="true"></span><div><b>' + e(row.title) + '</b>' + (row.detail ? '<p>' + e(row.detail) + '</p>' : '') + '<small>' + e(row.source) + (row.acknowledged ? ' · Acknowledged' : '') + '</small></div><time datetime="' + e(row.last_seen) + '">' + e(dt(row.last_seen, false)) + '</time></article>';
    }).join('') : '<p class="ops-empty">No open operational alerts.</p>';
  }
  function renderHealth(rows) {
    var important = rows.filter(function (row) { return ['iras_prices','iras_totalizers','paytm','sdms','atg'].indexOf(row.key) >= 0; });
    document.getElementById('ops-health-summary').innerHTML = important.map(function (row) {
      return '<div class="ops-health-row"><span><i class="ops-source__dot" aria-hidden="true" style="background:' + (row.state === 'healthy' ? 'var(--ok-600)' : row.state === 'failed' ? 'var(--error-600)' : 'var(--warn-600)') + '"></i>' + e(row.label) + '</span><small>' + e(label(row.state)) + ' · ' + e(age(row.age_minutes)) + '</small></div>';
    }).join('');
  }
  function renderPrices(rows) {
    document.getElementById('ops-prices').innerHTML = rows.map(function (row) {
      var fallback = row.source !== 'iras_exact' ? ' · Fallback visible' : '';
      return '<article class="ops-price"><b>' + e(row.product) + '</b><strong>' + (row.rate == null ? '—' : '₹' + e(Number(row.rate).toFixed(2))) + '</strong><small>' + e(row.source.replace(/_/g, ' ')) + fallback + '<br>Fetched ' + e(dt(row.fetched_at, true)) + '</small></article>';
    }).join('');
  }
  function readingCell(value, at, unit) {
    return '<b class="num">' + e(number(value, unit)) + '</b><small>' + (at ? e(dt(at, false)) : 'Not available') + '</small>';
  }
  function renderNozzles(rows) {
    document.getElementById('ops-nozzles').innerHTML = rows.map(function (row) {
      return '<tr><td data-label="Nozzle"><b>' + e(row.label) + '</b><small>' + e(row.product) + ' · Nozzle ' + e(row.nozzle_no) + '</small></td>' +
        '<td data-label="Opening">' + readingCell(row.opening, null, 'L') + '</td>' +
        '<td data-label="Manual close">' + readingCell(row.manual, row.manual_at, 'L') + '</td>' +
        '<td data-label="IRAS close">' + readingCell(row.iras, row.iras_at, 'L') + '</td>' +
        '<td data-label="Variance"><b class="num">' + e(number(row.variance, 'L')) + '</b></td>' +
        '<td data-label="Status">' + status(row.status) + '</td></tr>';
    }).join('');
  }
  function renderCng(cng) {
    var stateNode = document.getElementById('ops-cng-state');
    stateNode.className = 'ops-status ops-status--' + cng.state;
    stateNode.textContent = label(cng.state);
    document.getElementById('ops-cng').innerHTML = [
      ['Manual total', number(cng.manual_total, 'kg'), cng.manual_at ? dt(cng.manual_at, false) : 'Not submitted'],
      ['SDMS / eLedger', number(cng.sdms_total, 'kg'), cng.sdms_at ? dt(cng.sdms_at, false) : 'Pending'],
      ['Variance', number(cng.variance, 'kg'), cng.variance == null ? 'Awaiting both sources' : label(cng.state)],
      ['Selling price', cng.selling_price == null ? '—' : '₹' + Number(cng.selling_price).toFixed(2) + '/kg', cng.price_basis ? cng.price_basis.replace(/_/g, ' ') : 'Price pending']
    ].map(function (item) { return '<article class="ops-cng-metric"><span>' + e(item[0]) + '</span><b>' + e(item[1]) + '</b><small>' + e(item[2]) + '</small></article>'; }).join('');
  }
  function renderSources(rows) {
    document.getElementById('ops-sources').innerHTML = rows.map(function (row) {
      return '<article class="ops-source" data-state="' + e(row.state) + '"><span class="ops-source__dot" aria-hidden="true"></span><div><b>' + e(row.label) + '</b><span>' + e(row.note || 'Operational source') + '</span><small>Observed ' + e(row.observed_at ? dt(row.observed_at, true) : 'not yet') + ' · ' + e(age(row.age_minutes)) + '</small></div><strong>' + e(label(row.state)) + '</strong></article>';
    }).join('');
  }

  function mergeActivities(rows, announce) {
    var newKeys = [];
    (rows || []).forEach(function (row) {
      if (!activity.has(row.key)) newKeys.push(row.key);
      activity.set(row.key, row);
    });
    if (!initializedActivity) {
      activity.forEach(function (_, key) { visibleActivityKeys.add(key); });
      initializedActivity = true;
      renderActivity();
      return;
    }
    newKeys.forEach(function (key) { pendingActivityKeys.add(key); });
    updateNewCount();
    if (announce && prefs.announce) {
      var critical = newKeys.map(function (key) { return activity.get(key); }).find(function (row) { return row.severity === 'critical'; });
      if (critical) document.getElementById('ops-critical-announcer').textContent = 'Critical operations alert: ' + critical.title;
    }
  }
  function updateNewCount() {
    var count = pendingActivityKeys.size;
    var badge = document.getElementById('ops-new-count');
    badge.hidden = !count; badge.textContent = count;
    var show = document.getElementById('ops-show-new');
    show.hidden = !count; show.textContent = 'Show ' + count + ' new update' + (count === 1 ? '' : 's');
  }
  function renderActivity() {
    var rows = Array.from(visibleActivityKeys).map(function (key) { return activity.get(key); }).filter(Boolean).sort(function (a,b) { return String(b.at || '').localeCompare(String(a.at || '')); });
    document.getElementById('ops-activity').innerHTML = rows.length ? rows.slice(0,60).map(function (row) {
      return '<article class="ops-event"><span class="ops-event__marker" aria-hidden="true"></span><div><h3>' + e(row.title) + '</h3>' + (row.detail ? '<p>' + e(row.detail) + '</p>' : '') + '<p class="ops-event__meta">' + e((row.actor || row.source) + ' · ' + (row.actor_role || 'system') + ' · ' + row.source + (row.op_date ? ' · Operational date ' + row.op_date : '')) + '</p></div><time datetime="' + e(row.at) + '">' + e(dt(row.at, true)) + '</time></article>';
    }).join('') : '<p class="ops-empty">No activity has been recorded yet.</p>';
  }

  async function fetchSnapshot(manual) {
    if (snapshotController) return;
    snapshotController = new AbortController();
    var headers = snapshotEtag ? {'If-None-Match': snapshotEtag} : {};
    try {
      if (manual) connection('loading', 'Refreshing…');
      var response = await fetch(query(config.snapshotUrl), {credentials:'same-origin', headers:headers, signal:snapshotController.signal});
      if (response.status !== 304) {
        if (!response.ok) throw new Error('Snapshot request failed: ' + response.status);
        snapshotEtag = response.headers.get('ETag');
        renderSnapshot(await response.json());
      }
      lastSuccess = new Date(); retryMs = 10000;
      connection('live', prefs.live ? 'Live · checked just now' : 'Live updates paused');
      await fetchEvents();
    } catch (error) {
      if (error.name !== 'AbortError') {
        connection('error', 'Reconnecting · last check ' + (lastSuccess ? dt(lastSuccess.toISOString(), false) : 'not available'));
        retryMs = Math.min(retryMs * 2, 60000) + Math.floor(Math.random() * 1200);
      }
    } finally {
      snapshotController = null;
      schedule();
    }
  }
  async function fetchEvents() {
    var response = await fetch(query(config.eventsUrl, {after_id:lastEventId, limit:100}), {credentials:'same-origin'});
    if (!response.ok) return;
    var payload = await response.json();
    lastEventId = payload.last_id || lastEventId;
    mergeActivities(payload.events, true);
  }
  function schedule() {
    clearTimeout(timer);
    if (!prefs.live || document.hidden) return;
    timer = setTimeout(function () { fetchSnapshot(false); }, retryMs);
  }

  function openTab(name, focus) {
    document.querySelectorAll('[role="tab"]').forEach(function (tab) {
      var active = tab.dataset.tab === name;
      tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      if (active && focus) tab.focus();
    });
    document.querySelectorAll('[role="tabpanel"]').forEach(function (panel) { panel.hidden = panel.id !== 'panel-' + name; });
    if (name === 'audit' && !auditLoaded) fetchAudit();
  }
  async function fetchAudit() {
    var host = document.getElementById('ops-audit');
    try {
      var response = await fetch(config.auditUrl + '?after_id=0', {credentials:'same-origin'});
      if (!response.ok) throw new Error();
      var payload = await response.json();
      host.innerHTML = payload.audit.length ? payload.audit.slice().reverse().map(function (row) {
        return '<article class="ops-list-item"><span class="ops-list-item__icon" aria-hidden="true"></span><div><b>' + e(label(row.action) + ' · ' + row.kind) + '</b><p>' + e(row.reason || 'No reason recorded') + '</p><small>' + e(row.actor + ' · ' + (row.actor_role || 'system') + (row.op_date ? ' · Operational date ' + row.op_date : '')) + '</small></div><time datetime="' + e(row.created_at) + '">' + e(dt(row.created_at, true)) + '</time></article>';
      }).join('') : '<p class="ops-empty">No corrections, archives or restorations recorded.</p>';
      auditLoaded = true;
    } catch (_) { host.innerHTML = '<p class="ops-empty">Audit history could not be loaded. Try refreshing.</p>'; }
  }

  document.querySelector('.ops-tabs').addEventListener('click', function (event) {
    var tab = event.target.closest('[role="tab"]'); if (tab) openTab(tab.dataset.tab, false);
  });
  document.querySelector('.ops-tabs').addEventListener('keydown', function (event) {
    if (['ArrowRight','ArrowLeft','Home','End'].indexOf(event.key) < 0) return;
    var tabs = Array.from(document.querySelectorAll('[role="tab"]'));
    var index = tabs.indexOf(document.activeElement);
    if (event.key === 'Home') index = 0; else if (event.key === 'End') index = tabs.length - 1; else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault(); openTab(tabs[index].dataset.tab, true);
  });
  document.querySelectorAll('[data-open-tab]').forEach(function (link) { link.addEventListener('click', function (event) { event.preventDefault(); openTab(link.dataset.openTab, true); }); });
  document.getElementById('ops-show-new').addEventListener('click', function () {
    pendingActivityKeys.forEach(function (key) { visibleActivityKeys.add(key); });
    pendingActivityKeys.clear(); updateNewCount(); renderActivity();
  });
  document.getElementById('ops-refresh').addEventListener('click', function () { if (snapshotController) snapshotController.abort(); snapshotController = null; fetchSnapshot(true); });
  document.getElementById('ops-pause').addEventListener('click', function () { prefs.live = !prefs.live; savePrefs(); applyPrefs(); if (prefs.live) fetchSnapshot(true); else { clearTimeout(timer); connection('live', 'Live updates paused'); } });
  document.getElementById('ops-cycle-date').addEventListener('change', function (event) { selectedDate = event.target.value; snapshotEtag = null; lastEventId = 0; activity.clear(); visibleActivityKeys.clear(); pendingActivityKeys.clear(); initializedActivity = false; auditLoaded = false; fetchSnapshot(true); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden && prefs.live) fetchSnapshot(true); else clearTimeout(timer); });

  var dialog = document.getElementById('ops-a11y');
  document.getElementById('ops-a11y-open').addEventListener('click', function () { dialog.showModal(); });
  dialog.addEventListener('click', function (event) { if (event.target === dialog) dialog.close(); });
  dialog.querySelectorAll('input').forEach(function (input) { input.addEventListener('change', function () {
    if (input.name === 'text-size') prefs.text = input.value;
    if (input.name === 'contrast') prefs.contrast = input.value;
    if (input.name === 'density') prefs.density = input.value;
    if (input.id === 'ops-pref-motion') prefs.reduceMotion = input.checked;
    if (input.id === 'ops-pref-live') prefs.live = input.checked;
    if (input.id === 'ops-pref-announce') prefs.announce = input.checked;
    savePrefs(); applyPrefs(); schedule();
  }); });
  document.getElementById('ops-pref-reset').addEventListener('click', function () { prefs = Object.assign({}, defaults); savePrefs(); applyPrefs(); schedule(); });

  applyPrefs();
  fetchSnapshot(true);
})();
