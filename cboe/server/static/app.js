/* CBoE 검수 화면 (deck 3.1~3.5, 5.10). Vanilla JS, no build step.
   Every change is POSTed; list, evidence, timeline and the video overlay are all
   re-rendered from the state the server answers with (placement recomputed). A field the
   writer is filling in and a value still on its way to the server survive that re-render (the
   focus too, but only in a text field being typed in: a switch or a select that stored its value
   leaves the keys to the page); a write the server refuses puts the panel back to what is stored.
   A decision the writer made that is on no line since the script was read again (state aside) is
   listed at the top of the 대사 view: only the writer puts it on a line or lets it go.
   The page works in film time. project.media says which file /media/video gives: the source
   (film time = video time) or the 재생용 영상 of the processed part (film time = video time +
   media.t0); while that copy is being made the stage shows stills (/api/frame, film time). */
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
  /** 가 / 이 after a name (바다가 · 하늘이). */
  function ga(word) {
    const c = String(word).charCodeAt(String(word).length - 1);
    return c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 ? '이' : '가';
  }
  /** mm:ss.ss for the times the writer types (a frame is shorter than 0.1 s). */
  function fmtTime2(t) {
    if (t == null || !Number.isFinite(t)) return '';
    const c = Math.round(Math.max(0, t) * 100);
    const m = Math.floor(c / 6000);
    return `${String(m).padStart(2, '0')}:${((c - m * 6000) / 100).toFixed(2).padStart(5, '0')}`;
  }
  const enc = encodeURIComponent;

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
  const FLAGS = { label_uncertain: '종류 불확실', unknown: '목록 밖 소리', conflict: '근거 어긋남', script: '대본 단서', timing_estimated: '시각 어림' };
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
    return { ruler: v('--tl-ruler', 26), band: v('--tl-band', 12), pad: v('--tl-pad', 6), lane: v('--tl-lane', 26), gap: v('--tl-gap', 8) };
  }
  const ICON = {
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z"/></svg>',
    check: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  // the five views of the right-hand list; the left panel shows what is picked in the view
  const VIEWS = [['items', '후보'], ['lines', '대사'], ['extras', '대본에 없는 말'], ['people', '인물'], ['cells', '칸']];
  const VIEW_HINT = {
    items: '검출한 소리를 지우지 않고 모두 남긴다',
    lines: '대본 대사 줄 · 다른 줄은 대본 글과 들린 글을 견주어 작가가 고른다',
    extras: '받아쓰기에만 있는 말 · 저절로 넣지 않는다',
    people: '이름이 밝혀지기 전에 쓸 표시와 이름으로 바뀌는 줄',
    cells: '자막 칸마다 지침 검사 결과',
  };
  const TODO_TITLE = {
    items: '정하지 않은 확인 필요 후보 (화자 포함)', lines: '대본과 들린 말이 다른데 고르지 않은 줄 + 자리 없는 줄',
    extras: '정하지 않은 대본에 없는 말 (받아쓰기 헛소리 의심은 접어 두고 세지 않음)', people: '표시를 정하지 않은 인물 (자막 줄이 있는 사람 이름)', cells: '의무 조항에 걸린 칸',
  };
  /** A dialogue line's state on the list: [label, class]. */
  const LSTATE = {
    ok: ['맞음', 'ls-ok'], pinned: ['시각 정함', 'ls-ok'], differs: ['들린 말이 다름', 'ls-diff'], picked: ['고름', 'ls-done'],
    unheard: ['못 들음 · 시각 추정', 'ls-guess'], absent: ['영화에 없음', 'ls-out'], out_of_range: ['구간 밖', 'ls-out'],
    no_time: ['시각 없음', 'ls-out'], writer: ['작가가 뺌', 'ls-out'], added: ['작가가 넣음', 'ls-add'],
    revived: ['되살림', 'ls-add'], noroom: ['자리 없음', 'ls-noroom'],
  };
  const PICKED = { script: '대본대로 고름', heard: '들린 대로 고름', writer: '작가가 고침' };
  const LINE_FILTERS = [['all', '전체'], ['differs', '다름'], ['unheard', '못 들음'], ['out', '영화에 없음·구간 밖'],
    ['spelling', '맞춤법 확인'], ['lang', '외국어·노래'], ['noroom', '자리 없음'], ['writer', '작가가 고침']];
  const EXTRA_FILTERS = [['open', '정하지 않음'], ['overlap', '못 들은 줄과 겹침'], ['suspect', '헛소리 의심'], ['line', '대사로 넣음'], ['ignored', '무시'], ['all', '전체']];
  /** 받아쓰기 헛소리 (align ``suspect``): what the ASR model writes where nobody speaks, and the
      scores that tell nobody speaks there (``suspect_by``). */
  const SUSPECT_KO = { greeting: '인사말만 들림', credit: '자막·구독 안내 같은 글', repeat: '같은 말이 되풀이됨' };
  const SUSPECT_BY = { speech: '말소리(SED)', music: '음악(SED)', vad: '말소리 구간(VAD)', p: '낱말 확률(받아쓰기)' };
  const PEOPLE_FILTERS = [['all', '전체'], ['open', '정하지 않음'], ['early', '이름이 일찍 나감'], ['decided', '정함']];
  const CELL_FILTERS = [['all', '전체'], ['must', '의무 위반'], ['should', '권고'], ['estimated', '시각 추정'], ['overlap', '겹침']];
  const XSTATE = { open: ['정하지 않음', 'st-check'], line: ['대사로 넣음', 'st-inc-done'], ignored: ['무시', 'st-exc-done'] };
  const SCENE_ST = { found: '찾음', unknown: '모름', absent: '영화에 없음', out_of_range: '구간 밖' };
  const DIFF_KO = { word: '낱말이 다름', minor: '한두 글자 다름 · 받아쓰기 잘못일 수 있음', number: '숫자 표기가 다름' };
  const CELL_KIND = { dialogue: '대사', sfx: '효과음', music: '음악', mixed: '섞임' };
  const SCOPES = [['line', '이 줄'], ['from_here', '이 줄부터 끝까지'], ['before', '이 줄 앞까지'], ['scene', '이 장면'], ['person', '이 인물의 모든 줄']];
  const LANGS = ['영어', '일본어', '중국어', '독일어', '프랑스어', '러시아어', '스페인어'];
  /** Where a line comes from (state.SOURCES): the menu of the line panel. The word that goes with
      the name ('[하늘/속마음]') is the server's (style.SOURCE_LABELS); 화면 밖 writes no word. */
  const SOURCE_KO = { offscreen: '화면 밖 (표시 없음)', inner: '속마음', video: '영상', phone: '전화', radio: '무전', broadcast: '방송', narration: '내레이션' };
  /** The reason the server ends with when the speaker's line before is still 확인 필요 (state.WAITS). */
  const WAITS = '앞 줄을 먼저 정해야 함';
  /** A decision of the writer that is on no line since the script changed (state ``aside``). */
  const ASIDE_WHY = { text_changed: '대본 글이 바뀜', same_words: '같은 글 줄이 여럿', gone: '대본에서 사라짐' };
  const ASIDE_FROM = { script: '대본 글로 고름', heard: '들린 글로 고름' };
  const GENERIC = ['남자', '여자'];
  const ENV_LANE = { speech: 'cells', effects: 'sfx', music: 'music' };
  const CHUNK = 200;                 // rows drawn at a time; the rest when the list is scrolled down

  // -------------------------------------------------------------------- state
  const S = {
    st: null, P: null, sel: null, filter: 'all', kinds: new Set(Object.keys(KIND)), zoom: 15,
    t: 0, pps: 100, modal: null, moveOpen: null, lastExport: null, busy: 0, still: false,
    overlayKey: '', lastFocus: null,
    // views: what is picked in each, its filter, scenes opened or closed by hand, the open editor
    view: 'items', lf: 'all', xf: 'open', pf: 'all', cf: 'all', line: null, extra: null, person: null, cell: null,
    cellAt: null, showMinor: false, sceneOpen: new Map(), limit: CHUNK, ed: null, env: null, envOn: true, envTried: false,
    showSuspect: false, asideOpen: true,
    // 재생용 영상: project.media as last seen, which file the stage has (state|t0), its ?v=, whether
    // the server was asked to make the copy; canPlay: the <video> plays (with stills: its sound only)
    media: null, mediaKey: null, mediaVer: 0, buildAsked: false, canPlay: true,
  };
  const D = {
    items: [], byId: new Map(), lines: new Map(), allLines: [], cells: [], cellById: new Map(), noRoom: new Map(),
    lineNoRoom: new Map(), spkByLine: new Map(), visible: [], extras: [], extraById: new Map(), overlaps: new Map(),
    scenes: [], sceneById: new Map(), people: [], personByName: new Map(), added: new Map(), cellViol: new Map(),
    visibleLines: [], visibleExtras: [], visiblePeople: [], visibleCells: [],
  };
  const video = $('#video');
  const pvVideo = () => $('#pv-video');

  // ---------------------------------------------------------------------- api
  let chain = Promise.resolve();
  /** What the toast says when a request fails: the server's own Korean sentence (``detail``), or a
      Korean sentence of ours in front of what there is (no connection, a page of an error, 422 list). */
  function failText(method, status, data) {
    const d = data && data.detail;
    if (typeof d === 'string' && d) return d;
    const what = method === 'GET' ? '읽지 못했습니다' : '저장하지 못했습니다';
    if (Array.isArray(d)) return `보낸 값이 맞지 않아 ${what} · 서버 답 ${status} · ${d.map((x) => x && x.msg).filter(Boolean).join(', ')}`;
    return `서버 오류 ${status} · ${what}`;
  }
  const NO_SERVER = '서버에 연결할 수 없습니다 · 검수 서버가 켜져 있는지 확인하세요';
  /** Requests run one after another so responses are applied in order. */
  function api(method, url, body) {
    const run = async () => {
      const init = { method, headers: {} };
      if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
      let res;
      try { res = await fetch(url, init); } catch (_) { throw new Error(NO_SERVER); }
      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }
      if (!res.ok) throw new Error(failText(method, res.status, data));
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
      // the server kept nothing: the left panel is drawn again from the last state it sent, so a
      // switch, a box or a cut the browser already changed goes back to what is stored
      if (S.st) renderDetail();
      return null;
    } finally {
      setBusy(false);
    }
  }
  /** Fields whose new value is on its way to the server ({line id|field: {v, n}}; a person's under
      'person:<name>'): the left panel shows that value until the last request for it is
      answered, so the answer of an earlier write does not take back a click the writer made
      after it (끊을 자리 clicked fast, a reveal line picked right after 끝까지 일반 표시). */
  const pend = new Map();
  const PEND_FIELDS = ['breaks', 'keep', 'sung', 'language', 'source'];
  const PEND_PERSON = ['reveal', 'reveal_line'];
  function pendOf(ln, f) {
    const p = pend.get(`${ln.id}|${f}`);
    return p ? p.v : ln[f];
  }
  /** The line's own source decision as the panel shows it: null = the script's, '' = none. */
  function sourceOf(ln) {
    const p = pend.get(`${ln.id}|source`);
    if (p) return p.v;
    const d = ln.decision || {};
    return 'source' in d ? d.source : null;
  }
  function pendStart(id, fields, names = PEND_FIELDS) {
    const keys = names.filter((f) => f in fields).map((f) => `${id}|${f}`);
    for (const k of keys) {
      const p = pend.get(k) || { n: 0 };
      p.v = fields[k.slice(id.length + 1)];
      p.n += 1;
      pend.set(k, p);
    }
    return keys;
  }
  /** One request for these fields is answered: when it was the last, the panel shows the server's value. */
  function pendEnd(keys) {
    let cleared = false;
    for (const k of keys) {
      const p = pend.get(k);
      if (p && (p.n -= 1) <= 0) { pend.delete(k); cleared = true; }
    }
    if (cleared && S.st) renderDetail();
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
    // the merged lines in film order (the writer's choices, why a line is out); an older state: the script lines
    D.allLines = st.lines || (st.dialogue || []).map((l) => ({ ...l, include: l.start != null }));
    D.lines = new Map(D.allLines.map((l) => [l.id, l]));
    D.cells = ((st.draft && st.draft.cells) || []).slice().sort((a, b) => a.start - b.start);
    D.cellById = new Map(D.cells.map((c) => [c.id, c]));
    const nr = (st.draft && st.draft.no_room) || [];
    D.noRoom = new Map(nr.filter((e) => !e.line).map((e) => [e.item, e]));        // sounds without a cell
    D.lineNoRoom = new Map(nr.filter((e) => e.line).map((e) => [e.line, e]));     // guessed lines squeezed out
    D.items = (st.items || []).slice().sort((a, b) => num(a.onset) - num(b.onset)
      || (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) || a.id.localeCompare(b.id));
    D.byId = new Map(D.items.map((it) => [it.id, it]));
    D.spkByLine = new Map();
    for (const it of D.items) if (it.kind === 'speaker' && it.speaker && it.speaker.line_id) D.spkByLine.set(it.speaker.line_id, it);
    D.extras = st.extras || [];
    D.extraById = new Map(D.extras.map((x) => [x.id, x]));
    D.overlaps = new Map();                     // unheard line -> the speech heard where it should be
    for (const x of D.extras) {
      for (const lid of x.overlaps || []) { if (!D.overlaps.has(lid)) D.overlaps.set(lid, []); D.overlaps.get(lid).push(x); }
    }
    D.scenes = st.scenes || [];
    D.sceneById = new Map(D.scenes.map((s) => [s.id, s]));
    D.people = st.people || [];
    D.personByName = new Map(D.people.map((p) => [p.name, p]));
    D.added = new Map((st.added || []).map((a) => [a.id, a]));
    D.cellViol = new Map();                     // cell -> what the guide check found on it
    const gd = st.export_check && st.export_check.guide;
    for (const v of (gd && gd.violations) || []) { if (!D.cellViol.has(v.cell)) D.cellViol.set(v.cell, []); D.cellViol.get(v.cell).push(v); }
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
  /** A small sound found by the level jump alone stays folded until the writer opens the fold (4-03). */
  const isFolded = (it) => !!it.minor && !it.decided && !S.showMinor && it.id !== S.sel;
  /** Short tags of where a candidate comes from: the script, the writer, a chain it follows. */
  function itemTags(it) {
    const t = [];
    if (it.added) t.push('<span class="tag t-writer">작가가 넣음</span>');
    if (it.origin === 'script') t.push('<span class="tag t-script">대본 · 시각 어림</span>');
    else if (it.cue_id) t.push('<span class="tag t-script">대본 근거</span>');
    if (it.music && it.music.event === 'stop') t.push('<span class="tag">음악 멈춤</span>');
    if (it.follows && it.kind !== 'speaker') t.push('<span class="tag">딸림</span>');
    if (it.minor) t.push('<span class="tag t-minor">작은 소리</span>');
    return t.join('');
  }

  // -------------------------------------------------------------------- apply
  function applyState(st, { keepExport = false } = {}) {
    derive(st);
    if (!keepExport) S.lastExport = null;
    if (S.sel && !D.byId.has(S.sel)) S.sel = null;
    if (S.line && !D.lines.has(S.line)) S.line = null;
    if (S.extra && !D.extraById.has(S.extra)) S.extra = null;
    if (S.person && !D.personByName.has(S.person)) S.person = null;
    if (S.cell) {         // cell ids are numbered again by every placement: keep the cell at the same time
      const c = D.cells.find((x) => Math.abs(x.start - num(S.cellAt, -99)) < 0.05) || null;
      S.cell = c ? c.id : null;
    }
    if (S.ed && !(D.lines.has(S.ed.id) || D.extraById.has(S.ed.id))) S.ed = null;
    renderHeader();
    renderTabs();
    renderChips();
    renderList({ keep: true });
    renderDetail();
    renderTimeline();
    S.overlayKey = '';
    updateOverlay();
    if (S.modal && S.modal.type === 'export') renderExport();
    syncMedia(mediaOf(S.P));
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
    const kc = $('#kind-chips');
    kc.hidden = S.view !== 'items';
    $('#view-note').textContent = viewNote();
    const vm = viewModel();
    if (vm) {
      const cur = S[vm.key];
      // 대본에 없는 말: an undecided 받아쓰기 헛소리 의심 is not counted on a chip (but its own),
      // the same count as the tab and the note above the list
      const xs = S.view === 'extras' ? suspectOpen() : 0;
      $('#state-chips').innerHTML = vm.filters.map(([key, label]) => {
        const out = (r) => xs && key !== 'suspect' && isSuspect(r);
        const n = vm.rows.filter((r) => vm.pass(r, key) && !out(r)).length;
        const on = cur === key;
        const title = xs && key !== 'suspect' && vm.rows.some((r) => vm.pass(r, key) && out(r)) ? ` title="받아쓰기 헛소리 의심은 세지 않음 · '헛소리 의심'에서 본다"` : '';
        return `<button type="button" class="chip${on ? ' is-on' : ''}" data-act="filter" data-filter="${key}" data-toggle="1" aria-pressed="${on}"${title}>${label}<span class="n">${n}</span></button>`;
      }).join('');
      return;
    }
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
    return `<div class="row${sel ? ' is-sel' : ''}${D.noRoom.has(it.id) ? ' is-noroom' : ''}" role="option" id="row-${esc(it.id)}" data-view="items" data-id="${esc(it.id)}" aria-selected="${sel}">
      <span class="r-time tnum">${fmtTime(it.onset)}</span>
      <span class="badge ${k.cls}">${esc(k.label)}</span>
      <span class="r-main"><span class="r-phrase">${phraseHtml(it)}</span><span class="r-sub">${itemTags(it)}${esc(summaryOf(it))}</span></span>
      ${pillHtml(it)}
    </div>`;
  }
  /** Rows of a long list a chunk at a time: the rest come when the list is scrolled to the end. */
  function renderRows(box, rows, selIdx, tail, empty) {
    if (!rows.length && !tail) { box.innerHTML = `<div class="empty">${empty}</div>`; return; }
    const lim = Math.max(S.limit, selIdx >= 0 ? selIdx + 40 : 0);
    const more = rows.length > lim ? `<button type="button" class="more-row" data-act="more">아래 ${rows.length - lim}개 더 보기</button>` : '';
    box.innerHTML = rows.slice(0, lim).join('') + more + (tail || '');
    watchMore(box);
  }
  let moreObs = null;
  function watchMore(box) {
    const el = box.querySelector('.more-row');
    if (!el || !window.IntersectionObserver) return;
    if (!moreObs) moreObs = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) showMore(); }, { root: box, rootMargin: '240px' });
    moreObs.disconnect();
    moreObs.observe(el);
  }
  function showMore() {
    const box = $('#rows'), top = box.scrollTop;
    S.limit += CHUNK;
    renderList({ keep: true });
    box.scrollTop = top;
  }
  function finishList(opts, rowId) {
    const box = $('#rows');
    const row = rowId && document.getElementById(rowId);
    box.setAttribute('aria-activedescendant', row ? row.id : '');
    if (row && !(opts && opts.keep)) row.scrollIntoView({ block: 'nearest' });
  }
  function renderList(opts = {}) {
    renderAside();
    if (S.view === 'lines') return renderLines(opts);
    if (S.view === 'extras') return renderExtras(opts);
    if (S.view === 'people') return renderPeople(opts);
    if (S.view === 'cells') return renderCells(opts);
    const all = visibleItems();
    const vis = (D.visible = all.filter((it) => !isFolded(it)));
    const minor = all.filter((it) => it.minor && !it.decided);
    const folded = all.length - vis.length;
    const box = $('#rows');
    $('#list-count').textContent = `${vis.length} / ${D.items.length}개`;
    const tail = folded || (S.showMinor && minor.length)
      ? `<button type="button" class="fold-row" data-act="toggle-minor">${S.showMinor ? `작은 소리 ${minor.length}개 접기`
        : `작은 소리 ${folded}개를 접어 둠 · 세기만으로 잡힌 소리 · 펼치기`}</button>` : '';
    if (!D.items.length) box.innerHTML = '<div class="empty">후보가 아직 없습니다 · 파이프라인이 items.json 을 만든 뒤 다시 여세요</div>';
    else renderRows(box, vis.map(rowHtml), vis.findIndex((it) => it.id === S.sel), tail, '이 조건에 맞는 후보가 없습니다');
    finishList(opts, S.sel && `row-${S.sel}`);
  }
  function select(id, { seek = true, reveal = true } = {}) {
    const it = D.byId.get(id);
    if (!it) return;
    if (S.view !== 'items') { S.view = 'items'; S.limit = CHUNK; renderTabs(); renderChips(); }
    if (S.sel !== id) S.moveOpen = null;
    S.sel = id;
    for (const r of $$('#rows .row.is-sel')) { r.classList.remove('is-sel'); r.setAttribute('aria-selected', 'false'); }
    if (!document.getElementById(`row-${id}`)) renderList();      // folded, in a later chunk or another view
    const row = document.getElementById(`row-${id}`);
    if (row) {
      row.classList.add('is-sel');
      row.setAttribute('aria-selected', 'true');
      if (reveal) row.scrollIntoView({ block: 'nearest' });
    }
    $('#rows').setAttribute('aria-activedescendant', row ? row.id : '');
    renderDetail();
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
    const v = S.view;
    const vis = { items: D.visible, lines: D.visibleLines, extras: D.visibleExtras, people: D.visiblePeople, cells: D.visibleCells }[v];
    if (!vis || !vis.length) return;
    const key = (r) => (v === 'people' ? r.name : r.id);
    const cur = { items: S.sel, lines: S.line, extras: S.extra, people: S.person, cells: S.cell }[v];
    let i = vis.findIndex((r) => key(r) === cur);
    i = i < 0 ? (delta > 0 ? 0 : vis.length - 1) : clamp(i + delta, 0, vis.length - 1);
    pick(v, key(vis[i]));
  }
  function setFilter(f, toggle = false) {
    const vm = viewModel();
    if (vm) S[vm.key] = toggle && S[vm.key] === f && f !== 'all' ? 'all' : f;
    else S.filter = toggle && S.filter === f && f !== 'all' ? 'all' : f;
    S.limit = CHUNK;
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
    if (it.added) {                         // what the writer added: change it or take it away
      out.push(actBtn('고치기', 'added-edit', id, { primary: true }));
      out.push(actBtn('지우기', 'added-delete', id, { cls: 'btn-danger' }));
      return out.join('');
    }
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
    if (it.minor) bits.push('<span class="flag">작은 소리 · 세기만으로 잡힘</span>');
    if (it.cue_id && it.origin !== 'script') bits.push('대본 지문과 맞춰 본 후보');
    if (it.music && it.music.event === 'stop') bits.push('음악이 멈추는 자리');
    if (it.follows) {
      const r = D.byId.get(it.follows) || D.lines.get(it.follows);
      const what = !r ? it.follows : r.kind ? `${(KIND[r.kind] || {}).label || ''} ${fmtTime(r.onset)} ${cut(phraseText(r), 18)}` : `대사 ${fmtTime(r.start)} ${cut(r.text, 18)}`;
      const act = r && r.kind ? `data-act="goto-item" data-id="${esc(r.id)}"` : `data-act="goto-line" data-id="${esc(it.follows)}"`;
      // the speaker's line before is still 확인 필요 (the server's reason ends with WAITS): this
      // line's name waits for it, so the link says so and goes to it
      const waits = it.kind === 'speaker' && String(it.reason || '').endsWith(WAITS);
      const label = waits ? `${WAITS} · 앞 줄` : it.kind === 'speaker' ? '이어 말함 · 앞 줄' : '딸림 · 앞 후보';
      bits.push(`<button type="button" class="link${waits ? ' flag' : ''}" ${act}>${label} ${esc(what)}</button>`);
    }
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
    if (it.added) {                         // the writer's own sound: change its time or take it away
      return `<div class="nr-card${compact ? ' nr-compact' : ''}" data-nr="${esc(it.id)}"><span class="pill st-noroom">배치 확인 필요</span>
        <span class="nr-reason">${esc(nr.reason || '넣을 자리가 부족')}</span><div class="nr-actions">
        <button type="button" class="btn btn-sm" data-act="added-edit" data-id="${esc(it.id)}">시각 고치기</button>
        <button type="button" class="btn btn-sm btn-danger" data-act="added-delete" data-id="${esc(it.id)}">지우기</button></div></div>`;
    }
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
  /** A script cue's time range reaches past another scene of the script at one end or both
      (``window_out`` [before, after] from the server; ``window_by`` alone says nothing of it). */
  function windowOut(wo) {
    if (!Array.isArray(wo)) return '';
    const sides = [wo[0] === true && '앞 장면 쪽', wo[1] === true && '뒤 장면 쪽'].filter(Boolean);
    return sides.length ? '시각 범위가 장면 밖까지 (' + sides.join(' · ') + ')' : '';
  }
  function evidenceRowsHtml(it, target = 'main') {
    const evs = it.evidence || [];
    if (!evs.length) return '<div class="ev-empty">근거가 없습니다</div>';
    return evs.map((e, i) => {
      const pol = e.polarity === 'include' ? '<span class="pol pol-inc">포함 쪽</span>'
        : e.polarity === 'exclude' ? '<span class="pol pol-exc">제외 쪽</span>' : '<span></span>';
      const out = windowOut(e.window_out);
      return `<button type="button" class="ev-row" data-act="play-ev" data-target="${target}" data-id="${esc(it.id)}" data-i="${i}" aria-label="${esc(`${e.kind} ${fmtTime(e.t)} ${e.text}${out ? ` · ${out}` : ''}, 이 구간 재생`)}">
        <span class="ev-play">${ICON.play}</span><span class="ev-kind">${esc(e.kind)}</span><span class="ev-t tnum">${fmtTime(e.t)}</span>
        <span class="ev-text" title="${esc(e.text)}">${esc(e.text)}${out ? `<small class="ev-out">${esc(out)}</small>` : ''}</span>${pol}</button>`;
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
      ${noteHtml(it)}${noRoomHtml(it, true)}${scriptMoveHtml(it)}
      <div class="ev-rows">${evidenceRowsHtml(it)}</div>`;
  }
  /** A candidate the script writes down: its time is a guess, the writer moves it inside the span
      the script gives (the lines before and after it). */
  function scriptMoveHtml(it) {
    if (it.origin !== 'script' || it.added || !(it.kind === 'sfx' || it.kind === 'music')) return '';
    const w = it.window || [it.t0, it.t1];
    const t = it.placement && it.placement.action === 'move' ? it.placement.t : num(it.onset);
    const out = windowOut(it.window_out);
    return `<div class="move-box" data-box="move"><span class="mb-k">대본 단서 · 시각 어림</span>
      <span class="muted tnum">대본이 말한 범위 ${fmtTime(w[0])}–${fmtTime(w[1])}</span>${out ? `<span class="ev-out">${esc(out)}</span>` : ''}
      <span class="move-form"><input class="time-input" data-f="t" value="${fmtTime2(t)}" aria-label="옮길 시각 (분:초)" inputmode="decimal" autocomplete="off">
      <button type="button" class="btn btn-sm btn-text" data-act="ed-now" data-f="t" title="지금 재생 위치를 넣는다">지금 위치</button>
      <button type="button" class="btn btn-sm btn-primary" data-act="item-move" data-id="${esc(it.id)}" data-primary="1">여기로 옮기기</button>
      ${it.placement && it.placement.action === 'move' ? `<button type="button" class="btn btn-sm btn-text" data-act="place" data-action="clear" data-id="${esc(it.id)}">옮기기 취소</button>` : ''}</span></div>`;
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
    // the scene band under the ruler: found / not sure where / not in the film (a mark where it would be)
    let prevEnd = P.t0;
    for (const sc of D.scenes) {
      const head = `장면 ${sc.number || sc.id} · ${SCENE_ST[sc.status] || sc.status || ''}${sc.heading ? ` · ${sc.heading}` : ''}`;
      if (sc.start != null && sc.end != null) {
        prevEnd = sc.end;
        if (sc.end < P.t0 || sc.start > P.t1) continue;
        const l = x(Math.max(sc.start, P.t0)), w = Math.max(2, x(Math.min(sc.end, P.t1)) - l - 1);
        parts.push(`<div class="scb sc-${esc(sc.status || 'found')}" data-scene="${esc(sc.id)}" style="left:${l.toFixed(1)}px;width:${w.toFixed(1)}px;top:${TLG.ruler + 1}px;height:${TLG.band - 3}px" title="${esc(`${head}\n${fmtTime(sc.start)}–${fmtTime(sc.end)}`)}"></div>`);
      } else if (sc.status === 'absent' && prevEnd >= P.t0 && prevEnd <= P.t1) {
        parts.push(`<div class="scb-miss" style="left:${x(prevEnd).toFixed(1)}px;top:${TLG.ruler}px;height:${TLG.band - 1}px" title="${esc(head)}"></div>`);
      }
    }
    placeEnv(TLG, pps);
    for (const [name, blocks] of Object.entries(lanes)) {
      blocks.sort((a, b) => a.s - b.s);
      pack(blocks);
      const laneTop = TLG.ruler + TLG.band + TLG.pad + LANE[name] * (TLG.lane + TLG.gap);
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
    let blkId = null, cells = [], cls = 'is-rel';
    if (S.view === 'items') { const it = D.byId.get(S.sel); if (it) { blkId = it.id; cells = it.cells || []; } }
    else if (S.view === 'lines') {
      const ln = D.lines.get(S.line);
      if (ln) { const sp = D.spkByLine.get(ln.id); blkId = sp ? sp.id : null; cells = ln.cells || []; }
    } else if (S.view === 'cells' && S.cell) { cells = [S.cell]; cls = 'is-sel'; }
    const blk = blkId && $(`#tl-content [data-id="${cssEsc(blkId)}"]`);
    if (blk) blk.classList.add('is-sel');
    for (const cid of cells) { const c = $(`#tl-content [data-cell="${cssEsc(cid)}"]`); if (c) c.classList.add(cls); }
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
    if (S.view === 'cells') { pick('cells', cid); return; }
    if (S.view === 'lines') {
      const lid = (c.refs || []).find((r) => D.lines.has(r));
      if (lid) pick('lines', lid, { seek: false });
      clearRange(video);
      seekTo(c.start + 0.02);
      revealInTimeline(c.start, c.end);
      return;
    }
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
      // the label of a line with a speaker item opens the speaker dialog; one without (a line the
      // writer added or brought back) opens the line
      const lid = m && refs.find((r) => !used.has(r) && (spkLabel(D.spkByLine.get(r)) || (D.lines.get(r) || {}).label) === m[2]);
      if (!lid) return `<div class="sub-line">${esc(ln)}</div>`;
      used.add(lid);
      const sp = D.spkByLine.get(lid);
      const act = sp ? `data-act="name" data-id="${esc(sp.id)}"` : `data-act="goto-line" data-id="${esc(lid)}"`;
      return `<div class="sub-line">${esc(m[1] || '')}<span class="sub-name" ${act} title="화자 고치기">${esc(m[2])}</span>${esc(m[3])}${esc(m[4])}</div>`;
    }).join('')}</div>`;
  }
  function updateOverlay() {
    const t = S.t;
    const act = D.cells.filter((c) => c.start <= t + 1e-3 && t < c.end - 1e-3);
    // the selected sound or line that is not in any cell is shown as a ghost, so the writer sees what is left out
    const it = S.view === 'items' ? D.byId.get(S.sel) : null;
    let ghost = it && it.kind !== 'speaker' && !(it.cells || []).length && t >= num(it.onset) - 0.6 && t < num(it.onset) + 3
      ? { id: it.id, text: it.phrase || '', tag: `${stateLabel(it)} · 자막에 없음`, cls: `st-${stateOf(it)}` } : null;
    const ln = S.view === 'lines' ? D.lines.get(S.line) : null;
    const span = ln && (ln.start != null ? [ln.start, ln.end] : ln.guess);
    if (!ghost && ln && span && !(ln.cells || []).length && t >= span[0] - 0.3 && t < Math.max(span[1], span[0] + 1)) {
      ghost = { id: ln.id, text: `${ln.label ? `${ln.label} ` : ''}${ln.text}`, tag: `${lineStateLabel(ln)} · 자막에 없음`, cls: 'st-noroom' };
    }
    const key = act.map((c) => `${c.id}|${c.pos || ''}|${(c.lines || []).join('\n')}`).join('||') + (ghost ? `#${ghost.id}|${ghost.text}|${ghost.tag}` : '');
    if (key === S.overlayKey) return;
    S.overlayKey = key;
    // a cell with pos "top" is drawn at the top of the picture, as the TTML export places it
    let html = act.filter((c) => c.pos !== 'top').map(cellHtml).join('');
    if (ghost) {
      html += `<div class="sub-ghost"><span class="ghost-tag ${ghost.cls}">${esc(ghost.tag)}</span><div class="sub-line">${esc(ghost.text)}</div></div>`;
    }
    $('#subs').innerHTML = html;
    const top = $('#subs-top');
    if (top) top.innerHTML = act.filter((c) => c.pos === 'top').map(cellHtml).join('');
  }

  // ------------------------------------------------------------------- player
  // Film time is what the page works in (S.t, the cells, the lists). A <video> keeps the offset of
  // the file it was given (vid._off): the 재생용 영상 (media "ready") starts at the film's t0, the
  // source file at 0. Every read and write of currentTime goes through these two.
  const toVid = (vid, t) => t - num(vid && vid._off);
  const toFilm = (vid, v) => v + num(vid && vid._off);
  function seekVideo(vid, t) {
    const P = S.P;
    t = clamp(t, P.t0, Math.max(P.t0, P.t1 - 0.05));
    if (vid.readyState >= 1) vid.currentTime = Math.max(0, toVid(vid, t));
    else vid._pending = t;                                       // film time, set when the file is open
    if (vid === video) { S.t = t; onTimeUI(); }
    return t;
  }
  const seekTo = (t) => seekVideo(video, t);
  const fpsOf = () => num(S.P && S.P.fps, 25) || 25;
  /** The frame shown at t (frames start at k / fps, as the cell times do). */
  const frameOf = (t) => Math.floor(num(t) * fpsOf() + 1e-6);
  /** One frame back or on (the middle of the frame, so the decoder shows that frame); Shift: a second. */
  function stepFrame(n, second = false) {
    if (!S.P) return;
    if (!video.paused) video.pause();
    clearRange(video);
    if (second) { seekTo(S.t + n); return; }
    seekTo((frameOf(S.t) + n + 0.5) / fpsOf());
    followPlayhead();
  }
  function onTimeUI() {
    $('#stage-time').textContent = fmtTime(S.t);
    $('#tl-clock').textContent = `${fmtTime(S.t)} · ${frameOf(S.t)}프레임 / ${fmtTime(S.P.t1)}`;
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
    if (vid === video && !S.canPlay) { seekTo(a); toast(stillWhy(), 'error'); return; }
    if (vid !== video && !vid.getAttribute('src')) { toast(stillWhy(), 'error'); return; }
    a = clamp(a, S.P.t0, S.P.t1 - 0.05);
    b = clamp(b, a + 0.2, S.P.t1);
    clearRange(vid);
    vid._stopAt = b;                                             // film time (loop compares toFilm)
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
    if (!S.canPlay) { toast(stillWhy(), 'error'); return; }
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
      const t = toFilm(vid, vid.currentTime);
      if ((vid._stopAt != null && t >= vid._stopAt) || t >= S.P.t1 - 0.02) { vid.pause(); clearRange(vid); }
      if (vid === video) { S.t = clamp(t, S.P.t0, S.P.t1); onTimeUI(); followPlayhead(); }
      else updatePvTime(t);
    }
    if (again) raf = requestAnimationFrame(loop);
  }
  function kick() { if (!raf) raf = requestAnimationFrame(loop); }
  /** A message over the stage; ``note``: a small bar over the still instead of a cover. */
  function showStageMsg(msg, note = false) {
    const el = $('#stage-msg');
    el.hidden = !msg;
    el.textContent = msg || '';
    el.classList.toggle('is-note', !!note);
  }
  let stillTimer = 0;
  /** The still of the source at S.t: shortly after a seek, and every 0.7 s while the sound plays. */
  function scheduleStill() {
    if (stillTimer && !video.paused) return;
    clearTimeout(stillTimer);
    stillTimer = setTimeout(() => { stillTimer = 0; $('#stage-still').src = `api/frame?t=${S.t.toFixed(2)}&w=960`; }, video.paused ? 120 : 700);
  }

  // ------------------------------------------------------------ 재생용 영상
  // project.media (the server): none (no video file) | source (the file as it is, t0 0) | building
  // (the 재생용 영상 of the processed part is being made, progress 0~1) | ready (it is made: its 0 s
  // is the film's t0) | failed (note says why). Until a file plays, the stage shows stills of the
  // source (/api/frame, film time) and the bar over it says why.
  function mediaOf(P) {
    const m = P && P.media;
    if (m && typeof m === 'object' && m.state) return m;
    return { state: P && P.video ? 'source' : 'none', t0: 0, progress: null, note: null };    // an older server
  }
  const pctOf = (m) => (m && typeof m.progress === 'number' && Number.isFinite(m.progress) ? `${Math.round(clamp(m.progress, 0, 1) * 100)}%` : '');
  /** Building with no progress of its own but a note: another worker or server makes this work's
      copy and this one waits for it (proxy.WAIT_NOTE); the note is shown in place of NN%. */
  const waitNote = (m) => (m && m.state === 'building' && !pctOf(m) && m.note ? String(m.note) : '');
  /** What the toast says when the writer presses play and only stills can be shown. */
  function stillWhy() {
    const m = S.media || {};
    if (waitNote(m)) return `${waitNote(m)} · 다 되면 재생할 수 있습니다`;
    if (m.state === 'building') return `재생용 영상을 만드는 중입니다${pctOf(m) ? ` · ${pctOf(m)}` : ''} · 다 되면 재생할 수 있습니다`;
    if (m.state === 'failed') return `재생용 영상을 만들지 못해 정지 화면만 보여 줍니다${m.note ? ` · ${m.note}` : ''}`;
    if (m.state === 'none' || !(S.P && S.P.video)) return '영상 파일이 없습니다';
    return '이 브라우저에서는 영상을 재생할 수 없어 정지 화면만 보여 줍니다';
  }
  /** The stage shows stills; ``sound``: the file's sound still plays (only its picture would not open). */
  function goStill(sound = false) {
    S.still = true;
    S.canPlay = !!sound;
    $('#stage-still').hidden = false;
    $('#stage-play').hidden = !sound;
    if (!sound && !video.paused) video.pause();
    scheduleStill();
  }
  function goLive() {
    S.still = false;
    S.canPlay = true;
    $('#stage-still').hidden = true;
    $('#stage-play').hidden = false;
  }
  /** Let go of a <video>'s file (while the 재생용 영상 is made again /media/video answers 409). */
  function unload(vid) {
    if (!vid || !vid.getAttribute('src')) return;
    vid.pause();
    clearRange(vid);
    vid.removeAttribute('src');
    vid.load();
  }
  /** Give a <video> the file the server gives now, with its offset; ``t``: the film time to show. */
  function loadMedia(vid, t) {
    const m = S.media;
    vid._off = m.state === 'ready' ? num(m.t0) : 0;
    vid._pending = t;
    vid.src = m.state === 'ready' ? `${S.P.video_url}?v=${S.mediaVer}` : S.P.video_url;     // ?v=: not the old copy from the cache
  }
  let mediaTimer = 0;
  function pollMedia(on) {
    if (on && !mediaTimer) {
      mediaTimer = setInterval(async () => {
        try { const r = await fetch('api/media'); if (r.ok) syncMedia(await r.json()); } catch (_) { /* asked again in 2 s */ }
      }, 2000);
    } else if (!on && mediaTimer) { clearInterval(mediaTimer); mediaTimer = 0; }
  }
  async function refreshMedia() {
    try { const r = await fetch('api/media'); if (r.ok) syncMedia(await r.json()); } catch (_) { /* the next state says it */ }
  }
  /** Follow project.media: with every state the server sends and, while the copy is being made,
      every 2 s (GET api/media). A new file is opened only when what the server gives changes. */
  function syncMedia(m) {
    if (!S.P) return;
    m = m || mediaOf(S.P);
    S.media = m;
    pollMedia(m.state === 'building');
    if (!S.P.video || m.state === 'none') {
      if (S.mediaKey !== 'none') { S.mediaKey = 'none'; unload(video); unload(pvVideo()); S.canPlay = false; }
      showStageMsg(m.note && m.note !== '영상 파일이 없습니다' ? `영상 파일을 찾을 수 없습니다 · ${m.note}` : '영상 파일을 찾을 수 없습니다 · project.json 의 video 경로를 확인하세요');
      return;
    }
    if (m.state === 'building' || m.state === 'failed') {
      if (S.mediaKey !== m.state) { S.mediaKey = m.state; unload(video); unload(pvVideo()); goStill(false); }
      showStageMsg(waitNote(m) ? `${waitNote(m)} · 그동안 정지 화면으로 보여 줍니다`
        : m.state === 'building' ? `재생용 영상을 만드는 중 ${pctOf(m)}${m.note ? ` · ${m.note}` : ''} · 그동안 정지 화면으로 보여 줍니다`
        : `재생용 영상을 만들지 못했습니다${m.note ? ` · ${m.note}` : ''} · 정지 화면으로 보여 줍니다`, true);
      return;
    }
    const key = `${m.state}|${num(m.t0)}`;               // ready | source
    if (S.mediaKey === key) {
      // the same file: a source the browser could not open stays on stills (the note: why no copy)
      if (S.still) showStageMsg(m.note ? `재생용 영상을 만들 수 없습니다 · ${m.note}` : '', true);
      return;
    }
    const after = S.mediaKey === 'building';
    S.mediaKey = key;
    if (m.state === 'ready') S.mediaVer = Date.now();
    showStageMsg('');
    goLive();
    loadMedia(video, S.t);
    const pv = pvVideo();
    if (pv && S.modal && S.modal.pv && pv._film != null) loadMedia(pv, pv._film);
    if (after) toast('재생용 영상이 준비되었습니다 · 이제 재생할 수 있습니다');
  }
  /** The browser could not open the file (``sound`` false) or opened only its sound. The stage
      shows stills (and plays the sound); for the source file the server is asked once to make the
      재생용 영상 (POST api/media/build), and the page follows what it answers. */
  function cannotOpen(sound) {
    if (!S.P || !S.P.video || !video.getAttribute('src')) return;
    if (S.still && (!S.canPlay || sound)) return;        // already on stills (with the sound, or without)
    goStill(sound);
    toast(sound ? '브라우저가 이 영상의 그림을 열지 못해 정지 화면과 소리로 보여 줍니다' : '브라우저가 이 영상을 재생하지 못해 정지 화면으로 보여 줍니다', 'error', 5000);
    const m = S.media || {};
    if (m.state === 'source' && !S.buildAsked) {
      S.buildAsked = true;
      api('POST', 'api/media/build').then((r) => { if (r && r.state) syncMedia(r); }).catch(() => { /* an older server: stills stay */ });
    } else refreshMedia();
  }
  /** Opened, but no picture: a codec the browser does not decode (MPEG-4 Part 2 in an mkv), the sound plays. */
  function checkPicture() {
    if (S.P && S.P.width && S.P.height && video.readyState >= 1 && !video.videoWidth) cannotOpen(true);
  }
  function bindVideo() {
    video.addEventListener('loadedmetadata', () => {
      if (video._pending != null) { video.currentTime = Math.max(0, toVid(video, video._pending)); video._pending = null; }
      layoutStage();
      checkPicture();
    });
    video.addEventListener('loadeddata', checkPicture);
    video.addEventListener('seeked', () => { if (video.paused) { S.t = clamp(toFilm(video, video.currentTime), S.P.t0, S.P.t1); onTimeUI(); } });
    video.addEventListener('play', () => { $('#stage').classList.remove('is-paused'); $('#stage-play').setAttribute('aria-label', '일시 정지'); kick(); });
    video.addEventListener('pause', () => { $('#stage').classList.add('is-paused'); $('#stage-play').setAttribute('aria-label', '재생'); });
    video.addEventListener('timeupdate', () => {
      if (S.P && toFilm(video, video.currentTime) > S.P.t1 + 0.05) { video.pause(); video.currentTime = Math.max(0, toVid(video, S.P.t1 - 0.05)); }
    });
    video.addEventListener('error', () => cannotOpen(false));
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
    if (it.added) { toast('작가가 넣은 것은 고치거나 지웁니다', 'error'); return Promise.resolve(null); }
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
    if (inModal) renderExport(); else renderDetail();
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
    if (it.added) { const a = D.added.get(id); if (a) openAdd(a.kind, a); return; }
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
    pv._film = clamp(t, S.P.t0, S.P.t1 - 0.05);          // film time; the file is given when the media plays
    pv.addEventListener('loadedmetadata', () => {
      if (pv._pending != null) { pv.currentTime = Math.max(0, toVid(pv, pv._pending)); pv._pending = null; }
      fitLayer($('#pv'), $('#pv-layer'), pv);
    });
    pv.addEventListener('seeked', () => updatePvTime(toFilm(pv, pv.currentTime)));
    pv.addEventListener('play', kick);
    pv.addEventListener('click', () => {
      if (!pv.getAttribute('src')) { toast(stillWhy(), 'error'); return; }
      if (pv.paused) { clearRange(pv); playVideo(pv); } else { pv.pause(); clearRange(pv); }
    });
    if (S.P.video && !S.still) loadMedia(pv, pv._film);
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
  /** The lines a name change on line ``lineId`` reaches, by person as the server counts them
      (cboe.people.scope_lines): line · from_here · before (this one left out) · scene · person. */
  function scopeLines(lineId, which) {
    const all = D.allLines;
    const idx = all.findIndex((l) => l.id === lineId);
    if (idx < 0 || which === 'line') return [lineId];
    const me = all[idx];
    // a line without a name is nobody's: the server changes only this line (400 for another scope)
    if (!me.name) return [];
    const same = all.map((l, i) => [i, l]).filter(([, l]) => (l.name || null) === (me.name || null));
    const pickIds = (f) => same.filter(f).map(([, l]) => l.id);
    if (which === 'from_here') return pickIds(([i]) => i >= idx);
    if (which === 'before') return pickIds(([i]) => i < idx);
    if (which === 'scene') return pickIds(([, l]) => (l.scene_id || null) === (me.scene_id || null));
    return pickIds(() => true);
  }
  function syncSpeaker() {
    const m = S.modal, it = m.it || D.byId.get(m.id);
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
    const lid = ln ? ln.id : null;
    const counts = Object.fromEntries(SCOPES.map(([k]) => [k, lid ? scopeLines(lid, k).length : 1]));
    const unnamed = !!ln && !ln.name;             // 화자 미상: only 이 줄 is left (the server says 400 to the rest)
    const scopes = unnamed ? SCOPES.filter(([k]) => k === 'line') : SCOPES;
    S.modal = { type: 'speaker', id: it.id, it: it.lineMode ? it : null, chars, who: cur ? String(chars.indexOf(cur)) : 'new', newName: '', show: it.final !== 'exclude', scope: 'line', counts };
    const t = (ln && ln.start != null ? ln.start : num(it.onset)) + 0.3;
    const near = D.items.filter((o) => o.kind === 'speaker');
    const pos = near.findIndex((o) => o.id === it.id);
    const nb = pos < 0 ? [] : near.slice(Math.max(0, pos - 2), pos + 3);
    $('#modal-box').innerHTML = `<div class="mdl-grid">
      <section class="mdl-card">
        ${headLeft(it)}
        ${previewHtml(t)}
        ${nb.length ? `<div class="sec">자막의 이름이나 목록의 화자를 누르면 고칠 수 있다</div>
        <div class="nb-lines">${nb.map((o) => `<button type="button" class="nb-line${o.id === it.id ? ' is-cur' : ''}" data-act="edit" data-id="${esc(o.id)}">
          <span class="t tnum">${fmtTime(o.onset)}</span><span class="badge k-speaker">화자</span>
          <span class="tx">${phraseHtml(o)}<small>${esc(summaryOf(o))}</small></span>${pillHtml(o)}</button>`).join('')}</div>`
        : '<div class="sec">화자 항목이 없는 줄 · 이름 표시는 이 줄에만 정한다</div>'}
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
          <div><div class="sec">이름을 바꿀 범위<span class="sub">${unnamed ? '화자 이름이 없는 줄 · 이 줄만 바꾼다' : '같은 인물의 줄만 · 인물 표의 표시와 따로'}</span></div><div class="seg seg-scope seg-wrap" role="radiogroup" aria-label="이름을 바꿀 범위">
            ${scopes.map(([k, label]) => `<label title="${esc(k === 'before' ? '이 줄은 그대로 두고 앞의 줄만 바꾼다' : `${label}의 이름을 바꾼다`)}"><input type="radio" name="m-scope" value="${k}"${k === 'line' ? ' checked' : ''}${counts[k] ? '' : ' disabled'}><span>${label} · ${counts[k]}줄</span></label>`).join('')}
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
    const m = S.modal, it = m.it || D.byId.get(m.id);
    const name = chosenName(m);
    if (!name) { toast('이름을 고르거나 입력하세요', 'error'); const f = $('#f-newname'); if (f) f.focus(); return; }
    const scope = m.scope || 'line';
    const renamed = name !== ((it.speaker && it.speaker.name) || '') || scope !== 'line';
    const scopeKo = (SCOPES.find(([k]) => k === scope) || [])[1] || '이 줄';
    const msg = scope !== 'line'
      ? `${scopeKo} ${num(m.counts && m.counts[scope])}줄의 이름을 ${name}${ro(name)} 바꿈 · 이 줄은 ${m.show ? '이름 표시' : '이름 생략'}`
      : `${m.show ? '이름 표시' : '이름 생략'} · ${cut(m.show ? `${nameLabel(name, (it.speaker && it.speaker.label_tail) || '')} ${speakerText(it)}` : speakerText(it), 34)}`;
    if (it.lineMode) {                      // a line without a speaker item: its name and show_name directly
      const ln = D.lines.get(it.speaker.line_id);
      if (!ln) return;
      let st = true;
      if (renamed) st = await mutate(`api/lines/${enc(ln.id)}/speaker`, { name, scope }, msg);
      if (st && m.show !== !!ln.show_name) st = await lineUpdate(ln, { show_name: m.show }, renamed ? null : msg);
      if (st) closeModal();
      return;
    }
    const body = { decision: m.show ? 'include' : 'exclude' };
    if (renamed) { body.name = name; body.scope = scope; }
    const st = await mutate(`api/items/${encodeURIComponent(m.id)}`, body, msg);
    if (st) closeModal();
  }
  /** The speaker dialog for a line: its speaker item, or the line itself when it has none. */
  function openLineSpeaker(ln) {
    if (ln.speaker_item && D.byId.has(ln.speaker_item)) { openEdit(ln.speaker_item); return; }
    pauseMain();
    teardownPreview();
    const tail = ln.language ? `${STYLE().label_sep}${ln.language}` : '';
    openSpeaker({
      id: ln.id, kind: 'speaker', lineMode: true, onset: ln.start != null ? ln.start : 0, decided: true,
      final: ln.show_name ? 'include' : 'exclude', evidence: [], cells: ln.cells || [], flags: [],
      speaker: { line_id: ln.id, name: ln.name || '', label: ln.label || '', label_tail: tail },
    });
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
  /** 내보내기 전 검사: the dialogue lines, the speech that is in no script line and the names shown early. */
  function linesXpHtml(chk) {
    const ln = chk.lines || {}, n = (k) => (ln[k] || []).length;
    const go = (view, f, label = '보기') => `<button type="button" class="btn btn-sm" data-act="xp-view" data-view="${view}" data-filter="${f}">${label}</button>`;
    const bits = [n('differs_open') && `다른데 고르지 않은 줄 ${n('differs_open')}개 (대본 글로 나감)`,
      n('no_room') && `자리가 없어 빠진 줄 ${n('no_room')}개`, n('unheard') && `시각 추정 ${n('unheard')}줄`].filter(Boolean);
    const out = [xpRow(n('no_room') ? 'bad' : n('differs_open') ? 'warn' : n('unheard') ? 'info' : 'ok', '대사 줄',
      bits.length ? bits.join(' · ') : '고를 줄 없음',
      [n('differs_open') && go('lines', 'differs', '다른 줄 보기'), n('no_room') && go('lines', 'noroom', '자리 없는 줄')].filter(Boolean).join(''))];
    const na = asideCount();
    if (na) {
      out.push(xpRow('warn', '붙지 못한 작가 결정', `${na}개 · 대본이 바뀌어 어느 줄에도 붙지 못함 · 자막에 쓰지 않음`,
        '<button type="button" class="btn btn-sm" data-act="xp-aside">보기</button>'));
    }
    const out2 = [n('absent') && `영화에 없음 ${n('absent')}`, n('out_of_range') && `구간 밖 ${n('out_of_range')}`, n('writer_excluded') && `작가가 뺌 ${n('writer_excluded')}`].filter(Boolean);
    if (out2.length) out.push(xpRow('info', '빠진 줄', `${out2.join(' · ')} · 자막에 넣지 않음`, go('lines', 'out')));
    // the same numbers as the 대본에 없는 말 tab: the ones folded as 받아쓰기 헛소리 are told apart
    const xo = chk.extras_open || [];
    const ns = xo.filter((id) => (D.extraById.get(id) || {}).suspect).length, nx = xo.length - ns;
    if (nx || ns) {
      out.push(xpRow('info', '대본에 없는 말', `${[nx && `${nx}개 정하지 않음`, ns && `받아쓰기 헛소리 의심 ${ns}개 접힘`].filter(Boolean).join(' · ')} · 자막에 넣지 않음`,
        go('extras', nx ? 'open' : 'suspect')));
    }
    const ne = (chk.names_early || []).length;
    if (ne) out.push(xpRow('warn', '이름이 일찍 나감', `${ne}명 · 이름이 밝혀지기 전에 이름 표시가 나간다`, go('people', 'early')));
    return out.join('');
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
          ${linesXpHtml(chk)}
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
    else if (m.type === 'add') saveAdd();
  }
  function focusFirst() {
    const el = $('#modal-box .opt.is-on input[type=radio]') || $('#modal-box [data-act="m-confirm"]');
    if (el) el.focus({ preventScroll: true });
  }

  // ==================================================================== views
  // 대사 · 대본에 없는 말 · 인물 · 칸 (감사 1-01·1-04·1-06·3-01·3-10·9-x3): each view lists its rows on
  // the right and shows the picked one on the left, where the writer decides. Nothing in a line's
  // words changes unless the writer picks it.
  function viewModel(v = S.view) {
    if (v === 'lines') return { rows: D.allLines, filters: LINE_FILTERS, key: 'lf', pass: lineIn };
    if (v === 'extras') return { rows: D.extras, filters: EXTRA_FILTERS, key: 'xf', pass: extraIn };
    if (v === 'people') return { rows: D.people, filters: PEOPLE_FILTERS, key: 'pf', pass: personIn };
    if (v === 'cells') return { rows: D.cells, filters: CELL_FILTERS, key: 'cf', pass: cellIn };
    return null;
  }
  const peopleOpen = () => D.people.filter((p) => personIn(p, 'open'));
  function todoCount(v) {
    const sm = (S.st && S.st.summary) || {};
    if (v === 'items') return num(sm.final && sm.final.check);
    if (v === 'lines') return num(sm.lines && sm.lines.differs_open) + num(sm.lines && sm.lines.no_room);
    if (v === 'extras') return Math.max(0, num(sm.extras && sm.extras.open) - suspectOpen());
    if (v === 'people') return peopleOpen().length;
    if (v === 'cells') return num(sm.guide && sm.guide.must);
    return 0;
  }
  function renderTabs() {
    $('#tabs').innerHTML = VIEWS.map(([v, label], i) => {
      const on = S.view === v, n = todoCount(v);
      return `<button type="button" class="tab${on ? ' is-on' : ''}" role="tab" aria-selected="${on}" data-act="view" data-view="${v}" title="${esc(VIEW_HINT[v])} · 단축키 ${i + 1}">${label}${n ? `<span class="n" title="${esc(TODO_TITLE[v])}">${n}</span>` : ''}</button>`;
    }).join('');
    $('#list-hint').textContent = VIEW_HINT[S.view];
    $('#list-title').textContent = (VIEWS.find(([v]) => v === S.view) || [])[1] || '';
    renderKeys();
  }
  function viewNote() {
    const sm = (S.st && S.st.summary) || {};
    if (S.view === 'lines') {
      const l = sm.lines || {}, ex = l.excluded || {};
      const out = num(ex.absent) + num(ex.out_of_range) + num(ex.writer) + num(ex.no_time);
      return `자막에 ${num(l.included)}줄 · 빠짐 ${out}줄${num(l.added) ? ` · 작가가 넣은 줄 ${l.added}` : ''}`;
    }
    if (S.view === 'extras') {
      const x = sm.extras || {}, ns = suspectOpen();
      return `정하지 않음 ${Math.max(0, num(x.open) - ns)} / ${num(x.total)}개${ns ? ` · 받아쓰기 헛소리 의심 ${ns}개 접힘` : ''} · 정하지 않으면 자막에 넣지 않음`;
    }
    if (S.view === 'people') return `정하지 않은 인물 ${peopleOpen().length}명 · 이름이 일찍 나가는 인물 ${num(sm.people && sm.people.early)}명`;
    if (S.view === 'cells') { const g = sm.guide || {}; return `의무 위반 ${num(g.must)}칸 · 권고 ${num(g.should)}칸`; }
    return '';
  }
  function renderKeys() {
    const k = (keys, label) => `<span>${keys.map((x) => `<kbd>${x}</kbd>`).join('')} ${label}</span>`;
    const per = {
      items: [k(['Enter'], '추천대로'), k(['I'], '포함'), k(['E'], '제외')],
      lines: [k(['I'], '넣기'), k(['E'], '빼기')],
      extras: [k(['E'], '무시')],
    }[S.view] || [];
    const tail = S.view === 'items' ? '<span class="keys-tail">두 번 누르면 고치기</span>' : `<span class="keys-tail">${k(['1'], '~')}${k(['5'], '보기')}</span>`;
    $('#keys').innerHTML = [k(['↑', '↓'], '이동'), k(['←', '→'], '한 프레임'), k(['Space'], '재생'), ...per, k(['N'], '새로 넣기'), tail].join('');
  }
  function setView(v) {
    if (!VIEWS.some(([x]) => x === v) || S.view === v) return;
    S.view = v;
    S.limit = CHUNK;
    S.ed = null;
    renderTabs();
    renderChips();
    renderList();
    renderDetail();
    markTimeline();
    S.overlayKey = '';
    updateOverlay();
  }
  const SEL_KEY = { lines: 'line', extras: 'extra', people: 'person', cells: 'cell' };
  /** Pick a row of a view (and switch to that view): the list marks it, the left panel shows it, the
      video goes to it. */
  function pick(view, id, { seek = true } = {}) {
    if (view === 'items') { select(id, { seek }); return; }
    if (S.view !== view) { S.view = view; S.limit = CHUNK; renderTabs(); renderChips(); }
    const k = SEL_KEY[view];
    if (S[k] !== id) S.ed = null;
    S[k] = id;
    if (view === 'cells') S.cellAt = (D.cellById.get(id) || {}).start;
    renderList();
    renderDetail();
    markTimeline();
    if (seek) {
      const span = spanOf(view, id);
      if (span) {
        clearRange(video);
        if (!video.paused) video.pause();
        seekTo(span[0]);
        revealInTimeline(span[0], span[1]);
      }
    }
    S.overlayKey = '';
    updateOverlay();
  }
  function spanOf(view, id) {
    if (view === 'lines') {
      const ln = D.lines.get(id);
      if (!ln) return null;
      const c = (ln.cells || []).map((x) => D.cellById.get(x)).find(Boolean);
      if (c) return [c.start + 0.02, c.end];                 // its first cell shows on the picture
      if (ln.start != null) return [ln.start, ln.end];
      return ln.guess ? [ln.guess[0], ln.guess[1]] : null;
    }
    if (view === 'extras') { const x = D.extraById.get(id); return x ? [x.start, x.end] : null; }
    if (view === 'cells') { const c = D.cellById.get(id); return c ? [c.start + 0.02, c.end] : null; }
    return null;
  }
  /** What the left panel shows: the view, its picked row, the open editor. */
  function detailSubject() {
    const id = S.view === 'items' ? S.sel : S[SEL_KEY[S.view]];
    return `${S.view}|${id == null ? '' : id}|${S.ed ? `${S.ed.id}:${S.ed.mode}` : ''}|${S.view === 'items' ? S.moveOpen || '' : ''}`;
  }
  /** A field of the left panel by where it is: its box (data-box, a no-room card) and its name. */
  function fieldKey(el) {
    const box = el.closest('[data-box]'), nr = el.closest('[data-nr]');
    return `${box ? box.dataset.box : nr ? `nr:${nr.dataset.nr}` : ''}|${el.dataset.f || el.dataset.change || el.name || el.id || el.className}`;
  }
  const isText = (el) => el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit'].includes(el.type));
  /** The fields the writer is filling in (typed, picked, ticked and not stored yet) and the one that
      has the focus. A field that stores as soon as it changes (data-change) is kept only while the
      writer is still typing in it: once sent, the server's value is what the panel shows.
      The focus comes back only to a text field the writer is typing in (one that stores as it
      changes: while its text is not sent). A switch, a tick box or a select keeps no focus once
      drawn again (the body has it, as before): Space and ↑↓ are the page's keys again, not
      another write of the writer's decision through that control. */
  function grabFields(root) {
    const out = new Map(), seen = new Map();
    for (const el of root.querySelectorAll('input, textarea, select')) {
      let k = fieldKey(el);
      const n = seen.get(k) || 0;
      seen.set(k, n + 1);
      if (n) k += `#${n}`;
      const focused = document.activeElement === el && isText(el) && (!el.dataset.change || el.value !== el._sent);
      const dirty = el.tagName === 'SELECT' ? [...el.options].some((o) => o.selected !== o.defaultSelected)
        : isText(el) ? el.value !== el.defaultValue : el.checked !== el.defaultChecked;
      const keep = el.dataset.change ? focused && dirty : dirty;
      if (keep || focused) {
        out.set(k, { keep, focused, value: el.value, checked: el.checked,
          s: isText(el) ? el.selectionStart : null, e: isText(el) ? el.selectionEnd : null });
      }
    }
    return out;
  }
  function putFields(root, got) {
    if (!got.size) return;
    const seen = new Map();
    for (const el of root.querySelectorAll('input, textarea, select')) {
      let k = fieldKey(el);
      const n = seen.get(k) || 0;
      seen.set(k, n + 1);
      if (n) k += `#${n}`;
      const g = got.get(k);
      if (!g) continue;
      if (g.keep) { if (isText(el) || el.tagName === 'SELECT') el.value = g.value; else el.checked = g.checked; }
      if (g.focused && isText(el)) {
        el.focus({ preventScroll: true });
        if (g.s != null) { try { el.setSelectionRange(g.s, g.e); } catch (_) { /* a type without a caret */ } }
      }
    }
  }
  /** Draw the left panel. When it shows the same thing again (an answer to an earlier write came
      while the writer was typing), what the writer typed and where the caret was stay. */
  function renderDetail() {
    const box = $('#evidence');
    const subject = detailSubject();
    const got = box.dataset.subject === subject ? grabFields(box) : new Map();
    if (S.view === 'lines') renderLineDetail();
    else if (S.view === 'extras') renderExtraDetail();
    else if (S.view === 'people') renderPersonDetail();
    else if (S.view === 'cells') renderCellDetail();
    else renderEvidence();
    box.dataset.subject = subject;
    putFields(box, got);
  }
  const detailHead = (title, hint, actions) => `<div class="ev-head"><h2>${title}</h2>${hint ? `<span class="hint">${hint}</span>` : ''}<div class="ev-actions">${actions || ''}</div></div>`;
  /** A line's time from its text, for a line put back without a time: the reading speed and the
      shortest and longest cell of the style the server sent. */
  function lineDur(text) {
    const st = STYLE();
    const d = width(text || '') / (num(st.cps) || 1) + 0.3;
    return clamp(d, num(st.min_dur, d), Math.max(num(st.min_dur, d), num(st.max_dur, d)));
  }
  /** Store a change of a line: a script line through /api/lines, a line the writer added through /api/added. */
  function lineUpdate(ln, fields, msg) {
    let url = `api/lines/${enc(ln.id)}`;
    if (D.added.has(ln.id)) {
      fields = { ...fields };
      delete fields.include;
      delete fields.text_from;
      if (!Object.keys(fields).length) return Promise.resolve(null);
      url = `api/added/${enc(ln.id)}`;
    }
    const keys = pendStart(ln.id, fields);
    return mutate(url, fields, msg).then((st) => { pendEnd(keys); return st; });
  }
  /** [start, end] typed in a time box (the nearest [data-box] around ``el``), or null. */
  function readSpan(el) {
    const box = el.closest('[data-box]');
    const v = (f) => { const i = box && box.querySelector(`[data-f="${f}"]`); return parseTime(i && i.value); };
    const s = v('start'), e = v('end');
    if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) {
      toast('시작과 끝을 03:52.40 처럼 적고, 끝은 시작보다 뒤로', 'error');
      return null;
    }
    return [Math.round(s * 1000) / 1000, Math.round(e * 1000) / 1000];
  }
  function timeBoxHtml(box, s, e, buttons) {
    return `<div class="ed-time" data-box="${box}">
      <label>시작 <input class="time-input" data-f="start" value="${fmtTime2(s)}" inputmode="decimal" autocomplete="off"></label>
      <button type="button" class="btn btn-sm btn-text" data-act="ed-now" data-f="start" title="지금 재생 위치를 넣는다">지금</button>
      <label>끝 <input class="time-input" data-f="end" value="${fmtTime2(e)}" inputmode="decimal" autocomplete="off"></label>
      <button type="button" class="btn btn-sm btn-text" data-act="ed-now" data-f="end" title="지금 재생 위치를 넣는다">지금</button>
      ${buttons}</div>`;
  }

  // ---------------------------------------------------------- 대사 (lines)
  function lineIn(ln, f) {
    switch (f) {
      case 'differs': return ln.status === 'differs';
      case 'unheard': return ln.status === 'unheard';
      case 'out': return ln.status === 'absent' || ln.status === 'out_of_range';
      case 'spelling': return (ln.hints || []).includes('spelling');
      case 'lang': return !!(ln.language || ln.sung);
      case 'noroom': return !!ln.no_room;
      case 'writer': return D.added.has(ln.id) || !!(ln.decision && Object.keys(ln.decision).length);
      default: return true;
    }
  }
  function lineState(ln) {
    if (ln.no_room) return 'noroom';
    if (D.added.has(ln.id)) return 'added';
    if (ln.excluded) return LSTATE[ln.excluded] ? ln.excluded : 'no_time';      // absent · out_of_range · writer · no_time
    if (ln.status === 'absent' || ln.status === 'out_of_range') return 'revived';
    if (ln.status === 'differs') return ln.decision && (ln.decision.text_from || ln.decision.text) ? 'picked' : 'differs';
    if (ln.status === 'unheard') return ln.estimated ? 'unheard' : 'pinned';
    return ln.pinned ? 'pinned' : 'ok';
  }
  function lineStateLabel(ln, s = lineState(ln)) {
    if (s === 'picked') return PICKED[ln.decision.text_from || 'writer'] || '고름';
    return LSTATE[s][0];
  }
  const linePill = (ln) => { const s = lineState(ln); return `<span class="pill ${LSTATE[s][1]}">${esc(lineStateLabel(ln, s))}</span>`; };
  const whoOf = (ln) => ln.label_name || ln.name || ln.speaker || '';
  function lineSub(ln) {
    const bits = [];
    const s = lineState(ln);
    if (s === 'noroom') bits.push(`자리 없음 · ${ln.no_room.reason || ''}`);
    else if (ln.excluded) bits.push(((S.st.line_codes || {}).excluded || {})[ln.excluded] || ln.excluded);
    if (ln.status === 'differs') bits.push(`들린 말: ${ln.asr_text || '없음'}`);
    else if (s === 'unheard') bits.push('받아쓰기로 못 맞춰 시각을 어림');
    if ((ln.hints || []).includes('spelling')) bits.push('맞춤법 확인');
    if (ln.language) bits.push(ln.language);
    if (ln.sung) bits.push('노래');
    if (D.overlaps.has(ln.id)) bits.push('같은 자리에 대본에 없는 말');
    if (!bits.length && ln.include) bits.push(`자막 ${(ln.cells || []).length}칸`);
    return bits.join(' · ');
  }
  function lineRowHtml(ln) {
    const sel = ln.id === S.line;
    const t = ln.start != null ? fmtTime(ln.start) : ln.guess ? `~${fmtTime(ln.guess[0])}` : '--:--.-';
    return `<div class="row row-line${sel ? ' is-sel' : ''}${ln.include ? '' : ' is-out'}" role="option" id="lines-row-${esc(ln.id)}" data-view="lines" data-id="${esc(ln.id)}" aria-selected="${sel}">
      <span class="r-time tnum">${t}</span><span class="r-who" title="${esc(whoOf(ln))}">${esc(whoOf(ln))}</span>
      <span class="r-main"><span class="r-phrase">${esc(ln.text)}</span><span class="r-sub">${esc(lineSub(ln))}</span></span>
      ${linePill(ln)}</div>`;
  }
  /** A scene is open when one of its lines is in the subtitles (or picked); the writer opens and closes by hand. */
  function sceneOpen(g) {
    if (S.sceneOpen.has(g.sid)) return S.sceneOpen.get(g.sid) || g.lines.some((l) => l.id === S.line);
    return g.lines.some((l) => l.include || l.id === S.line);
  }
  const sceneName = (sid) => { const sc = D.sceneById.get(sid); return sc ? `장면 ${sc.number || sc.id}` : ''; };
  function sceneHeadHtml(g, open) {
    const sc = D.sceneById.get(g.sid);
    const head = sc ? sc.heading || `장면 ${sc.number || sc.id}` : g.sid ? g.sid : '장면 없음';
    const span = sc && sc.start != null ? `${fmtTime(sc.start)}–${fmtTime(sc.end)}` : '';
    const nIn = g.lines.filter((l) => l.include).length;
    return `<button type="button" class="scene-row${open ? ' is-open' : ''}" data-act="scene" data-sid="${esc(g.sid)}" aria-expanded="${open}">
      <span class="chev" aria-hidden="true"></span><span class="sc-h" title="${esc(head)}">${esc(head)}</span>
      ${sc && sc.status ? `<span class="sc-st sc-${esc(sc.status)}">${esc(SCENE_ST[sc.status] || sc.status)}</span>` : ''}
      <span class="sc-t tnum">${span}</span><span class="sc-n tnum">${g.lines.length}줄${nIn !== g.lines.length ? ` · 자막 ${nIn}` : ''}</span></button>`;
  }
  function renderLines(opts) {
    const box = $('#rows');
    const list = D.allLines.filter((ln) => lineIn(ln, S.lf));
    $('#list-count').textContent = `${list.length} / ${D.allLines.length}줄`;
    if (!D.allLines.length) {
      box.innerHTML = '<div class="empty">대사 줄이 없습니다 · 대본 정렬 단계가 dialogue.json 을 만든 뒤 다시 여세요</div>';
      D.visibleLines = [];
      return;
    }
    const groups = [];
    for (const ln of list) {
      const sid = ln.scene_id || '';
      let g = groups[groups.length - 1];
      if (!g || g.sid !== sid) groups.push((g = { sid, lines: [] }));
      g.lines.push(ln);
    }
    const rows = [];
    let selIdx = -1;
    D.visibleLines = [];
    for (const g of groups) {
      const open = sceneOpen(g);
      rows.push(sceneHeadHtml(g, open));
      if (!open) continue;
      for (const ln of g.lines) {
        if (ln.id === S.line) selIdx = rows.length;
        rows.push(lineRowHtml(ln));
        D.visibleLines.push(ln);
      }
    }
    renderRows(box, rows, selIdx, '', '이 조건에 맞는 줄이 없습니다');
    finishList(opts, S.line && `lines-row-${S.line}`);
  }
  /** The fold above the list (대사 view only; it does not scroll away with the lines). */
  function renderAside() {
    const el = $('#list-aside');
    if (!el) return;
    const html = S.view === 'lines' ? asideHtml() : '';
    if (el._html !== html) { el._html = html; el.innerHTML = html; }       // the same rows: its scroll and an armed 버리기 stay
    el.hidden = !html;
  }
  // 붙지 못한 작가 결정 (state ``aside``): the writer's decisions that are on no line since the
  // script was read again. Kept out of the subtitles until the writer puts one on a line
  // (POST api/lines/aside {key, line}) or lets it go ({key, line: null}); nothing picks a line for them.
  const asideRows = () => (S.st && S.st.aside) || [];
  function asideCount() {
    const sm = (S.st && S.st.summary && S.st.summary.lines) || {};
    return typeof sm.aside === 'number' ? sm.aside : asideRows().length;
  }
  /** What a kept-aside decision decided, in Korean (its stored fields; a speaker item's ``final``). */
  function asideWhat(a) {
    const d = a.decision || {}, out = [];
    if (a.kind === 'speaker' && 'final' in d) out.push(d.final === 'include' ? '화자 표시 넣기' : d.final === 'exclude' ? '화자 표시 빼기' : `화자 표시 · ${d.final}`);
    if ('include' in d) out.push(d.include ? '줄 넣기' : '줄 빼기');
    if (ASIDE_FROM[d.text_from]) out.push(ASIDE_FROM[d.text_from]);
    else if (d.text != null || d.text_from) out.push(`글 고침 「${cut(d.text || '', 30)}」`);
    if ('name' in d) out.push(d.name ? `이름 · ${d.name}` : '이름을 대본대로');
    if ('show_name' in d) out.push(d.show_name ? '이름 표시' : '이름 생략');
    if ('language' in d) out.push(d.language ? `언어 · ${bracket(d.language)}` : '언어 · 한국어 (표시 없음)');
    if ('sung' in d) out.push(d.sung ? '노래 (♪ … ♪)' : '노래 아님');
    if ('source' in d) out.push(d.source ? `출처 · ${SOURCE_KO[d.source] || d.source}` : '출처 없음');
    if ('start' in d || 'end' in d) out.push(`시각 · ${fmtTime(d.start)}–${fmtTime(d.end)}`);
    if ((d.breaks || []).length) out.push(`끊을 자리 · ${d.breaks.join(', ')}번째 어절 뒤`);
    if ('keep' in d) out.push(d.keep ? '한 칸에 두기' : '한 칸에 두기 풂');
    const known = new Set(['final', 'include', 'text', 'text_from', 'name', 'show_name', 'language', 'sung', 'source', 'start', 'end', 'breaks', 'keep', 'at', 'fp', 'ctx']);
    for (const [k, v] of Object.entries(d)) if (!known.has(k)) out.push(`${k} · ${typeof v === 'object' ? JSON.stringify(v) : v}`);
    return out.length ? out.join(' · ') : '정한 칸 없음';
  }
  function asideRowHtml(a) {
    const key = esc(a.key), spk = a.kind === 'speaker';
    const at = /^\d{4}-(\d\d-\d\d)T(\d\d:\d\d)/.exec(String(a.at || ''));
    const cands = (a.candidates || []).map((lid) => {
      const ln = D.lines.get(lid);
      const t = ln && ln.start != null ? fmtTime(ln.start) : '--:--.-';
      return `<div class="as-cand"><button type="button" class="link as-line" data-act="goto-line" data-id="${esc(lid)}" title="그 줄로">
          <span class="tnum">${t}</span><span class="as-who">${esc(ln ? whoOf(ln) : '')}</span><span class="as-now">${esc(ln ? ln.text : `${lid} · 지금 없는 줄`)}</span></button>
        <button type="button" class="btn btn-sm btn-primary" data-act="aside-put" data-key="${key}" data-line="${esc(lid)}"${ln ? '' : ' disabled'}>이 줄에 붙이기</button></div>`;
    }).join('');
    return `<div class="as-row" data-key="${key}">
      <div class="as-top">${spk ? '<span class="tag">화자 항목</span>' : ''}<span class="as-who">${esc(a.speaker || '화자 없음')}</span>
        <span class="as-text" title="${esc(a.text)}">「${esc(cut(a.text, 40))}」</span>
        <span class="pill st-check">${esc(ASIDE_WHY[a.why] || a.why || '')}</span>${at ? `<span class="muted tnum">${at[1]} ${at[2]}</span>` : ''}</div>
      <div class="as-dec"><b>정했던 것</b>${esc(asideWhat(a))}</div>
      ${cands ? `<div class="as-cands"><span class="as-k">후보 줄${spk ? ' · 그 줄의 화자 항목에 붙는다' : ''}</span>${cands}</div>` : '<div class="as-none">맞는 줄이 대본에 없다 · 버리거나 그대로 둔다 (그대로 두면 자막에 쓰지 않음)</div>'}
      <div class="as-acts"><button type="button" class="btn btn-sm btn-danger" data-act="aside-drop" data-key="${key}" title="이 결정을 버린다 · 되돌릴 수 없다">버리기</button></div></div>`;
  }
  /** The fold of kept-aside decisions above the list of the 대사 view (nothing when there is none). */
  function asideHtml() {
    const rows = asideRows(), n = asideCount();
    if (!rows.length && !n) return '';
    const open = S.asideOpen;
    return `<div class="aside-box${open ? ' is-open' : ''}" id="aside-box">
      <button type="button" class="aside-head" data-act="toggle-aside" aria-expanded="${open}"><span class="chev" aria-hidden="true"></span>
        <b>대본이 바뀌어 붙지 못한 작가 결정 ${n}개</b><span class="as-sub">자막에 쓰지 않음 · 줄에 다시 붙이거나 버린다</span><span class="as-fold">${open ? '접기' : '펼치기'}</span></button>
      ${open ? `<div class="aside-rows">${rows.map(asideRowHtml).join('')}</div>` : ''}</div>`;
  }
  function asidePut(key, line) {
    const a = asideRows().find((x) => x.key === key);
    const ln = line && D.lines.get(line);
    const msg = line ? `작가 결정을 다시 붙임 · ${cut(ln ? ln.text : line, 26)}` : `작가 결정을 버림 · ${cut(a ? a.text : key, 26)}`;
    return mutate('api/lines/aside', { key, line: line || null }, msg);
  }
  /** 버리기 cannot be undone: the first press asks (the button says so for 3 s), the second lets it go. */
  function armAsideDrop(el, key) {
    if (el.dataset.armed !== '1') {
      el.dataset.armed = '1';
      el.dataset.label = el.textContent;
      el.textContent = '한 번 더 누르면 버림 · 되돌릴 수 없음';
      setTimeout(() => { if (document.body.contains(el)) { el.dataset.armed = ''; el.textContent = el.dataset.label; } }, 3000);
      return;
    }
    asidePut(key, null);
  }
  /** Script and heard text with the words that differ marked (the line's diff gives the script word
      range; the heard words are found in order). */
  function diffHtml(text, diff, side) {
    const toks = String(text || '').split(/\s+/).filter(Boolean);
    const mark = new Array(toks.length).fill(null);
    if (side === 'script') {
      for (const d of diff) if (Array.isArray(d.at)) for (let i = d.at[0]; i < d.at[1] && i < toks.length; i++) mark[i] = d;
    } else {
      let from = 0;
      for (const d of diff) {
        const h = String(d.heard || '').split(/\s+/).filter(Boolean);
        if (!h.length) continue;
        for (let i = from; i + h.length <= toks.length; i++) {
          if (h.every((w, k) => toks[i + k] === w)) { for (let k = 0; k < h.length; k++) mark[i + k] = d; from = i + h.length; break; }
        }
      }
    }
    return toks.map((w, i) => {
      const d = mark[i];
      if (!d) return esc(w);
      return `<mark class="d-${esc(d.kind || 'word')}${d.spelling ? ' d-spell' : ''}" title="${esc(d.spelling ? '맞춤법만 다를 수 있음' : DIFF_KO[d.kind] || '')}">${esc(w)}</mark>`;
    }).join(' ');
  }
  const editingText = (ln) => S.ed && S.ed.id === ln.id && S.ed.mode === 'text';
  function textEditHtml(ln) {
    return `<div class="cmp-edit" data-box="text"><input class="input" data-f="text" value="${esc(ln.text)}" aria-label="자막 글" autocomplete="off">
      <button type="button" class="btn btn-sm btn-primary" data-act="ln-text-save" data-primary="1">저장</button>
      <button type="button" class="btn btn-sm btn-text" data-act="ln-text-cancel">취소</button></div>`;
  }
  function compareHtml(ln) {
    const added = D.added.has(ln.id);
    if (added || ln.asr_text == null) {                 // nothing heard to compare: the text and 직접 고치기
      const changed = !added && ln.script_text && ln.script_text !== ln.text;
      return `<div class="sec">글<span class="sub">${added ? (ln.extra ? '대본에 없는 말에서 넣은 줄' : '작가가 넣은 줄') : changed ? '작가가 고친 글' : '대본 글 그대로 나간다'}</span></div>
        <div class="cmp"><div class="cmp-row is-on"><span class="cmp-k">${changed ? '작가 글' : '자막 글'}</span><span class="cmp-t">${esc(ln.text)}</span></div>
          ${changed ? `<div class="cmp-row"><span class="cmp-k">대본</span><span class="cmp-t">${esc(ln.script_text)}</span><button type="button" class="btn btn-sm" data-act="ln-from" data-from="script">대본대로</button></div>` : ''}
          ${editingText(ln) ? textEditHtml(ln) : '<div class="cmp-foot"><button type="button" class="btn btn-sm" data-act="ln-edit-text">직접 고치기</button></div>'}</div>`;
    }
    const script = ln.script_text || ln.text;
    const dec = ln.decision || {};
    const chosen = dec.text_from || (dec.text ? 'writer' : null);
    const diff = ln.diff || [];
    const heard = ln.asr_text || '';
    const spell = (ln.hints || []).includes('spelling');
    const row = (k, label, html, btn) => `<div class="cmp-row${chosen === k ? ' is-on' : ''}"><span class="cmp-k">${label}</span><span class="cmp-t">${html}</span>${btn || ''}</div>`;
    const b = (k, label, dis) => `<button type="button" class="btn btn-sm${chosen === k ? ' is-on' : ''}" data-act="ln-from" data-from="${k}"${dis ? ' disabled' : ''}${chosen === k ? ' aria-pressed="true"' : ''}>${label}</button>`;
    const rows = [row('script', '대본', diffHtml(script, diff, 'script'), b('script', '대본대로')),
      row('heard', '들린 말', heard ? diffHtml(heard, diff, 'heard') : '<span class="muted">들린 말 없음 · 받아쓰기가 이 줄을 못 맞춤</span>', b('heard', '들린 대로', !heard))];
    if (chosen === 'writer') rows.push(row('writer', '작가 글', esc(ln.text), ''));
    const hint = [diff.length ? `다른 곳 ${diff.length}곳` : '', spell ? '맞춤법만 다를 수 있음 · 대본 글을 확인' : '',
      chosen ? `지금 ${PICKED[chosen] || ''}` : ln.status === 'differs' ? '아직 고르지 않음 · 지금은 대본 글로 나감' : ''].filter(Boolean).join(' · ');
    return `<div class="sec">대조<span class="sub">대본 글과 그 구간에서 들린 글 · 저절로 바꾸지 않는다</span></div>
      <div class="cmp">${rows.join('')}
        ${editingText(ln) ? textEditHtml(ln) : `<div class="cmp-foot"><button type="button" class="btn btn-sm" data-act="ln-edit-text">직접 고치기</button>${chosen ? '<button type="button" class="btn btn-sm btn-text" data-act="ln-from" data-from="">고르기 취소</button>' : ''}<span class="cmp-hint${spell ? ' is-spell' : ''}">${esc(hint)}</span></div>`}
      </div>`;
  }
  function lineNoteHtml(ln) {
    const bits = [];
    const sc = D.sceneById.get(ln.scene_id);
    if (sc) bits.push(`장면 ${esc(sc.number || sc.id)} · ${esc(SCENE_ST[sc.status] || sc.status || '')}`);
    bits.push(D.added.has(ln.id) ? (ln.extra ? '대본에 없는 말에서 넣은 줄' : '작가가 넣은 줄') : `대본 ${esc(ln.id)}`);
    if (ln.name && ln.speaker && ln.name !== ln.speaker) bits.push(`대본 화자 ${esc(ln.speaker)} → ${esc(ln.name)}`);
    if (ln.include && ln.label) bits.push(ln.show_name ? `이름 표시 ${esc(ln.label)}` : '이름 생략');
    if (ln.generic) bits.push('이름이 밝혀지기 전 일반 표시');
    if (ln.name_early) bits.push(`<button type="button" class="link flag" data-act="goto-person" data-id="${esc(ln.name)}">이름이 밝혀지기 전에 이름이 나감</button>`);
    const cells = (ln.cells || []).map((c) => D.cellById.get(c)).filter(Boolean);
    if (cells.length) bits.push(cells.map((c, i) => `<button type="button" class="g-cell" data-act="goto-cell" data-cell="${esc(c.id)}" title="${esc((c.lines || []).join(' / '))}">${i + 1}칸 ${fmtTime(c.start)}</button>`).join(' '));
    else if (ln.include) bits.push('자막 칸 없음');
    const notes = new Set(cells.flatMap((c) => (c.check && c.check.notes) || []));
    for (const n of notes) bits.push(`<span class="flag">${esc(n)}</span>`);
    let out = `<div class="ev-note">${bits.join('<i>·</i>')}</div>`;
    if (ln.raw && ln.raw !== (ln.script_text || ln.text)) out += `<div class="raw"><b>대본 원문</b>${esc(ln.raw)}</div>`;
    return out;
  }
  function lineNoRoomHtml(ln) {
    return `<div class="nr-card nr-compact"><span class="pill st-noroom">자리 없음</span>
      <span class="nr-reason" title="${esc(ln.no_room.reason || '')}">${esc(ln.no_room.reason || '앞뒤 칸 사이에 자리가 없음')}</span>
      <div class="nr-actions"><button type="button" class="btn btn-sm" data-act="ln-focus-time">시작·끝 정하기</button>
      ${D.added.has(ln.id) ? '' : '<button type="button" class="btn btn-sm btn-danger" data-act="ln-include" data-v="0">빼기</button>'}</div></div>`;
  }
  function reviveHtml(ln) {
    if (ln.include || D.added.has(ln.id) || ln.excluded === 'writer') return '';
    const g = ln.guess;
    const s = g && S.t >= g[0] && S.t <= g[1] ? S.t : g ? g[0] : S.t;
    const why = { absent: '이 줄의 장면을 영화에서 찾지 못했다', out_of_range: '처리한 구간 밖의 줄이다', no_time: '시각이 없는 줄이다' }[ln.excluded] || '';
    return `<div class="sec">되살리기<span class="sub">${why} · 영화에서 들리면 시각을 정해 넣는다</span></div>
      ${timeBoxHtml('revive', s, Math.min(num(S.P.t1, s + 2), s + lineDur(ln.text)),
        `<button type="button" class="btn btn-sm btn-primary" data-act="ln-revive" data-primary="1">이 시각으로 넣기</button>
        <button type="button" class="btn btn-sm" data-act="ln-revive-now" title="지금 재생 위치부터 글 길이만큼">지금 위치에 넣기</button>`)}
      ${g ? `<div class="field-hint">어림 ${fmtTime(g[0])}–${fmtTime(g[1])} · 앞뒤 장면으로 어림한 범위라 넓을 수 있다</div>` : ''}`;
  }
  function timeEditHtml(ln) {
    const added = D.added.has(ln.id);
    const revived = !added && (ln.status === 'absent' || ln.status === 'out_of_range');
    const sub = ln.estimated ? '받아쓰기로 못 맞춰 어림한 시각 · 소리에 맞춰 정하면 추정 표시가 없어진다'
      : ln.pinned ? '작가가 정한 시각' : '받아쓰기로 맞춘 시각';
    const extra = revived ? '<button type="button" class="btn btn-sm btn-text" data-act="ln-unrevive">다시 빼기</button>'
      : ln.pinned && !added ? '<button type="button" class="btn btn-sm btn-text" data-act="ln-unpin">시각 고정 풀기</button>' : '';
    return `<div class="sec">시각<span class="sub">${sub}</span></div>
      ${timeBoxHtml('time', ln.start, ln.end, `<button type="button" class="btn btn-sm btn-primary" data-act="ln-time" data-primary="1">시각 정하기</button>${extra}`)}`;
  }
  function breaksHtml(ln) {
    const toks = String(ln.text || '').split(/\s+/).filter(Boolean);
    const keep = `<label class="switch sm"><input type="checkbox" data-change="ln-keep"${pendOf(ln, 'keep') ? ' checked' : ''}><span>한 칸에 두기</span></label>`;
    if (toks.length < 2) return `<div class="sec">칸 나누기</div><div class="ed-row">${keep}</div>`;
    const set = new Set(pendOf(ln, 'breaks') || []);
    return `<div class="sec">끊을 자리<span class="sub">어절 사이를 누르면 그 자리에서 다음 칸으로 넘긴다 · 비워 두면 저절로</span></div>
      <div class="toks">${toks.map((w, i) => `${i ? `<button type="button" class="gap${set.has(i) ? ' is-on' : ''}" data-act="ln-gap" data-k="${i}" aria-pressed="${set.has(i)}" aria-label="${i}번째 어절 뒤에서 끊기" title="${i}번째 어절 뒤에서 끊기"></button>` : ''}<span class="tok">${esc(w)}</span>`).join('')}</div>
      <div class="ed-row">${keep}${set.size ? '<button type="button" class="btn btn-sm btn-text" data-act="ln-breaks-clear">끊을 자리 지우기</button>' : ''}</div>`;
  }
  function markHtml(ln) {
    const dec = ln.decision || {};
    const own = !D.added.has(ln.id) && ('language' in dec || 'sung' in dec);
    return `<div class="sec">말 표시<span class="sub">외국어는 [언어] 표시 · 노래는 ♪ 가사 ♪ · 출처는 이름 뒤에 [이름/출처]</span></div>
      <div class="ed-row"><label>언어 <input class="input input-sm" list="lang-list" data-change="ln-lang" value="${esc(pendOf(ln, 'language') || '')}" placeholder="한국어" autocomplete="off"></label>
        <label class="switch sm"><input type="checkbox" data-change="ln-sung"${pendOf(ln, 'sung') ? ' checked' : ''}><span>노래</span></label>
        ${own ? '<button type="button" class="btn btn-sm btn-text" data-act="ln-mark-reset">대본대로</button>' : ''}</div>
      ${D.added.has(ln.id) ? '' : sourceHtml(ln)}`;
  }
  /** Where the line comes from (state.SOURCES): the script's, none, or the writer's pick. The
      option values: '' = the script's (sends null), 'none' = no source (sends ''), else the code. */
  function sourceHtml(ln) {
    const v = sourceOf(ln);
    const cur = v == null ? '' : v === '' ? 'none' : v;
    const own = !('source' in (ln.decision || {}));
    const script = own ? ` · ${ln.source ? SOURCE_KO[ln.source] || ln.source : '없음'}` : '';
    const opts = [['', `대본대로${script}`], ['none', '없음'], ...Object.entries(SOURCE_KO)];
    if (cur && !opts.some(([k]) => k === cur)) opts.push([cur, cur]);
    return `<div class="ed-row"><label>출처 <select class="input input-sm" data-change="ln-source" aria-label="대사의 출처">${opts.map(([k, label]) =>
      `<option value="${esc(k)}"${k === cur ? ' selected' : ''}>${esc(label)}</option>`).join('')}</select></label>
      <span class="field-hint">화면 밖은 표시 없이 · 그 밖은 이름과 함께 [하늘/속마음]</span></div>`;
  }
  function overlapHtml(ln) {
    const xs = D.overlaps.get(ln.id) || [];
    if (!xs.length) return '';
    return `<div class="sec">같은 자리의 대본에 없는 말<span class="sub">받아쓰기가 이 줄을 다른 말로 들었을 수 있다</span></div>
      <div class="x-rows">${xs.map((x) => { const [lab, cls] = XSTATE[x.state] || XSTATE.open; return `<div class="x-row">
        <button type="button" class="ev-play x-play" data-act="x-play" data-id="${esc(x.id)}" aria-label="듣기">${ICON.play}</button>
        <span class="ev-t tnum">${fmtTime(x.start)}</span><span class="ev-text" title="${esc(x.text)}">${esc(x.text)}</span><span class="pill ${cls}">${lab}</span>
        <button type="button" class="btn btn-sm" data-act="x-use-time" data-id="${esc(x.id)}" data-line="${esc(ln.id)}" title="이 말의 시작·끝을 이 줄에 주고, 이 말은 무시">이 시각을 줄에 쓰기</button>
        <button type="button" class="btn btn-sm btn-text" data-act="goto-extra" data-id="${esc(x.id)}">보기</button></div>`; }).join('')}</div>`;
  }
  function renderLineDetail() {
    const box = $('#evidence');
    const ln = D.lines.get(S.line);
    if (!ln) {
      box.innerHTML = `${detailHead('대사 줄', '', '')}<div class="ev-empty">목록에서 줄을 고르면 대본 글과 들린 글을 나란히 보인다 · 다른 낱말은 칠해 보인다</div>`;
      return;
    }
    const added = D.added.has(ln.id);
    const timed = ln.start != null && ln.end != null;
    const acts = [];
    if (timed || ln.guess) acts.push(`<button type="button" class="btn" data-act="ln-play" title="이 줄의 구간을 재생">${ICON.play}${timed ? '구간 듣기' : '어림 구간 듣기'}</button>`);
    acts.push('<button type="button" class="btn" data-act="ln-speaker">화자 고치기</button>');
    if (added) {
      acts.push(`<button type="button" class="btn" data-act="added-edit" data-id="${esc(ln.id)}">고치기</button>`);
      acts.push(`<button type="button" class="btn btn-danger" data-act="added-delete" data-id="${esc(ln.id)}">지우기</button>`);
    } else if (ln.include) acts.push('<button type="button" class="btn btn-danger" data-act="ln-include" data-v="0">이 줄 빼기</button>');
    else if (ln.excluded === 'writer') acts.push('<button type="button" class="btn btn-primary" data-act="ln-include" data-v="">다시 넣기</button>');
    const span = timed ? `${fmtTime(ln.start)}–${fmtTime(ln.end)}` : ln.guess ? `어림 ${fmtTime(ln.guess[0])}–${fmtTime(ln.guess[1])}` : '시각 없음';
    box.innerHTML = `${detailHead('대사 줄', '', acts.join(''))}
      <div class="ev-subject"><span class="badge k-line">대사</span><span class="ev-time tnum">${span}</span>
        <span class="ev-phrase" title="${esc(ln.text)}">${ln.include && ln.show_name && ln.label ? `<span class="nm">${esc(ln.label)}</span> ` : ''}${esc(ln.text)}</span>${linePill(ln)}</div>
      ${lineNoteHtml(ln)}
      ${ln.no_room ? lineNoRoomHtml(ln) : ''}
      ${compareHtml(ln)}
      ${reviveHtml(ln)}
      ${ln.include && timed ? timeEditHtml(ln) : ''}
      ${ln.include ? breaksHtml(ln) : ''}
      ${markHtml(ln)}
      ${overlapHtml(ln)}`;
  }

  // -------------------------------------------- 대본에 없는 말 (extras, 1-04)
  function extraIn(x, f) {
    if (f === 'all') return true;
    if (f === 'overlap') return !!(x.overlaps || []).length;
    if (f === 'suspect') return !!x.suspect;
    return (x.state || 'open') === f;
  }
  /** An undecided 대본에 없는 말 that looks like 받아쓰기 헛소리: folded (not deleted) until the writer
      opens the fold, and not counted as left to do; once decided it is an ordinary row. */
  const isSuspect = (x) => !!x.suspect && (x.state || 'open') === 'open';
  const suspectFolded = (x) => isSuspect(x) && !S.showSuspect && x.id !== S.extra && S.xf !== 'suspect';
  const suspectOpen = () => D.extras.filter(isSuspect).length;
  const suspectKo = (x) => SUSPECT_KO[x.suspect] || String(x.suspect);
  const suspectBy = (x) => Object.entries(x.suspect_by || {}).map(([k, v]) => `${SUSPECT_BY[k] || k} ${v}`).join(' · ');     // plain text
  function extraRowHtml(x) {
    const sel = x.id === S.extra;
    const ov = (x.overlaps || []).map((l) => D.lines.get(l)).filter(Boolean);
    const sub = [sceneName(x.scene_id), ov.length ? `못 들은 줄과 겹침: ${ov.map((l) => cut(l.text, 16)).join(', ')}` : '',
      x.short ? '짧은 말' : '', x.state === 'line' && x.added ? '대사로 넣음' : ''].filter(Boolean).join(' · ');
    const [lab, cls] = XSTATE[x.state] || XSTATE.open;
    const tag = x.suspect ? `<span class="tag t-suspect" title="${esc(suspectBy(x))}">헛소리 의심 · ${esc(suspectKo(x))}</span>` : '';
    return `<div class="row${sel ? ' is-sel' : ''}${x.state === 'ignored' ? ' is-out' : ''}${isSuspect(x) ? ' is-suspect' : ''}" role="option" id="extras-row-${esc(x.id)}" data-view="extras" data-id="${esc(x.id)}" aria-selected="${sel}">
      <span class="r-time tnum">${fmtTime(x.start)}</span><span class="badge k-extra">말</span>
      <span class="r-main"><span class="r-phrase">${esc(x.text)}</span><span class="r-sub">${tag}${esc(sub)}</span></span>
      <span class="pill ${cls}">${lab}</span></div>`;
  }
  function renderExtras(opts) {
    const box = $('#rows');
    const list = D.extras.filter((x) => extraIn(x, S.xf));
    const vis = (D.visibleExtras = list.filter((x) => !suspectFolded(x)));
    const folded = list.length - vis.length, sus = list.filter(isSuspect).length;
    const tail = folded || (S.showSuspect && sus && S.xf !== 'suspect')
      ? `<button type="button" class="fold-row" data-act="toggle-suspect">${S.showSuspect ? `받아쓰기 헛소리 의심 ${sus}개 접기`
        : `받아쓰기 헛소리로 의심되는 말 ${folded}개 접힘 · 그 자리에서 목소리가 거의 잡히지 않음 · 펼치기`}</button>` : '';
    $('#list-count').textContent = `${vis.length} / ${D.extras.length}개`;
    renderRows(box, vis.map(extraRowHtml), vis.findIndex((x) => x.id === S.extra), tail,
      D.extras.length ? '이 조건에 맞는 말이 없습니다' : '대본에 없는 들린 말이 없습니다 · 받아쓰기와 대본 정렬 뒤에 생긴다');
    finishList(opts, S.extra && `extras-row-${S.extra}`);
  }
  function extraFormHtml(x) {
    const chars = S.st.characters || [];
    return `<div class="sec">대사로 넣기<span class="sub">들린 대로 글을 다듬고 화자를 고른다 · 넣으면 작가가 넣은 줄이 된다</span></div>
      <div class="x-form" data-box="x-line">
        <div class="ed-row"><label class="grow">글 <input class="input" data-f="text" value="${esc(x.text)}" autocomplete="off"></label></div>
        <div class="ed-row"><label>화자 <select class="input input-sm" data-f="name"><option value="">모름 (이름 없이)</option>${chars.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('')}</select></label>
          <input class="input input-sm" data-f="newname" placeholder="새 이름" autocomplete="off" aria-label="새 이름">
          <label>언어 <input class="input input-sm" list="lang-list" data-f="language" placeholder="한국어" autocomplete="off"></label>
          <label class="switch sm"><input type="checkbox" data-f="sung"><span>노래</span></label></div>
        ${timeBoxHtml('x-time', x.start, x.end, '')}
        <div class="ed-row"><button type="button" class="btn btn-primary" data-act="x-line" data-id="${esc(x.id)}" data-primary="1">대사로 넣기</button></div>
      </div>`;
  }
  function renderExtraDetail() {
    const box = $('#evidence');
    const x = D.extraById.get(S.extra);
    if (!x) {
      box.innerHTML = `${detailHead('대본에 없는 말', '', '')}<div class="ev-empty">받아쓰기에서 들렸지만 어느 대본 줄에도 없는 말 · 고르면 듣고 대사로 넣거나 무시한다</div>`;
      return;
    }
    const st = x.state || 'open';
    const [lab, cls] = XSTATE[st] || XSTATE.open;
    const acts = [`<button type="button" class="btn" data-act="x-play" data-id="${esc(x.id)}">${ICON.play}듣기</button>`];
    if (st === 'open') acts.push(`<button type="button" class="btn" data-act="x-ignore" data-id="${esc(x.id)}">무시</button>`);
    else {
      if (x.added && D.lines.has(x.added)) acts.push(`<button type="button" class="btn" data-act="goto-line" data-id="${esc(x.added)}">넣은 줄 보기</button>`);
      acts.push(`<button type="button" class="btn btn-text" data-act="x-clear" data-id="${esc(x.id)}">되돌리기</button>`);
    }
    const sc = D.sceneById.get(x.scene_id);
    const note = [sc ? `장면 ${esc(sc.number || sc.id)} · ${esc(SCENE_ST[sc.status] || '')}` : '', `${(num(x.end) - num(x.start)).toFixed(1)}초`,
      x.short ? '<span class="flag">짧은 말 · 낱말 조각일 수 있음</span>' : ''].filter(Boolean);
    const ov = (x.overlaps || []).map((l) => D.lines.get(l)).filter(Boolean);
    box.innerHTML = `${detailHead('대본에 없는 말', '', acts.join(''))}
      <div class="ev-subject"><span class="badge k-extra">말</span><span class="ev-time tnum">${fmtTime(x.start)}–${fmtTime(x.end)}</span>
        <span class="ev-phrase" title="${esc(x.text)}">${esc(x.text)}</span><span class="pill ${cls}">${lab}</span></div>
      <div class="ev-note">${note.join('<i>·</i>')}</div>
      ${x.suspect ? `<div class="sec">받아쓰기 헛소리 의심<span class="sub">아무도 말하지 않는 곳(음악·조용한 곳)에 받아쓰기 모델이 흔히 쓰는 꼴 · 지우지 않고 접어 둔다 · 들어 보고 정한다</span></div>
        <div class="ev-note x-suspect"><span class="flag">${esc(suspectKo(x))}</span>${x.suspect_by ? `<i>·</i>근거 점수 ${esc(suspectBy(x))}` : ''}</div>` : ''}
      ${ov.length ? `<div class="sec">같은 자리의 못 들은 대본 줄<span class="sub">이 말이 그 줄일 수 있다</span></div>
        <div class="x-rows">${ov.map((l) => `<div class="x-row"><span class="ev-t tnum">${fmtTime(l.start)}</span>
          <span class="ev-text" title="${esc(l.text)}">${l.label ? `<span class="nm">${esc(l.label)}</span> ` : ''}${esc(l.text)}</span>${linePill(l)}
          <button type="button" class="btn btn-sm" data-act="x-use-time" data-id="${esc(x.id)}" data-line="${esc(l.id)}" title="이 말의 시작·끝을 그 줄에 주고, 이 말은 무시">이 시각을 그 줄에 쓰기</button>
          <button type="button" class="btn btn-sm btn-text" data-act="goto-line" data-id="${esc(l.id)}">줄 보기</button></div>`).join('')}</div>` : ''}
      ${st === 'open' ? extraFormHtml(x) : ''}`;
  }
  function extraAct(id, action, fields = {}, msg) {
    const x = D.extraById.get(id);
    const what = { ignore: `무시 · ${cut(x ? x.text : id, 28)}`, clear: `되돌림 · ${cut(x ? x.text : id, 28)}`, line: '대사로 넣음' }[action];
    return mutate(`api/extras/${enc(id)}`, { action, ...fields }, msg === undefined ? what : msg);
  }
  async function extraToLine(id, el) {
    const box = el.closest('[data-box="x-line"]');
    if (!box) return;
    const v = (f) => { const e = box.querySelector(`[data-f="${f}"]`); return !e ? null : e.type === 'checkbox' ? e.checked : e.value; };
    const span = readSpan(box.querySelector('[data-box="x-time"] [data-f="start"]'));
    if (!span) return;
    const text = String(v('text') || '').trim();
    if (!text) { toast('넣을 글을 적어 주세요', 'error'); return; }
    const fields = { text, start: span[0], end: span[1], sung: !!v('sung') };
    const name = String(v('newname') || '').trim() || v('name');
    if (name) fields.name = name;
    const lang = String(v('language') || '').trim();
    if (lang) fields.language = lang;
    await extraAct(id, 'line', fields, `대사로 넣음 · ${cut(text, 28)}`);
  }
  /** An unheard line and the speech heard where it should be: the line takes that time, the speech is set aside. */
  async function useExtraTime(xid, lid) {
    const x = D.extraById.get(xid), ln = D.lines.get(lid);
    if (!x || !ln) return;
    const st = await lineUpdate(ln, { start: x.start, end: x.end }, null);
    if (st) await extraAct(xid, 'ignore', {}, `줄의 시각을 이 말에 맞춤 · ${fmtTime(x.start)}–${fmtTime(x.end)} · 이 말은 무시`);
  }

  // ------------------------------------------------------ 인물 (people, 3-01)
  // decided = the writer chose how the person is shown before the name is told (p.mode); a stored
  // reveal line alone does not decide it
  function personIn(p, f) {
    if (f === 'open') return num(p.lines) > 0 && !p.mode && !p.role;
    if (f === 'early') return !!p.early;
    if (f === 'decided') return !!p.mode;
    return true;
  }
  function personState(p) {
    if (p.early) return ['이름이 일찍 나감', 'st-noroom'];
    if (p.mode) return [p.mode === 'generic' && p.before ? `정함 · ${bracket(p.before)}` : '정함 · 이름 그대로', 'st-inc-done'];
    if (p.role) return ['역할 이름', 'st-exc'];
    if (!num(p.lines)) return ['자막 줄 없음', 'st-exc'];
    return ['정하지 않음', 'st-check'];
  }
  function personSub(p) {
    const bits = [`자막 ${num(p.lines)}줄`];
    const told = p.told && D.lines.get(p.told.line);
    if (p.told) bits.push(`처음 불림 ${told && told.start != null ? fmtTime(told.start) : p.told.line}${p.told.how === 'self' ? ' · 스스로 밝힘' : p.told.by ? ` · ${p.told.by}${ga(p.told.by)} 부름` : ''}`);
    else bits.push('대사에서 불리지 않음');
    if (p.mode === 'generic' && p.before) bits.push(`그 전에는 [${p.before}]`);
    else if (p.mode === 'name') bits.push('이름 그대로');
    return bits.join(' · ');
  }
  function personRowHtml(p) {
    const sel = p.name === S.person;
    const [lab, cls] = personState(p);
    const cues = (p.cues || []).filter((c) => c !== p.name);
    return `<div class="row row-person${sel ? ' is-sel' : ''}${num(p.lines) ? '' : ' is-out'}" role="option" id="people-row-${esc(p.id)}" data-view="people" data-id="${esc(p.name)}" aria-selected="${sel}">
      ${avatarHtml(p.name)}<span class="r-main"><span class="r-phrase">${esc(p.name)}${cues.length ? `<small> · ${esc(cues.join(', '))}</small>` : ''}</span>
      <span class="r-sub">${esc(personSub(p))}</span></span><span class="pill ${cls}">${esc(lab)}</span></div>`;
  }
  function renderPeople(opts) {
    const box = $('#rows');
    const list = (D.visiblePeople = D.people.filter((p) => personIn(p, S.pf)).sort((a, b) => (num(b.lines) > 0) - (num(a.lines) > 0)));
    $('#list-count').textContent = `${list.length} / ${D.people.length}명`;
    renderRows(box, list.map(personRowHtml), list.findIndex((p) => p.name === S.person), '',
      D.people.length ? '이 조건에 맞는 인물이 없습니다' : '인물이 없습니다 · 대본(script.json)의 인물 표에서 온다');
    const cur = D.personByName.get(S.person);
    finishList(opts, cur && `people-row-${cur.id}`);
  }
  function renderPersonDetail() {
    const box = $('#evidence');
    const p = D.personByName.get(S.person);
    if (!p) {
      box.innerHTML = `${detailHead('인물', '', '')}<div class="ev-empty">인물을 고르면 이름이 처음 불리는 줄과, 그 전에 쓸 표시([남자]·[여자])를 정한다</div>`;
      return;
    }
    const split = STYLE().name_split || '/';
    const shown = D.allLines.filter((l) => l.include && String(l.name || '').split(split).map((x) => x.trim()).includes(p.name));
    const told = p.told && D.lines.get(p.told.line);
    const dec = p.decision || {};
    const mode = p.mode || null, before = p.before || dec.before || '';
    const [lab, cls] = personState(p);
    const opt = (on, attrs, label) => `<button type="button" class="mchip${on ? ' is-on' : ''}" ${attrs} aria-pressed="${on}">${label}</button>`;
    const custom = mode === 'generic' && !GENERIC.includes(before) ? before : '';
    const modes = [
      ...GENERIC.map((g) => opt(mode === 'generic' && before === g, `data-act="pp-mode" data-mode="generic" data-before="${g}"`, esc(bracket(g)))),
      `<span class="pp-custom" data-box="pp"><input class="input input-sm" data-f="before" value="${esc(custom)}" placeholder="직접 (예: 경찰)" autocomplete="off" aria-label="일반 표시 직접 쓰기">
        <button type="button" class="btn btn-sm${custom ? ' is-on' : ''}" data-act="pp-custom" data-primary="1">쓰기</button></span>`,
      opt(mode === 'name', 'data-act="pp-mode" data-mode="name"', '이름 그대로'),
      opt(!mode, 'data-act="pp-mode" data-mode=""', '정하지 않음'),
    ];
    const held = (f) => { const q = pend.get(`person:${p.name}|${f}`); return q ? q.v : dec[f]; };
    const reveal = held('reveal') || 'auto', revealLine = held('reveal_line');
    const from = p.from_line && D.lines.get(p.from_line);
    const revealHtml = mode !== 'generic' ? '' : `<div class="sec">이름으로 바뀌는 줄<span class="sub">그 줄부터 ${esc(bracket(before))} 대신 이름을 쓴다</span></div>
      <div class="mchips">
        ${opt(reveal === 'auto', 'data-act="pp-reveal" data-reveal="auto"', `저절로 · 이름이 불린 다음 줄${from && reveal === 'auto' ? ` · ${fmtTime(from.start)}` : ''}`)}
        ${opt(reveal === 'never', 'data-act="pp-reveal" data-reveal="never"', '끝까지 일반 표시')}
        <label class="pp-line${reveal === 'line' ? ' is-on' : ''}">이 줄부터 <select class="input input-sm" data-change="pp-reveal-line"><option value="">고르기</option>${shown.map((l) => `<option value="${esc(l.id)}"${revealLine === l.id ? ' selected' : ''}>${fmtTime(l.start)} ${esc(cut(l.text, 22))}</option>`).join('')}</select></label>
      </div>`;
    const lineBtn = (l, small) => `<button type="button" class="nb-line" data-act="goto-line" data-id="${esc(l.id)}"><span class="t tnum">${fmtTime(l.start)}</span><span class="badge k-line">대사</span>
      <span class="tx">${l.show_name && l.label ? `<span class="nm">${esc(l.label)}</span> ` : ''}${esc(l.text)}${small ? `<small>${esc(small)}</small>` : ''}</span></button>`;
    box.innerHTML = `${detailHead('인물', '', '')}
      <div class="ev-subject">${avatarHtml(p.name)}<span class="ev-phrase">${esc(p.name)}</span><span class="pill ${cls}">${esc(lab)}</span></div>
      <div class="ev-note">${[`대본 표기 ${esc((p.cues && p.cues.length ? p.cues : [p.name]).join(', '))}`, `자막 ${num(p.lines)}줄`,
        p.role ? '역할 이름 (숫자로 끝남) · 기본은 이름 그대로' : ''].filter(Boolean).join('<i>·</i>')}</div>
      <div class="sec">이름이 처음 불리는 줄</div>
      ${told ? `<div class="nb-lines">${lineBtn(told, p.told.how === 'self' ? '스스로 이름을 밝힘' : `${p.told.by || ''}${ga(p.told.by || '')} 이름을 부름`)}</div>`
        : '<div class="ev-empty">대사에서 이름이 불리지 않는다 · 끝까지 일반 표시를 쓰거나, 이름이 화면 글자로 나오는 줄을 고른다</div>'}
      ${p.early ? `<div class="nr-card nr-compact"><span class="pill st-noroom">이름이 일찍 나감</span><span class="nr-reason">이름이 밝혀지기 전 줄에 이름이 나간다 · 아래에서 그 전 표시를 정한다</span>
        <div class="nr-actions"><button type="button" class="btn btn-sm" data-act="goto-line" data-id="${esc(p.early.line)}">처음 나가는 줄</button></div></div>` : ''}
      <div class="sec">이름이 밝혀지기 전에 쓸 표시<span class="sub">지침 II.8 · 아직 알려지지 않은 이름은 [남자]·[여자]</span></div>
      <div class="mchips">${modes.join('')}</div>
      ${revealHtml}
      <div class="sec">이 인물의 자막 줄<span class="sub">${shown.length}줄 · 누르면 그 줄로</span></div>
      ${shown.length ? `<div class="nb-lines">${shown.slice(0, 120).map((l) => lineBtn(l)).join('')}</div>` : '<div class="ev-empty">자막에 들어간 줄이 없다</div>'}`;
  }
  function peopleSet(fields) {
    const p = D.personByName.get(S.person);
    if (!p) return null;
    const what = 'mode' in fields
      ? (fields.mode === 'generic' ? `이름이 밝혀지기 전 ${bracket(fields.before)}` : fields.mode === 'name' ? '이름 그대로' : '정하지 않음으로')
      : fields.reveal === 'never' ? '끝까지 일반 표시' : fields.reveal === 'line' ? '고른 줄부터 이름' : '이름이 불린 다음 줄부터 이름';
    // the reveal and its line are kept on the panel until this write is answered (an earlier
    // write's answer would draw the select back to the stored line meanwhile)
    const keys = pendStart(`person:${p.name}`, fields, PEND_PERSON);
    const out = mutate('api/people', { names: [p.name], ...fields }, `${p.name} · ${what}`).then((st) => { pendEnd(keys); return st; });
    if (keys.length) renderDetail();                // the chips and the select show the pick at once
    return out;
  }

  // ------------------------------------------------------------ 칸 (cells, 9-x3)
  function cellIn(c, f) {
    const vs = D.cellViol.get(c.id) || [];
    if (f === 'must') return vs.some((v) => v.level === 'must');
    if (f === 'should') return vs.some((v) => v.level !== 'must');
    if (f === 'estimated') return (c.flags || []).includes('timing_estimated');
    if (f === 'overlap') return (c.flags || []).includes('overlap');
    return true;
  }
  function cellState(c) {
    const vs = D.cellViol.get(c.id) || [];
    const must = vs.filter((v) => v.level === 'must').length, should = vs.length - must;
    if (must) return [`의무 ${must}`, 'st-noroom'];
    if (should) return [`권고 ${should}`, 'st-check'];
    if ((c.flags || []).length) return [CELL_FLAGS[c.flags[0]] || c.flags[0], 'st-exc'];
    return ['통과', 'st-inc'];
  }
  function cellRowHtml(c, i) {
    const sel = c.id === S.cell;
    const vs = D.cellViol.get(c.id) || [];
    const ch = c.check || {};
    const [lab, cls] = cellState(c);
    const sub = vs.length ? vs.map((v) => `${v.level === 'must' ? '의무' : '권고'} ${v.clause} ${v.message || ''}`).join(' · ')
      : [`${num(ch.dur, c.end - c.start).toFixed(2)}초`, `초당 ${num(ch.cps).toFixed(1)}자`, ...(c.flags || []).map((f) => CELL_FLAGS[f] || f)].join(' · ');
    return `<div class="row row-cell${sel ? ' is-sel' : ''}" role="option" id="cells-row-${esc(c.id)}" data-view="cells" data-id="${esc(c.id)}" aria-selected="${sel}">
      <span class="r-time tnum">${fmtTime(c.start)}</span><span class="r-no tnum" title="${esc(CELL_KIND[c.kind] || '')}">${i + 1}</span>
      <span class="r-main"><span class="r-phrase">${esc((c.lines || []).join(' / '))}</span><span class="r-sub">${esc(sub)}</span></span>
      <span class="pill ${cls}">${esc(lab)}</span></div>`;
  }
  function renderCells(opts) {
    const box = $('#rows');
    const list = (D.visibleCells = D.cells.filter((c) => cellIn(c, S.cf)));
    const idx = new Map(D.cells.map((c, i) => [c.id, i]));
    $('#list-count').textContent = `${list.length} / ${D.cells.length}칸`;
    renderRows(box, list.map((c) => cellRowHtml(c, idx.get(c.id))), list.findIndex((c) => c.id === S.cell), '',
      D.cells.length ? '이 조건에 맞는 칸이 없습니다' : '자막 칸이 아직 없습니다');
    finishList(opts, S.cell && `cells-row-${S.cell}`);
  }
  function renderCellDetail() {
    const box = $('#evidence');
    const c = D.cellById.get(S.cell);
    if (!c) {
      box.innerHTML = `${detailHead('자막 칸', '', '')}<div class="ev-empty">칸을 고르면 지침 검사 결과와, 그 칸을 만든 줄·후보를 보인다 · ← → 로 한 프레임씩</div>`;
      return;
    }
    const ch = c.check || {};
    const i = D.cells.indexOf(c);
    const vs = D.cellViol.get(c.id) || [];
    const own = S.st.export_check ? [] : (ch.violations || []).map(violText);       // placement's own count, when the guide check could not run
    const f0 = frameOf(c.start), f1 = frameOf(c.end);
    const [lab, cls] = cellState(c);
    const acts = [`<button type="button" class="btn" data-act="cell-play">${ICON.play}칸 재생</button>`,
      `<button type="button" class="btn btn-text" data-act="cell-step" data-d="-1"${i <= 0 ? ' disabled' : ''}>앞 칸</button>`,
      `<button type="button" class="btn btn-text" data-act="cell-step" data-d="1"${i >= D.cells.length - 1 ? ' disabled' : ''}>뒤 칸</button>`];
    const facts = [`${fmtTime2(c.start)}–${fmtTime2(c.end)}`, `${f0}–${f1}프레임 · ${f1 - f0}프레임`, `${num(ch.dur, c.end - c.start).toFixed(2)}초`,
      `초당 ${num(ch.cps).toFixed(1)}자`, (ch.chars || []).length ? `줄 폭 ${ch.chars.map((x) => String(Math.round(num(x) * 10) / 10)).join(' / ')}자` : '',
      ch.gap_next != null ? `다음 칸까지 ${num(ch.gap_next).toFixed(2)}초` : '', c.pos === 'top' ? '화면 위' : ''].filter(Boolean);
    const refs = (c.refs || []).map((r) => {
      const ln = D.lines.get(r);
      if (ln) {
        return `<button type="button" class="nb-line" data-act="goto-line" data-id="${esc(r)}"><span class="t tnum">${fmtTime(ln.start)}</span><span class="badge k-line">대사</span>
          <span class="tx">${ln.show_name && ln.label ? `<span class="nm">${esc(ln.label)}</span> ` : ''}${esc(ln.text)}<small>${esc(lineStateLabel(ln))}</small></span></button>`;
      }
      const it = D.byId.get(r);
      if (!it) return '';
      const k = KIND[it.kind] || { label: it.kind, cls: '' };
      return `<button type="button" class="nb-line" data-act="goto-item" data-id="${esc(r)}"><span class="t tnum">${fmtTime(it.onset)}</span><span class="badge ${k.cls}">${esc(k.label)}</span>
        <span class="tx">${esc(phraseText(it))}<small>${esc(stateLabel(it))}</small></span></button>`;
    }).join('');
    const flags = (c.flags || []).map((f) => CELL_FLAGS[f] || f).concat(ch.notes || []);
    box.innerHTML = `${detailHead(`자막 칸 ${i + 1}`, `${D.cells.length}칸 가운데`, acts.join(''))}
      <div class="ev-subject"><span class="badge k-cell">${esc(CELL_KIND[c.kind] || '칸')}</span><span class="ev-time tnum">${fmtTime(c.start)}–${fmtTime(c.end)}</span>
        <span class="spacer"></span><span class="pill ${cls}">${esc(lab)}</span></div>
      <div class="cell-pv">${(c.lines || []).map((l) => `<div>${esc(l)}</div>`).join('')}</div>
      <div class="ev-note">${facts.map(esc).join('<i>·</i>')}</div>
      ${flags.length ? `<div class="ev-note">${flags.map((f) => `<span class="flag">${esc(f)}</span>`).join('<i>·</i>')}</div>` : ''}
      <div class="sec">지침 검사</div>
      ${vs.length || own.length ? `<div class="guide">${vs.map((v) => `<div class="g-rule g-rule2"><span class="g-lv ${v.level === 'must' ? 'must' : 'should'}">${v.level === 'must' ? '의무' : '권고'}</span>
        <span class="g-cl tnum">${esc(v.clause)}</span><span class="g-tt" title="${esc(v.message || '')}">${esc(v.message || '')}</span></div>`).join('')}
        ${own.map((t) => `<div class="g-rule g-rule2"><span class="g-lv must">한도</span><span class="g-cl"></span><span class="g-tt">${esc(t)}</span></div>`).join('')}</div>`
        : '<div class="ev-empty">걸린 조항이 없다</div>'}
      <div class="sec">이 칸을 만든 것<span class="sub">누르면 그 줄이나 후보로</span></div>
      ${refs ? `<div class="nb-lines">${refs}</div>` : '<div class="ev-empty">없음</div>'}`;
  }

  // ------------------------------------------------ 새로 넣기 (added, 4-05)
  function openAdd(kind = 'line', row = null) {
    pauseMain();
    teardownPreview();
    const t = clamp(S.t, S.P.t0, Math.max(S.P.t0, S.P.t1 - 0.5));
    const a = row || {};
    S.modal = {
      type: 'add', id: a.id || null, kind: a.kind || kind, start: a.start != null ? a.start : t,
      end: a.end != null ? a.end : Math.min(S.P.t1, t + 2), text: a.text || '', phrase: unbracket(a.phrase || ''),
      name: a.name || '', language: a.language || '', sung: !!a.sung, show: a.show_name !== false, timeOk: true,
    };
    renderAdd();
    showModal();
    const f = $('#add-text') || $('#add-phrase');
    if (f) f.focus();
  }
  function readAdd() {
    const m = S.modal, box = $('#modal-box');
    const v = (sel) => { const el = box.querySelector(sel); return el ? el.value : null; };
    const s = parseTime(v('[data-f="start"]')), e = parseTime(v('[data-f="end"]'));
    m.timeOk = Number.isFinite(s) && Number.isFinite(e) && e > s;
    if (Number.isFinite(s)) m.start = s;
    if (Number.isFinite(e)) m.end = e;
    if (m.kind === 'line') {
      m.text = v('#add-text') ?? m.text;
      m.name = String(v('#add-newname') || '').trim() || v('#add-name') || '';
      m.language = v('#add-lang') ?? m.language;
      const sung = box.querySelector('#add-sung');
      if (sung) m.sung = sung.checked;
      const show = box.querySelector('#add-show');
      if (show) m.show = show.checked;
    } else m.phrase = v('#add-phrase') ?? m.phrase;
  }
  /** How it will read in a cell: the label as placement writes it, ♪ for a song, [ ] for a sound. */
  function addPreview(m) {
    if (m.kind !== 'line') return bracket(m.phrase);
    const st = STYLE();
    const tail = String(m.language || '').trim() ? `${st.label_sep}${String(m.language).trim()}` : '';
    const lab = nameLabel(m.show ? m.name : '', tail);
    const body = m.sung ? `${st.note || '♪'} ${m.text} ${st.note || '♪'}` : m.text;
    return `${lab ? `${lab} ` : ''}${body}`;
  }
  function syncAddPreview() {
    readAdd();
    const el = $('#add-preview');
    if (el) el.textContent = addPreview(S.modal);
  }
  function renderAdd() {
    const m = S.modal;
    const chars = (S.st.characters || []).slice();
    const inChars = !m.name || chars.includes(m.name);
    const kinds = [['line', '대사'], ['sfx', '효과음'], ['music', '음악']];
    const music = STYLE().music || '음악';
    $('#modal-box').innerHTML = `<div class="xp add">
      <div class="xp-head"><span class="logo" aria-hidden="true">SDH</span><h3 id="modal-title">${m.id ? '넣은 것 고치기' : '새로 넣기'}</h3>
        <span class="muted">작가가 넣은 것은 그대로 자막에 들어간다</span><span class="spacer"></span>${CLOSE_BTN}</div>
      <div class="xp-body">
        <div class="seg seg-scope" role="radiogroup" aria-label="넣을 것">${kinds.map(([k, l]) => `<label><input type="radio" name="add-kind" value="${k}"${m.kind === k ? ' checked' : ''}${m.id && m.kind !== k ? ' disabled' : ''}><span>${l}</span></label>`).join('')}</div>
        ${timeBoxHtml('add', m.start, m.end, '')}
        ${m.kind === 'line' ? `
          <div class="field"><label for="add-text">대사 글</label><input class="input" id="add-text" value="${esc(m.text)}" placeholder="들린 말 그대로" autocomplete="off"></div>
          <div class="ed-row"><label>화자 <select class="input input-sm" id="add-name"><option value="">모름 (이름 없이)</option>${chars.map((n) => `<option value="${esc(n)}"${n === m.name ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>
            <input class="input input-sm" id="add-newname" value="${inChars ? '' : esc(m.name)}" placeholder="새 이름" autocomplete="off" aria-label="새 이름">
            <label class="switch sm"><input type="checkbox" id="add-show"${m.show ? ' checked' : ''}><span>이름 표시</span></label></div>
          <div class="ed-row"><label>언어 <input class="input input-sm" id="add-lang" list="lang-list" value="${esc(m.language)}" placeholder="한국어" autocomplete="off"></label>
            <label class="switch sm"><input type="checkbox" id="add-sung"${m.sung ? ' checked' : ''}><span>노래</span></label></div>`
        : `<div class="field"><label for="add-phrase">문구</label><input class="input" id="add-phrase" value="${esc(m.phrase)}" placeholder="${m.kind === 'music' ? `예: 음악이 멈춘다 · 잔잔한 ${esc(music)}` : '예: 쿵 · 달칵 - 문'}" autocomplete="off">
            <span class="field-hint">대괄호 [ ] 는 저절로 붙는다</span></div>
          ${m.kind === 'music' ? `<div class="mchips">${['음악이 멈춘다', music].map((p) => `<button type="button" class="mchip" data-act="add-phrase" data-val="${esc(p)}">${esc(bracket(p))}</button>`).join('')}</div>` : ''}`}
        <div class="add-pv"><span class="muted">자막에는</span><b id="add-preview">${esc(addPreview(m))}</b></div>
        <div class="mdl-actions"><button type="button" class="btn btn-primary btn-lg" data-act="add-save">${m.id ? '저장' : '넣기'}</button><button type="button" class="btn btn-lg" data-act="close">취소</button>
          ${m.id ? '<button type="button" class="btn btn-lg btn-danger" data-act="add-delete">지우기</button>' : ''}</div>
      </div></div>`;
  }
  async function saveAdd() {
    const m = S.modal;
    readAdd();
    if (!m.timeOk) { toast('시작과 끝을 03:52.40 처럼 적고, 끝은 시작보다 뒤로', 'error'); return; }
    const body = { start: Math.round(m.start * 1000) / 1000, end: Math.round(m.end * 1000) / 1000 };
    if (m.kind === 'line') {
      const text = String(m.text || '').trim();
      if (!text) { toast('대사 글을 적어 주세요', 'error'); const f = $('#add-text'); if (f) f.focus(); return; }
      Object.assign(body, { text, name: m.name || null, language: String(m.language || '').trim() || null, sung: m.sung, show_name: m.show });
    } else {
      const phrase = String(m.phrase || '').trim();
      if (!phrase) { toast('문구를 적어 주세요', 'error'); const f = $('#add-phrase'); if (f) f.focus(); return; }
      body.phrase = phrase;
    }
    const before = new Set(D.added.keys());
    const what = cut(addPreview(m), 30);
    const st = m.id ? await mutate(`api/added/${enc(m.id)}`, body, `고침 · ${what}`)
      : await mutate('api/added', { kind: m.kind, ...body }, `넣음 · ${what}`);
    if (!st) return;
    const id = m.id || [...D.added.keys()].find((k) => !before.has(k));
    const kind = m.kind;
    closeModal();
    if (id) pick(kind === 'line' ? 'lines' : 'items', id);
  }
  /** 지우기 asks once more on the same button (no dialog), then removes what the writer added. */
  async function armDelete(el, id) {
    if (!id || !D.added.has(id)) return;
    if (el.dataset.armed !== '1') {
      el.dataset.armed = '1';
      el.dataset.label = el.textContent;
      el.textContent = '한 번 더 누르면 지움';
      setTimeout(() => { if (document.body.contains(el)) { el.dataset.armed = ''; el.textContent = el.dataset.label; } }, 3000);
      return;
    }
    const a = D.added.get(id);
    setBusy(true);
    try {
      const st = await api('DELETE', `api/added/${enc(id)}`);
      if (S.modal && S.modal.type === 'add') closeModal();
      applyState(st);
      toast(`지움 · ${cut(a.text || a.phrase || id, 30)}`);
    } catch (e) {
      toast(e.message || String(e), 'error', 4500);
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------- 소리 세기 곡선 (env.npz, 9-x3)
  async function loadEnv() {
    if (S.envTried || !S.P || !S.P.envelope) return;      // no sed stage output: no curve, no request
    S.envTried = true;
    try {
      const step = Math.max(0.05, (S.P.t1 - S.P.t0) / 20000);
      const res = await fetch(`api/envelope?step=${step.toFixed(3)}`);
      if (!res.ok) return;                       // the sed stage has not run: no curve
      S.env = await res.json();
      buildEnv();
      const b = $('#env-toggle');
      b.hidden = false;
      b.classList.toggle('is-on', S.envOn);
      renderTimeline();
    } catch (_) { /* the timeline works without the curve */ }
  }
  /** One filled curve per track, in its own lane (speech under the cells, effects, music), drawn once
      in data units: the timeline only stretches it. */
  function buildEnv() {
    const lo = -70, hi = -10;
    $('#tl-env').innerHTML = Object.entries(S.env.tracks || {}).filter(([k, v]) => ENV_LANE[k] && v.length).map(([k, vals]) => {
      let d = 'M0 1';
      for (let i = 0; i < vals.length; i++) d += `L${i + 0.5} ${(1 - clamp((vals[i] - lo) / (hi - lo), 0, 1)).toFixed(3)}`;
      d += `L${vals.length} 1Z`;
      return `<svg class="env env-${k}" data-lane="${ENV_LANE[k]}" data-n="${vals.length}" viewBox="0 0 ${vals.length} 1" preserveAspectRatio="none" aria-hidden="true"><path d="${d}"/></svg>`;
    }).join('');
  }
  function placeEnv(TLG, pps) {
    const box = $('#tl-env');
    if (!box) return;
    box.hidden = !(S.env && S.envOn);
    if (box.hidden) return;
    for (const svg of box.children) {
      const lane = LANE[svg.dataset.lane];
      Object.assign(svg.style, {
        left: `${((num(S.env.t0) - S.P.t0) * pps).toFixed(1)}px`, width: `${(Number(svg.dataset.n) * num(S.env.step) * pps).toFixed(1)}px`,
        top: `${TLG.ruler + TLG.band + TLG.pad + lane * (TLG.lane + TLG.gap)}px`, height: `${TLG.lane}px`,
      });
    }
  }

  // ------------------------------------------------------------------- events
  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-act]');
    if (!el) {
      const row = ev.target.closest('.row[data-id]');
      if (row) pick(row.dataset.view || 'items', row.dataset.id);
      return;
    }
    if (el.disabled) return;
    const id = el.dataset.id;
    const ln = D.lines.get(S.line);
    switch (el.dataset.act) {
      case 'decide': decide(id, el.dataset.decision || null); break;
      case 'edit': openEdit(id); break;
      case 'name': ev.stopPropagation(); openEdit(id); break;
      case 'filter':
        if (!el.closest('#state-chips')) setView('items');           // the header pills filter the candidates
        setFilter(el.dataset.filter, !!el.dataset.toggle);
        break;
      case 'view': setView(el.dataset.view); break;
      case 'scene': S.sceneOpen.set(el.dataset.sid, !el.classList.contains('is-open')); renderList({ keep: true }); break;
      case 'more': showMore(); break;
      case 'toggle-minor': S.showMinor = !S.showMinor; renderList({ keep: true }); break;
      case 'toggle-suspect': S.showSuspect = !S.showSuspect; renderList({ keep: true }); break;
      case 'toggle-aside': S.asideOpen = !S.asideOpen; renderList({ keep: true }); break;
      case 'aside-put': asidePut(el.dataset.key, el.dataset.line); break;
      case 'aside-drop': armAsideDrop(el, el.dataset.key); break;
      case 'xp-aside':                       // 내보내기 전 검사 → the fold at the top of the 대사 view
        closeModal();
        S.asideOpen = true;
        setView('lines');
        renderList({ keep: true });
        break;
      case 'goto-line': if (S.modal) closeModal(); pick('lines', id); break;
      case 'goto-item': if (S.modal) closeModal(); pick('items', id); break;
      case 'goto-extra': pick('extras', id); break;
      case 'goto-person': pick('people', id, { seek: false }); break;
      case 'xp-view': closeModal(); setView(el.dataset.view); setFilter(el.dataset.filter); break;
      case 'frame': stepFrame(Number(el.dataset.d)); break;
      case 'env-toggle': S.envOn = !S.envOn; el.classList.toggle('is-on', S.envOn); renderTimeline(); break;
      case 'ed-now': {
        const box = el.closest('[data-box]');
        const inp = box && box.querySelector(`[data-f="${el.dataset.f}"]`);
        if (inp) inp.value = fmtTime2(S.t);
        if (S.modal && S.modal.type === 'add') syncAddPreview();
        break;
      }
      // 대사 줄
      case 'ln-play': { const sp = ln && (ln.start != null ? [ln.start, ln.end] : ln.guess); if (sp) playRange(video, sp[0] - 0.3, sp[1] + 0.3); break; }
      case 'ln-speaker': if (ln) openLineSpeaker(ln); break;
      case 'ln-include':
        if (ln) lineUpdate(ln, { include: el.dataset.v === '0' ? false : null }, el.dataset.v === '0' ? `뺌 · ${cut(ln.text, 28)}` : `다시 넣음 · ${cut(ln.text, 26)}`);
        break;
      case 'ln-from': {
        if (!ln) break;
        const f = el.dataset.from;
        S.ed = null;
        lineUpdate(ln, f ? { text_from: f } : { text_from: null, text: null }, f ? `${PICKED[f]} · ${cut(ln.text, 26)}` : `고르기 취소 · ${cut(ln.text, 26)}`);
        break;
      }
      case 'ln-edit-text': {
        S.ed = { id: S.line, mode: 'text' };
        renderDetail();
        const inp = $('#evidence [data-box="text"] [data-f="text"]');
        if (inp) { inp.focus(); inp.select(); }
        break;
      }
      case 'ln-text-cancel': S.ed = null; renderDetail(); break;
      case 'ln-text-save': {
        const inp = el.closest('[data-box]').querySelector('[data-f="text"]');
        const v = String(inp.value || '').replace(/\s+/g, ' ').trim();
        if (!ln) break;
        if (!v) { toast('글을 비울 수는 없습니다 · 줄을 빼려면 이 줄 빼기', 'error'); inp.focus(); break; }
        lineUpdate(ln, { text: v }, `글을 고침 · ${cut(v, 28)}`).then((st) => { if (st) { S.ed = null; renderDetail(); } });
        break;
      }
      case 'ln-time': { const sp = ln && readSpan(el); if (sp) lineUpdate(ln, { start: sp[0], end: sp[1] }, `시각을 정함 · ${fmtTime(sp[0])}–${fmtTime(sp[1])}`); break; }
      case 'ln-focus-time': {
        const inp = $('#evidence [data-box="time"] [data-f="start"]') || $('#evidence [data-box="revive"] [data-f="start"]');
        if (inp) { inp.focus(); inp.select(); }
        break;
      }
      case 'ln-unpin': if (ln) lineUpdate(ln, { start: null, end: null }, `시각 고정을 풂 · ${cut(ln.text, 26)}`); break;
      case 'ln-revive': { const sp = ln && readSpan(el); if (sp) lineUpdate(ln, { include: true, start: sp[0], end: sp[1] }, `되살림 · ${fmtTime(sp[0])} ${cut(ln.text, 24)}`); break; }
      case 'ln-revive-now': {
        if (!ln) break;
        const s = Math.round(S.t * 1000) / 1000, e = Math.round(Math.min(S.P.t1, S.t + lineDur(ln.text)) * 1000) / 1000;
        lineUpdate(ln, { include: true, start: s, end: e }, `지금 위치에 되살림 · ${fmtTime(s)} ${cut(ln.text, 24)}`);
        break;
      }
      case 'ln-unrevive': if (ln) lineUpdate(ln, { include: null, start: null, end: null }, `다시 뺌 · ${cut(ln.text, 26)}`); break;
      case 'ln-gap': {
        if (!ln) break;
        const k = Number(el.dataset.k);
        // the next click before the answer builds on the cuts still on their way (pendOf), not on
        // the state of an earlier answer; the line itself keeps what the server sent
        const set = new Set(pendOf(ln, 'breaks') || []);
        if (set.has(k)) set.delete(k); else set.add(k);
        lineUpdate(ln, { breaks: [...set].sort((a, b) => a - b) }, set.has(k) ? `${k}번째 어절 뒤에서 끊음` : `${k}번째 어절 뒤 끊기를 지움`);
        renderDetail();
        break;
      }
      case 'ln-breaks-clear': if (ln) { lineUpdate(ln, { breaks: [] }, '끊을 자리를 모두 지움'); renderDetail(); } break;
      case 'ln-mark-reset': if (ln) lineUpdate(ln, { language: null, sung: null }, '말 표시를 대본대로'); break;
      // 대본에 없는 말
      case 'x-play': { const x = D.extraById.get(id); if (x) playRange(video, x.start - 0.3, x.end + 0.3); break; }
      case 'x-ignore': extraAct(id, 'ignore'); break;
      case 'x-clear': extraAct(id, 'clear'); break;
      case 'x-line': extraToLine(id, el); break;
      case 'x-use-time': useExtraTime(id, el.dataset.line); break;
      // 인물
      case 'pp-mode':
        peopleSet(el.dataset.mode === 'generic' ? { mode: 'generic', before: el.dataset.before }
          : el.dataset.mode ? { mode: el.dataset.mode } : { mode: null, reveal: 'auto' });      // 정하지 않음: back to the default
        break;
      case 'pp-custom': {
        const inp = el.closest('[data-box]').querySelector('[data-f="before"]');
        const v = String(inp.value || '').trim().replace(/^\[\s*|\s*\]$/g, '');
        if (!v) { toast('쓸 표시를 적어 주세요 (예: 경찰)', 'error'); inp.focus(); break; }
        peopleSet({ mode: 'generic', before: v });
        break;
      }
      case 'pp-reveal': peopleSet({ reveal: el.dataset.reveal }); break;
      // 칸
      case 'cell-play': { const c = D.cellById.get(S.cell); if (c) playRange(video, c.start, c.end); break; }
      case 'cell-step': { const i = D.cells.findIndex((c) => c.id === S.cell); const c = D.cells[i + Number(el.dataset.d)]; if (c) pick('cells', c.id); break; }
      // 새로 넣기
      case 'open-add': openAdd('line'); break;
      case 'add-phrase': { const inp = $('#add-phrase'); if (inp) { inp.value = el.dataset.val; syncAddPreview(); } break; }
      case 'add-save': saveAdd(); break;
      case 'add-delete': armDelete(el, S.modal && S.modal.id); break;
      case 'added-edit': { const a = D.added.get(id); if (a) openAdd(a.kind, a); break; }
      case 'added-delete': armDelete(el, id); break;
      case 'item-move': {
        const inp = el.closest('[data-box]').querySelector('[data-f="t"]');
        const t = parseTime(inp && inp.value);
        if (!Number.isFinite(t)) { toast('시각을 03:52.40 처럼 적어 주세요', 'error'); if (inp) inp.focus(); break; }
        const it = D.byId.get(id), w = it && it.window;
        if (w && (t < w[0] - 0.5 || t > w[1] + 0.5)) toast('대본이 말한 범위 밖으로 옮깁니다', 'error', 3200);
        placement(id, 'move', t);
        break;
      }
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
      case 'xp-goto': closeModal(); setView('items'); setFilter(el.dataset.filter); break;
      case 'goto-cell': if (el.closest('#modal-box')) setView('cells'); gotoCell(el.dataset.cell); break;
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
    if (row && (row.dataset.view || 'items') === 'items') openEdit(row.dataset.id);
    else if (row && row.dataset.view === 'lines') { const ln = D.lines.get(row.dataset.id); if (ln) openLineSpeaker(ln); }
  });
  // the left panel's switches, the language field and the reveal line (they store as soon as they change)
  $('#evidence').addEventListener('change', (ev) => {
    const t = ev.target, act = t.dataset && t.dataset.change;
    if (!act) return;
    const ln = D.lines.get(S.line);
    if (act === 'ln-keep' && ln) lineUpdate(ln, { keep: t.checked || null }, t.checked ? '한 칸에 둠' : '한 칸에 두기를 풂');
    else if (act === 'ln-sung' && ln) lineUpdate(ln, { sung: t.checked }, t.checked ? '노래로 표시 (♪ … ♪)' : '노래 표시를 뺌');
    else if (act === 'ln-lang' && ln) {
      const v = t.value.trim().replace(/^\[\s*|\s*\]$/g, '');
      t._sent = t.value;               // sent: drawing the panel again shows what the server keeps
      if (v !== (pendOf(ln, 'language') || '')) lineUpdate(ln, { language: v }, v ? `${bracket(v)} 표시` : '한국어 (언어 표시 없음)');
    } else if (act === 'ln-source' && ln) {
      const v = t.value === '' ? null : t.value === 'none' ? '' : t.value;
      lineUpdate(ln, { source: v }, v == null ? '출처를 대본대로' : v === '' ? '출처 없음' : `출처 · ${SOURCE_KO[v] || v}`);
    } else if (act === 'pp-reveal-line' && t.value) peopleSet({ reveal: 'line', reveal_line: t.value });
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
    if (m.type === 'add') {
      if (t.name === 'add-kind') { readAdd(); m.kind = t.value; renderAdd(); } else syncAddPreview();
      return;
    }
    if (t.name === 'm-opt') { m.choice = t.value; if (m.type === 'sfx') syncSfx(); else syncMusic(false); }
    else if (t.name === 'm-who') { m.who = t.value; syncSpeaker(); if (t.value === 'new') $('#f-newname').focus(); }
    else if (t.id === 'f-show') { m.show = t.checked; syncSpeaker(); }
    else if (t.name === 'm-scope') { m.scope = t.value; }
  });
  $('#modal-box').addEventListener('input', (ev) => {
    const m = S.modal;
    if (!m) return;
    const t = ev.target;
    if (m.type === 'add') { syncAddPreview(); return; }
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
    const enter = ev.code === 'Enter' || ev.code === 'NumpadEnter';
    if (inInput && enter && tgt.tagName !== 'SELECT') {
      // Enter in a field of a box runs the box's main button (옮기기, 시각 정하기, 저장, 대사로 넣기 ...)
      const box = tgt.closest('.nr-card, [data-box]');
      let go = box && (box.querySelector('[data-primary]') || box.querySelector('[data-action="move"]'));
      if (!go && box && box.parentElement) { const up = box.parentElement.closest('[data-box]'); go = up && up.querySelector('[data-primary]'); }
      if (go) { ev.preventDefault(); go.click(); return; }
    }
    if (S.modal) {
      if (ev.code === 'Escape') { ev.preventDefault(); closeModal(); }
      else if (enter && inInput && tgt.type === 'text' && S.modal.type !== 'export') { ev.preventDefault(); confirmModal(); }
      return;
    }
    if (inInput && ev.code === 'Escape' && S.ed) { ev.preventDefault(); S.ed = null; renderDetail(); return; }
    if (inInput || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const onButton = !!(tgt.closest && tgt.closest('button, a[href]'));
    const ln = S.view === 'lines' ? D.lines.get(S.line) : null;
    switch (ev.code) {
      case 'Space': if (onButton) return; ev.preventDefault(); togglePlay(); break;
      case 'ArrowUp': ev.preventDefault(); moveSel(-1); break;
      case 'ArrowDown': ev.preventDefault(); moveSel(1); break;
      case 'ArrowLeft': case 'Comma': ev.preventDefault(); stepFrame(-1, ev.shiftKey && ev.code === 'ArrowLeft'); break;
      case 'ArrowRight': case 'Period': ev.preventDefault(); stepFrame(1, ev.shiftKey && ev.code === 'ArrowRight'); break;
      case 'Digit1': case 'Digit2': case 'Digit3': case 'Digit4': case 'Digit5':
        ev.preventDefault(); setView(VIEWS[Number(ev.code.slice(5)) - 1][0]); break;
      case 'KeyN': ev.preventDefault(); openAdd('line'); break;
      case 'Enter': case 'NumpadEnter': if (onButton) return; ev.preventDefault(); if (S.view === 'items') acceptRec(); break;
      case 'KeyI':
        ev.preventDefault();
        if (S.view === 'items' && S.sel) decide(S.sel, 'include');
        else if (ln && !ln.include && ln.excluded === 'writer') lineUpdate(ln, { include: null }, `다시 넣음 · ${cut(ln.text, 26)}`);
        else if (ln && !ln.include) toast('시각이 없는 줄입니다 · 왼쪽에서 시작·끝을 정해 되살린다', 'error');
        break;
      case 'KeyE':
        ev.preventDefault();
        if (S.view === 'items' && S.sel) decide(S.sel, 'exclude');
        else if (ln && ln.include && !D.added.has(ln.id)) lineUpdate(ln, { include: false }, `뺌 · ${cut(ln.text, 28)}`);
        else if (S.view === 'extras' && S.extra) { const x = D.extraById.get(S.extra); if (x && x.state === 'open') extraAct(x.id, 'ignore'); }
        break;
      default: break;
    }
  });

  // timeline: click a block to select it, click or drag elsewhere to seek
  const tlScroll = $('#tl-scroll');
  const tlTime = (ev) => S.P.t0 + (ev.clientX - $('#tl-inner').getBoundingClientRect().left) / S.pps;
  tlScroll.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || !S.P || ev.target === tlScroll) return;     // the scrollbar itself
    const blk = ev.target.closest('.blk');
    if (blk && blk.dataset.id) {
      const it = D.byId.get(blk.dataset.id);
      // in the dialogue view a speaker block opens its line; anything else opens the candidate
      if (S.view === 'lines' && it && it.kind === 'speaker' && it.speaker && D.lines.has(it.speaker.line_id)) pick('lines', it.speaker.line_id);
      else select(blk.dataset.id);
      return;
    }
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
      document.body.insertAdjacentHTML('beforeend', `<datalist id="lang-list">${LANGS.map((l) => `<option value="${l}"></option>`).join('')}</datalist>`);
      applyState(st);
      layoutStage();
      renderTimeline();
      loadEnv();                    // applyState has given the video its file (syncMedia)
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
