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
    // Curated seed list. Users can edit/remove these freely in Settings.
    defaultKeywords: [
      'crystal structure prediction',
      'molecular crystal',
      'polymorph',
      'lattice energy',
      'AI4Science',
      'machine learning potential',
      'graph neural network',
      'equivariant',
      'diffusion model',
      'generative model',
      'materials discovery',
      'DFT'
    ],
    seedFlag: 'mcsp_defaults_seeded_v1'
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

  function _filterJsonlByKeywords(text, keywords) {
    if (!keywords || !keywords.length || !text) return { text, kept: null, total: null };
    const lines = text.split('\n');
    const kept = [];
    let total = 0;
    for (const line of lines) {
      if (!line.trim()) continue;
      total++;
      let obj;
      try { obj = JSON.parse(line); } catch (e) { continue; }
      const ai = obj.AI || {};
      const blob = (
        (obj.title || '') + ' ' +
        (obj.summary || '') + ' ' +
        (ai.tldr || '') + ' ' +
        (ai.motivation || '') + ' ' +
        (ai.method || '') + ' ' +
        (ai.result || '') + ' ' +
        (ai.conclusion || '')
      ).toLowerCase();
      if (keywords.some(k => blob.includes(k))) kept.push(line);
    }
    return { text: kept.join('\n'), kept: kept.length, total };
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

      // Topical filter: rewrite JSONL bodies in place to drop non-matching papers.
      if (r.ok && _looksLikeJsonlData(url)) {
        const kws = _userKeywordsLower();
        if (kws.length) {
          try {
            const text = await r.clone().text();
            const { text: filtered, kept, total } = _filterJsonlByKeywords(text, kws);
            if (kept !== null) {
              console.info(`[personalization] keyword filter: ${kept}/${total} papers match`);
              window.__personalizationLastFilter = { kept, total, date: new Date().toISOString() };
              return new Response(filtered, {
                status: r.status,
                statusText: r.statusText,
                headers: r.headers
              });
            }
          } catch (e) {
            console.warn('[personalization] filter error, returning raw response', e);
          }
        }
      }
      return r;
    };
  }

  // ---- Seed default keywords on first visit ---------------------------------
  function seedDefaultKeywords() {
    try {
      const alreadySeeded = localStorage.getItem(PROFILE.seedFlag);
      const existing = localStorage.getItem('preferredKeywords');
      if (!alreadySeeded && (!existing || existing === '[]')) {
        localStorage.setItem(
          'preferredKeywords',
          JSON.stringify(PROFILE.defaultKeywords)
        );
        localStorage.setItem(PROFILE.seedFlag, '1');
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
        </div>
        <div class="personal-hero-side">
          <div class="hero-stat">
            <div class="hero-stat-num" id="heroPaperCount">—</div>
            <div class="hero-stat-label">papers today</div>
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
    const siteTitle = document.querySelector('.site-title');
    if (siteTitle && !siteTitle.dataset.personalized) {
      siteTitle.dataset.personalized = '1';
      siteTitle.innerHTML = `Daily arXiv <span class="site-title-suffix">· MCSP × AI4Sci</span>`;
    }
  }

  // ---- Init -----------------------------------------------------------------
  function init() {
    personalizeChrome();
    // Hero only on the index page (where #paperContainer exists).
    if (document.getElementById('paperContainer')) {
      buildHero();
    }
    // Refresh chips when user returns from Settings.
    window.addEventListener('storage', (e) => {
      if (e.key === 'preferredKeywords') {
        renderHeroChips();
        updateHeroStats();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
