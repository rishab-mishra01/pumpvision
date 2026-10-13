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
  var activityFilter = 'all';
  var shiftStream = 'liquid';
  var selectedNozzle = null;
  var latestSnapshot = null;

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
    latestSnapshot = data;
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
    setText('ops-fact-manual', shift.liquid_locked + ' / 6');
    setText('ops-fact-iras', shift.iras_received + ' / 6');
    setText('ops-fact-cng', shift.cng_entered + ' / 2');
    setText('ops-finance-state', label(f.state));
    var completion = Math.round(((shift.liquid_locked + shift.iras_received + shift.cng_entered) / 14) * 100);
    document.getElementById('ops-shift-progress').style.width = completion + '%';

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
      renderShiftOverview(shift, data.cng, data.sources);
      renderPrices(data.prices);
      renderNozzles(data.nozzles);
      renderCng(data.cng);
      renderShiftExplorer();
      renderSources(data.sources);
    });
    mergeActivities(data.activity, false);
  }

  function renderAlerts(rows) {
    document.getElementById('ops-alert-count').textContent = rows.length;
    document.getElementById('ops-alert-band').classList.toggle('is-clear', !rows.length);
    document.getElementById('ops-alerts').innerHTML = rows.length ? rows.map(function (row) {
      return '<article class="ops-alert-item ops-alert-item--' + e(row.severity) + '"><b>' + e(row.title) + '</b>' + (row.detail ? '<p>' + e(row.detail) + '</p>' : '') + '<time datetime="' + e(row.last_seen) + '">' + e(row.source) + ' · ' + e(dt(row.last_seen, false)) + (row.acknowledged ? ' · Acknowledged' : '') + '</time></article>';
    }).join('') : '<p class="ops-empty">No open operational alerts.</p>';
  }
  function renderHealth(rows) {
    var important = rows.filter(function (row) { return ['iras_prices','iras_totalizers','paytm','sdms','atg'].indexOf(row.key) >= 0; });
    document.getElementById('ops-health-summary').innerHTML = important.map(function (row) {
      return '<div class="ops-health-row"><i class="ops-source__dot" aria-hidden="true" style="background:' + stateColor(row.state) + '"></i><b>' + e(row.label) + '</b><small>' + e(label(row.state)) + '<br>' + e(age(row.age_minutes)) + '</small></div>';
    }).join('');
  }
  function stateColor(state) {
    return state === 'healthy' || state === 'reconciled' ? '#2c9560' : state === 'failed' || state === 'review' ? '#c64135' : '#d89a25';
  }
  function renderShiftOverview(shift, cng, sources) {
    var sourceMap = {};
    sources.forEach(function (row) { sourceMap[row.key] = row; });
    var rows = [
      {title:'Manual nozzle close', value:shift.liquid_locked + ' of 6 locked', note:shift.submitted_at ? 'Submitted ' + dt(shift.submitted_at, false) : 'Awaiting attendant submission', state:shift.liquid_locked >= 6 ? 'complete' : 'pending'},
      {title:'IRAS nozzle close', value:shift.iras_received + ' of 6 received', note:sourceMap.iras_totalizers ? age(sourceMap.iras_totalizers.age_minutes) : 'Not received', state:shift.iras_received >= 6 ? 'complete' : 'pending'},
      {title:'CNG manual entry', value:shift.cng_entered + ' of 2 entered', note:cng.manual_at ? 'Updated ' + dt(cng.manual_at, false) : 'Awaiting attendant entry', state:shift.cng_entered >= 2 ? 'complete' : 'pending'},
      {title:'SDMS / eLedger', value:label(cng.state), note:cng.sdms_at ? 'Fetched ' + dt(cng.sdms_at, false) : 'Settlement data pending', state:cng.state === 'reconciled' || cng.state === 'sdms_only' ? 'complete' : cng.state === 'review' ? 'attention' : 'pending'}
    ];
    document.getElementById('ops-shift-checklist').innerHTML = rows.map(function (row) {
      return '<article class="ops-check" data-state="' + e(row.state) + '"><div class="ops-check__top"><b>' + e(row.title) + '</b><i class="ops-check__dot" aria-hidden="true"></i></div><strong>' + e(row.value) + '</strong><small>' + e(row.note) + '</small></article>';
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
    document.getElementById('ops-nozzle-overview').innerHTML = rows.map(function (row) {
      var reading = row.manual_at ? 'Manual ' + dt(row.manual_at, false) : row.iras_at ? 'IRAS ' + dt(row.iras_at, false) : 'No close reading';
      return '<div class="ops-nozzle" data-state="' + e(row.status) + '"><b>' + e(row.label) + '</b><span>' + e(label(row.status)) + '</span><span>' + e(reading) + '</span></div>';
    }).join('');
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
      return '<article class="ops-source" data-state="' + e(row.state) + '"><span class="ops-source__dot" aria-hidden="true" style="background:' + stateColor(row.state) + '"></span><div><b>' + e(row.label) + '</b><span>' + e(row.note || 'Operational source') + '</span><small>Observed ' + e(row.observed_at ? dt(row.observed_at, true) : 'not yet') + ' · ' + e(age(row.age_minutes)) + '</small></div><strong>' + e(label(row.state)) + '</strong></article>';
    }).join('');
  }

  function activityGroup(row) {
    if (row.kind === 'expense') return 'expense';
    if (row.kind === 'credit_sale' || row.kind === 'payment') return 'credit';
    if (row.kind === 'shift_close' || row.kind === 'nozzle' || row.kind === 'cng') return 'shift';
    return 'other';
  }

  function renderShiftExplorer() {
    if (!latestSnapshot) return;
    var data = latestSnapshot;
    var explorerState = document.getElementById('ops-explorer-state');
    explorerState.className = 'ops-status ops-status--' + data.shift.state;
    explorerState.textContent = label(data.shift.state);

    if (!selectedNozzle || !data.nozzles.some(function (row) { return String(row.nozzle_no) === String(selectedNozzle); })) {
      selectedNozzle = data.nozzles.length ? String(data.nozzles[0].nozzle_no) : null;
    }
    document.getElementById('ops-nozzle-tabs').innerHTML = data.nozzles.map(function (row) {
      var active = String(row.nozzle_no) === String(selectedNozzle);
      return '<button id="ops-tab-nozzle-' + e(row.nozzle_no) + '" role="tab" aria-controls="ops-nozzle-detail" aria-selected="' + active + '" tabindex="' + (active ? '0' : '-1') + '" data-nozzle="' + e(row.nozzle_no) + '">' + e(row.label) + '</button>';
    }).join('');
    renderSelectedNozzle();

    var cng = data.cng;
    var manualDetail = (cng.manual_nozzles || []).map(function (row) {
      return '<div><span>CNG ' + e(row.nozzle_no) + '</span><b>' + e(number(row.kg_sold, 'kg')) + '</b><small>' + e(row.submitted_by) + ' · ' + e(dt(row.submitted_at, false)) + '</small></div>';
    }).join('');
    document.getElementById('ops-cng-explorer').innerHTML = '<div class="ops-reading-detail__head"><h4>CNG shift close</h4>' + status(cng.state) + '</div><div class="ops-reading-grid">' +
      (manualDetail || '<div><span>Manual entry</span><b>Pending</b><small>No attendant entry</small></div>') +
      '<div><span>Manual total</span><b>' + e(number(cng.manual_total, 'kg')) + '</b><small>' + e(cng.manual_at ? dt(cng.manual_at, false) : 'Not submitted') + '</small></div>' +
      '<div><span>SDMS / eLedger</span><b>' + e(number(cng.sdms_total, 'kg')) + '</b><small>' + e(cng.sdms_at ? dt(cng.sdms_at, false) : 'Not fetched') + '</small></div>' +
      '<div><span>Variance</span><b>' + e(number(cng.variance, 'kg')) + '</b><small>' + e(label(cng.state)) + '</small></div></div>';
  }

  function renderSelectedNozzle() {
    if (!latestSnapshot) return;
    var row = latestSnapshot.nozzles.find(function (item) { return String(item.nozzle_no) === String(selectedNozzle); });
    if (!row) return;
    document.getElementById('ops-nozzle-detail').setAttribute('aria-labelledby', 'ops-tab-nozzle-' + row.nozzle_no);
    document.getElementById('ops-nozzle-detail').innerHTML = '<div class="ops-reading-detail__head"><h4>' + e(row.label) + ' · Nozzle ' + e(row.nozzle_no) + '</h4>' + status(row.status) + '</div><div class="ops-reading-grid">' +
      '<div><span>Opening</span><b>' + e(number(row.opening, 'L')) + '</b><small>Cycle opening</small></div>' +
      '<div><span>Manual close</span><b>' + e(number(row.manual, 'L')) + '</b><small>' + e(row.manual_at ? dt(row.manual_at, false) : 'Not entered') + '</small></div>' +
      '<div><span>IRAS close</span><b>' + e(number(row.iras, 'L')) + '</b><small>' + e(row.iras_at ? dt(row.iras_at, false) : 'Not received') + '</small></div>' +
      '<div><span>Variance</span><b>' + e(number(row.variance, 'L')) + '</b><small>Manual minus IRAS</small></div></div><p class="ops-reading-note">Every time shown is Indian Standard Time (IST).</p>';
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
    newKeys.forEach(function (key) { visibleActivityKeys.add(key); });
    renderActivity();
    if (announce && prefs.announce) {
      var critical = newKeys.map(function (key) { return activity.get(key); }).find(function (row) { return row.severity === 'critical'; });
      if (critical) document.getElementById('ops-critical-announcer').textContent = 'Critical operations alert: ' + critical.title;
    }
  }
  function updateNewCount() {
    var count = pendingActivityKeys.size;
    var show = document.getElementById('ops-show-new');
    show.hidden = !count; show.textContent = 'Show ' + count + ' new update' + (count === 1 ? '' : 's');
  }
  function renderActivity() {
    var rows = Array.from(visibleActivityKeys).map(function (key) { return activity.get(key); }).filter(Boolean).sort(function (a,b) { return String(b.at || '').localeCompare(String(a.at || '')); });
    var counts = {all:rows.length, expense:0, credit:0, shift:0};
    rows.forEach(function (row) { var group = activityGroup(row); if (counts[group] != null) counts[group] += 1; });
    Object.keys(counts).forEach(function (key) { setText('ops-count-' + key, counts[key]); });
    var filtered = activityFilter === 'all' ? rows : rows.filter(function (row) { return activityGroup(row) === activityFilter; });
    var shiftVisible = activityFilter === 'shift';
    document.getElementById('ops-shift-explorer').hidden = !shiftVisible;
    document.querySelector('.ops-activity-layout').classList.toggle('is-shift', shiftVisible);
    document.getElementById('ops-activity').innerHTML = filtered.length ? filtered.slice(0,20).map(function (row) {
      var icon = row.kind === 'expense' ? '₹' : row.kind === 'payment' ? 'IN' : row.kind === 'shift_close' ? 'SC' : row.kind === 'nozzle' ? 'NZ' : row.kind === 'price' ? '₹' : row.kind === 'cng' ? 'CG' : 'SYS';
      return '<article class="ops-event" data-kind="' + e(row.kind) + '"><span class="ops-event__icon" aria-hidden="true">' + e(icon) + '</span><div><h3>' + e(row.title) + '</h3>' + (row.detail ? '<p>' + e(row.detail) + '</p>' : '') + '<div class="ops-event__who"><span class="ops-actor">' + e(row.actor || row.source) + ' · ' + e(row.actor_role || 'system') + '</span><span class="ops-source-tag">' + e(row.source) + '</span></div><time datetime="' + e(row.at) + '">' + e(dt(row.at, true)) + '</time></div></article>';
    }).join('') : '<p class="ops-empty">No ' + e(activityFilter === 'all' ? '' : activityFilter + ' ') + 'activity has been recorded for this cycle.</p>';
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

  function activateTab(container, tab, callback) {
    Array.from(container.querySelectorAll('[role="tab"]')).forEach(function (item) {
      var active = item === tab;
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
    });
    callback(tab);
  }
  function bindTabs(container, callback) {
    container.addEventListener('click', function (event) {
      var tab = event.target.closest('[role="tab"]');
      if (tab && container.contains(tab)) activateTab(container, tab, callback);
    });
    container.addEventListener('keydown', function (event) {
      if (['ArrowRight','ArrowLeft','Home','End'].indexOf(event.key) < 0) return;
      var tabs = Array.from(container.querySelectorAll('[role="tab"]'));
      var index = tabs.indexOf(document.activeElement);
      if (index < 0 || !tabs.length) return;
      if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = tabs.length - 1;
      else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault();
      activateTab(container, tabs[index], callback);
      tabs[index].focus();
    });
  }

  bindTabs(document.getElementById('ops-activity-filters'), function (tab) {
    activityFilter = tab.dataset.activityFilter;
    document.getElementById('ops-activity').setAttribute('aria-labelledby', tab.id);
    renderActivity();
  });
  bindTabs(document.getElementById('ops-stream-tabs'), function (tab) {
    shiftStream = tab.dataset.stream;
    document.getElementById('ops-liquid-panel').hidden = shiftStream !== 'liquid';
    document.getElementById('ops-cng-panel').hidden = shiftStream !== 'cng';
  });
  bindTabs(document.getElementById('ops-nozzle-tabs'), function (tab) {
    selectedNozzle = tab.dataset.nozzle;
    renderSelectedNozzle();
  });

  document.querySelectorAll('.ops-text-link').forEach(function (link) {
    link.addEventListener('click', function () {
      var detail = document.querySelector(link.getAttribute('href'));
      if (detail && detail.tagName === 'DETAILS') detail.open = true;
    });
  });
  var auditDetail = document.getElementById('ops-audit-detail');
  if (auditDetail) auditDetail.addEventListener('toggle', function () {
    if (auditDetail.open && !auditLoaded) fetchAudit();
  });
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
