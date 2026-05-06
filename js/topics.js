/**
 * Topics page — renders the field-guide primers.
 *
 * Reads topics/index.json for the list, then fetches the selected
 * topics/<slug>.md and renders it via marked + DOMPurify.
 *
 * URL fragment routing:  topics.html#mcsp  → renders mcsp.md
 *
 * No build step — files are served as-is from the repo's main branch
 * (same origin as topics.html), so no CORS issues.
 */
(function () {
  'use strict';

  const INDEX_URL = 'topics/index.json';

  // ---- helpers -----------------------------------------------------------

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // Strip the YAML frontmatter (the leading `---\n...\n---\n` block) before
  // handing the body to marked — we already have the metadata via index.json.
  function stripFrontmatter(text) {
    if (!text.startsWith('---')) return text;
    const end = text.indexOf('\n---', 3);
    if (end === -1) return text;
    return text.slice(end + 4).replace(/^\n/, '');
  }

  function renderMarkdown(md) {
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
      // Fallback: render as preformatted text. Better than nothing if the CDN
      // didn't load.
      return `<pre>${escapeHtml(md)}</pre>`;
    }
    marked.setOptions({
      gfm: true,
      breaks: false,
      headerIds: true,
      mangle: false,
    });
    const html = marked.parse(md);
    return DOMPurify.sanitize(html, {
      ADD_ATTR: ['target', 'rel'],   // allow external-link attrs
    });
  }

  function tagPills(tags) {
    if (!Array.isArray(tags) || !tags.length) return '';
    return `<div class="topics-tags">${
      tags.map(t => `<span class="topics-tag">${escapeHtml(t)}</span>`).join('')
    }</div>`;
  }

  function statusBadge(status) {
    if (!status) return '';
    return `<span class="topics-status topics-status-${escapeHtml(status)}">${escapeHtml(status)}</span>`;
  }

  // ---- index ------------------------------------------------------------

  async function loadIndex() {
    const r = await fetch(INDEX_URL, { cache: 'no-store' });
    if (!r.ok) throw new Error(`failed to load ${INDEX_URL}: HTTP ${r.status}`);
    const data = await r.json();
    return Array.isArray(data.topics) ? data.topics : [];
  }

  function renderSidebar(topics, activeSlug) {
    const list = document.getElementById('topicsList');
    if (!list) return;
    if (!topics.length) {
      list.innerHTML = `<div class="topics-empty">No topics yet — add one in <code>topics/</code>.</div>`;
      return;
    }
    list.innerHTML = topics.map(t => {
      const isActive = t.slug === activeSlug;
      return `
        <a class="topics-card ${isActive ? 'is-active' : ''}" href="#${escapeHtml(t.slug)}">
          <div class="topics-card-title">${escapeHtml(t.title || t.slug)}</div>
          ${t.summary ? `<div class="topics-card-summary">${escapeHtml(t.summary)}</div>` : ''}
          <div class="topics-card-meta">
            ${t.last_updated ? `<span class="topics-updated">Updated ${escapeHtml(t.last_updated)}</span>` : ''}
            ${statusBadge(t.status)}
          </div>
          ${tagPills(t.tags)}
        </a>
      `;
    }).join('');
  }

  // ---- article ---------------------------------------------------------

  async function loadTopic(slug) {
    const article = document.getElementById('topicsArticle');
    if (!article) return;
    article.innerHTML = `<div class="topics-loading">Loading…</div>`;
    article.scrollTo?.({ top: 0 });

    try {
      const r = await fetch(`topics/${encodeURIComponent(slug)}.md`, { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const text = await r.text();
      const body = stripFrontmatter(text);
      article.innerHTML = `
        <div class="topics-article-body">
          ${renderMarkdown(body)}
        </div>
      `;
      // Open all external links in a new tab.
      article.querySelectorAll('a[href^="http"]').forEach(a => {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      });
    } catch (e) {
      article.innerHTML = `
        <div class="topics-error">
          <strong>Couldn't load topic <code>${escapeHtml(slug)}</code>.</strong>
          <p>${escapeHtml(String(e.message || e))}</p>
          <p>Make sure <code>topics/${escapeHtml(slug)}.md</code> exists in the repo.</p>
        </div>
      `;
    }
  }

  // ---- routing ---------------------------------------------------------

  function currentSlug(topics) {
    const slug = (location.hash || '').replace(/^#/, '');
    if (slug && topics.some(t => t.slug === slug)) return slug;
    // Default: first topic in the index.
    return topics[0]?.slug || null;
  }

  // ---- init -----------------------------------------------------------

  async function init() {
    let topics;
    try {
      topics = await loadIndex();
    } catch (e) {
      const list = document.getElementById('topicsList');
      const article = document.getElementById('topicsArticle');
      if (list) list.innerHTML = `<div class="topics-error"><strong>Failed to load topics index.</strong><br>${escapeHtml(String(e.message || e))}</div>`;
      if (article) article.innerHTML = '';
      return;
    }

    let active = currentSlug(topics);
    renderSidebar(topics, active);
    if (active) loadTopic(active);

    window.addEventListener('hashchange', () => {
      const next = currentSlug(topics);
      if (next && next !== active) {
        active = next;
        renderSidebar(topics, active);
        loadTopic(active);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
