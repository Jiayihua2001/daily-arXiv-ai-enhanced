/**
 * Personalization layer for Jade (zefengc@andrew.cmu.edu)
 * Research focus: Molecular Crystal Structure Prediction (MCSP) & AI4Science
 *
 * Non-invasive: runs alongside the upstream scripts. Seeds default
 * keyword preferences on first visit, injects a hero banner, and adds
 * small quality-of-life UI tweaks. Safe to delete this file plus the
 * <script>/<link> tags in the HTML files to revert.
 */
(function () {
  'use strict';

  const PROFILE = {
    displayName: 'Jade',
    affiliation: 'Carnegie Mellon University',
    fields: ['Molecular Crystal Structure Prediction', 'AI for Science'],
    // Broader seed list for v3 — every term should still be a strong
    // MCSP / materials / ML-for-chemistry signal. Users can edit/remove
    // freely in Settings. The full filter list is in keywords.yaml on
    // the pipeline side; this is the subset surfaced as hero chips.
    defaultKeywords: [
      // core MCSP
      'crystal structure prediction',
      'molecular crystal',
      'crystal polymorph',
      'polymorph selection',
      'polymorph stability',
      'pharmaceutical co-crystal',
      'molecular co-crystal',
      'co-crystal formation',
      'lattice energy',
      'structure search',
      'molecular packing',
      'crystal packing',
      'crystal engineering',
      'space group',
      'pharmaceutical crystal',
      // adjacent materials
      'metal-organic framework',
      'covalent organic framework',
      'perovskite',
      // ML for materials
      'machine learning potential',
      'neural network potential',
      'interatomic potential',
      'equivariant neural network',
      'inverse design',
      'materials discovery',
      'molecular generation',
      'crystal generation',
      'generative chemistry',
      // specific frameworks (high precision signals)
      'MACE',
      'NequIP',
      'M3GNet',
      'CHGNet',
      // computational chemistry
      'density functional theory',
      'ab initio molecular dynamics',
      'molecular dynamics simulation'
    ],
    // Bump the version when the default list changes — existing visitors
    // who haven't customized get migrated to the new list.
    seedFlag: 'mcsp_defaults_seeded_v5'
  };

  // ---- Data-source fallback + topical keyword filter shim ------------------
  // Two responsibilities:
  //   1. If the user's own fork hasn't published a data branch yet, fall
  //      back to upstream so the page isn't stuck on "Loading...".
  //   2. Filter every fetched JSONL response by the user's preferred
  //      keywords *before* the rest of the app sees it — so cross-field
  //      papers (e.g. upstream cs.CV/cs.CL during bootstrap) never reach
  //      the UI.
  function _userKeywordsLower() {
    try {
      const arr = JSON.parse(localStorage.getItem('preferredKeywords') || '[]');
      return arr.map(k => String(k).toLowerCase()).filter(Boolean);
    } catch (e) { return []; }
  }

  // Detect non-Latin (essentially CJK) text. We only need a coarse signal —
  // upstream summaries are Chinese; the user's own pipeline produces English.
  function _isCJK(s) {
    if (!s) return false;
    return /[㐀-鿿]/.test(s);  // CJK ideographs
  }

  // If the AI summary is in Chinese (because we fell back to upstream),
  // replace it with the original English abstract so the user actually
  // gets English content. We split a long abstract into pseudo-fields
  // so the existing UI rendering still works.
  function _swapChineseAIWithEnglishAbstract(obj) {
    const abstract = obj.summary || '';
    if (!abstract) return obj;
    const ai = obj.AI || {};
    const looksChinese = _isCJK(ai.tldr) || _isCJK(ai.motivation)
                      || _isCJK(ai.method) || _isCJK(ai.conclusion);
    if (!looksChinese) return obj;

    // First sentence of the abstract → tldr; rest goes into motivation.
    const sentences = abstract.match(/[^.!?]+[.!?]+/g) || [abstract];
    const tldr = (sentences[0] || abstract).trim();
    const rest = sentences.slice(1).join(' ').trim();
    obj.AI = {
      tldr: tldr,
      motivation:
        '⚠️ Upstream-fallback paper — AI summary is in Chinese on the source repo. ' +
        'Showing the original English abstract instead. Once your own pipeline ' +
        'finishes a successful run, this paper will be replaced by an English ' +
        'AI summary tailored to your field.',
      method:     rest || '',
      result:     '',
      conclusion: ''
    };
    obj._fallbackEnglishAbstract = true;
    return obj;
  }

  // Global map of paper id → real URLs from the JSONL, used by the per-card
  // URL pill so OpenAlex/biorxiv items show their actual landing URL
  // (a doi.org / pubmed link) instead of a constructed arxiv URL that
  // would 404.
  if (!window.__personalizationUrls) window.__personalizationUrls = new Map();

  function _filterJsonlByKeywords(text, keywords) {
    if (!text) return { text, kept: null, total: null };
    const lines = text.split('\n');
    const kept = [];
    let total = 0;
    for (const line of lines) {
      if (!line.trim()) continue;
      total++;
      let obj;
      try { obj = JSON.parse(line); } catch (e) { continue; }

      // Always strip Chinese AI summaries (whether or not we filter).
      const cleaned = _swapChineseAIWithEnglishAbstract(obj);

      // Capture URLs in the global map keyed by id. The id may be
      // 'openalex:W...', 'chemrxiv:...', or a bare arxiv id like '2604.X'.
      if (cleaned.id) {
        window.__personalizationUrls.set(cleaned.id, {
          abs: cleaned.abs || null,
          pdf: cleaned.pdf || null,
          source: cleaned.source || (cleaned.id.match(/^[a-z]+:/) ? cleaned.id.split(':')[0] : 'arxiv')
        });
      }

      if (keywords && keywords.length) {
        const ai = cleaned.AI || {};
        const blob = (
          (cleaned.title || '') + ' ' +
          (cleaned.summary || '') + ' ' +
          (ai.tldr || '') + ' ' +
          (ai.method || '')
        ).toLowerCase();
        if (!keywords.some(k => blob.includes(k))) continue;
      }
      kept.push(JSON.stringify(cleaned));
    }
    return {
      text: kept.join('\n'),
      kept: kept.length,
      total: total
    };
  }

  function _looksLikeJsonlData(url) {
    return /\/data\/.*\.jsonl(?:$|\?)/.test(url);
  }

  if (typeof DATA_CONFIG !== 'undefined' && typeof DATA_CONFIG.getFallbackUrl === 'function') {
    const _origFetch = window.fetch.bind(window);
    const primaryBase = DATA_CONFIG.getDataBaseUrl();
    const fallbackBase = DATA_CONFIG.getFallbackBaseUrl();
    const hasFallback = primaryBase !== fallbackBase;

    async function _fetchWithFallback(resource, init) {
      const url = typeof resource === 'string' ? resource : (resource && resource.url) || '';
      if (hasFallback && typeof url === 'string' && url.startsWith(primaryBase)) {
        try {
          const r = await _origFetch(resource, init);
          if (r.ok) return r;
          const altUrl = url.replace(primaryBase, fallbackBase);
          console.info('[personalization] primary 404; fallback →', altUrl);
          return _origFetch(altUrl, init);
        } catch (e) {
          const altUrl = url.replace(primaryBase, fallbackBase);
          console.info('[personalization] primary threw; fallback →', altUrl, e);
          return _origFetch(altUrl, init);
        }
      }
      return _origFetch(resource, init);
    }

    window.fetch = async function (resource, init) {
      const url = typeof resource === 'string' ? resource : (resource && resource.url) || '';
      const r = await _fetchWithFallback(resource, init);

      // Rewrite JSONL bodies. Two things happen here:
      //   1. Strip Chinese AI summaries when upstream fallback fires
      //      (replaces them with the original English abstract).
      //   2. Apply the user's personal keyword list as a *soft* filter:
      //      drop unrelated upstream-fallback papers; but if the filter
      //      would zero-out a feed that the SERVER pipeline already
      //      filtered (the user's own fork's data branch), don't
      //      double-filter — pipeline already vetted it.
      if (r.ok && _looksLikeJsonlData(url)) {
        try {
          const kws  = _userKeywordsLower();
          const text = await r.clone().text();
          const isUserOwnData = url.startsWith(primaryBase);
          const result = _filterJsonlByKeywords(text, kws);

          // If filtering own-pipeline data would erase everything, fall
          // back to the rewritten-but-unfiltered text so the user still
          // sees the papers their own pipeline curated.
          let outText = result.text;
          let didClientFilter = true;
          if (isUserOwnData && result.kept === 0 && result.total > 0) {
            const passthrough = _filterJsonlByKeywords(text, []);
            outText = passthrough.text;
            didClientFilter = false;
          }

          console.info(
            `[personalization] feed=${isUserOwnData ? 'own' : 'fallback'}` +
            ` total=${result.total} kept=${didClientFilter ? result.kept : result.total}` +
            (didClientFilter ? '' : ' (client filter zeroed; showing pipeline-filtered set)')
          );
          window.__personalizationLastFilter = {
            kept: didClientFilter ? result.kept : result.total,
            total: result.total,
            clientFilterApplied: didClientFilter,
            feed: isUserOwnData ? 'own' : 'fallback',
            date: new Date().toISOString()
          };
          return new Response(outText, {
            status: r.status,
            statusText: r.statusText,
            headers: r.headers
          });
        } catch (e) {
          console.warn('[personalization] filter error, returning raw response', e);
        }
      }
      return r;
    };
  }

  // ---- Seed default keywords (or migrate them) on first visit --------------
  // Migration policy: if the user is still on a known stock list (v1 or v2),
  // upgrade to the current default. If they've customized the list, leave it.
  const V1_DEFAULTS = [
    'crystal structure prediction','molecular crystal','polymorph',
    'lattice energy','AI4Science','machine learning potential',
    'graph neural network','equivariant','diffusion model',
    'generative model','materials discovery','DFT'
  ];
  const V2_DEFAULTS = [
    'crystal structure prediction','molecular crystal','polymorph',
    'co-crystal','lattice energy','structure search','molecular packing',
    'crystal packing','crystal engineering','machine learning potential',
    'inverse design','materials discovery','molecular generation',
    'crystal generation'
  ];
  // v3 had bare 'polymorph' which caught genetic-polymorphism papers.
  // v4 used 'co-crystal' which sometimes matched protein co-crystals.
  // v5 disambiguates both.
  const V4_DEFAULTS = [
    'crystal structure prediction','molecular crystal','crystal polymorph',
    'polymorph selection','polymorph stability','co-crystal','lattice energy',
    'structure search','molecular packing','crystal packing',
    'crystal engineering','space group','pharmaceutical crystal',
    'metal-organic framework','covalent organic framework','perovskite',
    'machine learning potential','neural network potential',
    'interatomic potential','equivariant neural network','inverse design',
    'materials discovery','molecular generation','crystal generation',
    'generative chemistry','MACE','NequIP','M3GNet','CHGNet',
    'density functional theory','ab initio molecular dynamics',
    'molecular dynamics simulation'
  ];
  const V3_DEFAULTS = [
    'crystal structure prediction','molecular crystal','polymorph','co-crystal',
    'lattice energy','structure search','molecular packing','crystal packing',
    'crystal engineering','space group','pharmaceutical crystal',
    'metal-organic framework','covalent organic framework','perovskite',
    'machine learning potential','neural network potential',
    'interatomic potential','equivariant neural network','inverse design',
    'materials discovery','molecular generation','crystal generation',
    'generative chemistry','MACE','NequIP','M3GNet','CHGNet',
    'density functional theory','ab initio molecular dynamics',
    'molecular dynamics simulation'
  ];
  function arraysEqual(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => v === b[i]);
  }
  function seedDefaultKeywords() {
    try {
      const seedDoneAt = localStorage.getItem(PROFILE.seedFlag);
      let saved = [];
      try { saved = JSON.parse(localStorage.getItem('preferredKeywords') || '[]'); }
      catch (_) { saved = []; }

      const isFresh        = !saved.length;
      const isStockV1      = arraysEqual(saved, V1_DEFAULTS);
      const isStockV2      = arraysEqual(saved, V2_DEFAULTS);
      const isStockV3      = arraysEqual(saved, V3_DEFAULTS);
      const isStockV4      = arraysEqual(saved, V4_DEFAULTS);
      const needsMigration = !seedDoneAt && (isFresh || isStockV1 || isStockV2 || isStockV3 || isStockV4);

      if (needsMigration) {
        localStorage.setItem('preferredKeywords',
          JSON.stringify(PROFILE.defaultKeywords));
        localStorage.setItem(PROFILE.seedFlag, '1');
        if (isStockV1 || isStockV2 || isStockV3 || isStockV4) {
          console.info(`[personalization] migrated stock keyword list → v5 (sharper co-crystal/solid-form terms; ${PROFILE.defaultKeywords.length} terms)`);
        }
      }
    } catch (e) {
      console.warn('[personalization] could not seed defaults:', e);
    }
  }
  seedDefaultKeywords();

  // ---- Time-aware greeting --------------------------------------------------
  function greeting() {
    const h = new Date().getHours();
    if (h < 5)  return 'Burning the midnight oil';
    if (h < 12) return 'Good morning';
    if (h < 18) return 'Good afternoon';
    return 'Good evening';
  }

  // ---- Hero banner ----------------------------------------------------------
  function buildHero() {
    // Claude-style index.html sets this flag to skip the gradient hero —
    // it provides its own .editorial-head + integrated date strip.
    if (window.SUPPRESS_PERSONAL_HERO) return;
    const main = document.querySelector('main');
    if (!main || document.getElementById('personalHero')) return;

    const hero = document.createElement('section');
    hero.id = 'personalHero';
    hero.className = 'personal-hero';
    hero.innerHTML = `
      <div class="personal-hero-inner">
        <div class="personal-hero-text">
          <div class="personal-hero-eyebrow">
            <span class="hero-dot"></span>
            <span>${greeting()}, ${PROFILE.displayName}</span>
          </div>
          <h1 class="personal-hero-title">
            Your daily feed for
            <span class="hero-grad">Molecular Crystal Structure Prediction</span>
            &amp;
            <span class="hero-grad-alt">AI4Science</span>
          </h1>
          <p class="personal-hero-sub">
            Curated arXiv papers, AI-summarized — filtered by the keywords you care about.
            Tap a chip below to focus the feed; manage your list in
            <a href="settings.html" class="hero-link">Settings</a>.
          </p>
          <div class="personal-hero-chips" id="personalHeroChips"></div>
          <div class="personal-hero-ranges" id="personalHeroRanges">
            <span class="hero-range-label">Window</span>
            <button class="hero-range-btn" data-range="1">Today</button>
            <button class="hero-range-btn" data-range="7">Last 7d</button>
            <button class="hero-range-btn active" data-range="30">Last 30d</button>
            <button class="hero-range-btn" data-range="90">Last 3mo</button>
            <button class="hero-range-btn" data-range="custom">Custom…</button>
          </div>
        </div>
        <div class="personal-hero-side">
          <div class="hero-stat">
            <div class="hero-stat-num" id="heroPaperCount">—</div>
            <div class="hero-stat-label">papers in window</div>
          </div>
          <div class="hero-stat">
            <div class="hero-stat-num" id="heroKeywordCount">—</div>
            <div class="hero-stat-label">tracked keywords</div>
          </div>
        </div>
      </div>
    `;

    // Insert before the paper container.
    const paperContainer = document.getElementById('paperContainer');
    if (paperContainer) {
      main.insertBefore(hero, paperContainer);
    } else {
      main.prepend(hero);
    }

    renderHeroChips();
    updateHeroStats();
    wireRangePresets();
  }

  // ---- Time-range presets --------------------------------------------------
  // The upstream app already exposes loadPapersByDate / loadPapersByDateRange
  // and a global `availableDates` array. We just compute start/end dates and
  // call them. Default = Last 30 days.

  const DEFAULT_RANGE_DAYS = 30;

  function _isoDaysAgo(n) {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - n);
    return d.toISOString().slice(0, 10);  // YYYY-MM-DD
  }
  function _todayIso() {
    return new Date().toISOString().slice(0, 10);
  }

  // app.js declares `let availableDates` at script-top-level (not on window).
  // Use a guarded eval to read it from the shared script lexical scope.
  function _readAvailableDates() {
    try { return availableDates; } catch (_) { return undefined; }  // eslint-disable-line no-undef
  }
  function _availableDatesReady() {
    const v = _readAvailableDates();
    return Array.isArray(v) && v.length > 0;
  }

  // Wait until availableDates is populated, then call cb.
  function _whenDatesReady(cb, attempts = 60) {
    if (_availableDatesReady()) return cb();
    if (attempts <= 0) return;
    setTimeout(() => _whenDatesReady(cb, attempts - 1), 100);
  }

  function applyRange(rangeDays) {
    _whenDatesReady(() => {
      const dates = _readAvailableDates() || [];
      if (rangeDays === 1) {
        if (typeof window.loadPapersByDate === 'function' && dates.length) {
          window.loadPapersByDate(dates[0]);
        }
        return;
      }
      const today = _todayIso();
      const start = _isoDaysAgo(rangeDays - 1);
      const inRange = dates.filter(d => d >= start && d <= today).sort();
      if (inRange.length === 0) {
        // Window had no dates with data — fall back to the newest available.
        if (typeof window.loadPapersByDate === 'function' && dates.length) {
          window.loadPapersByDate(dates[0]);
        }
        return;
      }
      const startEffective = inRange[0];
      const endEffective   = inRange[inRange.length - 1];
      if (typeof window.loadPapersByDateRange === 'function') {
        window.loadPapersByDateRange(startEffective, endEffective);
      }
    });
  }

  function wireRangePresets() {
    const bar = document.getElementById('personalHeroRanges');
    if (!bar || bar.dataset.wired) return;
    bar.dataset.wired = '1';
    bar.querySelectorAll('.hero-range-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        bar.querySelectorAll('.hero-range-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        const r = btn.dataset.range;
        if (r === 'custom') {
          const calBtn = document.getElementById('calendarButton');
          if (calBtn) calBtn.click();
          // Make sure range mode is on so the user can pick two dates.
          const rangeToggle = document.getElementById('dateRangeMode');
          if (rangeToggle && !rangeToggle.checked) {
            rangeToggle.checked = true;
            rangeToggle.dispatchEvent(new Event('change'));
          }
          return;
        }
        applyRange(parseInt(r, 10));
      });
    });

    // First-load override: load the last 30 days instead of just today.
    _whenDatesReady(() => {
      // Honor URL params (?date=...) — if the upstream app already chose a
      // specific date due to a URL param or single-date click, don't override.
      if (window.location.search.includes('date=')) return;
      applyRange(DEFAULT_RANGE_DAYS);
    });
  }

  function renderHeroChips() {
    const wrap = document.getElementById('personalHeroChips');
    if (!wrap) return;
    let kws = [];
    try {
      kws = JSON.parse(localStorage.getItem('preferredKeywords') || '[]');
    } catch (e) {}
    if (!kws.length) {
      wrap.innerHTML = `<span class="hero-chip hero-chip-empty">
        No keywords yet — add some in Settings to personalize the feed.
      </span>`;
      return;
    }
    // Show first 6, then "+N more"
    const shown = kws.slice(0, 6);
    const extra = kws.length - shown.length;
    wrap.innerHTML = shown
      .map(k => `<span class="hero-chip" data-kw="${escapeHtml(k)}">${escapeHtml(k)}</span>`)
      .join('') + (extra > 0
        ? `<span class="hero-chip hero-chip-more" title="${escapeHtml(kws.slice(6).join(', '))}">+${extra} more</span>`
        : '');

    // Clicking a hero chip activates the matching filter tag in the navbar.
    wrap.querySelectorAll('.hero-chip[data-kw]').forEach(chip => {
      chip.addEventListener('click', () => {
        const kw = chip.dataset.kw;
        const target = document.querySelector(`[data-keyword="${cssEscape(kw)}"]`);
        if (target) target.click();
        chip.classList.add('hero-chip-pulse');
        setTimeout(() => chip.classList.remove('hero-chip-pulse'), 600);
      });
    });
  }

  function updateHeroStats() {
    let kws = [];
    try {
      kws = JSON.parse(localStorage.getItem('preferredKeywords') || '[]');
    } catch (e) {}
    const kEl = document.getElementById('heroKeywordCount');
    if (kEl) kEl.textContent = String(kws.length);

    const pEl = document.getElementById('heroPaperCount');
    if (!pEl) return;
    const tryCount = () => {
      const cards = document.querySelectorAll('#paperContainer .paper-card');
      if (cards.length) pEl.textContent = String(cards.length);
      maybeShowEmptyState();
    };
    tryCount();
    const container = document.getElementById('paperContainer');
    if (container) {
      const mo = new MutationObserver(tryCount);
      mo.observe(container, { childList: true, subtree: true });
    }
  }

  // ---- Friendly empty state when filter zeroes out the feed ----------------
  function maybeShowEmptyState() {
    const container = document.getElementById('paperContainer');
    if (!container) return;
    const hasCards   = container.querySelector('.paper-card');
    const hasSpinner = container.querySelector('.loading-spinner');
    if (hasCards || hasSpinner) {
      const old = document.getElementById('personalEmpty');
      if (old) old.remove();
      return;
    }
    if (document.getElementById('personalEmpty')) return;
    const stats = window.__personalizationLastFilter;
    const filteredAll = stats && stats.total > 0 && stats.kept === 0;
    const wrap = document.createElement('div');
    wrap.id = 'personalEmpty';
    wrap.className = 'personal-empty';
    wrap.innerHTML = filteredAll
      ? `<div class="personal-empty-icon">⌬</div>
         <h3>No papers in your field today</h3>
         <p>Today's feed had <strong>${stats.total}</strong> papers but
         none matched any of your <strong>${_userKeywordsLower().length}</strong>
         MCSP × AI4Sci keywords. Once your fork's pipeline produces its first
         data file (categories: cond-mat.mtrl-sci, physics.chem-ph, etc.), this
         will show only the relevant papers.</p>
         <p class="personal-empty-hint">
           Tweak your keywords in
           <a href="settings.html">Settings</a> to broaden the match,
           or check the
           <a href="https://github.com/Jiayihua2001/daily-arXiv-ai-enhanced/actions"
              target="_blank" rel="noopener">workflow status</a>.
         </p>`
      : `<div class="personal-empty-icon">⌬</div>
         <h3>Your daily MCSP feed is being set up</h3>
         <p>Once the GitHub Actions pipeline runs and publishes the
         <code>data</code> branch, papers will appear here automatically.</p>
         <p class="personal-empty-hint">
           Check
           <a href="https://github.com/Jiayihua2001/daily-arXiv-ai-enhanced/actions"
              target="_blank" rel="noopener">workflow status</a>.
         </p>`;
    container.innerHTML = '';
    container.appendChild(wrap);
  }

  // ---- Helpers --------------------------------------------------------------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/["\\]/g, '\\$&');
  }

  // ---- Personalize page chrome ---------------------------------------------
  function personalizeChrome() {
    // Title — only on the index page.
    const isIndex = /(?:^|\/)(index\.html)?$/.test(location.pathname);
    if (isIndex) {
      document.title = `Jade · MCSP × AI4Sci · Daily arXiv`;
    }

    // Tweak the site title to subtly show personalization.
    // Skip when the index already provides its own suffix (Claude-style page).
    const siteTitle = document.querySelector('.site-title');
    const alreadyHasSuffix = !!document.querySelector('.site-title-suffix');
    if (siteTitle && !siteTitle.dataset.personalized && !alreadyHasSuffix) {
      siteTitle.dataset.personalized = '1';
      siteTitle.innerHTML = `Daily arXiv <span class="site-title-suffix">· MCSP × AI4Sci</span>`;
    }
  }

  // ===========================================================================
  // Per-paper enhancements: arXiv URL on each card + personal status tags
  // ===========================================================================

  const STATUS_KEY = 'paperStatuses_v1';
  // statuses: { [arxivId]: { important: bool, status: 'unread'|'reading'|'finished' } }

  function loadStatuses() {
    try { return JSON.parse(localStorage.getItem(STATUS_KEY) || '{}'); }
    catch (e) { return {}; }
  }
  function saveStatuses(s) {
    try { localStorage.setItem(STATUS_KEY, JSON.stringify(s)); } catch (e) {}
  }
  function getStatus(id) {
    const s = loadStatuses();
    return s[id] || { important: false, status: 'unread' };
  }
  function setStatus(id, patch) {
    const s = loadStatuses();
    s[id] = Object.assign({ important: false, status: 'unread' }, s[id] || {}, patch);
    saveStatuses(s);
    updateStatusFilterCounts();
  }

  // Inject paper URL row + status controls into a single .paper-card.
  function enhanceCard(card) {
    if (!card || card.dataset.personalEnhanced) return;
    const id = card.dataset.id;
    if (!id) return;
    card.dataset.personalEnhanced = '1';

    const status = getStatus(id);
    if (status.important) card.classList.add('paper-status-important');
    card.classList.add(`paper-status-${status.status}`);

    // Pull real URLs from the global map populated by the fetch shim.
    // Falls back to constructed arxiv URLs only when the id LOOKS like
    // a bare arxiv id (no source-prefix colon).
    const meta = window.__personalizationUrls?.get(id);
    const isArxivLike = !id.includes(':');
    const absUrl = meta?.abs || (isArxivLike ? `https://arxiv.org/abs/${id}` : null);
    const pdfUrl = meta?.pdf || (isArxivLike ? `https://arxiv.org/pdf/${id}` : null);
    const source = meta?.source || (isArxivLike ? 'arxiv' : id.split(':')[0]);

    // Friendly id label: 'arXiv:2604.123' or 'openalex:W7159...' or
    // 'doi.org/10.123/...' for openalex with a DOI url.
    let displayId = id;
    if (source === 'openalex' && absUrl && absUrl.includes('doi.org/')) {
      displayId = absUrl.split('doi.org/')[1].slice(0, 30);
    } else if (source === 'arxiv' && !id.includes(':')) {
      displayId = id;
    } else if (id.includes(':')) {
      displayId = id.split(':')[1].slice(0, 16);
    }
    const sourceBadge = source.toUpperCase();

    // ---- URL row (under header) ----
    const header = card.querySelector('.paper-card-header');
    if (header && !header.querySelector('.paper-arxiv-link') && absUrl) {
      const urlRow = document.createElement('div');
      urlRow.className = 'paper-arxiv-link';
      const pdfChip = pdfUrl
        ? `<a href="${pdfUrl}" target="_blank" rel="noopener"
              onclick="event.stopPropagation()" class="paper-arxiv-pdf"
              title="Open PDF">PDF</a>`
        : '';
      urlRow.innerHTML = `
        <a href="${absUrl}" target="_blank" rel="noopener"
           onclick="event.stopPropagation()" title="Open paper">
          <span class="arxiv-id-prefix">${escapeHtml(sourceBadge)}:</span>${escapeHtml(displayId)}
        </a>
        ${pdfChip}
        <button class="paper-arxiv-copy" type="button"
           onclick="event.stopPropagation()" title="Copy URL">⧉</button>
      `;
      header.appendChild(urlRow);
      urlRow.querySelector('.paper-arxiv-copy').addEventListener('click', async (ev) => {
        ev.preventDefault();
        try {
          await navigator.clipboard.writeText(absUrl);
          ev.currentTarget.textContent = '✓';
          setTimeout(() => { ev.currentTarget.textContent = '⧉'; }, 1200);
        } catch (e) { /* ignore */ }
      });
    }

    // ---- Status control row (in body footer) ----
    const footer = card.querySelector('.paper-card-footer .footer-left') ||
                   card.querySelector('.paper-card-footer');
    if (footer && !card.querySelector('.paper-status-controls')) {
      const ctrl = document.createElement('div');
      ctrl.className = 'paper-status-controls';
      ctrl.innerHTML = `
        <button class="status-pip status-star ${status.important ? 'on' : ''}"
                data-act="star" title="Mark important"
                onclick="event.stopPropagation()">★</button>
        <button class="status-pip status-set ${status.status !== 'unread' ? 'on' : ''}"
                data-act="cycle" title="Cycle: unread → reading → finished"
                onclick="event.stopPropagation()">${statusLabel(status.status)}</button>
      `;
      footer.appendChild(ctrl);

      ctrl.querySelector('[data-act=star]').addEventListener('click', (ev) => {
        const next = !getStatus(id).important;
        setStatus(id, { important: next });
        ev.currentTarget.classList.toggle('on', next);
        card.classList.toggle('paper-status-important', next);
      });
      ctrl.querySelector('[data-act=cycle]').addEventListener('click', (ev) => {
        const cur = getStatus(id).status;
        const next = cur === 'unread' ? 'reading'
                   : cur === 'reading' ? 'finished'
                   : 'unread';
        setStatus(id, { status: next });
        ev.currentTarget.textContent = statusLabel(next);
        ev.currentTarget.classList.toggle('on', next !== 'unread');
        card.classList.remove('paper-status-unread', 'paper-status-reading', 'paper-status-finished');
        card.classList.add(`paper-status-${next}`);
      });
    }
  }

  function statusLabel(s) {
    return s === 'reading'  ? '📖 Reading'
         : s === 'finished' ? '✅ Finished'
                            : '📥 Unread';
  }

  // Run enhanceCard on every existing card + observe for new ones.
  function watchPaperCards() {
    const container = document.getElementById('paperContainer');
    if (!container) return;
    container.querySelectorAll('.paper-card').forEach(enhanceCard);
    const mo = new MutationObserver(muts => {
      muts.forEach(m => {
        m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          if (n.classList && n.classList.contains('paper-card')) enhanceCard(n);
          if (n.querySelectorAll) n.querySelectorAll('.paper-card').forEach(enhanceCard);
        });
      });
    });
    mo.observe(container, { childList: true, subtree: true });
  }

  // ---- Status filter row (in header) ---------------------------------------
  let activeStatusFilter = 'all'; // all|important|unread|reading|finished

  function buildStatusFilter() {
    const filterContainer = document.querySelector('.filter-label-container');
    if (!filterContainer || document.getElementById('personalStatusFilter')) return;

    const bar = document.createElement('div');
    bar.id = 'personalStatusFilter';
    bar.className = 'personal-status-filter';
    bar.innerHTML = `
      <span class="filter-nav-label">Status</span>
      <button class="status-filter-btn active" data-f="all">All <span class="cnt"></span></button>
      <button class="status-filter-btn" data-f="important">⭐ Important <span class="cnt"></span></button>
      <button class="status-filter-btn" data-f="reading">📖 Reading <span class="cnt"></span></button>
      <button class="status-filter-btn" data-f="finished">✅ Finished <span class="cnt"></span></button>
      <button class="status-filter-btn" data-f="unread">📥 Unread <span class="cnt"></span></button>
    `;
    // Insert as a new row below the existing filter container.
    filterContainer.parentNode.insertBefore(bar, filterContainer.nextSibling);

    bar.querySelectorAll('.status-filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        bar.querySelectorAll('.status-filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeStatusFilter = btn.dataset.f;
        applyStatusFilter();
      });
    });
    updateStatusFilterCounts();
  }

  function applyStatusFilter() {
    const cards = document.querySelectorAll('#paperContainer .paper-card');
    cards.forEach(card => {
      const id = card.dataset.id;
      const st = id ? getStatus(id) : { important: false, status: 'unread' };
      let show = true;
      switch (activeStatusFilter) {
        case 'important': show = !!st.important; break;
        case 'reading':   show = st.status === 'reading';  break;
        case 'finished':  show = st.status === 'finished'; break;
        case 'unread':    show = st.status === 'unread' && !st.important; break;
        case 'all':       show = true;
      }
      card.style.display = show ? '' : 'none';
    });
  }

  function updateStatusFilterCounts() {
    const all = loadStatuses();
    const cards = document.querySelectorAll('#paperContainer .paper-card');
    const counts = { all: cards.length, important: 0, reading: 0, finished: 0, unread: 0 };
    cards.forEach(c => {
      const st = c.dataset.id ? all[c.dataset.id] : null;
      if (st && st.important) counts.important++;
      const status = (st && st.status) || 'unread';
      counts[status]++;
    });
    const bar = document.getElementById('personalStatusFilter');
    if (!bar) return;
    bar.querySelectorAll('.status-filter-btn').forEach(btn => {
      const c = btn.querySelector('.cnt');
      if (c) c.textContent = counts[btn.dataset.f] != null ? `(${counts[btn.dataset.f]})` : '';
    });
  }

  // ---- Init -----------------------------------------------------------------
  function init() {
    personalizeChrome();
    if (document.getElementById('paperContainer')) {
      buildHero();
      buildStatusFilter();
      watchPaperCards();
    }
    window.addEventListener('storage', (e) => {
      if (e.key === 'preferredKeywords') {
        renderHeroChips();
        updateHeroStats();
      }
      if (e.key === STATUS_KEY) {
        updateStatusFilterCounts();
        applyStatusFilter();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
