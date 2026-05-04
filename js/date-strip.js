/**
 * Date-strip: the single, integrated time-range control.
 *
 * Replaces the old hero "Today / 7d / 30d / 3mo / Custom" buttons + the
 * standalone calendar modal that didn't sync with them.
 *
 * Renders a horizontal pill row covering the last N days. Each pill shows
 * the day-of-week, the day-of-month, and a small dot — filled when that
 * day actually has data on the data branch, hollow when it doesn't.
 *   click          -> single-day load (calls loadPapersByDate)
 *   shift+click    -> select range endpoints
 *   drag           -> select range
 *   range buttons  -> jump to last N available days
 *   "Custom range" -> opens the flatpickr modal (escape hatch)
 *
 * Reads `availableDates` from app.js (it's a script-scoped `let`, so we
 * poll until it's populated). Calls `loadPapersByDate` /
 * `loadPapersByDateRange` on `window` — app.js exposes them.
 */
(function () {
  'use strict';

  const STRIP_DAYS = 30;             // pills to render
  const PRESETS = [1, 7, 14, 30];    // last-N-days quick buttons

  let rangeAnchor = null;            // first endpoint while building a range
  let lastSingleSelected = null;

  // ---- helpers -------------------------------------------------------------
  function isoDay(d) {
    return d.getFullYear() + '-' +
           String(d.getMonth() + 1).padStart(2, '0') + '-' +
           String(d.getDate()).padStart(2, '0');
  }
  function daysAgoIso(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return isoDay(d);
  }
  function getAvailable() {
    try { return availableDates; }   // eslint-disable-line no-undef
    catch (_) { return []; }
  }
  function whenReady(cb, attempts = 80) {
    const arr = getAvailable();
    if (Array.isArray(arr) && arr.length) return cb();
    if (attempts <= 0) return;
    setTimeout(() => whenReady(cb, attempts - 1), 100);
  }

  // ---- DOM ----------------------------------------------------------------
  function ensureMounted() {
    if (document.getElementById('dateStrip')) return;

    const main = document.querySelector('main');
    if (!main) return;

    const wrap = document.createElement('section');
    wrap.className = 'date-strip-wrap';
    wrap.innerHTML = `
      <div class="date-strip-meta">
        <span class="label">Activity</span>
        <span class="summary" id="dateStripSummary">—</span>
      </div>
      <div class="date-strip" id="dateStrip" aria-label="Daily activity strip"></div>
      <div class="date-strip-controls">
        <span class="label" style="font-size:11px; letter-spacing:0.14em; text-transform:uppercase; color:var(--ink-3);">Quick</span>
        <div class="date-range-pill-group" id="dateRangePresets">
          ${PRESETS.map(n => `<button class="date-range-btn" data-preset="${n}">${n === 1 ? 'Today' : `Last ${n}d`}</button>`).join('')}
          <button class="date-range-btn" data-preset="all">All</button>
        </div>
        <span class="date-strip-spacer"></span>
        <span class="custom-range-link" id="customRangeLink">Custom range…</span>
      </div>
    `;

    // Mount right after the editorial head (so the strip sits between the
    // page title and the category/keyword filters, not buried at the bottom).
    const categoryNav = main.querySelector('.category-nav');
    if (categoryNav) main.insertBefore(wrap, categoryNav);
    else {
      const paperContainer = document.getElementById('paperContainer');
      if (paperContainer) main.insertBefore(wrap, paperContainer);
      else main.prepend(wrap);
    }

    document.getElementById('customRangeLink').addEventListener('click', () => {
      const calBtn = document.getElementById('calendarButton');
      if (calBtn) calBtn.click();
      const rangeToggle = document.getElementById('dateRangeMode');
      if (rangeToggle && !rangeToggle.checked) {
        rangeToggle.checked = true;
        rangeToggle.dispatchEvent(new Event('change'));
      }
    });

    document.querySelectorAll('#dateRangePresets .date-range-btn').forEach(btn => {
      btn.addEventListener('click', () => applyPreset(btn.dataset.preset));
    });
  }

  // ---- render -------------------------------------------------------------
  function render(activeStart = null, activeEnd = null) {
    ensureMounted();
    const strip = document.getElementById('dateStrip');
    if (!strip) return;

    const available = new Set(getAvailable());
    const today = new Date();

    // Build [oldest..today] so the most recent day is on the right.
    const days = [];
    for (let i = STRIP_DAYS - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      days.push(d);
    }

    const parts = days.map(d => {
      const iso = isoDay(d);
      const dow = d.toLocaleDateString(undefined, { weekday: 'short' }).slice(0, 2);
      const dom = d.getDate();
      const has = available.has(iso);
      const inRange = activeStart && activeEnd && iso >= activeStart && iso <= activeEnd;
      const isActive = activeStart && !activeEnd && iso === activeStart;
      const cls = [
        'date-pill',
        has ? 'has-data' : '',
        isActive ? 'active' : '',
        inRange ? 'in-range' : '',
      ].filter(Boolean).join(' ');
      return `<button class="${cls}" data-iso="${iso}" title="${iso}${has ? '' : ' (no data)'}">
        <span class="dow">${dow}</span>
        <span class="dom">${dom}</span>
        <span class="dot"></span>
      </button>`;
    });

    strip.innerHTML = parts.join('');

    // Wire pill clicks
    strip.querySelectorAll('.date-pill').forEach(p => {
      p.addEventListener('click', (e) => onPillClick(p.dataset.iso, e));
    });

    // Scroll right so today is visible at first paint
    requestAnimationFrame(() => {
      strip.scrollLeft = strip.scrollWidth;
    });

    updateSummary(activeStart, activeEnd);
  }

  function updateSummary(start, end) {
    const el = document.getElementById('dateStripSummary');
    if (!el) return;
    const available = getAvailable();
    if (start && end) {
      const inRange = available.filter(d => d >= start && d <= end);
      el.textContent = `${start} → ${end} · ${inRange.length} day${inRange.length === 1 ? '' : 's'} with data`;
    } else if (start) {
      el.textContent = start;
    } else {
      el.textContent = `${available.length} day${available.length === 1 ? '' : 's'} of data on file`;
    }
  }

  // ---- click handling -----------------------------------------------------
  function onPillClick(iso, e) {
    const available = new Set(getAvailable());
    if (!available.has(iso)) return;       // dead pill: no data

    if (e.shiftKey && lastSingleSelected) {
      // Range from anchor → this pill
      const start = lastSingleSelected < iso ? lastSingleSelected : iso;
      const end   = lastSingleSelected < iso ? iso : lastSingleSelected;
      loadRange(start, end);
      return;
    }

    // Plain click: single day
    rangeAnchor = null;
    lastSingleSelected = iso;
    if (typeof window.loadPapersByDate === 'function') {
      window.loadPapersByDate(iso);
    }
    render(iso, null);
  }

  function applyPreset(preset) {
    const available = getAvailable();
    if (!available.length) return;

    document.querySelectorAll('#dateRangePresets .date-range-btn')
      .forEach(b => b.classList.toggle('active', b.dataset.preset === String(preset)));

    if (preset === '1') {
      const newest = available[0];          // app.js sorts desc
      lastSingleSelected = newest;
      window.loadPapersByDate?.(newest);
      render(newest, null);
      return;
    }
    if (preset === 'all') {
      const start = available[available.length - 1];
      const end   = available[0];
      loadRange(start, end);
      return;
    }
    const n = parseInt(preset, 10);
    const start = daysAgoIso(n - 1);
    const today = isoDay(new Date());
    const inRange = available.filter(d => d >= start && d <= today).sort();
    if (!inRange.length) {
      // Window has no data — fall back to newest.
      window.loadPapersByDate?.(available[0]);
      render(available[0], null);
      return;
    }
    loadRange(inRange[0], inRange[inRange.length - 1]);
  }

  function loadRange(start, end) {
    if (start === end && typeof window.loadPapersByDate === 'function') {
      window.loadPapersByDate(start);
      lastSingleSelected = start;
      render(start, null);
      return;
    }
    if (typeof window.loadPapersByDateRange === 'function') {
      window.loadPapersByDateRange(start, end);
      render(start, end);
    }
  }

  // ---- init ---------------------------------------------------------------
  function init() {
    whenReady(() => {
      ensureMounted();

      // Honor ?date= URL param (app.js handles single-date URL load itself)
      if (window.location.search.includes('date=')) {
        render();
        return;
      }

      const available = getAvailable();
      // Default landing: if the newest date is "today", show today; else
      // show the newest day with data.
      const newest = available[0];
      const today = isoDay(new Date());
      const target = newest === today ? today : newest;
      lastSingleSelected = target;
      render(target, null);

      // Also mark the matching preset as active for clarity
      const preset = newest === today ? '1' : null;
      if (preset) {
        document.querySelectorAll('#dateRangePresets .date-range-btn')
          .forEach(b => b.classList.toggle('active', b.dataset.preset === preset));
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
