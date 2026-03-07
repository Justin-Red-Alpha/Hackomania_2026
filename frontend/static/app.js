/* ── FactGuard frontend app ── */
'use strict';

// ─────────────────────────────────────────────
// DOM refs
// ─────────────────────────────────────────────
const form           = document.getElementById('analyse-form');
const submitBtn      = document.getElementById('submit-btn');
const btnLabel       = submitBtn.querySelector('.btn-label');
const btnSpinner     = submitBtn.querySelector('.btn-spinner');
const errorBanner    = document.getElementById('error-banner');
const errorMessage   = document.getElementById('error-message');
const pipelineSteps  = document.getElementById('pipeline-steps');
const resultsSection = document.getElementById('results-section');

// ─────────────────────────────────────────────
// Input mode tabs (URL / File)
// ─────────────────────────────────────────────
let activeTab = 'url';

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    activeTab = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(b => {
      b.classList.toggle('active', b === btn);
      b.setAttribute('aria-selected', String(b === btn));
    });
    document.getElementById('tab-url').hidden  = activeTab !== 'url';
    document.getElementById('tab-file').hidden = activeTab !== 'file';
    document.getElementById('tab-text').hidden = activeTab !== 'text';
  });
});

// ─────────────────────────────────────────────
// File drag-and-drop
// ─────────────────────────────────────────────
const fileInput   = document.getElementById('articleFile');
const dropZone    = document.getElementById('file-drop-zone');
const fileSelected = document.getElementById('file-selected');
const fileNameEl  = document.getElementById('file-name');
const fileClear   = document.getElementById('file-clear');
const fileDropInner = document.getElementById('file-drop-inner');

dropZone.addEventListener('click', (e) => {
  if (!e.target.closest('.file-clear')) fileInput.click();
});

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) setSelectedFile(file);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) setSelectedFile(fileInput.files[0]);
});

fileClear.addEventListener('click', (e) => {
  e.stopPropagation();
  clearFile();
});

function setSelectedFile(file) {
  fileNameEl.textContent = file.name;
  fileDropInner.hidden = true;
  fileSelected.hidden  = false;
}

function clearFile() {
  fileInput.value = '';
  fileDropInner.hidden = false;
  fileSelected.hidden  = true;
}

// ─────────────────────────────────────────────
// Form submission
// ─────────────────────────────────────────────
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError();
  clearResults();
  setLoading(true);
  showPipeline();

  try {
    const result = activeTab === 'file' ? await submitFile()
                 : activeTab === 'text' ? await submitText()
                 : await submitUrl();
    showResults(result);
  } catch (err) {
    showError(err.message || 'An unexpected error occurred.');
  } finally {
    setLoading(false);
    pipelineSteps.hidden = true;
  }
});

// ─────────────────────────────────────────────
// API calls
// ─────────────────────────────────────────────
async function submitUrl() {
  const url = document.getElementById('articleUrl').value.trim();
  if (!url) throw new Error('Please enter an article URL.');
  try { new URL(url); } catch {
    throw new Error('Please enter a valid URL (must start with https:// or http://).');
  }
  return callApi('/api/v1/analyse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ articleUrl: url }),
  });
}

async function submitText() {
  const text = document.getElementById('articleText').value.trim();
  if (!text) throw new Error('Please paste some article text.');
  return callApi('/api/v1/analyse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ articleText: text }),
  });
}

async function submitFile() {
  const file = fileInput.files[0];
  if (!file) throw new Error('Please select a file to upload.');
  const fd = new FormData();
  fd.append('articleFile', file);
  return callApi('/api/v1/analyse', { method: 'POST', body: fd });
}

async function callApi(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    let msg;
    try { msg = JSON.parse(text).detail || text; } catch { msg = text; }
    throw new Error(`Server error ${res.status}: ${msg}`);
  }
  return res.json();
}

// ─────────────────────────────────────────────
// Pipeline step animation
// ─────────────────────────────────────────────
const STEPS = ['step-extract', 'step-claims', 'step-search', 'step-judge'];
let stepTimer = null;

function showPipeline() {
  pipelineSteps.hidden = false;
  STEPS.forEach(id => document.getElementById(id).classList.remove('active', 'done'));

  let current = 0;
  activateStep(current);

  stepTimer = setInterval(() => {
    document.getElementById(STEPS[current]).classList.replace('active', 'done');
    current++;
    if (current < STEPS.length) activateStep(current);
    else clearInterval(stepTimer);
  }, 2800);
}

function activateStep(idx) {
  document.getElementById(STEPS[idx]).classList.add('active');
}

// ─────────────────────────────────────────────
// Render results  (uses real schema field names)
// Response shape:
//   { content, publisher_credibility, content_credibility, claims[] }
// ─────────────────────────────────────────────
function showResults(data) {
  resultsSection.hidden = false;
  resultsSection.classList.add('fade-in');

  renderContentMeta(data.content, data.claims || []);
  renderFakenessGauge(data.content_credibility);
  renderContentCredibility(data.content_credibility);
  renderPublisherCredibility(data.publisher_credibility);
  renderClaims(data.claims || []);

  setTimeout(() => resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);
}

// ── Content metadata ──
function renderContentMeta(content, claims) {
  if (!content) return;
  const el = document.getElementById('content-meta');

  const chips = [
    content.publisher  && `<span class="meta-chip"><strong>Publisher</strong> ${esc(content.publisher)}</span>`,
    content.author     && `<span class="meta-chip"><strong>Author</strong> ${esc(content.author)}</span>`,
    content.date       && `<span class="meta-chip"><strong>Date</strong> ${esc(content.date)}</span>`,
    content.section    && `<span class="meta-chip"><strong>Section</strong> ${esc(content.section)}</span>`,
    content.is_opinion != null && `<span class="meta-chip"><strong>Opinion</strong> ${content.is_opinion ? 'Yes' : 'No'}</span>`,
  ].filter(Boolean).join('');

  const bodyHTML = content.body
    ? renderArticleWithHighlights(content.body, claims)
    : '';

  el.innerHTML = `
    ${content.title
      ? `<a class="article-title-link" href="${esc(content.url)}" target="_blank" rel="noopener">${esc(content.title)}</a>`
      : `<a class="article-title-link" href="${esc(content.url)}" target="_blank" rel="noopener">${esc(content.url)}</a>`
    }
    ${chips ? `<div class="meta-row">${chips}</div>` : ''}
    ${bodyHTML}
  `;
}

// ── Article body with per-claim colour highlights ──
const HL_COLOURS = ['#58a6ff','#f85149','#e3722e','#d29922','#3fb950','#bc8cff'];

function renderArticleWithHighlights(body, claims) {
  // Build [{start, end, idx}] for each claim extract found in body
  const hits = [];
  claims.forEach((claim, idx) => {
    if (!claim.extract) return;
    const raw = claim.extract.replace(/^[\u201c\u2018"']/u, '').replace(/[\u201d\u2019"']$/u, '').trim();
    const pos = body.indexOf(raw);
    if (pos !== -1) hits.push({ start: pos, end: pos + raw.length, idx });
  });
  hits.sort((a, b) => a.start - b.start);

  // Build HTML segments
  let html = '';
  let cursor = 0;
  for (const h of hits) {
    if (h.start < cursor) continue; // skip overlaps
    html += esc(body.slice(cursor, h.start));
    html += `<mark class="claim-hl claim-hl-${h.idx + 1}" onclick="scrollToClaim(${h.idx})" title="Claim #${h.idx + 1}">${esc(body.slice(h.start, h.end))}</mark>`;
    cursor = h.end;
  }
  html += esc(body.slice(cursor));

  // Build legend
  const legend = hits.map(h =>
    `<span class="legend-chip" onclick="scrollToClaim(${h.idx})">
      <span class="legend-dot" style="background:${HL_COLOURS[h.idx % HL_COLOURS.length]}"></span>
      Claim ${h.idx + 1}
    </span>`
  ).join('');

  return `<div class="article-body">${html}</div>
    ${legend ? `<div class="highlight-legend">${legend}</div>` : ''}`;
}

// ── Degree of Fakeness gauge ──
const ARC = 283;

function renderFakenessGauge(cred) {
  if (!cred) return;
  const score    = cred.score ?? 0;
  const fakeness = 100 - score;
  const dash     = ((fakeness / 100) * ARC).toFixed(1);

  const gaugeFg  = document.getElementById('gauge-fg');
  const gaugeVal = document.getElementById('gauge-value');
  const gaugeRat = document.getElementById('gauge-rating');

  const colour = scoreColour(score);
  gaugeFg.style.stroke = colour;
  setTimeout(() => { gaugeFg.style.strokeDasharray = `${dash} ${ARC}`; }, 80);

  gaugeVal.textContent = `${fakeness}%`;
  gaugeVal.style.color = colour;
  gaugeRat.textContent = verdictLabel(cred.rating);
}

// ── Content credibility panel ──
function renderContentCredibility(cred) {
  if (!cred) return;
  const el = document.getElementById('content-cred-content');

  const score      = cred.score ?? 0;
  const barColour  = scoreColour(score);
  const ratingCls  = ratingToClass(cred.rating);
  const wq         = cred.writing_quality || {};

  const qualityChips = Object.entries(wq).map(([k, v]) => {
    const label  = camelToLabel(k);
    const isBad  = Boolean(v) && k !== 'named_sources';
    const isGood = k === 'named_sources' && Boolean(v);
    return `<span class="quality-chip${isBad ? ' flag' : isGood ? ' ok' : ''}">${label}: ${formatBool(v)}</span>`;
  }).join('');

  el.innerHTML = `
    <div class="score-bar-wrap">
      <div class="score-bar-label"><span>Score</span><span>${score}/100</span></div>
      <div class="score-bar-track">
        <div class="score-bar-fill" style="background:${barColour}" data-width="${score}"></div>
      </div>
    </div>
    <div>
      <span class="rating-badge ${ratingCls}">${verdictLabel(cred.rating)}</span>
      ${cred.government_source_only_flag
        ? '<div class="govt-only-flag" style="margin-top:.5rem"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> Govt sources only</div>'
        : ''}
    </div>
    ${cred.summary ? `<p class="summary-text">${esc(cred.summary)}</p>` : ''}
    <div style="display:flex;gap:.4rem;flex-wrap:wrap">${claimCountChips(cred)}</div>
    ${qualityChips ? `<div class="quality-grid">${qualityChips}</div>` : ''}
  `;
  animateBars(el);
}

function claimCountChips(cred) {
  return [
    ['True',         cred.claims_true,        'v-true'],
    ['Mostly True',  cred.claims_mostly_true,  'v-mostly-true'],
    ['Misleading',   cred.claims_misleading,   'v-misleading'],
    ['Unverified',   cred.claims_unverified,   'v-unverified'],
    ['Mostly False', cred.claims_mostly_false, 'v-mostly-false'],
    ['False',        cred.claims_false,        'v-false'],
  ]
    .filter(([, v]) => v != null && v > 0)
    .map(([label, v, cls]) =>
      `<span class="rating-badge ${cls}" style="font-size:.7rem">${v} ${label}</span>`)
    .join('');
}

// ── Publisher credibility panel ──
function renderPublisherCredibility(pub) {
  if (!pub) return;
  const el = document.getElementById('publisher-cred-content');

  const score      = pub.score ?? 0;
  const barColour  = scoreColour(score);
  const ratingCls  = publisherRatingClass(pub.rating);
  const biasCls    = biasClass(pub.bias);
  const issuesList = (pub.known_issues || []).map(i => `<li>${esc(i)}</li>`).join('');
  const factRatings = (pub.fact_checker_ratings || []).map(r => `<span class="meta-chip">${esc(r)}</span>`).join('');

  el.innerHTML = `
    <div class="score-bar-wrap">
      <div class="score-bar-label"><span>Score</span><span>${score}/100</span></div>
      <div class="score-bar-track">
        <div class="score-bar-fill" style="background:${barColour}" data-width="${score}"></div>
      </div>
    </div>
    <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center">
      <span class="rating-badge ${ratingCls}">${esc(pub.rating || 'unknown')}</span>
      ${pub.bias ? `<span class="meta-chip ${biasCls}" style="border:none">Bias: ${esc(pub.bias)}</span>` : ''}
    </div>
    ${pub.summary ? `<p class="summary-text">${esc(pub.summary)}</p>` : ''}
    ${issuesList ? `<ul class="known-issues">${issuesList}</ul>` : ''}
    ${factRatings ? `<div class="meta-row">${factRatings}</div>` : ''}
  `;
  animateBars(el);
}

// ── Claims list ──
function renderClaims(claims) {
  const list    = document.getElementById('claims-list');
  const summary = document.getElementById('claims-summary');
  summary.textContent = `${claims.length} claim${claims.length !== 1 ? 's' : ''}`;

  if (!claims.length) {
    list.innerHTML = '<p style="color:var(--text-muted);font-size:.85rem">No individual claims were extracted.</p>';
    return;
  }

  list.innerHTML = claims.map((claim, i) => buildClaimHTML(claim, i)).join('');
}

function buildClaimHTML(claim, i) {
  const vClass     = verdictClass(claim.verdict);
  const govtFlag   = claim.government_source_only
    ? `<div class="govt-only-flag"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> Backed by government sources only</div>`
    : '';

  const sourcesHTML = buildSourcesHTML(claim.sources || []);
  const evidenceHTML = buildEvidenceHTML(claim.evidence || []);

  return `
    <div class="claim-item" id="claim-${i}">
      <div class="claim-header" onclick="toggleClaim(${i})">
        <span class="claim-number">#${claim.claim_id ?? i + 1}</span>
        <span class="claim-summary-text">${esc(claim.claim_summary || '')}</span>
        <span class="claim-verdict-badge ${vClass}">${verdictLabel(claim.verdict)}</span>
        <svg class="claim-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </div>
      <div class="claim-body" hidden>
        ${claim.extract
          ? `<blockquote class="claim-extract">${esc(claim.extract)}</blockquote>`
          : ''}
        ${claim.overall_reason
          ? `<p class="claim-reason">${esc(claim.overall_reason)}</p>`
          : ''}
        ${govtFlag}
        ${evidenceHTML}
        ${sourcesHTML}
      </div>
    </div>
  `;
}

// Evidence snippets
function buildEvidenceHTML(evidence) {
  if (!evidence.length) return '';
  const rows = evidence.map(e => {
    const supportCls  = e.supports_claim ? 'ev-support' : 'ev-contradict';
    const supportIcon = e.supports_claim
      ? '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
      : '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

    return `
      <div class="evidence-item ${supportCls}">
        <div class="evidence-header">
          <span class="evidence-badge">${supportIcon} ${e.supports_claim ? 'Supports' : 'Contradicts'}</span>
          <a href="${esc(e.source_url || '#')}" target="_blank" rel="noopener" class="evidence-source">${esc(e.source_name || e.source_url || 'Source')}</a>
        </div>
        ${e.snippet ? `<blockquote class="evidence-snippet">${esc(e.snippet)}</blockquote>` : ''}
        ${e.judgement_reason ? `<p class="evidence-reason">${esc(e.judgement_reason)}</p>` : ''}
      </div>
    `;
  }).join('');

  return `
    <div>
      <div class="claim-sources-label">Evidence</div>
      <div class="evidence-list">${rows}</div>
    </div>
  `;
}

// Source links
function buildSourcesHTML(sources) {
  if (!sources.length) return '';
  const rows = sources.map(s => {
    const independent = s.is_independent !== false;
    const dotColour   = independent ? 'var(--green)' : 'var(--yellow)';
    return `
      <div class="source-item">
        <span class="independent-dot" style="background:${dotColour}"
              title="${independent ? 'Independent' : 'Related party'}"></span>
        <a href="${esc(s.url || '#')}" target="_blank" rel="noopener">${esc(s.name || s.url || 'Source')}</a>
        ${s.type ? `<span class="source-type">${esc(s.type)}</span>` : ''}
      </div>
    `;
  }).join('');

  return `
    <div>
      <div class="claim-sources-label">Sources</div>
      <div class="claim-sources">${rows}</div>
    </div>
  `;
}

// ─────────────────────────────────────────────
// Claim accordion toggle
// ─────────────────────────────────────────────
window.toggleClaim = function(i) {
  const item = document.getElementById(`claim-${i}`);
  const body = item.querySelector('.claim-body');
  const isOpen = !body.hidden;
  body.hidden = isOpen;
  item.classList.toggle('open', !isOpen);
};

window.scrollToClaim = function(i) {
  const item = document.getElementById(`claim-${i}`);
  if (!item) return;
  const body = item.querySelector('.claim-body');
  if (body.hidden) { body.hidden = false; item.classList.add('open'); }
  item.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

// ─────────────────────────────────────────────
// Animate score bars
// ─────────────────────────────────────────────
function animateBars(container) {
  requestAnimationFrame(() => {
    container.querySelectorAll('.score-bar-fill').forEach(bar => {
      bar.style.width = `${bar.dataset.width || 0}%`;
    });
  });
}

// ─────────────────────────────────────────────
// UI helpers
// ─────────────────────────────────────────────
function setLoading(on) {
  submitBtn.disabled = on;
  btnLabel.hidden    = on;
  btnSpinner.hidden  = !on;
}

function showError(msg) {
  errorMessage.textContent = msg;
  errorBanner.hidden = false;
}

function hideError() {
  errorBanner.hidden = true;
}

function clearResults() {
  resultsSection.hidden = true;
  resultsSection.classList.remove('fade-in');
  ['content-meta', 'content-cred-content', 'publisher-cred-content', 'claims-list'].forEach(id => {
    document.getElementById(id).innerHTML = '';
  });
  document.getElementById('claims-summary').textContent = '';
  document.getElementById('gauge-value').textContent    = '—';
  document.getElementById('gauge-rating').textContent   = 'Pending';
  const fg = document.getElementById('gauge-fg');
  fg.style.strokeDasharray = '0 283';
  fg.style.stroke = 'var(--green)';
  if (stepTimer) clearInterval(stepTimer);
}

// ─────────────────────────────────────────────
// Colour / label helpers
// ─────────────────────────────────────────────
function scoreColour(score) {
  if (score >= 80) return '#3fb950';
  if (score >= 60) return '#85d996';
  if (score >= 40) return '#d29922';
  if (score >= 20) return '#e3722e';
  return '#f85149';
}

function verdictClass(v) {
  const map = {
    true:          'v-true',
    mostly_true:   'v-mostly-true',
    misleading:    'v-misleading',
    unverified:    'v-unverified',
    mostly_false:  'v-mostly-false',
    false:         'v-false',
  };
  return map[(v || '').toLowerCase()] || 'v-unverified';
}

function verdictLabel(v) {
  const map = {
    true:            'True',
    mostly_true:     'Mostly True',
    misleading:      'Misleading',
    unverified:      'Unverified',
    mostly_false:    'Mostly False',
    false:           'False',
    credible:        'Credible',
    mostly_credible: 'Mostly Credible',
    mixed:           'Mixed',
    low_credibility: 'Low Credibility',
    not_credible:    'Not Credible',
  };
  return map[(v || '').toLowerCase()] || v || '—';
}

function ratingToClass(v) {
  const map = {
    credible:        'v-true',
    mostly_credible: 'v-mostly-true',
    mixed:           'v-misleading',
    low_credibility: 'v-mostly-false',
    not_credible:    'v-false',
  };
  return map[(v || '').toLowerCase()] || 'v-unverified';
}

function publisherRatingClass(v) {
  const map = {
    highly_credible: 'r-highly-credible',
    credible:        'r-credible',
    mixed:           'r-mixed',
    low_credibility: 'r-low-credibility',
    not_credible:    'r-not-credible',
  };
  return map[(v || '').toLowerCase()] || '';
}

function biasClass(v) {
  const map = {
    far_left:     'bias-far-left',
    left:         'bias-left',
    center_left:  'bias-center-left',
    center:       'bias-center',
    center_right: 'bias-center-right',
    right:        'bias-right',
    far_right:    'bias-far-right',
  };
  return map[(v || '').toLowerCase().replace(/-/g, '_')] || '';
}

function camelToLabel(str) {
  return str.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatBool(v) {
  if (v === true  || v === 'true')  return 'Yes';
  if (v === false || v === 'false') return 'No';
  return String(v);
}

function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ─────────────────────────────────────────────
// Demo mode  (?demo=1)
// ─────────────────────────────────────────────
if (new URLSearchParams(location.search).get('demo') === '1') {
  loadDemo();
}

function loadDemo() {
  showResults({
    content: {
      url:        'https://example.com/health/coffee-longevity',
      title:      'Drink 5 Cups of Coffee a Day and Live 15 Years Longer, Scientists Say',
      publisher:  'Daily Health Buzz',
      date:       '2026-03-01',
      author:     'Sarah Wellman',
      section:    'Health',
      is_opinion: false,
      body:
`Researchers from Boston University published findings this week claiming that drinking five cups of coffee per day can extend a person's lifespan by up to 15 years. The study, which tracked just 500 volunteers over six months, has quickly gone viral on social media, with health influencers urging their followers to increase their daily coffee intake immediately.

"Coffee completely prevents Alzheimer's disease in regular drinkers," the lead researcher was quoted as saying at a press conference on Monday. The sweeping claim has been welcomed by millions of coffee lovers worldwide, though several independent neurologists have expressed significant scepticism.

Adding to the excitement, the article states that the World Health Organization recommends coffee as a daily health supplement — a statement that does not appear in any official WHO publication or guideline.

The piece further argues that caffeine boosts metabolism by up to 50%, leading to significant weight loss in regular consumers, a figure that sharply contradicts the findings of most peer-reviewed nutrition research, which places the effect far closer to 3–11%.

On a more credible note, it is well established that a single cup of coffee contains more antioxidants than a serving of blueberries, a fact that has been cited in legitimate nutritional literature.

Coffee is indeed one of the most widely consumed beverages on earth. Over 2 billion cups of coffee are consumed globally every day, making it a cornerstone of daily life for people across every continent.`,
    },
    publisher_credibility: {
      score:  34,
      rating: 'low_credibility',
      summary: 'Daily Health Buzz frequently publishes exaggerated health claims with little citation of peer-reviewed sources. Multiple fact-checkers have flagged past articles.',
      bias:   'center',
      known_issues: [
        'History of sensationalised health headlines',
        'Rarely links to primary research',
        'Two prior corrections issued for false medical claims',
      ],
      fact_checker_ratings: ['Snopes: False (3 articles)', 'Full Fact: Misleading'],
    },
    content_credibility: {
      score:  28,
      rating: 'not_credible',
      summary: 'The article makes two well-supported claims but surrounds them with three false or misleading statements that dramatically overstate coffee\'s health effects.',
      total_claims_found: 5,
      claims_true:        1,
      claims_mostly_true: 1,
      claims_misleading:  1,
      claims_unverified:  0,
      claims_mostly_false: 0,
      claims_false:        2,
      government_source_only_flag: false,
      writing_quality: {
        sensationalism:     true,
        named_sources:      false,
        anonymous_sources:  true,
        emotional_language: true,
        hedging_language:   false,
      },
    },
    claims: [
      {
        claim_id: 1,
        claim_summary: 'Drinking 5 cups of coffee daily extends lifespan by up to 15 years.',
        extract: 'drinking five cups of coffee per day can extend a person\'s lifespan by up to 15 years',
        verdict: 'false',
        overall_reason: 'No peer-reviewed study supports a 15-year lifespan extension from coffee. Observational studies show modest associations with longevity, not causation. A 6-month study of 500 people cannot establish such a conclusion.',
        government_source_only: false,
        sources: [
          { name: 'Harvard T.H. Chan School of Public Health', url: 'https://www.hsph.harvard.edu', type: 'academic', is_independent: true },
          { name: 'BMJ — Coffee and Mortality Meta-analysis',  url: 'https://www.bmj.com',          type: 'journal',  is_independent: true },
        ],
        evidence: [
          {
            source_id: '1a', source_name: 'Harvard Public Health', source_url: 'https://www.hsph.harvard.edu',
            snippet: 'Observational studies suggest moderate coffee consumption is associated with a small reduction in all-cause mortality, but causation has not been established.',
            supports_claim: false,
            judgement_reason: 'A "small association" is a far cry from extending life by 15 years.',
          },
          {
            source_id: '1b', source_name: 'BMJ Meta-analysis', source_url: 'https://www.bmj.com',
            snippet: 'The largest meta-analysis to date found no study that reported a lifespan benefit of more than 1–2 years from any dietary habit.',
            supports_claim: false,
            judgement_reason: 'Directly refutes the 15-year claim.',
          },
        ],
      },
      {
        claim_id: 2,
        claim_summary: 'Coffee completely prevents Alzheimer\'s disease in regular drinkers.',
        extract: 'Coffee completely prevents Alzheimer\'s disease in regular drinkers',
        verdict: 'false',
        overall_reason: 'Some studies suggest caffeine may reduce the risk of Alzheimer\'s, but no evidence supports complete prevention. The word "completely" makes this claim false.',
        government_source_only: false,
        sources: [
          { name: 'Alzheimer\'s Society',     url: 'https://www.alzheimers.org.uk', type: 'charity',  is_independent: true },
          { name: 'Journal of Alzheimer\'s Disease', url: 'https://www.j-alz.com', type: 'journal',  is_independent: true },
        ],
        evidence: [
          {
            source_id: '2a', source_name: "Alzheimer's Society", source_url: 'https://www.alzheimers.org.uk',
            snippet: 'While some studies suggest a modest protective effect of caffeine, the evidence is inconsistent and far from conclusive. Coffee cannot be considered preventive.',
            supports_claim: false,
            judgement_reason: 'Leading Alzheimer\'s charity explicitly contradicts the "completely prevents" framing.',
          },
        ],
      },
      {
        claim_id: 3,
        claim_summary: 'The WHO recommends coffee as a daily health supplement.',
        extract: 'the World Health Organization recommends coffee as a daily health supplement',
        verdict: 'false',
        overall_reason: 'The WHO has never issued guidance recommending coffee as a health supplement. In 2016 the WHO reclassified coffee as "not classifiable as a carcinogen" but issued no positive recommendation.',
        government_source_only: true,
        sources: [
          { name: 'WHO Official Guidelines', url: 'https://www.who.int', type: 'government', is_independent: true },
        ],
        evidence: [
          {
            source_id: '3a', source_name: 'WHO Official Guidelines', source_url: 'https://www.who.int',
            snippet: 'The World Health Organization has not issued any recommendation for coffee as a dietary supplement or health intervention.',
            supports_claim: false,
            judgement_reason: 'The WHO directly contradicts this claim.',
          },
        ],
      },
      {
        claim_id: 4,
        claim_summary: 'Caffeine boosts metabolism by up to 50%, causing significant weight loss.',
        extract: 'caffeine boosts metabolism by up to 50%, leading to significant weight loss in regular consumers',
        verdict: 'misleading',
        overall_reason: 'Caffeine does temporarily boost metabolic rate, but the effect is 3–11%, not 50%. Any associated weight loss is modest and diminishes with habitual use.',
        government_source_only: false,
        sources: [
          { name: 'American Journal of Clinical Nutrition', url: 'https://academic.oup.com/ajcn', type: 'journal', is_independent: true },
          { name: 'Mayo Clinic',                            url: 'https://www.mayoclinic.org',    type: 'medical', is_independent: true },
        ],
        evidence: [
          {
            source_id: '4a', source_name: 'American Journal of Clinical Nutrition', source_url: 'https://academic.oup.com/ajcn',
            snippet: 'Caffeine increases resting metabolic rate by approximately 3–11% in a dose-dependent manner.',
            supports_claim: false,
            judgement_reason: 'The real figure is far lower than the 50% claimed in the article.',
          },
          {
            source_id: '4b', source_name: 'Mayo Clinic', source_url: 'https://www.mayoclinic.org',
            snippet: 'Caffeine may slightly boost metabolism and fat burning, but any effect is small and short-lived in regular coffee drinkers as tolerance develops.',
            supports_claim: false,
            judgement_reason: 'Contradicts the "significant weight loss" framing.',
          },
        ],
      },
      {
        claim_id: 5,
        claim_summary: 'One cup of coffee contains more antioxidants than a serving of blueberries.',
        extract: 'a single cup of coffee contains more antioxidants than a serving of blueberries',
        verdict: 'mostly_true',
        overall_reason: 'Multiple nutritional studies confirm coffee is one of the largest single sources of antioxidants in the Western diet. The comparison to blueberries is broadly supported, though it depends on the measurement method.',
        government_source_only: false,
        sources: [
          { name: 'Journal of Nutrition — Antioxidant Study', url: 'https://academic.oup.com/jn', type: 'journal', is_independent: true },
        ],
        evidence: [
          {
            source_id: '5a', source_name: 'Journal of Nutrition', source_url: 'https://academic.oup.com/jn',
            snippet: 'Brewed coffee was found to contain higher total antioxidant activity per serving than many fruits and vegetables, including blueberries, when measured by FRAP assay.',
            supports_claim: true,
            judgement_reason: 'Directly supports the antioxidant comparison.',
          },
        ],
      },
    ],
  });
}
