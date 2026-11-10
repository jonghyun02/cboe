/* CBoE 검수 화면 (deck 3.1~3.5, 5.10). Vanilla JS, no build step.
   Every change is POSTed; list, evidence, timeline and the video overlay are all
   re-rendered from the state the server answers with (placement recomputed). */
'use strict';
(() => {
  // ------------------------------------------------------------------ helpers
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (x, a, b) => Math.min(Math.max(x, a), b);
  const num = (x, d = 0) => (typeof x === 'number' && Number.isFinite(x) ? x : d);
  const cssEsc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'));
  const cut = (s, n = 34) => { s = String(s ?? ''); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };
  const unbracket = (s) => String(s ?? '').trim().replace(/^\[\s*/, '').replace(/\s*\]$/, '');
  /** Limits and notation of the guide (16 units, [ ], ♪ ...). They are written in one place only,
      cboe/style.py: the server sends them with every state and the page keeps no copy. */
  const STYLE = () => (S.st && S.st.style) || { formats: [] };
  /** "[글]" — a sound or music label; an old '♬ 글' or a label that already has brackets too. */
  const bracket = (s) => { const t = unbracket(String(s ?? '').trim().replace(/^[♬♫♩]\s*/, '')); return t ? `${STYLE().open}${t}${STYLE().close}` : ''; };
  /** II.2 width of a short text (Hangul and CJK 1, the rest 0.5). Only for the name preview below;
      every cell text and every check comes from the server. */
  const wide = (ch) => { const c = ch.codePointAt(0); return (c >= 0x1100 && c <= 0x11ff) || (c >= 0x3130 && c <= 0x318f) || (c >= 0xac00 && c <= 0xd7a3) || (c >= 0x4e00 && c <= 0x9fff); };
  const width = (s) => Array.from(String(s)).reduce((n, ch) => n + (wide(ch) ? 1 : 0.5), 0);
  /** The speaker label for a name the writer is choosing: "[이름/출처/언어]"; "A/B" = both speak. */
  function nameLabel(name, tail = '') {
    const st = STYLE();
    const names = String(name || '').split(st.name_split).map((x) => x.trim()).filter(Boolean);
    let who = names.join(st.joint_sep);
    if (names.length > 1 && width(`${st.open}${who}${st.close}`) > st.joint_max_units) who = st.joint_all;
    const inner = who ? `${who}${tail}` : tail.slice(st.label_sep.length);
    return inner ? `${st.open}${inner}${st.close}` : '';
  }
  /** Where the project list lives ('/' under the studio GUI, '' when served alone). */
  const HOME = document.body.dataset.home || '';

  /** mm:ss.s as on the review screen (03:48.7). */
  function fmtTime(t) {
    if (t == null || !Number.isFinite(t)) return '--:--.-';
    const d = Math.round(Math.max(0, t) * 10);
    const m = Math.floor(d / 600);
    return `${String(m).padStart(2, '0')}:${((d - m * 600) / 10).toFixed(1).padStart(4, '0')}`;
  }
  const fmtRuler = (t) => { const s = Math.round(t); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  /** "03:52.4", "1:03:52.4" or "232.4" -> seconds. */
  function parseTime(v) {
    const s = String(v ?? '').trim();
    if (!/^(\d+:){0,2}\d+(\.\d+)?$/.test(s)) return NaN;
    return s.split(':').map(Number).reduce((a, x) => a * 60 + x, 0);
  }
  /** 로 / 으로 after a name. */
  function ro(word) {
    const c = String(word).charCodeAt(String(word).length - 1);
    if (c >= 0xac00 && c <= 0xd7a3) { const j = (c - 0xac00) % 28; return j === 0 || j === 8 ? '로' : '으로'; }
    return '로';
  }
  function hash(s) { let h = 0; for (const ch of String(s)) h = (h * 31 + ch.codePointAt(0)) >>> 0; return h; }

  // ---------------------------------------------------------------- constants
  const KIND = {
    sfx: { label: '효과음', cls: 'k-sfx' },
    music: { label: '음악', cls: 'k-music' },
    speaker: { label: '화자', cls: 'k-speaker' },
  };
  const KIND_ORDER = { speaker: 0, sfx: 1, music: 2 };
  const STATE_LABEL = { inc: '포함 추천', exc: '제외 추천', check: '확인 필요', 'inc-done': '포함 확정', 'exc-done': '제외 확정', noroom: '배치 확인 필요' };
  const SPK_LABEL = { inc: '이름 표시 추천', exc: '이름 생략 추천', check: '확인 필요', 'inc-done': '이름 표시', 'exc-done': '이름 생략', noroom: '배치 확인 필요' };
  const FILTERS = [['all', '전체'], ['include', '포함 추천'], ['exclude', '제외 추천'], ['check', '확인 필요'], ['noroom', '배치 확인 필요']];
  const TOOLS = { look_frames: '프레임 보기', describe_sound: '소리 설명', rescan_sound: '다시 검출', search_dialogue: '대사 찾기', scene_context: '앞뒤 장면' };
  const FLAGS = { label_uncertain: '종류 불확실', unknown: '목록 밖 소리', conflict: '근거 어긋남' };
  const CELL_FLAGS = { timing_estimated: '시각 추정', overlap: '겹친 대사', flash: '몇 프레임짜리 칸' };
  const SOURCE_NOTE = { caption: '소리 설명 모델 제안', detected: '검출 종류', parent: '상위 종류', agent: '에이전트 제안', mood: '분위기' };
  const FACE = { visible: '말하는 얼굴 보임', not_visible: '말하는 얼굴 안 보임', unclear: '얼굴 판정 애매' };
  const CHANGES = ['고조되는', '잦아드는', '음악 끝'];
  const AVATAR = ['#15807A', '#7180CB', '#C95A1C', '#7448C2', '#1E7A43', '#B5407A', '#3A7BB8', '#8F6100'];
  const LANE = { cells: 0, speaker: 1, sfx: 2, music: 3 };
  /** Lane geometry comes from the CSS variables, so the labels and blocks always line up. */
  function tlGeometry() {
    const cs = getComputedStyle(document.documentElement);
    const v = (name, d) => parseFloat(cs.getPropertyValue(name)) || d;
    return { ruler: v('--tl-ruler', 26), pad: v('--tl-pad', 6), lane: v('--tl-lane', 26), gap: v('--tl-gap', 8) };
  }
  const ICON = {
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z"/></svg>',
    check: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  // -------------------------------------------------------------------- state
  const S = {
    st: null, P: null, sel: null, filter: 'all', kinds: new Set(Object.keys(KIND)), zoom: 15,
    t: 0, pps: 100, modal: null, moveOpen: null, lastExport: null, busy: 0, still: false,
    overlayKey: '', lastFocus: null,
  };
  const D = { items: [], byId: new Map(), lines: new Map(), cells: [], cellById: new Map(), noRoom: new Map(), spkByLine: new Map(), visible: [] };
  const video = $('#video');
  const pvVideo = () => $('#pv-video');

  // ---------------------------------------------------------------------- api
  let chain = Promise.resolve();
  /** Requests run one after another so responses are applied in order. */
  function api(method, url, body) {
    const run = async () => {
      const init = { method, headers: {} };
      if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
      const res = await fetch(url, init);
      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }
      if (!res.ok) {
        const d = data && data.detail;
        throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => x.msg).join(', ') : `${res.status} ${res.statusText}`);
      }
      return data;
    };
    const p = chain.then(run, run);
    chain = p.catch(() => {});
    return p;
  }
  let busyTimer = 0;
  function setBusy(on) {
    S.busy = Math.max(0, S.busy + (on ? 1 : -1));
    clearTimeout(busyTimer);
    if (S.busy) busyTimer = setTimeout(() => $('#app').classList.add('is-busy'), 150);
    else $('#app').classList.remove('is-busy');
  }
  async function mutate(url, body, okMsg) {
    setBusy(true);
    try {
      const st = await api('POST', url, body);
      applyState(st);
      if (okMsg) toast(okMsg);
      return st;
    } catch (e) {
      toast(e.message || String(e), 'error', 4500);
      return null;
    } finally {
      setBusy(false);
    }
  }
  function toast(msg, kind = 'ok', ms = 2300) {
    const el = document.createElement('div');
    el.className = `toast toast-${kind}`;
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    requestAnimationFrame(() => el.classList.add('in'));
    setTimeout(() => { el.classList.remove('in'); setTimeout(() => el.remove(), 260); }, ms);
    while ($('#toasts').children.length > 2) $('#toasts').firstElementChild.remove();
  }

  // ------------------------------------------------------------------ derived
  function derive(st) {
    S.st = st;
    S.P = st.project;
    D.lines = new Map((st.dialogue || []).map((l) => [l.id, l]));
    D.cells = ((st.draft && st.draft.cells) || []).slice().sort((a, b) => a.start - b.start);
    D.cellById = new Map(D.cells.map((c) => [c.id, c]));
    D.noRoom = new Map(((st.draft && st.draft.no_room) || []).map((e) => [e.item, e]));
    D.items = (st.items || []).slice().sort((a, b) => num(a.onset) - num(b.onset)
      || (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) || a.id.localeCompare(b.id));
    D.byId = new Map(D.items.map((it) => [it.id, it]));
    D.spkByLine = new Map();
    for (const it of D.items) if (it.kind === 'speaker' && it.speaker && it.speaker.line_id) D.spkByLine.set(it.speaker.line_id, it);
  }
  function stateOf(it) {
    if (D.noRoom.has(it.id)) return 'noroom';
    if (it.decided) return it.final === 'include' ? 'inc-done' : 'exc-done';
    return { include: 'inc', exclude: 'exc' }[it.final] || 'check';
  }
  const stateLabel = (it, st = stateOf(it)) => (it.kind === 'speaker' ? SPK_LABEL : STATE_LABEL)[st];
  function pillHtml(it, extra = '') {
    const st = stateOf(it);
    return `<span class="pill st-${st} ${extra}">${st.endsWith('-done') ? ICON.check : ''}${esc(stateLabel(it, st))}</span>`;
  }
  const lineOf = (it) => (it.speaker && D.lines.get(it.speaker.line_id)) || null;
  const speakerText = (it) => { const ln = lineOf(it); return ln ? ln.text : it.phrase || ''; };
  /** The label placement writes in front of this speaker's line, "[이름/출처/언어]" (from the server). */
  const spkLabel = (it) => (it && it.speaker && it.speaker.label) || '';
  function phraseText(it) {
    if (it.kind !== 'speaker') return it.phrase || it.short || '';
    const lab = spkLabel(it);
    return it.final === 'include' && lab ? `${lab} ${speakerText(it)}` : speakerText(it);
  }
  function phraseHtml(it) {
    if (it.kind !== 'speaker') return esc(it.phrase || it.short || '');
    const lab = spkLabel(it);
    return it.final === 'include' && lab ? `<span class="nm">${esc(lab)}</span> ${esc(speakerText(it))}` : esc(speakerText(it));
  }
  function summaryOf(it) {
    const nr = D.noRoom.get(it.id);
    if (nr) return `넣을 자리 부족 · ${nr.reason || ''}`;
    if (it.kind === 'speaker') return [(it.speaker && it.speaker.name) || '대본 화자 없음', it.reason].filter(Boolean).join(' · ');
    if (it.reason) return it.reason;
    return (it.evidence || []).slice(0, 2).map((e) => `${e.kind} · ${e.text}`).join(' / ');
  }
  function inFilter(it, f = S.filter) {
    if (f === 'all') return true;
    if (f === 'noroom') return D.noRoom.has(it.id);
    return (it.rec || 'check') === f;
  }
  const visibleItems = () => D.items.filter((it) => S.kinds.has(it.kind) && inFilter(it));

  // -------------------------------------------------------------------- apply
  function applyState(st, { keepExport = false } = {}) {
    derive(st);
    if (!keepExport) S.lastExport = null;
    if (S.sel && !D.byId.has(S.sel)) S.sel = null;
    renderHeader();
    renderChips();
    renderList();
    renderEvidence();
    renderTimeline();
    S.overlayKey = '';
    updateOverlay();
    if (S.modal && S.modal.type === 'export') renderExport();
  }

  // ------------------------------------------------------------------- header
  function renderHeader() {
    const P = S.P, sm = S.st.summary || {};
    $('#work-title').textContent = P.title || '';
    $('#sample-tag').hidden = !P.sample;
    document.title = `SDH 자막 검수 · ${P.title || ''}`;
    const chk = S.st.export_check;
    const nCheck = chk ? chk.check.length : num(sm.final && sm.final.check);
    const nNo = num(sm.no_room);
    const pct = sm.total ? Math.round((100 * num(sm.decided)) / sm.total) : 0;
    // cells that break the guide: obligations by the independent check (it covers the limits too);
    // when that check could not run, the cells placement itself marked
    const guide = sm.guide || null;
    const nViol = guide && chk ? num(guide.must) : num(sm.violations);
    const nShould = guide && chk ? num(guide.should) : 0;
    const violTitle = `지침 검사에서 의무 조항에 걸린 자막 칸${nShould ? ` · 권고 조항은 ${nShould}칸` : ''} (누르면 조항별 목록)`;
    $('#progress').innerHTML = `
      <span class="prog" title="작가가 직접 정한 후보 수">작가 결정 <b class="tnum">${num(sm.decided)}</b> / ${num(sm.total)}
        <span class="prog-bar"><i style="width:${pct}%"></i></span></span>
      ${nCheck ? `<button type="button" class="pill pill-btn st-check" data-act="filter" data-filter="check" title="아직 정하지 않은 확인 필요 항목">확인 필요 ${nCheck}</button>` : ''}
      ${nNo ? `<button type="button" class="pill pill-btn st-noroom" data-act="filter" data-filter="noroom">배치 확인 필요 ${nNo}</button>` : ''}
      ${nViol ? `<button type="button" class="pill pill-btn st-viol" data-act="open-export" data-fmt="srt" title="${esc(violTitle)}">지침 위반 ${nViol}칸</button>`
        : `<span class="pill st-clean" title="지침 검사의 의무 조항에 걸린 자막 칸이 없다${nShould ? ` · 권고 조항은 ${nShould}칸` : ''}">지침 위반 0칸</span>`}`;
    const ban = $('#banner');
    ban.hidden = !S.st.error;
    ban.textContent = S.st.error || '';
    $('#tl-stub').hidden = !(S.st.draft && S.st.draft.stub);
  }

  // --------------------------------------------------------------------- list
  function renderChips() {
    const byKind = D.items.filter((it) => S.kinds.has(it.kind));
    $('#state-chips').innerHTML = FILTERS.map(([key, label]) => {
      const n = byKind.filter((it) => inFilter(it, key)).length;
      const on = S.filter === key;
      const extra = key === 'noroom' ? ` chip-noroom${n ? ' has' : ''}` : '';
      return `<button type="button" class="chip${extra}${on ? ' is-on' : ''}" data-act="filter" data-filter="${key}" data-toggle="1" aria-pressed="${on}">${label}<span class="n">${n}</span></button>`;
    }).join('');
    const byState = D.items.filter((it) => inFilter(it));
    $('#kind-chips').innerHTML = Object.entries(KIND).map(([k, v]) => {
      const on = S.kinds.has(k);
      const n = byState.filter((it) => it.kind === k).length;
      return `<button type="button" class="chip kchip ${v.cls}${on ? ' is-on' : ''}" data-act="kind" data-kind="${k}" aria-pressed="${on}" title="${v.label} ${on ? '숨기기' : '보이기'}">${v.label}<span class="n">${n}</span></button>`;
    }).join('');
  }
  function rowHtml(it) {
    const sel = it.id === S.sel;
    const k = KIND[it.kind] || { label: it.kind, cls: '' };
    return `<div class="row${sel ? ' is-sel' : ''}${D.noRoom.has(it.id) ? ' is-noroom' : ''}" role="option" id="row-${esc(it.id)}" data-id="${esc(it.id)}" aria-selected="${sel}">
      <span class="r-time tnum">${fmtTime(it.onset)}</span>
      <span class="badge ${k.cls}">${esc(k.label)}</span>
      <span class="r-main"><span class="r-phrase">${phraseHtml(it)}</span><span class="r-sub">${esc(summaryOf(it))}</span></span>
      ${pillHtml(it)}
    </div>`;
  }
  function renderList() {
    const vis = (D.visible = visibleItems());
    const box = $('#rows');
    $('#list-count').textContent = `${vis.length} / ${D.items.length}개`;
    if (!D.items.length) box.innerHTML = '<div class="empty">후보가 아직 없습니다 · 파이프라인이 items.json 을 만든 뒤 다시 여세요</div>';
    else if (!vis.length) box.innerHTML = '<div class="empty">이 조건에 맞는 후보가 없습니다</div>';
    else box.innerHTML = vis.map(rowHtml).join('');
    const row = S.sel && document.getElementById(`row-${S.sel}`);
    box.setAttribute('aria-activedescendant', row ? row.id : '');
    if (row) row.scrollIntoView({ block: 'nearest' });
  }
  function select(id, { seek = true, reveal = true } = {}) {
    const it = D.byId.get(id);
    if (!it) return;
    if (S.sel !== id) S.moveOpen = null;
    S.sel = id;
    for (const r of $$('#rows .row.is-sel')) { r.classList.remove('is-sel'); r.setAttribute('aria-selected', 'false'); }
    const row = document.getElementById(`row-${id}`);
    if (row) {
      row.classList.add('is-sel');
      row.setAttribute('aria-selected', 'true');
      if (reveal) row.scrollIntoView({ block: 'nearest' });
    }
    $('#rows').setAttribute('aria-activedescendant', row ? row.id : '');
    renderEvidence();
    markTimeline();
    if (seek) {
      clearRange(video);
      if (!video.paused) video.pause();
      seekTo(num(it.onset) - 0.5);
      const [s, e] = itemSpan(it);
      revealInTimeline(s, e);
    }
    S.overlayKey = '';
    updateOverlay();
  }
  function moveSel(delta) {
    const vis = D.visible;
    if (!vis.length) return;
    let i = vis.findIndex((it) => it.id === S.sel);
    i = i < 0 ? (delta > 0 ? 0 : vis.length - 1) : clamp(i + delta, 0, vis.length - 1);
    select(vis[i].id);
  }
  function setFilter(f, toggle = false) {
    S.filter = toggle && S.filter === f && f !== 'all' ? 'all' : f;
    renderChips();
    renderList();
  }
  function toggleKind(k) {
    if (S.kinds.has(k) && S.kinds.size === 1) S.kinds = new Set(Object.keys(KIND));   // never hide everything
    else if (S.kinds.has(k)) S.kinds.delete(k);
    else S.kinds.add(k);
    renderChips();
    renderList();
  }

  // ----------------------------------------------------------------- evidence
  function actBtn(label, act, id, { primary = false, decision, on = false, cls = '' } = {}) {
    const dec = decision === undefined ? '' : ` data-decision="${decision ?? ''}"`;
    return `<button type="button" class="btn${primary ? ' btn-primary' : ''}${on ? ' is-on' : ''}${cls ? ` ${cls}` : ''}" data-act="${act}" data-id="${esc(id)}"${dec}${on ? ' aria-pressed="true"' : ''}>${label}</button>`;
  }
  function actionsHtml(it) {
    const id = it.id, f = it.final, done = it.decided, out = [];
    if (it.kind === 'speaker') {
      out.push(actBtn('이름 표시', 'decide', id, { decision: 'include', on: done && f === 'include' }));
      out.push(actBtn('이름 생략', 'decide', id, { decision: 'exclude', on: done && f === 'exclude' }));
      out.push(actBtn('화자 고치기', 'edit', id, { primary: true }));
    } else if (!done && f === 'exclude') {
      out.push(actBtn('제외 확정', 'decide', id, { decision: 'exclude' }));
      out.push(actBtn('포함으로 바꾸기', 'decide', id, { decision: 'include', primary: true }));
    } else if (!done && f === 'include') {
      out.push(actBtn('포함 확정', 'decide', id, { decision: 'include' }));
      out.push(actBtn('제외로 바꾸기', 'decide', id, { decision: 'exclude' }));
      out.push(actBtn('문구 고치기', 'edit', id, { primary: true }));
    } else if (!done) {
      out.push(actBtn('포함', 'decide', id, { decision: 'include', primary: true }));
      out.push(actBtn('제외', 'decide', id, { decision: 'exclude' }));
      out.push(actBtn('문구 고치기', 'edit', id));
    } else if (f === 'include') {
      out.push(actBtn('제외로 바꾸기', 'decide', id, { decision: 'exclude' }));
      out.push(actBtn('문구 고치기', 'edit', id, { primary: true }));
    } else {
      out.push(actBtn('포함으로 바꾸기', 'decide', id, { decision: 'include', primary: true }));
    }
    if (done) out.push(actBtn('추천대로', 'decide', id, { decision: null, cls: 'btn-text' }));
    return out.join('');
  }
  function noteHtml(it) {
    const bits = [];
    if (it.kind === 'speaker') {
      const ln = lineOf(it);
      if (ln) bits.push(`대본 화자 ${esc(ln.speaker || '없음')}`, `대사 ${fmtTime(ln.start)}–${fmtTime(ln.end)}`);
      const face = FACE[it.speaker && it.speaker.face];
      if (face) bits.push(face);
    } else {
      if (it.reason) bits.push(esc(it.reason));
      const cell = (it.cells || []).map((c) => D.cellById.get(c)).find(Boolean);
      if (cell) bits.push(`자막 칸 ${fmtTime(cell.start)}–${fmtTime(cell.end)}`);
      else if (it.final !== 'include') bits.push('자막에 넣지 않음');
    }
    for (const f of it.flags || []) bits.push(`<span class="flag">${esc(FLAGS[f] || f)}</span>`);
    if (it.placement && it.placement.action === 'move') bits.push(`작가가 ${fmtTime(it.placement.t)}로 옮김`);
    let agent = '';
    if (it.agent && it.agent.summary) {
      const tools = (it.agent.tools || []).map((t) => TOOLS[t] || t);
      agent = `<div class="ev-agent"><b>재확인 에이전트</b>${esc(it.agent.summary)}${tools.length ? ` <span class="tools">· ${esc(tools.join(', '))}</span>` : ''}</div>`;
    }
    return (bits.length ? `<div class="ev-note">${bits.join('<i>·</i>')}</div>` : '') + agent;
  }
  function moveFormHtml(it, t) {
    return `<span class="move-form">
      <input class="time-input" value="${fmtTime(t)}" aria-label="옮길 시각 (분:초)" inputmode="decimal" autocomplete="off">
      <button type="button" class="btn btn-sm btn-text" data-act="move-now" data-id="${esc(it.id)}" title="지금 재생 위치를 넣는다">지금 위치</button>
      <button type="button" class="btn btn-sm btn-primary" data-act="place" data-action="move" data-id="${esc(it.id)}">옮기기</button>
    </span>`;
  }
  /** 배치 확인 필요 card with its three fixes; compact = one row for the 근거 panel. */
  function noRoomHtml(it, compact = false) {
    const nr = D.noRoom.get(it.id);
    if (!nr) return '';
    const t = num(nr.t, num(it.onset));
    const sw = it.swap_prev || {};
    const swap = sw.error
      ? `<button type="button" class="btn btn-sm is-unavailable" data-act="swap-why" data-reason="${esc(sw.error)}" title="${esc(sw.error)}" aria-disabled="true">앞 칸과 바꾸기</button>`
      : `<button type="button" class="btn btn-sm" data-act="place" data-action="swap_prev" data-id="${esc(it.id)}"${Number.isFinite(sw.t) ? ` title="${fmtTime(sw.t)}로 옮긴다"` : ''}>앞 칸과 바꾸기</button>`;
    const actions = `<div class="nr-actions">
        <button type="button" class="btn btn-sm btn-danger" data-act="place" data-action="exclude" data-id="${esc(it.id)}">제외</button>${swap}
        ${S.moveOpen === it.id ? moveFormHtml(it, t) : `<button type="button" class="btn btn-sm" data-act="move-open" data-id="${esc(it.id)}">직접 옮기기</button>`}
      </div>`;
    if (compact) {
      return `<div class="nr-card nr-compact" data-nr="${esc(it.id)}"><span class="pill st-noroom">배치 확인 필요</span>
        <span class="nr-reason" title="넣을 자리가 부족 · ${esc(nr.reason || '')}">${esc(nr.reason || '넣을 자리가 부족')}</span>${actions}</div>`;
    }
    return `<div class="nr-card" data-nr="${esc(it.id)}">
      <div class="nr-top"><span class="pill st-noroom">배치 확인 필요</span><b class="tnum">${fmtTime(t)}</b><b>${esc(nr.text || it.short || it.phrase || '')}</b></div>
      <div class="nr-reason">${it.decided ? '포함 확정' : '포함 추천'}이지만 넣을 자리가 부족 · ${esc(nr.reason || '')}</div>
      ${actions}
    </div>`;
  }
  function evidenceRowsHtml(it, target = 'main') {
    const evs = it.evidence || [];
    if (!evs.length) return '<div class="ev-empty">근거가 없습니다</div>';
    return evs.map((e, i) => {
      const pol = e.polarity === 'include' ? '<span class="pol pol-inc">포함 쪽</span>'
        : e.polarity === 'exclude' ? '<span class="pol pol-exc">제외 쪽</span>' : '<span></span>';
      return `<button type="button" class="ev-row" data-act="play-ev" data-target="${target}" data-id="${esc(it.id)}" data-i="${i}" aria-label="${esc(`${e.kind} ${fmtTime(e.t)} ${e.text}, 이 구간 재생`)}">
        <span class="ev-play">${ICON.play}</span><span class="ev-kind">${esc(e.kind)}</span><span class="ev-t tnum">${fmtTime(e.t)}</span>
        <span class="ev-text" title="${esc(e.text)}">${esc(e.text)}</span>${pol}</button>`;
    }).join('');
  }
  function renderEvidence() {
    const box = $('#evidence');
    const it = D.byId.get(S.sel);
    const head = (actions) => `<div class="ev-head"><h2>근거</h2><span class="hint">누르면 그 구간을 바로 재생</span><div class="ev-actions">${actions}</div></div>`;
    if (!it) { box.innerHTML = `${head('')}<div class="ev-empty">목록에서 후보를 고르면 근거가 여기에 나온다</div>`; return; }
    const k = KIND[it.kind];
    box.innerHTML = `${head(actionsHtml(it))}
      <div class="ev-subject">
        <span class="badge ${k.cls}">${k.label}</span><span class="ev-time tnum">${fmtTime(it.onset)}</span>
        <span class="ev-phrase" title="${esc(phraseText(it))}">${phraseHtml(it)}</span>${pillHtml(it)}
      </div>
      ${noteHtml(it)}${noRoomHtml(it, true)}
      <div class="ev-rows">${evidenceRowsHtml(it)}</div>`;
  }

  // ----------------------------------------------------------------- timeline
  function itemSpan(it) {
    let s = num(it.t0, num(it.onset));
    let e = num(it.t1, s);
    if (it.kind === 'speaker') { const ln = lineOf(it); if (ln) { s = ln.start; e = ln.end; } }
    // music: from this cue to the next music item (t1); the whole segment only when t1 is missing
    if (it.kind === 'music' && it.t1 == null && it.music && Array.isArray(it.music.segment)) { e = num(it.music.segment[1], e); }
    return [s, Math.max(e, s + 0.3)];
  }
  function pickStep(pps) {
    const steps = [[1, 0.5], [2, 1], [5, 1], [10, 2], [15, 5], [30, 10], [60, 15], [120, 30], [300, 60], [600, 120], [1200, 300]];
    for (const [maj, min] of steps) if (maj * pps >= 110) return { maj, min };
    return { maj: 1800, min: 600 };
  }
  /** Sub-tracks only where blocks overlap: sets b.track and b.ntr per overlap cluster (blocks sorted by s). */
  function pack(blocks) {
    let cluster = [], end = -Infinity;
    const flush = () => {
      const ends = [];
      for (const b of cluster) {
        let k = ends.findIndex((e) => e <= b.s + 1e-6);
        if (k < 0) { k = ends.length; ends.push(b.e); } else ends[k] = b.e;
        b.track = k;
      }
      for (const b of cluster) b.ntr = Math.max(1, ends.length);
      cluster = [];
    };
    for (const b of blocks) {
      if (cluster.length && b.s >= end - 1e-6) { flush(); end = -Infinity; }
      cluster.push(b);
      end = Math.max(end, b.e);
    }
    flush();
  }
  function blkLabel(it) {
    if (it.kind === 'speaker') { const n = (it.speaker && it.speaker.name) || '이름 없음'; return it.final === 'include' ? (spkLabel(it) || bracket(n)) : n; }
    if (it.kind === 'music') return it.phrase || bracket(STYLE().music);
    return unbracket(it.phrase || it.short || '');
  }
  function cellTitle(c) {
    const ch = c.check || {};
    const v = (ch.violations || []).map(violText).join(', ');
    const fl = (c.flags || []).map((f) => CELL_FLAGS[f] || f).join(', ');      // what placement marked on the cell
    return `${fmtTime(c.start)}–${fmtTime(c.end)}\n${(c.lines || []).join('\n')}\n초당 ${num(ch.cps).toFixed(1)}자${v ? ` · ${v}` : ''}${fl ? ` · ${fl}` : ''}`;
  }
  function renderTimeline() {
    const P = S.P;
    if (!P) return;
    const sc = $('#tl-scroll');
    const span = Math.max(1, P.t1 - P.t0);
    const view = Math.max(200, sc.clientWidth);
    const pps = (S.pps = S.zoom ? view / Math.min(S.zoom, span) : view / span);
    $('#tl-inner').style.width = `${Math.max(view, Math.ceil(span * pps))}px`;
    const x = (t) => (t - P.t0) * pps;
    const parts = [];
    const { maj, min } = pickStep(pps);
    const ratio = Math.round(maj / min);
    for (let k = Math.ceil(P.t0 / min - 1e-9); k * min <= P.t1 + 1e-9; k++) {
      const major = k % ratio === 0;
      parts.push(`<div class="tick${major ? ' major' : ''}" style="left:${x(k * min).toFixed(1)}px">${major ? `<span>${fmtRuler(k * min)}</span>` : ''}</div>`);
    }
    for (const c of S.st.cuts || []) {
      if (c >= P.t0 && c <= P.t1) parts.push(`<div class="cut" style="left:${x(c).toFixed(1)}px" title="장면 전환 ${fmtTime(c)}"></div>`);
    }
    const lanes = { cells: D.cells.map((c) => ({ s: c.start, e: c.end, cell: c })), speaker: [], sfx: [], music: [] };
    for (const it of D.items) {
      if (!lanes[it.kind]) continue;
      const [s, e] = itemSpan(it);
      lanes[it.kind].push({ s, e, it });
    }
    const TLG = tlGeometry();
    for (const [name, blocks] of Object.entries(lanes)) {
      blocks.sort((a, b) => a.s - b.s);
      pack(blocks);
      const laneTop = TLG.ruler + TLG.pad + LANE[name] * (TLG.lane + TLG.gap);
      for (const b of blocks) {
        const n = b.ntr;
        const top = laneTop + b.track * (TLG.lane / n);
        const h = TLG.lane / n - (n > 1 ? 2 : 0);
        const left = x(b.s);
        const w = Math.max(8, x(b.e) - left - 2);
        const style = `left:${left.toFixed(1)}px;width:${w.toFixed(1)}px;top:${top.toFixed(1)}px;height:${h.toFixed(1)}px`;
        const small = w < 34 || h < 14 ? ' tiny' : '';           // too small for a readable label: color only
        if (b.cell) {
          const c = b.cell;
          const viol = ((c.check && c.check.violations) || []).length;
          parts.push(`<div class="blk cell${viol ? ' has-viol' : ''}${small}" data-cell="${esc(c.id)}" style="${style}" title="${esc(cellTitle(c))}"><span>${esc((c.lines || []).join(' / '))}</span></div>`);
        } else {
          const it = b.it, st = stateOf(it);
          const title = `${KIND[it.kind].label} ${fmtTime(it.onset)} · ${phraseText(it)} · ${stateLabel(it, st)}`;
          parts.push(`<div class="blk st-${st}${small}" data-id="${esc(it.id)}" style="${style}" title="${esc(title)}"><span>${esc(blkLabel(it))}</span></div>`);
        }
      }
    }
    $('#tl-content').innerHTML = parts.join('');
    for (const btn of $$('.seg-zoom button')) btn.classList.toggle('is-on', Number(btn.dataset.zoom) === S.zoom);
    markTimeline();
    updatePlayhead();
  }
  function markTimeline() {
    for (const el of $$('#tl-content .is-sel, #tl-content .is-rel')) el.classList.remove('is-sel', 'is-rel');
    const it = D.byId.get(S.sel);
    if (!it) return;
    const blk = $(`#tl-content [data-id="${cssEsc(it.id)}"]`);
    if (blk) blk.classList.add('is-sel');
    for (const cid of it.cells || []) { const c = $(`#tl-content [data-cell="${cssEsc(cid)}"]`); if (c) c.classList.add('is-rel'); }
  }
  function updatePlayhead() {
    if (S.P) $('#tl-head').style.transform = `translateX(${((S.t - S.P.t0) * S.pps).toFixed(1)}px)`;
  }
  function revealInTimeline(s, e) {
    const sc = $('#tl-scroll');
    const x0 = (s - S.P.t0) * S.pps, x1 = (e - S.P.t0) * S.pps;
    if (x0 < sc.scrollLeft + 24 || x1 > sc.scrollLeft + sc.clientWidth - 24) sc.scrollLeft = Math.max(0, (x0 + x1) / 2 - sc.clientWidth / 2);
  }
  function followPlayhead() {
    const sc = $('#tl-scroll');
    const x = (S.t - S.P.t0) * S.pps;
    if (x < sc.scrollLeft + 16 || x > sc.scrollLeft + sc.clientWidth - 40) sc.scrollLeft = Math.max(0, x - sc.clientWidth * 0.2);
  }
  function setZoom(z) {
    S.zoom = z;
    renderTimeline();
    const sc = $('#tl-scroll');
    sc.scrollLeft = Math.max(0, (S.t - S.P.t0) * S.pps - sc.clientWidth * 0.35);
  }
  function gotoCell(cid) {
    const c = D.cellById.get(cid);
    if (!c) return;
    if (S.modal) closeModal();
    const ref = (c.refs || []).find((r) => D.byId.has(r)) || (c.refs || []).map((r) => D.spkByLine.get(r)).find(Boolean);
    const id = typeof ref === 'string' ? ref : ref && ref.id;
    if (id) select(id, { seek: false });
    clearRange(video);
    seekTo(c.start + 0.02);
    revealInTimeline(c.start, c.end);
  }

  // ------------------------------------------------------------------ overlay
  function cellHtml(c) {
    const refs = (c.refs || []).filter((r) => D.lines.has(r));
    const used = new Set();
    return `<div class="sub-cell">${(c.lines || []).map((ln) => {
      const m = /^(-\s*)?(\[[^\]]+\])(\s*)([\s\S]*)$/.exec(ln);
      // a leading [ ] is a speaker label only when it is the label of one of this cell's lines;
      // any other one is a sound ([재채기] 나도야!) or the language of a later piece ([영어] …)
      const lid = m && refs.find((r) => !used.has(r) && spkLabel(D.spkByLine.get(r)) === m[2]);
      if (!lid) return `<div class="sub-line">${esc(ln)}</div>`;
      used.add(lid);
      const sp = D.spkByLine.get(lid);
      return `<div class="sub-line">${esc(m[1] || '')}<span class="sub-name" data-act="name" data-id="${esc(sp.id)}" title="화자 고치기">${esc(m[2])}</span>${esc(m[3])}${esc(m[4])}</div>`;
    }).join('')}</div>`;
  }
  function updateOverlay() {
    const t = S.t;
    const act = D.cells.filter((c) => c.start <= t + 1e-3 && t < c.end - 1e-3);
    // the selected sound that is not in any cell is shown as a ghost, so the writer sees what is left out
    const it = D.byId.get(S.sel);
    const ghost = it && it.kind !== 'speaker' && !(it.cells || []).length && t >= num(it.onset) - 0.6 && t < num(it.onset) + 3 ? it : null;
    const key = act.map((c) => `${c.id}|${c.pos || ''}|${(c.lines || []).join('\n')}`).join('||') + (ghost ? `#${ghost.id}|${ghost.phrase}|${stateOf(ghost)}` : '');
    if (key === S.overlayKey) return;
    S.overlayKey = key;
    // a cell with pos "top" is drawn at the top of the picture, as the TTML export places it
    let html = act.filter((c) => c.pos !== 'top').map(cellHtml).join('');
    if (ghost) {
      html += `<div class="sub-ghost"><span class="ghost-tag st-${stateOf(ghost)}">${esc(stateLabel(ghost))} · 자막에 없음</span><div class="sub-line">${esc(ghost.phrase || '')}</div></div>`;
    }
    $('#subs').innerHTML = html;
    const top = $('#subs-top');
    if (top) top.innerHTML = act.filter((c) => c.pos === 'top').map(cellHtml).join('');
  }

  // ------------------------------------------------------------------- player
  function seekVideo(vid, t) {
    const P = S.P;
    t = clamp(t, P.t0, Math.max(P.t0, P.t1 - 0.05));
    if (vid.readyState >= 1) vid.currentTime = t;
    else vid._pending = t;
    if (vid === video) { S.t = t; onTimeUI(); }
    return t;
  }
  const seekTo = (t) => seekVideo(video, t);
  function onTimeUI() {
    $('#stage-time').textContent = fmtTime(S.t);
    $('#tl-clock').textContent = `${fmtTime(S.t)} / ${fmtTime(S.P.t1)}`;
    updatePlayhead();
    updateOverlay();
    if (S.still) scheduleStill();
  }
  function playVideo(vid) {
    const p = vid.play();
    if (p && p.catch) p.catch((e) => { if (e.name !== 'AbortError') toast(`재생할 수 없습니다 · ${e.message}`, 'error'); });
    kick();
  }
  function clearRange(vid) {
    if (!vid) return;
    vid._stopAt = null;
    if (vid._row) vid._row.classList.remove('is-playing');
    vid._row = null;
    if (vid === video) $('#stage-range').hidden = true;
  }
  function playRange(vid, a, b, rowEl) {
    if (!vid || !S.P.video) return;
    if (vid === video && S.still) { seekTo(a); toast('이 브라우저에서는 영상을 재생할 수 없어 정지 화면만 보여 줍니다', 'error'); return; }
    a = clamp(a, S.P.t0, S.P.t1 - 0.05);
    b = clamp(b, a + 0.2, S.P.t1);
    clearRange(vid);
    vid._stopAt = b;
    vid._row = rowEl || null;
    if (rowEl) rowEl.classList.add('is-playing');
    seekVideo(vid, a);
    if (vid === video) { const r = $('#stage-range'); r.hidden = false; r.textContent = `근거 구간 ${fmtTime(a)}–${fmtTime(b)}`; }
    playVideo(vid);
  }
  function playEvidence(it, i, target, rowEl) {
    const e = (it.evidence || [])[i];
    if (!e) return;
    const t = num(e.t, num(it.onset));
    const end = Number.isFinite(e.t1) && e.t1 > t ? e.t1 : t + 2;
    playRange(target === 'pv' ? pvVideo() : video, t - 0.3, end, rowEl);
  }
  function togglePlay() {
    if (!S.P || !S.P.video) return;
    if (S.still) { toast('이 브라우저에서는 영상을 재생할 수 없어 정지 화면만 보여 줍니다', 'error'); return; }
    if (video.paused) {
      clearRange(video);
      if (S.t >= S.P.t1 - 0.1) seekTo(S.P.t0);
      playVideo(video);
    } else {
      video.pause();
      clearRange(video);
    }
  }
  let raf = 0;
  function loop() {
    raf = 0;
    let again = false;
    for (const vid of [video, pvVideo()]) {
      if (!vid || vid.paused) continue;
      again = true;
      const t = vid.currentTime;
      if ((vid._stopAt != null && t >= vid._stopAt) || t >= S.P.t1 - 0.02) { vid.pause(); clearRange(vid); }
      if (vid === video) { S.t = clamp(t, S.P.t0, S.P.t1); onTimeUI(); followPlayhead(); }
      else updatePvTime(t);
    }
    if (again) raf = requestAnimationFrame(loop);
  }
  function kick() { if (!raf) raf = requestAnimationFrame(loop); }
  function showStageMsg(msg) { const el = $('#stage-msg'); el.hidden = !msg; el.textContent = msg || ''; }
  let stillTimer = 0;
  function scheduleStill() {
    clearTimeout(stillTimer);
    stillTimer = setTimeout(() => { $('#stage-still').src = `api/frame?t=${S.t.toFixed(2)}&w=960`; }, 120);
  }
  function bindVideo() {
    video.addEventListener('loadedmetadata', () => {
      if (video._pending != null) { video.currentTime = video._pending; video._pending = null; }
      layoutStage();
    });
    video.addEventListener('seeked', () => { if (video.paused) { S.t = video.currentTime; onTimeUI(); } });
    video.addEventListener('play', () => { $('#stage').classList.remove('is-paused'); $('#stage-play').setAttribute('aria-label', '일시 정지'); kick(); });
    video.addEventListener('pause', () => { $('#stage').classList.add('is-paused'); $('#stage-play').setAttribute('aria-label', '재생'); });
    video.addEventListener('timeupdate', () => {
      if (S.P && video.currentTime > S.P.t1 + 0.05) { video.pause(); video.currentTime = S.P.t1 - 0.05; }
    });
    video.addEventListener('error', () => {
      if (!S.P || !S.P.video || S.still) return;
      S.still = true;
      $('#stage-still').hidden = false;
      $('#stage-play').hidden = true;
      scheduleStill();
      toast('브라우저가 이 영상을 재생하지 못해 정지 화면으로 보여 줍니다', 'error', 5000);
    });
    video.addEventListener('click', togglePlay);
  }
  function fitLayer(box, layer, vid) {
    if (!box || !layer) return;
    const bw = box.clientWidth, bh = box.clientHeight;
    const vw = (vid && vid.videoWidth) || (S.P && S.P.width) || 16;
    const vh = (vid && vid.videoHeight) || (S.P && S.P.height) || 9;
    const sc = Math.min(bw / vw, bh / vh);
    const w = vw * sc, h = vh * sc;
    Object.assign(layer.style, { left: `${(bw - w) / 2}px`, top: `${(bh - h) / 2}px`, width: `${w}px`, height: `${h}px`, fontSize: `${clamp(h * 0.062, 12, 40)}px` });
  }
  function layoutStage() {
    const left = $('#left'), stage = $('#stage');
    const ar = S.P && S.P.width && S.P.height ? S.P.height / S.P.width : 9 / 16;
    let h = left.clientWidth * ar;
    if (window.innerWidth > 1100) h = Math.min(h, Math.max(180, left.clientHeight - 230));
    stage.style.height = `${Math.round(h)}px`;
    fitLayer(stage, $('#stage-layer'), video);
    if (S.modal && S.modal.pv) fitLayer($('#pv'), $('#pv-layer'), pvVideo());
  }

  // -------------------------------------------------------------------- edits
  function decisionMsg(it, d) {
    if (it.kind === 'speaker') {
      const lab = spkLabel(it);
      if (d === 'include') return `이름 표시 · ${cut(lab ? `${lab} ${speakerText(it)}` : speakerText(it), 34)}`;
      if (d === 'exclude') return `이름 생략 · ${cut(speakerText(it), 30)}`;
      return `추천대로 되돌림 · ${cut(speakerText(it), 30)}`;
    }
    const what = cut(it.phrase || '', 30);
    return d === 'include' ? `포함 확정 · ${what}` : d === 'exclude' ? `제외 확정 · ${what}` : `추천대로 되돌림 · ${what}`;
  }
  function decide(id, d) {
    const it = D.byId.get(id);
    if (!it) return Promise.resolve(null);
    return mutate(`api/items/${encodeURIComponent(id)}`, { decision: d }, decisionMsg(it, d));
  }
  function acceptRec() {
    const it = D.byId.get(S.sel);
    if (!it) return;
    if (it.rec === 'include' || it.rec === 'exclude') decide(it.id, it.rec);
    else toast('확인 필요 항목은 I(포함)나 E(제외)로 정하세요');
  }
  async function placement(id, action, t) {
    const it = D.byId.get(id);
    const what = { exclude: '제외 확정', swap_prev: '앞 칸 앞으로 옮김', move: `${fmtTime(t)}로 옮김`, clear: '옮기기 취소' }[action];
    const st = await mutate(`api/placement/${encodeURIComponent(id)}`, t === undefined ? { action } : { action, t }, `${what} · ${cut(it ? it.phrase : id, 26)}`);
    if (st && action !== 'exclude' && D.noRoom.has(id)) toast('아직 넣을 자리가 없습니다 · 다른 시각으로 옮겨 보세요', 'error', 4200);
  }
  async function bulkExclude() {
    const ids = (S.st.export_check && S.st.export_check.exclude_recommended) || [];
    if (!ids.length) return;
    await mutate('api/items/bulk', { ids, decision: 'exclude' }, `제외 추천 ${ids.length}개를 묶어서 확정`);
  }
  function openMoveForm(id, inModal) {
    S.moveOpen = id;
    if (inModal) renderExport(); else renderEvidence();
    const root = inModal ? $('#modal-box') : $('#evidence');
    const inp = root.querySelector(`.nr-card[data-nr="${cssEsc(id)}"] .time-input`);
    if (inp) { inp.focus(); inp.select(); }
  }

  // ------------------------------------------------------------------- modals
  function showModal() {
    if ($('#modal').hidden) S.lastFocus = document.activeElement;
    $('#modal').hidden = false;
    $('#app').inert = true;                 // keyboard and clicks stay inside the dialog
  }
  function teardownPreview() {
    const pv = pvVideo();
    if (pv) { pv.pause(); clearRange(pv); pv.removeAttribute('src'); pv.load(); }
  }
  function closeModal() {
    teardownPreview();
    S.modal = null;
    S.moveOpen = null;
    $('#app').inert = false;
    $('#modal').hidden = true;
    $('#modal-box').innerHTML = '';
    if (S.lastFocus && S.lastFocus.focus && document.body.contains(S.lastFocus)) S.lastFocus.focus({ preventScroll: true });
  }
  function pauseMain() { if (!video.paused) video.pause(); clearRange(video); }
  function openEdit(id) {
    const it = D.byId.get(id);
    if (!it) return;
    pauseMain();
    teardownPreview();
    if (it.kind === 'speaker') openSpeaker(it);
    else if (it.kind === 'music') openMusic(it);
    else openSfx(it);
  }
  function previewHtml(t) {
    return `<div class="pv" id="pv">
      <video id="pv-video" preload="metadata" playsinline poster="api/frame?t=${t.toFixed(2)}&amp;w=960"></video>
      <div class="stage-layer" id="pv-layer"><div class="subs" id="pv-subs"></div></div>
      <div class="stage-time tnum" id="pv-time">${fmtTime(t)}</div>
    </div>`;
  }
  function mountPreview(t) {
    const pv = pvVideo();
    if (!pv) return;
    S.modal.pv = true;
    if (S.P.video && !S.still) {
      pv._pending = clamp(t, S.P.t0, S.P.t1 - 0.05);
      pv.addEventListener('loadedmetadata', () => {
        if (pv._pending != null) { pv.currentTime = pv._pending; pv._pending = null; }
        fitLayer($('#pv'), $('#pv-layer'), pv);
      });
      pv.addEventListener('seeked', () => updatePvTime(pv.currentTime));
      pv.addEventListener('play', kick);
      pv.addEventListener('click', () => { if (pv.paused) { clearRange(pv); playVideo(pv); } else { pv.pause(); clearRange(pv); } });
      pv.src = S.P.video_url;
    }
    requestAnimationFrame(() => fitLayer($('#pv'), $('#pv-layer'), pv));
  }
  function updatePvTime(t) { const el = $('#pv-time'); if (el) el.textContent = fmtTime(t); }
  const subsHtml = (lines) => `<div class="sub-cell">${lines.map((l) => `<div class="sub-line">${l}</div>`).join('')}</div>`;
  const CLOSE_BTN = '<button type="button" class="icon-btn" data-act="close" aria-label="닫기">×</button>';
  function setPvSubs(html) { const el = $('#pv-subs'); if (el) el.innerHTML = html; }
  function headLeft(it) {
    const k = KIND[it.kind];
    return `<div class="mdl-head"><span class="badge ${k.cls}">${k.label}</span><span class="when tnum">${fmtTime(it.onset)}</span><span class="muted">${esc(S.P.title)}</span><span class="spacer"></span>${pillHtml(it)}</div>`;
  }
  function optHtml(value, label, note) {
    return `<label class="opt" data-opt="${esc(value)}"><input type="radio" name="m-opt" value="${esc(value)}"><span class="opt-main">${label}</span><span class="opt-note">${esc(note || '')}</span></label>`;
  }
  function customOptHtml(placeholder, val) {
    return `<label class="opt opt-custom" data-opt="custom"><input type="radio" name="m-opt" value="custom"><span class="opt-main">직접 입력</span><input type="text" class="opt-input" id="f-custom" placeholder="${esc(placeholder)}" value="${esc(val || '')}" autocomplete="off"></label>`;
  }
  function markOpts() {
    const m = S.modal;
    for (const el of $$('#modal-box .opt[data-opt]')) {
      const on = el.dataset.opt === m.choice;
      el.classList.toggle('is-on', on);
      const r = el.querySelector('input[type=radio]');
      if (r) r.checked = on;
    }
  }
  function confirmBar(resultLabel) {
    return `<div class="mdl-actions"><button type="button" class="btn btn-primary btn-lg" data-act="m-confirm">확정</button><button type="button" class="btn btn-lg" data-act="close">취소</button>
      <span class="result">확정하면 <span class="pill st-inc-done">${ICON.check}${resultLabel}</span></span></div>`;
  }
  // what the writer types becomes a label in the guide's notation: "[…]" for a sound and for music
  // (an old "♬ …" too); a line that starts with ( or ♪ is left as typed
  const normSfx = (s) => { s = String(s || '').trim(); return !s || /^[(♪]/.test(s) ? s : bracket(s); };
  const normMusic = (s) => { s = String(s || '').trim(); return !s || /^♪/.test(s) ? s : bracket(s); };

  // 효과음 (3.2)
  function sfxOptions(it) {
    const out = [], seen = new Set();
    const add = (text, shortText, note) => {
      text = String(text || '').trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      out.push({ text, short: String(shortText || text).trim(), note: note || '' });
    };
    for (const p of it.phrases || []) add(p.text, p.short, p.note || SOURCE_NOTE[p.source] || '');
    ((it.label && it.label.top) || []).forEach((c, i) => {
      const ko = String(c.ko || '').trim();
      if (!ko) return;
      add(/소리$/.test(ko) ? `[${ko}]` : `[${ko} 소리]`, `[${ko}]`, `${i === 0 ? '검출 종류' : '다른 후보'} ${num(c.score).toFixed(2)}`);
    });
    return out;
  }
  function chosenSfx(m) {
    if (m.choice === 'custom') { const t = normSfx(m.custom); return { text: t, short: t }; }
    const o = m.opts[Number(m.choice)];
    return o ? { text: o.text, short: o.short } : { text: '', short: '' };
  }
  function syncSfx() {
    const m = S.modal, c = chosenSfx(m);
    markOpts();
    if (!m.shortTouched) { m.short = c.short; $('#f-short').value = c.short; }
    setPvSubs(c.text ? subsHtml([esc(c.text)]) : '');
  }
  function openSfx(it) {
    const opts = sfxOptions(it);
    const idx = opts.findIndex((o) => o.text === it.phrase);
    S.modal = { type: 'sfx', id: it.id, opts, choice: idx >= 0 ? String(idx) : 'custom', custom: idx >= 0 ? '' : it.phrase || '', short: it.short || '', shortTouched: false };
    const t = num(it.onset) + 0.15;
    $('#modal-box').innerHTML = `<div class="mdl-grid">
      <section class="mdl-card">
        ${headLeft(it)}
        ${previewHtml(t)}
        <div class="sec">근거<span class="sub">누르면 그 구간을 재생</span></div>
        <div class="ev-rows framed">${evidenceRowsHtml(it, 'pv')}</div>
        <div class="sec">추천</div>
        <div class="rec-row">${pillHtml(it, 'pill-lg')}<span class="draft">초안 문구<b>${esc(it.phrase || '')}</b></span></div>
      </section>
      <section class="mdl-card">
        <div class="mdl-head"><span class="badge k-sfx">효과음</span><h3 id="modal-title">문구 고치기</h3><span class="muted tnum">${fmtTime(it.onset)}</span>
          <span class="spacer"></span>${CLOSE_BTN}</div>
        <div class="sec">문구 고르기</div>
        <div class="opts" role="radiogroup" aria-label="문구 고르기">
          ${opts.map((o, i) => {
            const core = unbracket(o.text);
            const label = core && core !== o.text ? `<b>${esc(core)}</b><span class="arrow">→</span><span class="out">${esc(o.text)}</span>` : `<b>${esc(o.text)}</b>`;
            return optHtml(String(i), label, o.note);
          }).join('')}
          ${customOptHtml('예: 쿵 · 달칵 - 문 (괄호는 저절로)', S.modal.custom)}
        </div>
        <div class="field"><label for="f-short">짧은 문구</label><input class="input" id="f-short" value="" autocomplete="off">
          <span class="field-hint">넣을 자리가 좁을 때 이 문구로 줄여 넣는다</span></div>
        ${confirmBar('포함 확정')}
      </section></div>`;
    showModal();
    mountPreview(t);
    syncSfx();
    focusFirst();
  }
  async function confirmSfx() {
    const m = S.modal, c = chosenSfx(m);
    if (!c.text) { toast('문구를 고르거나 입력하세요', 'error'); const f = $('#f-custom'); if (f) f.focus(); return; }
    const shortText = String(m.short || '').trim() || c.text;
    const st = await mutate(`api/items/${encodeURIComponent(m.id)}`, { decision: 'include', phrase: c.text, short: shortText }, `포함 확정 · ${cut(c.text, 30)}`);
    if (st) closeModal();
  }

  // 음악 (3.3)
  function composeMusic(m) {                 // "[고조되는 무서운 음악]" (II.7: a label in brackets)
    const noun = m.inst || STYLE().music;
    const mood = m.mood ? `${m.mood} ` : '';
    if (m.change === '음악 끝') return bracket(`${mood}${noun} 끝`);
    return bracket(`${m.change ? `${m.change} ` : ''}${mood}${noun}`);
  }
  const composeShort = (m) => bracket(`${m.mood ? `${m.mood} ` : ''}${m.inst || STYLE().music}${m.change === '음악 끝' ? ' 끝' : ''}`);
  function composeNote(m) {
    const p = [m.change && '변화', m.mood && '분위기', m.inst && '악기'].filter(Boolean);
    return !p.length ? '재료 없이' : p.length === 1 ? `${p[0]}만` : p.join(' + ');
  }
  function chosenMusic(m, it) {
    if (m.choice === 'composed') return { text: composeMusic(m), short: composeShort(m) };
    if (m.choice === 'custom') { const t = normMusic(m.custom); return { text: t, short: t }; }
    const p = (it.phrases || [])[Number(String(m.choice).slice(1))];
    return p ? { text: p.text, short: p.short || p.text } : { text: '', short: '' };
  }
  function musicChipsHtml(m) {
    const chip = (group, val, label, extra = '', title = '') => {
      const on = m[group] === val;
      return `<button type="button" class="mchip${on ? ' is-on' : ''}${extra}" data-act="m-chip" data-group="${group}" data-val="${esc(val)}" aria-pressed="${on}"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</button>`;
    };
    const rows = [`<span class="k">분위기 후보</span><div class="mchips">${m.moods.length ? m.moods.map((x) => chip('mood', x.ko, x.ko, '', `점수 ${num(x.score).toFixed(2)}`)).join('') : '<span class="muted">후보 없음</span>'}</div>`,
      `<span class="k">변화</span><div class="mchips">${CHANGES.map((c) => chip('change', c, c, m.detected === c ? ' detected' : '', m.detected === c ? '검출된 변화' : '')).join('')}</div>`];
    if (m.insts.length) rows.push(`<span class="k">악기·장르</span><div class="mchips">${m.insts.map((x) => chip('inst', x.ko, x.ko, '', `점수 ${num(x.score).toFixed(2)}`)).join('')}</div>`);
    return rows.join('');
  }
  function musicOptsHtml(m, it) {
    const comp = composeMusic(m);
    const rows = [optHtml('composed', `<b>${esc(comp)}</b>`, composeNote(m))];
    (it.phrases || []).forEach((p, i) => { if (p.text && p.text !== comp) rows.push(optHtml(`p${i}`, `<b>${esc(p.text)}</b>`, p.note || '')); });
    rows.push(customOptHtml('예: 잔잔한 피아노 연주 (괄호는 저절로)', m.custom));
    return rows.join('');
  }
  function syncMusic(rebuild) {
    const m = S.modal, it = D.byId.get(m.id);
    if (rebuild) {
      $('#m-mat').innerHTML = musicChipsHtml(m);
      const custom = $('#f-custom');
      if (custom) m.custom = custom.value;
      $('#m-opts').innerHTML = musicOptsHtml(m, it);
    }
    if (!$(`#modal-box .opt[data-opt="${cssEsc(m.choice)}"]`)) m.choice = 'composed';
    markOpts();
    const c = chosenMusic(m, it);
    if (!m.shortTouched) { m.short = c.short; $('#f-short').value = c.short; }
    setPvSubs(c.text ? subsHtml([esc(c.text)]) : '');
  }
  function musicChip(group, val) {
    const m = S.modal;
    m[group] = m[group] === val ? null : val;
    m.choice = 'composed';
    syncMusic(true);
  }
  function openMusic(it) {
    const mu = it.music || {};
    const cur = it.phrase || '';
    const moods = (mu.moods || []).filter((x) => x && x.ko);
    const insts = (mu.instruments || []).filter((x) => x && x.ko);
    const m = (S.modal = {
      type: 'music', id: it.id, moods, insts, detected: mu.change || null,
      mood: (moods.find((x) => cur.includes(x.ko)) || {}).ko || null,
      change: CHANGES.find((c) => (c === '음악 끝' ? /끝\s*\]?\s*$/.test(cur) : cur.includes(c))) || null,
      inst: (insts.find((x) => cur.includes(x.ko)) || {}).ko || null,
      choice: 'composed', custom: '', short: '', shortTouched: false,
    });
    if (composeMusic(m) !== cur) {
      const pi = (it.phrases || []).findIndex((p) => p.text === cur);
      if (pi >= 0) m.choice = `p${pi}`;
      else if (cur) { m.choice = 'custom'; m.custom = cur; }
    }
    const t = num(it.onset) + 0.3;
    $('#modal-box').innerHTML = `<div class="mdl-grid">
      <section class="mdl-card">
        ${headLeft(it)}
        ${previewHtml(t)}
        <div class="sec">근거<span class="sub">누르면 그 구간을 재생</span></div>
        <div class="ev-rows framed">${evidenceRowsHtml(it, 'pv')}</div>
        <div class="sec">추천</div>
        <div class="rec-row">${pillHtml(it, 'pill-lg')}<span class="draft">초안 문구<b>${esc(cur)}</b></span></div>
      </section>
      <section class="mdl-card">
        <div class="mdl-head"><span class="badge k-music">음악</span><h3 id="modal-title">문구 고치기</h3><span class="muted tnum">${fmtTime(it.onset)}</span>
          <span class="spacer"></span>${CLOSE_BTN}</div>
        <div class="sec">문구 재료</div>
        <div class="mat" id="m-mat">${musicChipsHtml(m)}</div>
        <div class="sec">문구 고르기</div>
        <div class="opts" id="m-opts" role="radiogroup" aria-label="문구 고르기">${musicOptsHtml(m, it)}</div>
        <div class="field"><label for="f-short">짧은 문구</label><input class="input" id="f-short" value="" autocomplete="off">
          <span class="field-hint">넣을 자리가 좁을 때 이 문구로 줄여 넣는다</span></div>
        ${confirmBar('포함 확정')}
      </section></div>`;
    showModal();
    mountPreview(t);
    syncMusic(false);
    focusFirst();
  }
  async function confirmMusic() {
    const m = S.modal, c = chosenMusic(m, D.byId.get(m.id));
    if (!c.text) { toast('문구를 고르거나 입력하세요', 'error'); return; }
    const shortText = String(m.short || '').trim() || c.text;
    const st = await mutate(`api/items/${encodeURIComponent(m.id)}`, { decision: 'include', phrase: c.text, short: shortText }, `포함 확정 · ${cut(c.text, 30)}`);
    if (st) closeModal();
  }

  // 화자 (3.4)
  function avatarHtml(name) {
    const ini = String(name).replace(/\s+/g, '').slice(0, 2) || '?';
    return `<span class="avatar" style="background:${AVATAR[hash(name) % AVATAR.length]}" aria-hidden="true">${esc(ini)}</span>`;
  }
  function whoNote(name, it, scriptName) {
    if (name === scriptName) {
      const face = (it.evidence || []).find((e) => e.kind === '화면');
      return [`대본 = ${name}`, face && face.text].filter(Boolean).join(' · ');
    }
    const n = (S.st.dialogue || []).filter((l) => l.speaker === name).length;
    return n ? `대본 ${n}줄` : '대본에 없는 이름';
  }
  /** The chosen name without brackets: a writer who types "[빌]" or "(빌)" means the name 빌 (the label adds [ ]). */
  function chosenName(m) { return String(m.who === 'new' ? m.newName : m.chars[Number(m.who)] || '').trim().replace(/^[[(]\s*|\s*[\])]$/g, ''); }
  function syncSpeaker() {
    const m = S.modal, it = D.byId.get(m.id);
    for (const el of $$('#modal-box .opt[data-who]')) {
      const on = el.dataset.who === m.who;
      el.classList.toggle('is-on', on);
      el.querySelector('input[type=radio]').checked = on;
    }
    $('#f-show-label').textContent = m.show ? '표시' : '생략';
    const name = chosenName(m);
    const text = speakerText(it);
    // the label as placement will write it for the chosen name: "[이름/출처/언어]"; without a name
    // what is left of it ("[영어]") still stands in front of the line
    const tail = (it.speaker && it.speaker.label_tail) || '';
    const label = nameLabel(m.show ? name : '', tail);
    const mine = (dash, body) => `${esc(dash)}${label ? `<span class="sub-name${m.show && name ? ' is-hl' : ''}">${esc(label)}</span> ` : ''}${esc(body)}`;
    // the line's first cell as placed (long lines are split, two speakers share a hyphen cell):
    // the name goes on the first piece of this line, after its "- "
    const cell = (it.cells || []).map((c) => D.cellById.get(c)).find(Boolean);
    const first = text.trim().split(/\s+/)[0] || '';
    const labels = [spkLabel(it), nameLabel('', tail)].filter(Boolean);     // what this line carries now
    let lines = null;
    if (cell && first) {
      let used = false;
      lines = (cell.lines || []).map((l) => {
        const p = /^(-\s*)?([\s\S]*)$/.exec(l);
        const lab = labels.find((x) => p[2].startsWith(`${x} `));
        const body = lab ? p[2].slice(lab.length + 1) : p[2];
        if (!used && body.startsWith(first)) { used = true; return mine(p[1] || '', body); }
        return esc(l);
      });
      if (!used) lines = null;
    }
    setPvSubs(subsHtml(lines || [mine('', text)]));
    const res = $('#m-result');
    if (res) res.innerHTML = `${ICON.check}${m.show ? '이름 표시' : '이름 생략'}`;
  }
  function openSpeaker(it) {
    const ln = lineOf(it);
    const scriptName = ln ? ln.speaker : null;
    const cur = (it.speaker && it.speaker.name) || scriptName || '';
    const chars = (S.st.characters || []).slice();
    if (cur && !chars.includes(cur)) chars.unshift(cur);
    const key = !ln ? null : ln.speaker != null ? 'speaker' : ln.voice != null ? 'voice' : null;
    const same = key ? D.items.filter((o) => o.kind === 'speaker' && (lineOf(o) || {})[key] === ln[key]).length : 1;
    S.modal = { type: 'speaker', id: it.id, chars, who: cur ? String(chars.indexOf(cur)) : 'new', newName: '', show: it.final !== 'exclude', scope: 'line', scopeKey: key, same };
    const t = (ln ? ln.start : num(it.onset)) + 0.3;
    const near = D.items.filter((o) => o.kind === 'speaker');
    const pos = near.findIndex((o) => o.id === it.id);
    const nb = near.slice(Math.max(0, pos - 2), pos + 3);
    $('#modal-box').innerHTML = `<div class="mdl-grid">
      <section class="mdl-card">
        ${headLeft(it)}
        ${previewHtml(t)}
        <div class="sec">자막의 이름이나 목록의 화자를 누르면 고칠 수 있다</div>
        <div class="nb-lines">${nb.map((o) => `<button type="button" class="nb-line${o.id === it.id ? ' is-cur' : ''}" data-act="edit" data-id="${esc(o.id)}">
          <span class="t tnum">${fmtTime(o.onset)}</span><span class="badge k-speaker">화자</span>
          <span class="tx">${phraseHtml(o)}<small>${esc(summaryOf(o))}</small></span>${pillHtml(o)}</button>`).join('')}</div>
      </section>
      <section class="mdl-card">
        <div class="mdl-head"><span class="badge k-speaker">화자</span><h3 id="modal-title">화자와 표시 고치기</h3><span class="spacer"></span>
          <span class="muted tnum">${fmtTime(ln ? ln.start : it.onset)}</span><span class="quote">「${esc(speakerText(it))}」</span>${CLOSE_BTN}</div>
        <div class="sec">누가 말했나</div>
        <div class="opts" role="radiogroup" aria-label="누가 말했나">
          ${chars.map((name, i) => `<label class="opt" data-who="${i}"><input type="radio" name="m-who" value="${i}">${avatarHtml(name)}<span class="who-name">${esc(name)}</span><span class="who-note">${esc(whoNote(name, it, scriptName))}</span></label>`).join('')}
          <label class="opt opt-custom" data-who="new"><input type="radio" name="m-who" value="new"><span class="plus">+ 새 인물</span><input type="text" class="opt-input" id="f-newname" placeholder="이름 입력" autocomplete="off"></label>
        </div>
        <div class="sec">근거<span class="sub">누르면 그 구간을 재생</span></div>
        <div class="ev-rows framed">${evidenceRowsHtml(it, 'pv')}</div>
        <div class="mdl-row2">
          <div><div class="sec">이름 표시</div><label class="switch"><input type="checkbox" id="f-show" role="switch"${S.modal.show ? ' checked' : ''}><span id="f-show-label">표시</span></label></div>
          <div><div class="sec">바꿀 범위</div><div class="seg seg-scope" role="radiogroup" aria-label="바꿀 범위">
            <label><input type="radio" name="m-scope" value="line" checked><span>이 줄만</span></label>
            <label title="${key ? '대본에서 같은 화자인 줄의 이름을 모두 바꾼다' : '대본 화자가 없는 줄'}"><input type="radio" name="m-scope" value="all"${key ? '' : ' disabled'}><span>같은 화자 줄 모두 · ${same}줄</span></label>
          </div></div>
        </div>
        <div class="mdl-actions"><button type="button" class="btn btn-primary btn-lg" data-act="m-confirm">확정</button><button type="button" class="btn btn-lg" data-act="close">취소</button>
          <span class="result">확정하면 <span class="pill st-inc-done" id="m-result"></span></span></div>
      </section></div>`;
    showModal();
    mountPreview(t);
    syncSpeaker();
    focusFirst();
  }
  async function confirmSpeaker() {
    const m = S.modal, it = D.byId.get(m.id);
    const name = chosenName(m);
    if (!name) { toast('이름을 고르거나 입력하세요', 'error'); const f = $('#f-newname'); if (f) f.focus(); return; }
    const body = { decision: m.show ? 'include' : 'exclude' };
    const scope = m.scope === 'all' && m.scopeKey ? m.scopeKey : 'line';
    if (name !== ((it.speaker && it.speaker.name) || '') || scope !== 'line') { body.name = name; body.scope = scope; }
    const msg = scope !== 'line'
      ? `같은 화자 ${m.same}줄의 이름을 ${name}${ro(name)} 바꿈 · 이 줄은 ${m.show ? '이름 표시' : '이름 생략'}`
      : `${m.show ? '이름 표시' : '이름 생략'} · ${cut(m.show ? `${nameLabel(name, (it.speaker && it.speaker.label_tail) || '')} ${speakerText(it)}` : speakerText(it), 34)}`;
    const st = await mutate(`api/items/${encodeURIComponent(m.id)}`, body, msg);
    if (st) closeModal();
  }

  // 내보내기 전 검사 (3.5)
  function violText(v) {
    if (typeof v === 'string') return v;
    if (v && typeof v === 'object') return v.msg || v.message || v.text || [v.rule || v.kind, v.value, v.limit].filter((x) => x != null).join(' ');
    return String(v);
  }
  function xpRow(kind, name, desc, actions = '') {
    const icon = { ok: '✓', warn: '!', bad: '!', info: '…' }[kind];
    return `<div class="xp-row"><span class="xi ${kind}" aria-hidden="true">${icon}</span><span class="nm2">${name}</span><span class="ds">${desc}</span><span class="ac">${actions}</span></div>`;
  }
  function savedHtml() {
    const r = S.lastExport;
    if (!r) return '';
    return `<div class="saved"><span class="xi ok" aria-hidden="true">✓</span>
      <div><b>${esc(r.format.toUpperCase())} 저장함</b> <span class="muted">· ${num(r.cells)}칸</span><div class="path">${esc(r.path)}</div>
      ${(r.warnings || []).length ? `<ul class="warns">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}</div>
      <div class="ac">${HOME ? '<button type="button" class="btn btn-sm" data-act="open-folder">폴더 열기</button>' : ''}<a class="btn btn-sm" href="${esc(r.url)}" download>파일 받기</a><button type="button" class="btn btn-sm" data-act="copy-path">경로 복사</button></div></div>`;
  }
  /** 지침 검사 (cboe/check.py): the rules that were broken, obligations first, each with its cells. */
  function guideHtml(gd) {
    if (!gd || !(gd.rules || []).length) return '';
    const rules = gd.rules.slice().sort((a, b) => (a.level === 'must' ? 0 : 1) - (b.level === 'must' ? 0 : 1));
    return `<div class="sec">지침 검사<span class="sub">조항별 · 칸을 누르면 그 자리로</span></div>
      <div class="guide">${rules.map((r) => {
        const cells = (r.cells || []).slice(0, 12).map((cid) => {
          const c = D.cellById.get(cid);
          return `<button type="button" class="g-cell" data-act="goto-cell" data-cell="${esc(cid)}" title="${esc(c ? (c.lines || []).join(' / ') : cid)}">${c ? fmtTime(c.start) : esc(cid)}</button>`;
        }).join('');
        const more = (r.cells || []).length > 12 ? `<span class="muted">외 ${r.cells.length - 12}칸</span>` : '';
        return `<div class="g-rule"><span class="g-lv ${r.level === 'must' ? 'must' : 'should'}">${r.level === 'must' ? '의무' : '권고'}</span>
          <span class="g-cl tnum">${esc(r.clause)}</span><span class="g-tt">${esc(r.title)}</span><span class="g-n tnum">${num(r.count)}건</span>
          <span class="g-cells">${cells}${more}</span></div>`;
      }).join('')}</div>`;
  }
  function renderExport() {
    const box = $('#modal-box');
    const scroll = $('#modal-box').scrollTop;
    const chk = S.st.export_check;
    const st = STYLE();
    let body;
    if (!chk) body = `<div class="ev-empty">${esc(S.st.error || '검사를 할 수 없습니다')}</div>`;
    else {
      const nC = chk.check.length, nN = chk.no_room.length, nE = chk.exclude_recommended.length, nV = chk.violations.length;
      const gd = chk.guide || null, gs = (gd && gd.summary) || {}, fl = chk.flags || {};
      const nEst = (fl.timing_estimated || []).length, nOv = (fl.overlap || []).length, nFl = (fl.flash || []).length;
      const firstOf = (ids) => `<button type="button" class="btn btn-sm" data-act="goto-cell" data-cell="${esc(ids[0])}">첫 칸 보기</button>`;
      body = `<div class="sec">남은 항목</div>
        <div class="xp-list">
          ${xpRow(nC ? 'warn' : 'ok', '확인 필요', nC ? `${nC}개 · 목록에서 정한다 (정하지 않으면 자막에 넣지 않음)` : '0개 · 모두 작가가 정함',
            nC ? '<button type="button" class="btn btn-sm" data-act="xp-goto" data-filter="check">목록에서 보기</button>' : '')}
          ${(chk.check_speaker || []).length ? xpRow('warn', '화자 확인 필요', `${chk.check_speaker.length}줄 · 정하지 않으면 이름 없이 대사만 나감`,
            '<button type="button" class="btn btn-sm" data-act="xp-goto" data-filter="check">목록에서 보기</button>') : ''}
          ${xpRow(nN ? 'bad' : 'ok', '배치 확인 필요', nN ? `${nN}개 · 아래에서 정한다` : '0개 · 모두 자리를 찾음')}
          ${xpRow(nE ? 'info' : 'ok', '제외 추천', nE ? `${nE}개 · 목록을 훑어본 뒤 묶어서 확정` : '모두 확정함',
            nE ? '<button type="button" class="btn btn-sm" data-act="xp-goto" data-filter="exclude">목록에서 보기</button><button type="button" class="btn btn-sm btn-primary" data-act="bulk-exclude">묶어서 확정</button>' : '')}
          ${xpRow(nV ? 'warn' : 'ok', '가독성 검사', `${num(st.max_units)}자 · ${num(st.max_lines)}줄 · 초당 ${num(st.cps)}자${st.profile === 'children' ? ' (어린이)' : ''} · 표시 시간${nV ? ` · ${nV}칸 넘음` : ' · 모두 지킴'}`)}
          ${gd ? xpRow(gs.must ? 'bad' : gs.should ? 'warn' : 'ok', '지침 검사',
            gs.must || gs.should ? `의무 ${num(gs.must)}건 (${num(gs.cells_must)}칸) · 권고 ${num(gs.should)}건 (${num(gs.cells_should)}칸) · 아래 조항별 목록`
              : '표기 · 문장부호 · 시간 · 장면 전환 조항에 걸린 칸 없음') : ''}
          ${nFl ? xpRow('bad', '몇 프레임짜리 칸', `${nFl}칸 · 읽을 수 없는 길이로 나간다`, firstOf(fl.flash)) : ''}
          ${nOv ? xpRow('warn', '겹친 대사', `${nOv}칸 · 두 줄의 말이 겹쳐 시작이나 길이를 양보했다`, firstOf(fl.overlap)) : ''}
          ${nEst ? xpRow('info', '시각 추정', `${nEst}칸 · 받아쓰기로 못 맞춘 줄이라 시작·끝을 확인한다`, firstOf(fl.timing_estimated)) : ''}
          ${(chk.unnamed || []).length ? xpRow('warn', '문구 없는 소리', `${chk.unnamed.length}개 · [소리] 그대로라 문구를 정해야 한다`,
            `<button type="button" class="btn btn-sm btn-primary" data-act="edit" data-id="${esc(chk.unnamed[0])}">문구 고치기</button>`) : ''}
        </div>
        ${nV ? `<div class="viol">${chk.violations.map((v) => {
          const c = D.cellById.get(v.cell);
          return `<button type="button" data-act="goto-cell" data-cell="${esc(v.cell)}"><span class="t tnum">${c ? fmtTime(c.start) : esc(v.cell)}</span><span class="tx">${esc(c ? (c.lines || []).join(' / ') : '')}</span><span class="why">${esc((v.violations || []).map(violText).join(', '))}</span></button>`;
        }).join('')}</div>` : ''}
        ${chk.no_room.map((e) => { const it = D.byId.get(e.item); return it ? noRoomHtml(it) : ''; }).join('')}
        ${guideHtml(gd)}
        ${savedHtml()}
        <div class="xp-note">저장하면 작업 폴더의 export 에 쓴다 · 확인 필요와 제외 항목은 자막에 넣지 않는다 · TTML 은 프레임 단위 시각과 위·아래 위치를 담는다</div>`;
    }
    const fmt = exportFmt(S.modal.fmt);
    box.innerHTML = `<div class="xp">
      <div class="xp-head"><span class="logo" aria-hidden="true">SDH</span><h3 id="modal-title">내보내기 전 검사</h3><span class="muted">${esc(S.P.title)}</span><span class="spacer"></span>
        ${['ttml', 'vtt', 'srt'].filter((f) => st.formats.includes(f)).map((f) => `<button type="button" class="btn${fmt === f ? ' btn-primary' : ''}" data-act="export" data-fmt="${f}">${f.toUpperCase()} 저장</button>`).join('')}
        <button type="button" class="icon-btn" data-act="close" aria-label="닫기">×</button></div>
      <div class="xp-body">${body}</div></div>`;
    box.scrollTop = scroll;
  }
  /** One of the formats the server writes (srt when unknown). */
  const exportFmt = (f) => (STYLE().formats.includes(f) ? f : 'srt');
  function openExport(fmt) {
    pauseMain();
    teardownPreview();
    S.modal = { type: 'export', fmt };
    S.lastExport = null;
    renderExport();
    showModal();
    const b = $(`#modal-box [data-act="export"][data-fmt="${exportFmt(fmt)}"]`);
    if (b) b.focus();
  }
  async function doExport(fmt) {
    setBusy(true);
    try {
      const res = await api('POST', 'api/export', { format: fmt });
      S.lastExport = res;
      if (S.modal && S.modal.type === 'export') S.modal.fmt = fmt;
      applyState(res.state, { keepExport: true });
      toast(`${fmt.toUpperCase()} 저장함 · ${res.name}`);
    } catch (e) {
      toast(e.message || String(e), 'error', 4500);
    } finally {
      setBusy(false);
    }
  }
  /** Show the saved file in Explorer (desktop window: its own bridge; browser: the studio server). */
  async function openFolder() {
    const p = S.lastExport && S.lastExport.path;
    if (!p) return;
    const dir = p.replace(/[\\/][^\\/]*$/, '');
    try {
      const bridge = window.pywebview && window.pywebview.api;
      if (bridge && bridge.open_path) { if (!(await bridge.open_path(dir))) throw new Error('폴더가 없습니다'); }
      else await api('POST', `${HOME}api/open`, { path: dir });
    } catch (e) { toast(e.message || '폴더를 열지 못했습니다', 'error'); }
  }
  async function copyPath() {
    const p = S.lastExport && S.lastExport.path;
    if (!p) return;
    try { await navigator.clipboard.writeText(p); toast('경로를 복사했습니다'); } catch (_) { toast('복사하지 못했습니다', 'error'); }
  }
  function confirmModal() {
    const m = S.modal;
    if (!m) return;
    if (m.type === 'sfx') confirmSfx();
    else if (m.type === 'music') confirmMusic();
    else if (m.type === 'speaker') confirmSpeaker();
  }
  function focusFirst() {
    const el = $('#modal-box .opt.is-on input[type=radio]') || $('#modal-box [data-act="m-confirm"]');
    if (el) el.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------- events
  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-act]');
    if (!el) {
      const row = ev.target.closest('.row[data-id]');
      if (row) select(row.dataset.id);
      return;
    }
    const id = el.dataset.id;
    switch (el.dataset.act) {
      case 'decide': decide(id, el.dataset.decision || null); break;
      case 'edit': openEdit(id); break;
      case 'name': ev.stopPropagation(); openEdit(id); break;
      case 'filter': setFilter(el.dataset.filter, !!el.dataset.toggle); break;
      case 'kind': toggleKind(el.dataset.kind); break;
      case 'zoom': setZoom(Number(el.dataset.zoom)); break;
      case 'toggle-play': togglePlay(); break;
      case 'play-ev': { const it = D.byId.get(id); if (it) playEvidence(it, Number(el.dataset.i), el.dataset.target, el); break; }
      case 'place': {
        if (el.dataset.action === 'move') {
          const inp = el.closest('.nr-card') && el.closest('.nr-card').querySelector('.time-input');
          const t = parseTime(inp && inp.value);
          if (!Number.isFinite(t)) { toast('시각을 03:52.4 처럼 적어 주세요', 'error'); if (inp) inp.focus(); break; }
          S.moveOpen = null;
          placement(id, 'move', t);
        } else placement(id, el.dataset.action);
        break;
      }
      case 'move-open': openMoveForm(id, !!el.closest('#modal-box')); break;
      case 'swap-why': toast(el.dataset.reason, 'error', 4200); break;
      case 'move-now': { const inp = el.closest('.nr-card') && el.closest('.nr-card').querySelector('.time-input'); if (inp) inp.value = fmtTime(S.t); break; }
      case 'open-export': openExport(el.dataset.fmt); break;
      case 'export': doExport(el.dataset.fmt); break;
      case 'bulk-exclude': bulkExclude(); break;
      case 'xp-goto': closeModal(); setFilter(el.dataset.filter); break;
      case 'goto-cell': gotoCell(el.dataset.cell); break;
      case 'copy-path': copyPath(); break;
      case 'open-folder': openFolder(); break;
      case 'close': closeModal(); break;
      case 'm-confirm': confirmModal(); break;
      case 'm-chip': musicChip(el.dataset.group, el.dataset.val); break;
      default: break;
    }
  });
  document.addEventListener('dblclick', (ev) => {
    const row = ev.target.closest('.row[data-id]');
    if (row) openEdit(row.dataset.id);
  });
  // Mouse clicks must not leave focus on toolbar buttons, so Space/Enter keep working as shortcuts.
  $('#app').addEventListener('mousedown', (ev) => {
    if (ev.target.closest('button, .row, .blk') && !ev.target.closest('input')) ev.preventDefault();
  });

  // dialog form inputs
  $('#modal-box').addEventListener('change', (ev) => {
    const m = S.modal;
    if (!m) return;
    const t = ev.target;
    if (t.name === 'm-opt') { m.choice = t.value; if (m.type === 'sfx') syncSfx(); else syncMusic(false); }
    else if (t.name === 'm-who') { m.who = t.value; syncSpeaker(); if (t.value === 'new') $('#f-newname').focus(); }
    else if (t.id === 'f-show') { m.show = t.checked; syncSpeaker(); }
    else if (t.name === 'm-scope') { m.scope = t.value; }
  });
  $('#modal-box').addEventListener('input', (ev) => {
    const m = S.modal;
    if (!m) return;
    const t = ev.target;
    if (t.id === 'f-custom') { m.custom = t.value; m.choice = 'custom'; if (m.type === 'sfx') syncSfx(); else syncMusic(false); }
    else if (t.id === 'f-short') { m.short = t.value; m.shortTouched = true; }
    else if (t.id === 'f-newname') { m.newName = t.value; m.who = 'new'; syncSpeaker(); }
  });
  $('#modal-box').addEventListener('focusin', (ev) => {
    const m = S.modal;
    if (!m) return;
    if (ev.target.id === 'f-custom' && m.choice !== 'custom') { m.choice = 'custom'; if (m.type === 'sfx') syncSfx(); else syncMusic(false); }
    if (ev.target.id === 'f-newname' && m.who !== 'new') { m.who = 'new'; syncSpeaker(); }
  });

  document.addEventListener('keydown', (ev) => {
    if (ev.isComposing || ev.keyCode === 229) return;
    const tgt = ev.target;
    const inInput = !!(tgt.closest && tgt.closest('input, textarea, select, [contenteditable="true"]'));
    if (inInput && tgt.classList.contains('time-input') && (ev.code === 'Enter' || ev.code === 'NumpadEnter')) {
      ev.preventDefault();
      const go = tgt.closest('.nr-card').querySelector('[data-action="move"]');
      if (go) go.click();
      return;
    }
    if (S.modal) {
      if (ev.code === 'Escape') { ev.preventDefault(); closeModal(); }
      else if ((ev.code === 'Enter' || ev.code === 'NumpadEnter') && inInput && tgt.type === 'text' && S.modal.type !== 'export') { ev.preventDefault(); confirmModal(); }
      return;
    }
    if (inInput || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const onButton = !!(tgt.closest && tgt.closest('button, a[href]'));
    switch (ev.code) {
      case 'Space': if (onButton) return; ev.preventDefault(); togglePlay(); break;
      case 'ArrowUp': ev.preventDefault(); moveSel(-1); break;
      case 'ArrowDown': ev.preventDefault(); moveSel(1); break;
      case 'Enter': case 'NumpadEnter': if (onButton) return; ev.preventDefault(); acceptRec(); break;
      case 'KeyI': ev.preventDefault(); if (S.sel) decide(S.sel, 'include'); break;
      case 'KeyE': ev.preventDefault(); if (S.sel) decide(S.sel, 'exclude'); break;
      default: break;
    }
  });

  // timeline: click a block to select it, click or drag elsewhere to seek
  const tlScroll = $('#tl-scroll');
  const tlTime = (ev) => S.P.t0 + (ev.clientX - $('#tl-inner').getBoundingClientRect().left) / S.pps;
  tlScroll.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || !S.P || ev.target === tlScroll) return;     // the scrollbar itself
    const blk = ev.target.closest('.blk');
    if (blk && blk.dataset.id) { select(blk.dataset.id); return; }
    if (blk && blk.dataset.cell) { gotoCell(blk.dataset.cell); return; }
    ev.preventDefault();
    clearRange(video);
    seekTo(tlTime(ev));
    tlScroll.setPointerCapture(ev.pointerId);
    const move = (e2) => seekTo(tlTime(e2));
    const up = () => {
      tlScroll.removeEventListener('pointermove', move);
      tlScroll.removeEventListener('pointerup', up);
      tlScroll.removeEventListener('pointercancel', up);
    };
    tlScroll.addEventListener('pointermove', move);
    tlScroll.addEventListener('pointerup', up);
    tlScroll.addEventListener('pointercancel', up);
  });

  let resizeRaf = 0;
  function onResize() {
    cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(() => {
      if (!S.P) return;
      layoutStage();
      const sc = $('#tl-scroll');
      const keep = (sc.scrollLeft + sc.clientWidth / 2) / S.pps + S.P.t0;
      renderTimeline();
      sc.scrollLeft = Math.max(0, (keep - S.P.t0) * S.pps - sc.clientWidth / 2);
    });
  }
  window.addEventListener('resize', onResize);
  if (window.ResizeObserver) new ResizeObserver(onResize).observe($('#left'));

  // --------------------------------------------------------------------- init
  async function init() {
    if (HOME) { const a = $('#home-link'); a.href = HOME; a.hidden = false; }
    bindVideo();
    try {
      const st = await api('GET', 'api/state');
      applyState(st);
      layoutStage();
      renderTimeline();
      if (S.P.video) video.src = S.P.video_url;
      else showStageMsg('영상 파일을 찾을 수 없습니다 · project.json 의 video 경로를 확인하세요');
      const first = D.visible[0];
      if (first) select(first.id);
      else seekTo(S.P.t0);
    } catch (e) {
      const ban = $('#banner');
      ban.hidden = false;
      ban.textContent = `상태를 불러오지 못했습니다 · ${e.message || e}`;
    }
  }
  init();
})();
