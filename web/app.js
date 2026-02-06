const PAGE_SIZE = 20;

const state = {
  report: null,
  keyword: 'all',
  query: '',
  sort: 'score',
  visibleCount: PAGE_SIZE,
};

const dateSelect = document.getElementById('date-select');
const keywordSelect = document.getElementById('keyword-select');
const searchInput = document.getElementById('search-input');
const sortSelect = document.getElementById('sort-select');
const resultCountEl = document.getElementById('result-count');
const clearFiltersBtn = document.getElementById('clear-filters');
const summaryEl = document.getElementById('summary');
const papersEl = document.getElementById('papers');
const trendsEl = document.getElementById('trends');

const statDate = document.getElementById('stat-date');
const statTotal = document.getElementById('stat-total');
const statMatched = document.getElementById('stat-matched');
const footerDate = document.getElementById('footer-date');

let pagerObserver = null;
let reportLoadToken = 0;
let datesLoadToken = 0;
let peekedCardId = null;

// Debounce utility
function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function sortKeywordsAlphabetically(keywords) {
  if (!Array.isArray(keywords)) return [];
  return [...keywords].sort((a, b) =>
    String(a || '').localeCompare(String(b || ''), 'en', { sensitivity: 'base' })
  );
}

function showSkeletonSummary() {
  summaryEl.innerHTML = `
    <div class="skeleton skeleton-title"></div>
    <div class="skeleton skeleton-line"></div>
    <div class="skeleton skeleton-line"></div>
    <div class="skeleton skeleton-line skeleton-line-short"></div>
  `;
}

function showSkeletonTrends() {
  trendsEl.innerHTML = `
    <div class="trend-card">
      <div class="skeleton skeleton-subtitle"></div>
      <div class="skeleton skeleton-line"></div>
      <div class="skeleton skeleton-line skeleton-line-short"></div>
    </div>
    <div class="trend-card">
      <div class="skeleton skeleton-subtitle"></div>
      <div class="skeleton skeleton-line"></div>
      <div class="skeleton skeleton-line skeleton-line-short"></div>
    </div>
  `;
}

function showSkeletonPapers() {
  papersEl.innerHTML = Array.from({ length: 6 })
    .map(
      () => `
        <div class="paper-card paper-skeleton">
          <div class="paper-head">
            <div style="flex: 1;">
              <div class="skeleton skeleton-subtitle"></div>
              <div class="skeleton skeleton-title"></div>
            </div>
            <div class="skeleton skeleton-chip"></div>
          </div>
          <div class="skeleton skeleton-line"></div>
          <div class="skeleton skeleton-line"></div>
          <div class="skeleton skeleton-line skeleton-line-short"></div>
        </div>
      `
    )
    .join('');
}

function showErrorState(element, title, message, onRetry) {
  element.innerHTML = `
    <div class="empty-state">
      <p class="error-title">${escapeHtml(title)}</p>
      <p>${escapeHtml(message)}</p>
      ${onRetry ? '<button class="retry-btn" type="button">重试</button>' : ''}
    </div>
  `;

  if (onRetry) {
    const btn = element.querySelector('.retry-btn');
    if (btn) {
      btn.addEventListener('click', () => onRetry(), { once: true });
    }
  }
}

async function fetchDates() {
  const res = await fetch('/api/dates');
  if (!res.ok) {
    throw new Error(`获取日期列表失败（HTTP ${res.status}）`);
  }
  const data = await res.json();
  if (!Array.isArray(data)) return [];
  return data;
}

async function fetchReport(date) {
  const url = date ? `/api/report?date=${date}` : '/api/report';
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`获取报告失败（HTTP ${res.status}）`);
  }
  return await res.json();
}

function updateStats(report) {
  statDate.textContent = report.date || '-';
  statTotal.textContent = report.total_papers ?? '-';
  statMatched.textContent = report.matched_papers ?? '-';
  footerDate.textContent = report.date ? `更新于 ${report.date}` : '';
}

function updateSummary(report) {
  const keyword = state.keyword;
  const summary = report.summaries || {};

  if (keyword === 'all') {
    const totalPapers = Number(report.total_papers) || 0;
    const matchedPapers = Number(report.matched_papers) || 0;
    const analyzedPapers = Number(report.analyzed_papers) || 0;
    summaryEl.innerHTML = `
      <h2>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -3px; margin-right: 8px;">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="16" y1="13" x2="8" y2="13"></line>
          <line x1="16" y1="17" x2="8" y2="17"></line>
          <polyline points="10 9 9 9 8 9"></polyline>
        </svg>
        今日总览
      </h2>
      <p>共抓取 <strong>${totalPapers}</strong> 篇论文，匹配 <strong>${matchedPapers}</strong> 篇，完成深度分析 <strong>${analyzedPapers}</strong> 篇。</p>
      <p>请选择领域查看对应总结，或使用搜索框定位具体论文。</p>
    `;
    return;
  }

  const content = summary[keyword] || '今日该领域暂无相关论文更新。';
  const linkedContent = linkifyPaperRefsHtml(renderMarkdown(content), keyword);
  summaryEl.innerHTML = `
    <h2>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -3px; margin-right: 8px;">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
      </svg>
      ${escapeHtml(keyword)}
    </h2>
    <div class="markdown">${linkedContent}</div>
  `;
}

function collectUniquePapers(report) {
  const map = new Map();
  for (const list of Object.values(report.papers_by_keyword || {})) {
    for (const paper of list || []) {
      const key = paper.id || paper.arxiv_id || paper.title;
      if (!key) continue;

      if (!map.has(key)) {
        map.set(key, {
          ...paper,
          matched_keywords: Array.isArray(paper.matched_keywords) ? [...paper.matched_keywords] : [],
        });
        continue;
      }

      const existing = map.get(key);
      const mergedKeywords = new Set([
        ...(existing.matched_keywords || []),
        ...(paper.matched_keywords || []),
      ]);
      existing.matched_keywords = Array.from(mergedKeywords);

      const existingScore = existing.quality_score || 0;
      const nextScore = paper.quality_score || 0;
      if (nextScore > existingScore) {
        existing.quality_score = paper.quality_score;
        existing.score_reason = paper.score_reason;
      }
    }
  }
  return Array.from(map.values());
}

function getPaperKey(paper) {
  return paper?.id || paper?.arxiv_id || paper?.title || '';
}

function getBasePapers(report) {
  if (!report) return [];
  if (state.keyword === 'all') return collectUniquePapers(report);
  return report.papers_by_keyword?.[state.keyword] || [];
}

function buildPaperNumberByKey(report, keyword) {
  const map = new Map();
  if (!report || !keyword || keyword === 'all') return map;
  const originalList = report.papers_by_keyword?.[keyword] || [];
  originalList.forEach((paper, idx) => {
    const key = getPaperKey(paper);
    if (!key || map.has(key)) return;
    const explicit = typeof paper.paper_number === 'number' && paper.paper_number > 0 ? paper.paper_number : null;
    map.set(key, explicit ?? idx + 1);
  });
  return map;
}

function getPaperNumber(paper, paperNumberByKey, fallbackNumber) {
  const explicit =
    typeof paper.paper_number === 'number' && paper.paper_number > 0 ? paper.paper_number : null;
  if (explicit) return explicit;
  const key = getPaperKey(paper);
  const mapped = key ? paperNumberByKey.get(key) : null;
  if (typeof mapped === 'number' && mapped > 0) return mapped;
  return fallbackNumber;
}

function getPublishedTimestamp(paper) {
  const raw = paper?.published || paper?.updated;
  if (!raw) return 0;
  const ts = Date.parse(raw);
  return Number.isFinite(ts) ? ts : 0;
}

function parseYmdToUtcMs(ymd) {
  const match = String(ymd || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return 0;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!year || !month || !day) return 0;
  return Date.UTC(year, month - 1, day);
}

function formatRelativeFromReport(published, reportDate) {
  const pubShort = typeof published === 'string' ? published.slice(0, 10) : '';
  const pubMs = parseYmdToUtcMs(pubShort);
  const refMs = parseYmdToUtcMs(reportDate) || Date.now();
  if (!pubMs || !refMs) return '';
  const diffDays = Math.round((refMs - pubMs) / 86400000);
  if (!Number.isFinite(diffDays)) return '';
  if (diffDays === 0) return '今天';
  if (diffDays === 1) return '昨天';
  if (diffDays > 1 && diffDays < 7) return `${diffDays}天前`;
  if (diffDays >= 7 && diffDays < 30) return `${Math.floor(diffDays / 7)}周前`;
  if (diffDays >= 30 && diffDays < 365) return `${Math.floor(diffDays / 30)}个月前`;
  if (diffDays >= 365) return `${Math.floor(diffDays / 365)}年前`;
  return '';
}

function formatPublishedLabel(paper, report) {
  const full = paper?.published || paper?.updated || '';
  const short = typeof full === 'string' ? full.slice(0, 10) : '';
  const rel = formatRelativeFromReport(full, report?.date);
  return {
    full,
    short: short || '',
    relative: rel || '',
  };
}

function renderSourceBadges(paper) {
  const isJournal = paper?.source === 'journal';
  const primary = isJournal ? '期刊' : 'arXiv';
  const cat = paper?.primary_category || '';

  const badges = [
    `<span class="source-badge ${isJournal ? 'source-journal' : 'source-arxiv'}">${escapeHtml(primary)}</span>`,
  ];

  if (cat) {
    badges.push(`<span class="source-badge source-cat">${escapeHtml(cat)}</span>`);
  }

  return `<div class="source-badges">${badges.join('')}</div>`;
}

function updateControlBar({ baseTotal, filteredTotal, visibleTotal }) {
  const base = Number(baseTotal) || 0;
  const filtered = Number(filteredTotal) || 0;
  const visible = Number(visibleTotal) || 0;

  const filterHint = state.query.trim()
    ? `（搜索：${escapeHtml(state.query.trim().slice(0, 20))}${state.query.trim().length > 20 ? '…' : ''}）`
    : '';

  const pagingHint = filtered > visible ? ` · 已显示 ${visible} / ${filtered}` : '';
  resultCountEl.innerHTML = `结果：<strong>${filtered}</strong> / ${base}${filterHint}${pagingHint}`;

  const isDefault =
    state.keyword === 'all' &&
    !state.query.trim() &&
    state.sort === 'score' &&
    state.visibleCount === PAGE_SIZE;
  clearFiltersBtn.disabled = isDefault;
}

function updateSortOptions() {
  const keyword = state.keyword;
  const previous = state.sort;

  const options = keyword === 'all'
    ? [
        { value: 'score', label: '按评分' },
        { value: 'published', label: '按发布时间' },
      ]
    : [
        { value: 'number', label: '按编号' },
        { value: 'score', label: '按评分' },
        { value: 'published', label: '按发布时间' },
      ];

  sortSelect.innerHTML = '';
  for (const opt of options) {
    const option = document.createElement('option');
    option.value = opt.value;
    option.textContent = opt.label;
    sortSelect.appendChild(option);
  }

  const allowed = new Set(options.map((o) => o.value));
  if (!allowed.has(previous)) {
    state.sort = keyword === 'all' ? 'score' : 'number';
  }

  sortSelect.value = state.sort;
}

function updateTrends(report) {
  const keywords = sortKeywordsAlphabetically(report.keywords);
  const counts = keywords.map((kw) => ({
    name: kw,
    count: (report.papers_by_keyword?.[kw] || []).length,
  }));

  const uniquePapers = collectUniquePapers(report);
  let arxivCount = 0;
  let journalCount = 0;
  uniquePapers.forEach((paper) => {
    if (paper.source === 'journal') {
      journalCount += 1;
    } else {
      arxivCount += 1;
    }
  });

  trendsEl.innerHTML = `
    <div class="trend-card">
      <div class="trend-head">
        <h3>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -2px; margin-right: 6px;">
            <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline>
            <polyline points="17 6 23 6 23 12"></polyline>
          </svg>
          今日主题趋势
        </h3>
        <span class="trend-sub">按首字母排序</span>
      </div>
      <div class="trend-tags">
        ${counts
          .map(
            (item) =>
              `<button type="button" class="trend-tag trend-button" data-keyword="${escapeHtml(
                item.name
              )}" title="查看该领域"><strong>${item.count}</strong> ${escapeHtml(item.name)}</button>`
          )
          .join('')}
      </div>
    </div>
    <div class="trend-card">
      <div class="trend-head">
        <h3>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -2px; margin-right: 6px;">
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
          </svg>
          来源覆盖
        </h3>
        <span class="trend-sub">去重后的论文来源分布</span>
      </div>
      <div class="trend-metrics">
        <div>
          <span class="trend-label">arXiv</span>
          <span class="trend-value">${arxivCount}</span>
        </div>
        <div>
          <span class="trend-label">期刊</span>
          <span class="trend-value">${journalCount}</span>
        </div>
      </div>
    </div>
  `;
}

function getFilteredPapers(report) {
  const keyword = state.keyword;
  const query = state.query.trim().toLowerCase();
  let papers = getBasePapers(report);

  // Filter by search query
  if (query) {
    papers = papers.filter((paper) => {
      const haystack = [
        paper.title,
        paper.tldr,
        paper.methodology,
        paper.experiments,
        paper.matched_keywords?.join(' '),
        paper.primary_category,
        paper.authors?.join(' '),
        paper.affiliations?.join(' '),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
  }

  // Sort
  const sort = state.sort;
  if (sort === 'published') {
    papers.sort((a, b) => {
      const diff = getPublishedTimestamp(b) - getPublishedTimestamp(a);
      if (diff !== 0) return diff;
      return (b.quality_score || 0) - (a.quality_score || 0);
    });
  } else if (sort === 'number' && keyword !== 'all') {
    const paperNumberByKey = buildPaperNumberByKey(report, keyword);
    papers.sort((a, b) => {
      const an = getPaperNumber(a, paperNumberByKey, Number.POSITIVE_INFINITY);
      const bn = getPaperNumber(b, paperNumberByKey, Number.POSITIVE_INFINITY);
      if (an !== bn) return an - bn;
      return (b.quality_score || 0) - (a.quality_score || 0);
    });
  } else {
    // Default: score
    papers.sort((a, b) => {
      const diff = (b.quality_score || 0) - (a.quality_score || 0);
      if (diff !== 0) return diff;
      return getPublishedTimestamp(b) - getPublishedTimestamp(a);
    });
  }

  return papers;
}

function renderInfoCard(label, content, icon) {
  const normalized = typeof content === 'string' ? content.trim() : '';
  if (!normalized) return '';
  const safeContent = renderMarkdown(normalized);
  return `
    <div class="paper-info-item">
      <div class="paper-info-label">${icon}${label}</div>
      <div class="markdown">${safeContent}</div>
    </div>
  `;
}

function renderPapers(report) {
  const basePapers = getBasePapers(report);
  const papers = getFilteredPapers(report);
  const total = papers.length;
  const visibleTotal = Math.min(state.visibleCount, total);
  const visiblePapers = papers.slice(0, visibleTotal);
  updateControlBar({ baseTotal: basePapers.length, filteredTotal: total, visibleTotal });

  if (!total) {
    papersEl.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <p>暂无匹配结果</p>
      </div>
    `;
    return;
  }

  const keyword = state.keyword;
  const keywordSlug = keyword === 'all' ? '' : slugify(keyword);
  const paperNumberById = keyword === 'all' ? new Map() : buildPaperNumberByKey(report, keyword);

  const listHtml = visiblePapers
    .map((paper, index) => {
      const uniqueTags = sortKeywordsAlphabetically(
        Array.from(new Set(paper.matched_keywords || [])).filter(Boolean)
      );
      const tagButtons = uniqueTags
        .map(
          (tag) =>
            `<button type="button" class="tag tag-button" data-tag="${escapeHtml(
              tag
            )}" title="点击：切换领域；Shift+点击：加入搜索">${escapeHtml(tag)}</button>`
        )
        .join('');

      const authors = escapeHtml(paper.authors?.slice(0, 4).join(', ') || '');
      const abstractText = paper.summary || '';
      const abstractUrl = sanitizeUrl(paper.abstract_url || '');
      const pdfUrl = sanitizeUrl(paper.pdf_url || '');
      const codeUrl = sanitizeUrl(paper.code_url || '');

      const paperKey = getPaperKey(paper);
      const paperNumber = keyword === 'all' ? null : getPaperNumber(paper, paperNumberById, index + 1);
      const cardId =
        keyword === 'all'
          ? (() => {
              const base = paper.id || paper.arxiv_id || paper.title || Math.random().toString(36).slice(2);
              const slug = slugify(String(base));
              return `paper-${slug || Math.random().toString(36).slice(2)}`;
            })()
          : `paper-${keywordSlug}-${paperNumber}`;
      const numberBadge =
        keyword === 'all'
          ? ''
          : `<span class="paper-number">${paperNumber}</span>`;
      const contributions = Array.isArray(paper.contributions)
        ? paper.contributions.map((c) => `- ${c}`).join('\n')
        : '';
      const innovations = Array.isArray(paper.innovations)
        ? paper.innovations.map((c) => `- ${c}`).join('\n')
        : '';
      const limitations = Array.isArray(paper.limitations)
        ? paper.limitations.map((c) => `- ${c}`).join('\n')
        : '';
      const methodText = paper.methodology || '';
      const expText = paper.experiments || '';
      const dataText = paper.dataset_info || '';
      const codeText = paper.code_url ? `[代码仓库](${paper.code_url})` : '';

      const methodIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -1px; margin-right: 4px;"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>';
      const expIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -1px; margin-right: 4px;"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>';
      const contribIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -1px; margin-right: 4px;"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>';
      const dataIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -1px; margin-right: 4px;"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>';
      const limitIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -1px; margin-right: 4px;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>';

      const infoItems = [
        renderInfoCard('方法', methodText, methodIcon),
        renderInfoCard('实验', expText, expIcon),
        renderInfoCard('贡献', contributions || innovations, contribIcon),
        renderInfoCard('数据 / 代码', [dataText, codeText].filter(Boolean).join('\n\n'), dataIcon),
        renderInfoCard('局限', limitations, limitIcon),
      ].filter(Boolean);

      const tldrText = paper.tldr || '';
      const tldrHtml = tldrText
        ? `<div class="paper-tldr">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="flex-shrink: 0;">
              <circle cx="12" cy="12" r="10"></circle>
              <path d="M12 16v-4"></path>
              <path d="M12 8h.01"></path>
            </svg>
            <div class="paper-tldr-content markdown">${renderMarkdown(tldrText)}</div>
          </div>`
        : '';

      // Quality score badge
      const score = paper.quality_score || 0;
      const scoreReason = paper.score_reason || '';
      const scoreClass = score >= 8 ? 'score-high' : score >= 6 ? 'score-medium' : 'score-low';
      const scoreHtml = score > 0
        ? `<div class="paper-score ${scoreClass}" title="${escapeHtml(scoreReason)}">
            <span class="score-value">${score}</span>
            <span class="score-label">/ 10</span>
          </div>`
        : '';

      const paperTitle = escapeHtml(paper.title || 'Untitled');
      const published = formatPublishedLabel(paper, report);
      const dateDisplay = published.short ? escapeHtml(published.short) : '-';
      const relDisplay = published.relative ? `<span class="paper-date-rel">· ${escapeHtml(published.relative)}</span>` : '';

      return `
        <article class="paper-card" id="${cardId}" data-paper-key="${escapeHtml(paperKey)}">
          <div class="paper-head">
            <div class="paper-head-left">
              ${renderSourceBadges(paper)}
              <h3>${numberBadge}${paperTitle}</h3>
              ${authors ? `<div class="paper-meta">${authors}</div>` : ''}
              ${tagButtons ? `<div class="paper-tags">${tagButtons}</div>` : ''}
            </div>
            <div class="paper-head-right">
              ${scoreHtml}
              <div class="paper-date" title="${escapeHtml(published.full)}">${dateDisplay} ${relDisplay}</div>
            </div>
          </div>
          ${tldrHtml}

          <details class="paper-details">
            <summary>
              <span>展开详情</span>
              <span class="paper-details-hint">方法 / 实验 / 贡献 / 数据</span>
            </summary>
            <div class="paper-details-body">
              ${
                scoreReason
                  ? `<div class="paper-score-reason"><span class="score-reason-label">评分理由：</span>${escapeHtml(
                      scoreReason
                    )}</div>`
                  : ''
              }
              ${infoItems.length ? `<div class="paper-info-list">${infoItems.join('')}</div>` : '<p class="empty-hint">暂无更多细节。</p>'}
            </div>
          </details>

          <details class="paper-details">
            <summary>
              <span>英文摘要</span>
              <span class="paper-details-hint">原文摘要</span>
            </summary>
            <div class="paper-details-body">
              <div class="markdown paper-abstract">${renderMarkdown(abstractText || '暂无摘要。')}</div>
            </div>
          </details>

          <div class="paper-actions">
            ${abstractUrl ? `<a class="secondary" href="${escapeHtml(abstractUrl)}" target="_blank" rel="noreferrer noopener">页面</a>` : ''}
            ${pdfUrl ? `<a href="${escapeHtml(pdfUrl)}" target="_blank" rel="noreferrer noopener">PDF</a>` : ''}
            ${codeUrl ? `<a href="${escapeHtml(codeUrl)}" target="_blank" rel="noreferrer noopener">代码</a>` : ''}
          </div>
        </article>
      `;
    })
    .join('');

  const pagerHtml =
    total > visibleTotal
      ? `
        <div class="pager">
          <button class="load-more" type="button">加载更多</button>
          <span class="pager-meta">已显示 ${visibleTotal} / ${total}</span>
          <div class="pager-sentinel" aria-hidden="true"></div>
        </div>
      `
      : '';

  papersEl.innerHTML = `${listHtml}${pagerHtml}`;

  const sentinel = papersEl.querySelector('.pager-sentinel');
  if (pagerObserver) {
    pagerObserver.disconnect();
    pagerObserver = null;
  }
  if (sentinel && 'IntersectionObserver' in window) {
    pagerObserver = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry?.isIntersecting) return;
        if (state.visibleCount >= total) return;
        state.visibleCount = Math.min(state.visibleCount + PAGE_SIZE, total);
        renderPapers(state.report);
      },
      { rootMargin: '300px 0px' }
    );
    pagerObserver.observe(sentinel);
  }
}

function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeUrl(url) {
  if (!url) return '';
  const trimmed = String(url).trim();
  const lower = trimmed.toLowerCase();
  if (lower.startsWith('http://') || lower.startsWith('https://')) return trimmed;
  if (trimmed.startsWith('#')) return trimmed;
  return '';
}

function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function linkifyPaperRefsHtml(html, keyword) {
  if (!html || keyword === 'all') return html;
  const slug = slugify(keyword);
  return html.replace(
    /论文\s*([0-9]+(?:\s*(?:(?:[,，、/])|(?:和|及|与|以及|&))\s*[0-9]+)+|[0-9]+)/g,
    (match, refs) => {
      const linked = refs.replace(/[0-9]+/g, (num) => {
        return `<a href="#paper-${slug}-${num}" class="paper-ref">${num}</a>`;
      });
      return `论文${linked}`;
    }
  );
}

function renderMarkdownInline(rawText) {
  if (!rawText) return '';
  const text = String(rawText);
  const parts = text.split('`');

  function renderLinks(segment) {
    const re = /\[([^\]]+)\]\(([^)]+)\)/g;
    let out = '';
    let last = 0;
    let match;
    while ((match = re.exec(segment))) {
      out += escapeHtml(segment.slice(last, match.index));
      const label = match[1];
      const url = match[2];
      const safeUrl = sanitizeUrl(url);
      if (!safeUrl) {
        out += escapeHtml(label);
      } else {
        const extraAttrs = safeUrl.startsWith('#')
          ? ''
          : ' target="_blank" rel="noreferrer noopener"';
        out += `<a href="${escapeHtml(safeUrl)}"${extraAttrs}>${escapeHtml(label)}</a>`;
      }
      last = match.index + match[0].length;
    }
    out += escapeHtml(segment.slice(last));
    return out;
  }

  return parts
    .map((part, idx) => {
      if (idx % 2 === 1) {
        return `<code>${escapeHtml(part)}</code>`;
      }

      let html = renderLinks(part);
      html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      html = html.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
      return html;
    })
    .join('');
}

function renderMarkdown(text) {
  if (!text) return '';
  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let paragraph = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const html = paragraph.map((line) => renderMarkdownInline(line)).join('<br>');
    blocks.push(`<p>${html}</p>`);
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      continue;
    }

    if (trimmed.startsWith('```')) {
      flushParagraph();
      const codeLines = [];
      for (let j = i + 1; j < lines.length; j += 1) {
        const next = lines[j];
        if (next.trim().startsWith('```')) {
          i = j;
          break;
        }
        codeLines.push(next);
        if (j === lines.length - 1) {
          i = j;
        }
      }
      blocks.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
      continue;
    }

    const h3 = line.match(/^###\s+(.+)$/);
    if (h3) {
      flushParagraph();
      blocks.push(`<h3>${renderMarkdownInline(h3[1].trim())}</h3>`);
      continue;
    }
    const h2 = line.match(/^##\s+(.+)$/);
    if (h2) {
      flushParagraph();
      blocks.push(`<h2>${renderMarkdownInline(h2[1].trim())}</h2>`);
      continue;
    }
    const h1 = line.match(/^#\s+(.+)$/);
    if (h1) {
      flushParagraph();
      blocks.push(`<h1>${renderMarkdownInline(h1[1].trim())}</h1>`);
      continue;
    }

    if (/^\s*---+\s*$/.test(line)) {
      flushParagraph();
      blocks.push('<hr>');
      continue;
    }

    const ulItem = line.match(/^\s*[-*+]\s+(.+)$/);
    if (ulItem) {
      flushParagraph();
      const items = [ulItem[1]];
      for (let j = i + 1; j < lines.length; j += 1) {
        const next = lines[j];
        const m = next.match(/^\s*[-*+]\s+(.+)$/);
        if (!m) break;
        items.push(m[1]);
        i = j;
      }
      blocks.push(`<ul>${items.map((t) => `<li>${renderMarkdownInline(t.trim())}</li>`).join('')}</ul>`);
      continue;
    }

    const olItem = line.match(/^\s*\d+\.\s+(.+)$/);
    if (olItem) {
      flushParagraph();
      const items = [olItem[1]];
      for (let j = i + 1; j < lines.length; j += 1) {
        const next = lines[j];
        const m = next.match(/^\s*\d+\.\s+(.+)$/);
        if (!m) break;
        items.push(m[1]);
        i = j;
      }
      blocks.push(`<ol>${items.map((t) => `<li>${renderMarkdownInline(t.trim())}</li>`).join('')}</ol>`);
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph();
  return blocks.join('');
}

function highlightCardById(targetId) {
  if (!targetId) return;
  const card = document.getElementById(targetId);
  if (!card) return;
  card.classList.remove('paper-highlight');
  void card.offsetWidth;
  card.classList.add('paper-highlight');
  setTimeout(() => {
    card.classList.remove('paper-highlight');
  }, 1200);
}

function clearPeekedCard() {
  if (!peekedCardId) return;
  const el = document.getElementById(peekedCardId);
  if (el) el.classList.remove('paper-peek');
  peekedCardId = null;
}

function setPeekedCard(targetId) {
  if (!targetId || targetId === peekedCardId) return;
  clearPeekedCard();
  const el = document.getElementById(targetId);
  if (!el) return;
  el.classList.add('paper-peek');
  peekedCardId = targetId;
}

function parseKeywordPaperTarget(targetId, report) {
  const match = String(targetId || '').match(/^paper-(.+)-(\d+)$/);
  if (!match) return null;
  const slug = match[1];
  const number = Number(match[2]);
  if (!Number.isFinite(number) || number <= 0) return null;
  const keyword = report?.keywords?.find((kw) => slugify(kw) === slug);
  if (!keyword) return null;
  return { keyword, slug, number };
}

function ensurePaperVisible(targetId, parsedTarget) {
  if (document.getElementById(targetId)) return true;
  if (!state.report) return false;
  if (!parsedTarget) return false;
  if (state.keyword !== parsedTarget.keyword) return false;

  const papers = getFilteredPapers(state.report);
  const paperNumberByKey = buildPaperNumberByKey(state.report, state.keyword);
  const idx = papers.findIndex(
    (paper, index) => getPaperNumber(paper, paperNumberByKey, index + 1) === parsedTarget.number
  );
  if (idx < 0) return false;

  state.visibleCount = Math.max(state.visibleCount, idx + 1);
  renderPapers(state.report);
  return Boolean(document.getElementById(targetId));
}

function navigateToPaper(targetId, { updateUrl = false } = {}) {
  const report = state.report;
  if (!report || !targetId) return;

  const parsed = parseKeywordPaperTarget(targetId, report);

  if (parsed && state.keyword !== parsed.keyword) {
    state.keyword = parsed.keyword;
    state.sort = 'number';
    state.query = '';
    state.visibleCount = PAGE_SIZE;
    keywordSelect.value = parsed.keyword;
    searchInput.value = '';
    updateSortOptions();
    updateSummary(report);
    renderPapers(report);
  }

  if (parsed && state.query.trim()) {
    state.query = '';
    searchInput.value = '';
    state.visibleCount = PAGE_SIZE;
    renderPapers(report);
  }

  ensurePaperVisible(targetId, parsed);

  const el = document.getElementById(targetId);
  if (!el) return;

  if (updateUrl) {
    history.pushState(null, '', `#${targetId}`);
  }

  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  highlightCardById(targetId);
}

function fillKeywordOptions(report) {
  const keywords = sortKeywordsAlphabetically(report.keywords);
  keywordSelect.innerHTML = '<option value="all">全部</option>';
  keywords.forEach((kw) => {
    const option = document.createElement('option');
    option.value = kw;
    option.textContent = kw;
    keywordSelect.appendChild(option);
  });
}

async function loadReportForDate(date, { resetFilters = true } = {}) {
  const token = (reportLoadToken += 1);
  clearPeekedCard();
  showSkeletonSummary();
  showSkeletonTrends();
  showSkeletonPapers();
  resultCountEl.textContent = '-';
  clearFiltersBtn.disabled = true;

  try {
    const report = await fetchReport(date);
    if (token !== reportLoadToken) return;

    state.report = report;
    if (resetFilters) {
      state.keyword = 'all';
      state.query = '';
      state.sort = 'score';
      state.visibleCount = PAGE_SIZE;
      keywordSelect.value = 'all';
      searchInput.value = '';
    }

    updateStats(report);
    updateTrends(report);
    fillKeywordOptions(report);
    updateSortOptions();
    updateSummary(report);
    renderPapers(report);

    const targetId = window.location.hash.replace('#', '');
    if (targetId) {
      navigateToPaper(targetId);
    }
  } catch (err) {
    if (token !== reportLoadToken) return;
    const message = err instanceof Error ? err.message : '加载失败，请稍后再试。';
    showErrorState(summaryEl, '加载失败', message, () => loadReportForDate(date, { resetFilters }));
    showErrorState(trendsEl, '加载失败', message, () => loadReportForDate(date, { resetFilters }));
    showErrorState(papersEl, '加载失败', message, () => loadReportForDate(date, { resetFilters }));
    resultCountEl.textContent = '-';
  }
}

async function init() {
  const token = (datesLoadToken += 1);
  clearPeekedCard();
  showSkeletonSummary();
  showSkeletonTrends();
  showSkeletonPapers();
  resultCountEl.textContent = '-';
  clearFiltersBtn.disabled = true;

  try {
    const dates = await fetchDates();
    if (token !== datesLoadToken) return;

    if (!dates.length) {
      showErrorState(summaryEl, '暂无报告数据', '尚未生成任何日报。');
      trendsEl.innerHTML = '';
      papersEl.innerHTML = '';
      return;
    }

    dateSelect.innerHTML = '';
    dates.forEach((date) => {
      const option = document.createElement('option');
      option.value = date;
      option.textContent = date;
      dateSelect.appendChild(option);
    });
    dateSelect.value = dates[0];
    await loadReportForDate(dates[0], { resetFilters: true });
  } catch (err) {
    if (token !== datesLoadToken) return;
    const message = err instanceof Error ? err.message : '加载失败，请稍后再试。';
    showErrorState(summaryEl, '无法加载', message, () => init());
    showErrorState(trendsEl, '无法加载', message, () => init());
    showErrorState(papersEl, '无法加载', message, () => init());
    resultCountEl.textContent = '-';
  }
}

dateSelect.addEventListener('change', async (event) => {
  const date = event.target.value;
  await loadReportForDate(date, { resetFilters: true });
});

keywordSelect.addEventListener('change', (event) => {
  state.keyword = event.target.value;
  state.sort = state.keyword === 'all' ? 'score' : 'number';
  state.visibleCount = PAGE_SIZE;
  updateSortOptions();
  updateSummary(state.report);
  renderPapers(state.report);
});

trendsEl.addEventListener('click', (event) => {
  const btn = event.target.closest('.trend-button');
  if (!btn) return;
  const keyword = btn.getAttribute('data-keyword');
  if (!keyword) return;
  if (!state.report?.keywords?.includes(keyword)) return;
  state.keyword = keyword;
  state.sort = 'number';
  state.visibleCount = PAGE_SIZE;
  keywordSelect.value = keyword;
  updateSortOptions();
  updateSummary(state.report);
  renderPapers(state.report);
});

const debouncedSearch = debounce(() => {
  renderPapers(state.report);
}, 200);

searchInput.addEventListener('input', (event) => {
  state.query = event.target.value;
  state.visibleCount = PAGE_SIZE;
  debouncedSearch();
});

sortSelect.addEventListener('change', (event) => {
  state.sort = event.target.value;
  state.visibleCount = PAGE_SIZE;
  renderPapers(state.report);
});

clearFiltersBtn.addEventListener('click', () => {
  state.keyword = 'all';
  state.query = '';
  state.sort = 'score';
  state.visibleCount = PAGE_SIZE;
  keywordSelect.value = 'all';
  searchInput.value = '';
  updateSortOptions();
  updateSummary(state.report);
  renderPapers(state.report);
});

summaryEl.addEventListener('click', (event) => {
  const link = event.target.closest('a.paper-ref[href^="#paper-"]');
  if (!link) return;
  event.preventDefault();
  const targetId = link.getAttribute('href').slice(1);
  navigateToPaper(targetId, { updateUrl: true });
});

window.addEventListener('hashchange', () => {
  const targetId = window.location.hash.replace('#', '');
  navigateToPaper(targetId);
});

window.addEventListener('popstate', () => {
  const targetId = window.location.hash.replace('#', '');
  if (targetId) {
    navigateToPaper(targetId);
  } else {
    clearPeekedCard();
  }
});

summaryEl.addEventListener('mouseover', (event) => {
  const link = event.target.closest('a.paper-ref[href^="#paper-"]');
  if (!link) return;
  const targetId = link.getAttribute('href').slice(1);
  setPeekedCard(targetId);
});

summaryEl.addEventListener('mouseout', (event) => {
  const link = event.target.closest('a.paper-ref[href^="#paper-"]');
  if (!link) return;
  const related = event.relatedTarget;
  if (related && link.contains(related)) return;
  clearPeekedCard();
});

summaryEl.addEventListener('focusin', (event) => {
  const link = event.target.closest('a.paper-ref[href^="#paper-"]');
  if (!link) return;
  const targetId = link.getAttribute('href').slice(1);
  setPeekedCard(targetId);
});

summaryEl.addEventListener('focusout', (event) => {
  const link = event.target.closest('a.paper-ref[href^="#paper-"]');
  if (!link) return;
  clearPeekedCard();
});

papersEl.addEventListener('click', (event) => {
  const loadMore = event.target.closest('.load-more');
  if (loadMore) {
    state.visibleCount = state.visibleCount + PAGE_SIZE;
    renderPapers(state.report);
    return;
  }

  const tagBtn = event.target.closest('.tag-button');
  if (!tagBtn) return;
  const tag = tagBtn.getAttribute('data-tag');
  if (!tag) return;

  if (event.shiftKey) {
    const nextQuery = state.query.trim() ? `${state.query.trim()} ${tag}` : tag;
    state.query = nextQuery;
    state.visibleCount = PAGE_SIZE;
    searchInput.value = nextQuery;
    renderPapers(state.report);
    return;
  }

  if (state.report?.keywords?.includes(tag)) {
    state.keyword = tag;
    state.sort = 'number';
    state.visibleCount = PAGE_SIZE;
    keywordSelect.value = tag;
    updateSortOptions();
    updateSummary(state.report);
    renderPapers(state.report);
    return;
  }

  const nextQuery = state.query.trim() ? `${state.query.trim()} ${tag}` : tag;
  state.query = nextQuery;
  state.visibleCount = PAGE_SIZE;
  searchInput.value = nextQuery;
  renderPapers(state.report);
});

init();
