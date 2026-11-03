/* CBoE 시작 화면: 작업 목록 · 새 작업 · 분석 진행.
   The studio server (cboe/server/studio_app.py) runs `python -m cboe run` for one project at
   a time; this page polls /api/job while it runs. A project's review screen is /p/<id>/. */
'use strict';
(() => {
  // ------------------------------------------------------------------ helpers
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (x, d = 0) => (typeof x === 'number' && Number.isFinite(x) ? x : d);
  const pct = (a, b) => (b > 0 ? Math.round((100 * a) / b) : 0);
  const dirOf = (p) => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const unquote = (s) => String(s ?? '').trim().replace(/^"(.*)"$/, '$1').trim();

  const S = {
    data: null, sel: null, modal: null, form: null, poll: 0, busy: 0, logOpen: false, lastFocus: null,
    confirm: null,
  };

  // per-viewer conveniences only (the project files are the real state)
  function remember(k, v) { try { localStorage.setItem(`cboe.${k}`, JSON.stringify(v)); } catch (_) { /* storage off */ } }
  function recall(k, d) {
    try { const v = localStorage.getItem(`cboe.${k}`); return v == null ? d : JSON.parse(v); } catch (_) { return d; }
  }

  async function api(method, url, body) {
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
  }
  let busyTimer = 0;
  function setBusy(on) {
    S.busy = Math.max(0, S.busy + (on ? 1 : -1));
    clearTimeout(busyTimer);
    if (S.busy) busyTimer = setTimeout(() => $('#home').classList.add('is-busy'), 150);
    else $('#home').classList.remove('is-busy');
  }
  function toast(msg, kind = 'ok', ms = 2600) {
    const el = document.createElement('div');
    el.className = `toast toast-${kind}`;
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    requestAnimationFrame(() => el.classList.add('in'));
    setTimeout(() => { el.classList.remove('in'); setTimeout(() => el.remove(), 260); }, ms);
    while ($('#toasts').children.length > 2) $('#toasts').firstElementChild.remove();
  }
  function banner(msg) { const b = $('#banner'); b.hidden = !msg; b.textContent = msg || ''; }

  // ------------------------------------------------------------------- format
  function fmtDur(sec) {
    const t = Math.max(0, Math.round(num(sec)));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    if (h) return m ? `${h}시간 ${m}분` : `${h}시간`;
    if (m) return s ? `${m}분 ${s}초` : `${m}분`;
    return `${s}초`;
  }
  /** 03:52.4 / 1:03:52 (the review screen writes times the same way). */
  function fmtClock(t) {
    if (t == null || !Number.isFinite(t)) return '';
    const d = Math.round(t * 10) / 10;
    const h = Math.floor(d / 3600), m = Math.floor((d % 3600) / 60);
    const s = Math.round((d - h * 3600 - m * 60) * 10) / 10;
    const ss = Number.isInteger(s) ? String(s).padStart(2, '0') : s.toFixed(1).padStart(4, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${String(m).padStart(2, '0')}:${ss}`;
  }
  /** '' -> null, "12:34" / "1:02:03" / "754.5" -> seconds, anything else -> NaN. */
  function parseClock(v) {
    const s = String(v ?? '').trim();
    if (!s) return null;
    if (!/^(\d+:){0,2}\d+(\.\d+)?$/.test(s)) return NaN;
    return s.split(':').map(Number).reduce((a, x) => a * 60 + x, 0);
  }
  function fmtWhen(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const p2 = (x) => String(x).padStart(2, '0');
    const hm = `${p2(d.getHours())}:${p2(d.getMinutes())}`;
    return d.toDateString() === new Date().toDateString() ? `오늘 ${hm}` : `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${hm}`;
  }

  // -------------------------------------------------------------------- model
  const STATUS = {
    new: ['분석 전', 'st-exc'], partial: ['일부만 분석', 'st-check'], running: ['분석 중', 'st-run'],
    analysed: ['검수 가능', 'st-inc'], failed: ['분석 실패', 'st-noroom'], cancelled: ['분석 멈춤', 'st-check'],
    broken: ['읽을 수 없음', 'st-noroom'], external: ['다른 곳에서 분석 중', 'st-run'],
  };
  // deck 5.1 전체 처리 흐름
  const GROUPS = [
    { title: '① 후보 검출 · 근거 수집', stages: ['ingest', 'separate', 'cuts', 'sed', 'asr', 'align', 'sfx', 'music', 'speaker', 'screen', 'dialog_ev'] },
    { title: '② 추천 · 에이전트 재확인', stages: ['recommend', 'agent'] },
    { title: '③ 초안 배치', stages: ['placement'] },
  ];
  const projects = () => (S.data ? S.data.projects : []);
  const byId = (id) => projects().find((p) => p.id === id) || null;
  const current = () => byId(S.sel);
  const job = () => (S.data ? S.data.job : null);
  const running = () => { const j = job(); return !!(j && j.state === 'running'); };
  const jobOf = (p) => { const j = job(); return j && p && j.project === p.id ? j : null; };
  const llmReady = () => { const e = (S.data && S.data.env) || {}; return !!(e.llm && e.llm.logged_in); };
  const spanOf = (p) => Math.max(0, num(p.t1, num(p.duration)) - num(p.t0));

  // ------------------------------------------------------------------- render
  function render() {
    renderEnv();
    renderList();
    renderDetail();
  }

  function shortGpu(s) {
    const [name, mem] = String(s).split(',').map((x) => x.trim());
    const gb = mem ? Math.round(parseFloat(mem) / 1024) : 0;
    const short = name.replace(/^NVIDIA\s+/, '').replace(/^GeForce\s+/, '').replace(/^Intel\(R\)\s+/, '')
      .replace(/\(TM\)/g, '').replace(/\s*\(\d+GB\)/, '').replace(/\s+GPU$/, '');
    return `${short}${gb ? ` · ${gb}GB` : ''}`;
  }
  function renderEnv() {
    const e = S.data.env || {};
    const chip = (cls, text, title) => `<button type="button" class="echip ${cls}" data-act="env" title="${esc(title || '')}"><i></i>${esc(text)}</button>`;
    const out = [];
    out.push(e.gpu ? chip('ok', shortGpu(e.gpu), e.gpu) : chip('warn', 'GPU 없음 · CPU', 'NVIDIA(CUDA) · 인텔(XPU) GPU 를 찾지 못해 CPU 로 분석합니다(많이 느림)'));
    if (!e.ffmpeg) out.push(chip('bad', 'ffmpeg 없음', 'ffmpeg · ffprobe 를 PATH 에 두세요'));
    const l = e.llm || {};
    out.push(l.logged_in ? chip('ok', 'Claude 연결됨', '화면 근거 · 대사 근거 · 재확인 에이전트에 씀')
      : chip('warn', l.cli ? 'Claude 로그인 필요' : 'Claude 없음', 'LLM 없이 규칙으로만 분석합니다'));
    const miss = e.models_missing || [];
    out.push(miss.length ? chip('bad', `모델 파일 ${miss.length}개 없음`, miss.join(', ')) : chip('ok', '모델 파일 모두 있음', ''));
    $('#env').innerHTML = out.join('');
  }

  const FILM = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.5v5l4-2.5z"/></svg>';
  function barHtml(p, j) {
    if (j && j.state === 'running') {
      const cur = j.current;
      const n = pct(num(j.fraction), 1);
      return `<span class="prog-bar"><i style="width:${n}%"></i></span><span class="tnum">${n}%</span>${cur ? `<span class="pnote">${esc(cur.label)}${cur.total ? ` ${cur.done}/${cur.total}` : ''}</span>` : ''}`;
    }
    if (p.total) {
      return `<span class="prog-bar"><i style="width:${pct(p.decided, p.total)}%"></i></span><span class="tnum">작가 결정 ${p.decided} / ${p.total}</span>${(p.exports || []).length ? '<span class="pdone">· 내보냄</span>' : ''}`;
    }
    if (p.status === 'partial') return `<span class="pnote">${(p.stages_done || []).length} / 14 단계 끝남</span>`;
    return '';
  }
  function statusOf(p, j) { return j && j.state === 'running' ? 'running' : p.status; }
  function rowHtml(p) {
    const j = jobOf(p);
    const [label, cls] = STATUS[statusOf(p, j)] || ['?', 'st-exc'];
    const sel = p.id === S.sel;
    const sub = p.status === 'broken' ? esc(p.error || '')
      : `${esc(p.video_name || '영상 없음')} · ${fmtDur(spanOf(p))} · ${p.script ? '대본 있음' : '대본 없음'}`;
    const img = p.video_ok ? `<img loading="lazy" src="/api/projects/${encodeURIComponent(p.id)}/thumb" alt="">` : '';
    return `<div class="prow${sel ? ' is-sel' : ''}" role="option" aria-selected="${sel}" tabindex="${sel ? 0 : -1}" data-id="${esc(p.id)}">
      <div class="thumb">${FILM}${img}</div>
      <div class="pmain">
        <div class="ptitle"><span class="t">${esc(p.title)}</span>${p.sample ? '<span class="tag-sample">예시 데이터</span>' : ''}</div>
        <div class="psub">${sub}</div>
        <div class="pbar">${barHtml(p, j)}</div>
      </div>
      <div class="pside"><span class="pill ${cls}">${label}</span><span class="when tnum">${fmtWhen(p.modified)}</span></div>
    </div>`;
  }
  function renderList() {
    const ps = projects();
    $('#plist-count').textContent = ps.length ? `${ps.length}개` : '';
    $('#ws-path').textContent = S.data.workspace;
    if (!ps.length) {
      $('#plist').innerHTML = `<div class="p-empty"><b>아직 작업이 없습니다</b>
        <span>영상과 대본을 고르면 소리 후보를 찾고 근거를 모아 추천까지 해 둡니다</span>
        <button type="button" class="btn btn-primary btn-lg" data-act="new">새 작업 만들기</button></div>`;
      return;
    }
    $('#plist').innerHTML = ps.map(rowHtml).join('');
    for (const img of $$('#plist img')) img.addEventListener('error', () => img.remove(), { once: true });
  }
  /** While a run goes on only its row's bar and pill change (no image reloads). */
  function updateRunRow() {
    const j = job();
    if (!j) return;
    const p = byId(j.project);
    const row = p && $(`#plist .prow[data-id="${CSS.escape(p.id)}"]`);
    if (!row) return;
    row.querySelector('.pbar').innerHTML = barHtml(p, j);
    const [label, cls] = STATUS[statusOf(p, j)] || ['?', 'st-exc'];
    const pill = row.querySelector('.pill');
    pill.className = `pill ${cls}`;
    pill.textContent = label;
  }

  function introHtml() {
    return `<div class="intro"><h2>이렇게 씁니다</h2><ol>
      <li><span class="n">1</span><div><b>새 작업</b><span class="muted">영상과 대본을 고른다 · 대본은 한 줄에 <code>이름: 대사</code> (txt · docx · srt)</span></div></li>
      <li><span class="n">2</span><div><b>분석</b><span class="muted">음원 분리 · 후보 검출 · 화면 · 화자 · 대사 근거 · 추천 · 에이전트 재확인 · 초안 배치</span></div></li>
      <li><span class="n">3</span><div><b>검수</b><span class="muted">근거를 보고 포함 · 제외를 정하고 문구 · 화자 · 자리를 고친다</span></div></li>
      <li><span class="n">4</span><div><b>내보내기</b><span class="muted">가독성 검사 뒤 SRT · VTT 로 저장</span></div></li>
    </ol><div><button type="button" class="btn btn-primary btn-lg" data-act="new">새 작업 만들기</button></div></div>`;
  }
  function statsHtml(p) {
    if (!p.counts) return '';
    const c = p.counts, r = p.recs || {};
    const k = (n, label, cls) => `<div class="stat"><span class="badge ${cls}">${label}</span><b class="tnum">${num(n)}</b></div>`;
    return `<div class="stats">${k(c.sfx, '효과음', 'k-sfx')}${k(c.music, '음악', 'k-music')}${k(c.speaker, '화자', 'k-speaker')}</div>
      <div class="recs"><span class="pill st-inc">포함 ${num(r.include)}</span><span class="pill st-exc">제외 ${num(r.exclude)}</span>
        <span class="pill st-check">확인 필요 ${num(r.check)}</span>
        <span class="dec tnum">작가 결정 <b>${num(p.decided)}</b> / ${num(p.total)}<span class="prog-bar"><i style="width:${pct(p.decided, p.total)}%"></i></span></span></div>`;
  }
  function exportsHtml(p) {
    const xs = p.exports || [];
    if (!xs.length) return '';
    return `<div class="exports"><div class="sec-title">내보낸 자막</div>${xs.map((e) => `<div class="xrow">
      <span class="xname">${esc(e.name)}</span><span class="muted tnum">${fmtWhen(e.mtime)}</span>
      <button type="button" class="btn btn-sm" data-act="open-path" data-path="${esc(dirOf(e.path))}">폴더 열기</button></div>`).join('')}</div>`;
  }
  function actionsHtml(p, j) {
    const isRun = !!(j && j.state === 'running');
    const out = [];
    if (p.review_url && !isRun && p.status !== 'external') out.push(`<a class="btn btn-primary btn-lg" href="${esc(p.review_url)}" data-act="review">검수 열기</a>`);
    if (isRun) out.push('<button type="button" class="btn btn-danger" data-act="cancel">분석 멈추기</button>');
    else if (p.status === 'external') out.push('<span class="muted">다른 창이나 명령줄에서 분석하는 중 · 끝나면 여기서 이어서 할 수 있다</span>');
    else {
      const other = running() ? ' disabled title="다른 작업을 분석하는 중입니다"' : '';
      const first = p.status === 'new';
      const label = first ? '분석 시작' : p.status === 'analysed' ? '다시 분석…' : '이어서 분석…';
      out.push(`<button type="button" class="btn${p.review_url ? '' : ' btn-primary btn-lg'}" data-act="run"${other}>${label}</button>`);
    }
    out.push('<span class="spacer"></span>');
    out.push(`<button type="button" class="btn btn-text btn-sm" data-act="open-path" data-path="${esc(p.work)}">작업 폴더</button>`);
    if (!isRun && p.status !== 'external') out.push('<button type="button" class="btn btn-text btn-sm btn-danger-text" data-act="delete">지우기</button>');
    return out.join('');
  }
  function renderDetail() {
    const box = $('#detail');
    const p = current();
    if (!p) { box.innerHTML = introHtml(); return; }
    if (p.status === 'broken') {
      box.innerHTML = `<div class="d-head"><h2>${esc(p.title)}</h2><span class="pill st-noroom">읽을 수 없음</span></div>
        <div class="ferr">${esc(p.error || '')}</div>`;
      return;
    }
    const j = jobOf(p);
    const [label, cls] = STATUS[statusOf(p, j)] || ['?', 'st-exc'];
    box.innerHTML = `<div class="d-head"><h2 title="${esc(p.title)}">${esc(p.title)}</h2><span class="pill ${cls}">${label}</span>
        ${p.sample ? '<span class="tag-sample">예시 데이터</span>' : ''}</div>
      <dl class="facts">
        <dt>영상</dt><dd>${p.video_ok ? '' : '<b class="bad">파일 없음 · </b>'}${esc(p.video || '')}</dd>
        <dt>대본</dt><dd>${p.script ? esc(p.script) : '<span class="muted">없음 · 받아쓰기로 대사를 만든다</span>'}</dd>
        <dt>처리 구간</dt><dd class="tnum">${fmtClock(num(p.t0))} ~ ${fmtClock(num(p.t1, num(p.duration)))} <span class="muted">· ${fmtDur(spanOf(p))}</span></dd>
      </dl>
      ${statsHtml(p)}
      <div class="actions">${actionsHtml(p, j)}</div>
      ${exportsHtml(p)}
      <div class="jobsec" id="jobsec">${jobHtml(p, j)}</div>`;
    scrollLog();
  }
  function renderJobOnly() {
    const p = current();
    const sec = $('#jobsec');
    if (p && sec && jobOf(p)) { sec.innerHTML = jobHtml(p, jobOf(p)); scrollLog(); }
    updateRunRow();
  }
  function scrollLog() {
    const pre = $('#jobsec .logbox pre');
    if (pre) pre.scrollTop = pre.scrollHeight;
  }

  /** name -> {label, state, seconds, note} from the running job, or from files and the last run. */
  function stageStates(p, j) {
    const m = new Map();
    if (j) { for (const s of j.stages) m.set(s.name, s); return m; }
    const labels = S.data.stage_labels || {};
    const last = new Map(((p.last_run && p.last_run.stages) || []).map((s) => [s.name, s]));
    const done = new Set(p.stages_done || []);
    for (const name of Object.keys(labels)) {
      const l = last.get(name);
      if (done.has(name)) {
        const ok = l && (l.state === 'done' || l.state === 'skipped');
        m.set(name, { name, label: labels[name], state: 'done', seconds: ok && l.state === 'done' ? l.seconds : null, note: ok && l.state === 'done' ? l.note : '' });
      } else {
        m.set(name, { name, label: labels[name], state: l && (l.state === 'failed' || l.state === 'cancelled') ? l.state : 'pending', seconds: null, note: '' });
      }
    }
    return m;
  }
  function stageRow(s, cur) {
    const icon = { done: '✓', skipped: '↷', failed: '✕', cancelled: '■' }[s.state] || '';
    let note = s.state === 'skipped' ? '이전 결과 사용' : esc(s.note || '');
    let bar = '';
    if (cur) {
      if (cur.total) {
        bar = `<span class="mini"><i style="width:${pct(cur.done, cur.total)}%"></i></span>`;
        note = `${cur.done} / ${cur.total}${cur.note ? ` · ${esc(cur.note)}` : ''}`;
      } else note = '진행 중';
    }
    const sec = s.state === 'done' && num(s.seconds) >= 0.5 ? fmtDur(s.seconds) : '';
    return `<div class="srow is-${esc(s.state)}"><span class="ic" aria-hidden="true">${icon}</span><span class="nm">${esc(s.label)}</span>
      <span class="note">${bar}<span>${note}</span></span><span class="sec tnum">${sec}</span></div>`;
  }
  function jobHtml(p, j) {
    const states = stageStates(p, j);
    const isRun = !!(j && j.state === 'running');
    let head = '';
    if (j) {
      const n = pct(num(j.fraction), 1);
      const doneN = j.stages.filter((s) => s.state === 'done' || s.state === 'skipped').length;
      const label = { running: '분석 중', done: '분석 끝', failed: '분석 실패', cancelled: '분석 멈춤' }[j.state] || j.state;
      head = `<div class="jobhead ${esc(j.state)}">
        <div class="jl"><b>${label}</b><span class="muted tnum">${doneN} / ${j.stages.length} 단계 · ${fmtDur(j.elapsed)}${isRun ? ' 지남' : ' 걸림'}</span>
          <span class="spacer"></span>${j.options && !j.options.llm ? '<span class="pill st-exc">LLM 없이</span>' : ''}<span class="tnum">${n}%</span></div>
        <div class="bigbar"><i style="width:${n}%"></i></div>
        ${j.state === 'failed' && j.error ? `<pre class="errbox">${esc(j.error)}</pre>` : ''}
        ${j.state === 'done' && p.review_url ? '<div><a class="btn btn-primary" href="' + esc(p.review_url) + '" data-act="review">검수 시작</a></div>' : ''}
      </div>`;
    } else if (p.last_run) {
      const l = p.last_run;
      const label = { done: '지난 분석 끝남', failed: '지난 분석 실패', cancelled: '지난 분석 멈춤' }[l.state] || '';
      head = label ? `<div class="jobhead ${esc(l.state)}"><div class="jl"><b>${label}</b>
          <span class="muted tnum">${fmtWhen(l.finished)} · ${fmtDur(l.seconds)} 걸림</span></div>
        ${l.state === 'failed' && l.error ? `<pre class="errbox">${esc(l.error)}</pre>` : ''}</div>` : '';
    }
    const agent = p.agent && !isRun ? `<span class="muted">에이전트 재확인 ${p.agent.done}개${p.agent.left ? ` · 한도 밖 ${p.agent.left}개` : ''}</span>` : '';
    const groups = GROUPS.map((g) => `<div class="sgroup"><h4>${g.title}</h4>${g.stages.map((n) => stageRow(
      states.get(n) || { name: n, label: n, state: 'pending' }, isRun && j.current && j.current.stage === n ? j.current : null)).join('')}</div>`).join('');
    const tail = (j && j.log_tail) || [];
    const log = tail.length ? `<details class="logbox"${S.logOpen ? ' open' : ''}><summary>실행 기록 (마지막 ${tail.length}줄)</summary><pre>${esc(tail.join('\n'))}</pre></details>` : '';
    return `<div class="sec-title">분석 과정 ${agent}</div>${head}<div class="stages">${groups}</div>${log}`;
  }

  // -------------------------------------------------------------------- modal
  function showModal() {
    if ($('#modal').hidden) S.lastFocus = document.activeElement;
    $('#modal').hidden = false;
    $('#home').inert = true;
  }
  function closeModal() {
    if (S.confirm) { const c = S.confirm; S.confirm = null; c(false); }
    S.modal = null;
    S.form = null;
    $('#home').inert = false;
    $('#modal').hidden = true;
    $('#modal-box').innerHTML = '';
    if (S.lastFocus && S.lastFocus.focus && document.body.contains(S.lastFocus)) S.lastFocus.focus({ preventScroll: true });
  }
  /** Small yes/no dialog; resolves true on the ok button. */
  function askConfirm(title, body, ok, danger = false) {
    return new Promise((resolve) => {
      S.modal = 'confirm';
      S.confirm = resolve;
      $('#modal-box').innerHTML = `<div class="form form-sm">
        <div class="mdl-head"><h3 id="modal-title">${esc(title)}</h3></div>
        <div class="confirm-body">${body}</div>
        <div class="form-foot"><button type="button" class="btn" data-act="close">그만두기</button>
          <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="confirm-ok">${esc(ok)}</button></div></div>`;
      showModal();
      $('#modal-box [data-act="confirm-ok"]').focus();
    });
  }
  function confirmOk() {
    const c = S.confirm;
    S.confirm = null;
    closeModal();
    if (c) c(true);
  }

  // analysis options shared by '새 작업' and '분석'
  function defaults() {
    const o = recall('opts', {}) || {};
    return {
      llm: llmReady() && o.llm !== false,
      agent_limit: Number.isFinite(o.agent_limit) ? o.agent_limit : 40,
      jobs: Number.isFinite(o.jobs) ? o.jobs : 3,
      model: ['sonnet', 'haiku', 'opus'].includes(o.model) ? o.model : 'sonnet',
    };
  }
  function optsHtml(o) {
    const ready = llmReady();
    return `<div class="opts">
      <label class="check"><input type="checkbox" id="o-llm"${o.llm ? ' checked' : ''}${ready ? '' : ' disabled'}>
        <span><b>LLM 근거와 재확인 에이전트</b><span class="muted">${ready
    ? ' · 화면 근거 · 대사 근거 · 애매한 후보 다시 보기 (Claude)'
    : ' · Claude 로그인이 없어 규칙으로만 분석 (나중에 로그인하고 이어서 분석하면 채워짐)'}</span></span></label>
      <div class="opt-grid${o.llm ? '' : ' is-off'}" id="o-grid">
        <label>에이전트로 다시 볼 후보 <input type="number" id="o-agent" class="text-input num-input" min="0" max="999" step="10" value="${o.agent_limit}">개까지</label>
        <label>동시 호출 <input type="number" id="o-jobs" class="text-input num-input" min="1" max="8" value="${o.jobs}"></label>
        <span class="seg" role="group" aria-label="모델">${['sonnet', 'haiku', 'opus'].map((m) => `<button type="button" data-act="model" data-model="${m}" class="${o.model === m ? 'is-on' : ''}">${m}</button>`).join('')}</span>
      </div></div>`;
  }
  function readOpts(f) {
    const llm = $('#o-llm');
    if (llm) f.llm = llm.checked && !llm.disabled;
    const a = parseInt($('#o-agent') ? $('#o-agent').value : f.agent_limit, 10);
    const j = parseInt($('#o-jobs') ? $('#o-jobs').value : f.jobs, 10);
    f.agent_limit = Number.isFinite(a) ? Math.min(Math.max(a, 0), 999) : 40;
    f.jobs = Number.isFinite(j) ? Math.min(Math.max(j, 1), 8) : 3;
    return { llm: f.llm, agent_limit: f.agent_limit, jobs: f.jobs, model: f.model };
  }

  // ------------------------------------------------------------- new project
  function openNew() {
    S.form = { kind: 'new', video: '', script: '', title: '', titleAuto: true, start: '', end: '', endAuto: false, vinfo: null, sinfo: null,
      vseq: 0, sseq: 0, err: '', sending: false, ...defaults() };
    S.modal = 'new';
    const f = S.form;
    $('#modal-box').innerHTML = `<form class="form" id="new-form" novalidate autocomplete="off">
      <div class="mdl-head"><span class="logo" aria-hidden="true">SDH</span><h3 id="modal-title">새 작업</h3><span class="spacer"></span>
        <button type="button" class="icon-btn" data-act="close" aria-label="닫기">×</button></div>
      <div class="field"><label for="f-video">영상 파일 <span class="req">*</span></label>
        <div class="pickrow"><input id="f-video" class="text-input path" placeholder="고르기를 누르거나 경로를 붙여 넣기" spellcheck="false">
          <button type="button" class="btn" data-act="pick" data-kind="video">고르기</button></div>
        <div class="finfo" id="f-vinfo"></div></div>
      <div class="field"><label for="f-script">대본 파일 <span class="muted">(권장)</span></label>
        <div class="pickrow"><input id="f-script" class="text-input path" placeholder="없으면 받아쓰기로 대사를 만든다" spellcheck="false">
          <button type="button" class="btn" data-act="pick" data-kind="script">고르기</button>
          <button type="button" class="btn btn-text" data-act="clear-script" id="f-script-clear" hidden>빼기</button></div>
        <div class="finfo" id="f-sinfo"></div><div id="f-preview"></div></div>
      <div class="row2">
        <div class="field"><label for="f-title">제목</label><input id="f-title" class="text-input" placeholder="영상 파일 이름"></div>
        <div class="field"><span class="lbl">처리 구간 <span class="muted">(비우면 영상 전체)</span></span>
          <div class="pickrow range"><input id="f-start" class="text-input tnum" placeholder="00:00" aria-label="시작">
            <span class="muted">~</span><input id="f-end" class="text-input tnum" placeholder="끝까지" aria-label="끝"></div></div>
      </div>
      ${optsHtml(f)}
      <div class="ferr" id="f-err" role="alert" hidden></div>
      <div class="form-foot"><span class="est" id="f-est"></span>
        <button type="button" class="btn" data-act="close">취소</button>
        <button type="button" class="btn" data-act="create" data-run="0">만들기만</button>
        <button type="submit" class="btn btn-primary" data-act="create" data-run="1">만들고 분석 시작</button></div>
    </form>`;
    showModal();
    updVideo();
    updScript();
    updEst();
    $('#f-video').focus();
  }
  function updVideo() {
    const f = S.form, v = f.vinfo, el = $('#f-vinfo');
    if (!el) return;
    if (!f.video) el.innerHTML = 'mp4 · mkv · mov 등 소리가 있는 영상';
    else if (!v) el.innerHTML = '읽는 중…';
    else if (v.ok) {
      el.innerHTML = `<span class="ok">✓ ${esc(v.name)} · ${fmtClock(v.duration)} · ${num(v.width)}×${num(v.height)} · ${num(v.fps).toFixed(2)}fps</span>${v.guard
        ? ` · <span class="err">연구 규칙: ${esc(v.guard.title)}은 ${fmtClock(v.guard.dev_end)}까지만</span>` : ''}`;
    }
    else el.innerHTML = `<span class="err">${esc(v.error || '쓸 수 없는 영상입니다')}</span>`;
    $('#f-video').classList.toggle('is-bad', !!(v && !v.ok));
    $('#f-end').placeholder = v && v.ok ? `끝까지 (${fmtClock(v.duration)})` : '끝까지';
  }
  function updScript() {
    const f = S.form, s = f.sinfo, el = $('#f-sinfo');
    if (!el) return;
    $('#f-script-clear').hidden = !f.script;
    let prev = '';
    if (!f.script) el.innerHTML = '한 줄에 <code>이름: 대사</code>, 또는 SRT · VTT · CSV · JSON';
    else if (!s) el.innerHTML = '읽는 중…';
    else if (s.ok) {
      const cs = s.characters || [];
      el.innerHTML = `<span class="ok">✓ ${num(s.lines)}줄 · 이름 있는 줄 ${num(s.named)}${s.timed ? ' · 시각 있음' : ''}</span>${cs.length ? ` · 인물 ${esc(cs.slice(0, 8).join(', '))}${cs.length > 8 ? ` 외 ${cs.length - 8}` : ''}` : ''}`;
      prev = (s.preview || []).length ? `<div class="preview">${s.preview.map((x) => `<div>${esc(x)}</div>`).join('')}</div>` : '';
    } else el.innerHTML = `<span class="err">${esc(s.error || '읽지 못한 대본입니다')}</span>`;
    $('#f-preview').innerHTML = prev;
    $('#f-script').classList.toggle('is-bad', !!(s && !s.ok));
  }
  function updEst() {
    const f = S.form, el = $('#f-est');
    if (!el) return;
    const v = f.vinfo;
    if (!v || !v.ok) { el.textContent = ''; return; }
    const a = parseClock(f.start), b = parseClock(f.end);
    const t0 = a == null || Number.isNaN(a) ? 0 : a;
    const t1 = b == null || Number.isNaN(b) ? v.duration : Math.min(b, v.duration);
    const min = Math.max(0, t1 - t0) / 60;
    const est = Math.max(1, Math.round(min * (f.llm ? 0.5 : 0.27) + 1));
    el.textContent = `${fmtDur(Math.max(0, t1 - t0))} 분량 · 대략 ${est}분 걸림${f.llm ? '' : ' (LLM 없이)'}`;
  }
  function showErr(msg) {
    const el = $('#f-err');
    if (!el) { if (msg) toast(msg, 'error', 4000); return; }
    el.hidden = !msg;
    el.textContent = msg || '';
  }
  async function inspect(which) {
    const f = S.form;
    if (!f || f.kind !== 'new') return;
    const path = which === 'video' ? f.video : f.script;
    const seq = which === 'video' ? ++f.vseq : ++f.sseq;
    if (which === 'video') { f.vinfo = null; updVideo(); } else { f.sinfo = null; updScript(); }
    if (!path) { updEst(); return; }
    try {
      const r = await api('POST', '/api/inspect', which === 'video' ? { video: path } : { script: path });
      if (S.form !== f) return;
      if (which === 'video' && seq === f.vseq) {
        f.vinfo = r.video;
        if (f.titleAuto && r.video && r.video.ok) {
          f.title = r.video.name.replace(/\.[^.]+$/, '');
          $('#f-title').value = f.title;
        }
        const g = r.video && r.video.ok && r.video.guard;
        if (g && (!f.end.trim() || f.endAuto)) {   // 연구 규칙: start inside the development episodes
          f.end = fmtClock(Math.min(g.dev_end, num(r.video.duration, g.dev_end)));
          f.endAuto = true;
          $('#f-end').value = f.end;
        }
        updVideo();
      } else if (which === 'script' && seq === f.sseq) {
        f.sinfo = r.script;
        updScript();
      }
    } catch (e) {
      if (S.form !== f) return;
      const bad = { ok: false, error: e.message || String(e) };
      if (which === 'video') { f.vinfo = bad; updVideo(); } else { f.sinfo = bad; updScript(); }
    }
    updEst();
  }
  async function pickFile(kind) {
    const f = S.form;
    if (!f) return;
    let path = null;
    try {
      const bridge = window.pywebview && window.pywebview.api;
      if (bridge && bridge.pick) path = await bridge.pick(kind);
      else path = (await api('POST', '/api/pick', { kind })).path;
    } catch (e) {
      toast(`파일 고르기 창을 열지 못했습니다 · ${e.message || e}`, 'error', 4000);
      return;
    }
    if (!path || S.form !== f) return;
    const input = kind === 'video' ? $('#f-video') : $('#f-script');
    input.value = path;
    f[kind] = path;
    if (kind === 'video' && f.endAuto) { f.end = ''; f.endAuto = false; $('#f-end').value = ''; }
    inspect(kind);
  }
  function validate(f) {
    if (!f.video) return '영상 파일을 고르세요';
    if (!f.vinfo) return '영상 정보를 읽는 중입니다 · 잠시 뒤 다시 누르세요';
    if (!f.vinfo.ok) return f.vinfo.error || '쓸 수 없는 영상입니다';
    if (f.script && !f.sinfo) return '대본을 읽는 중입니다 · 잠시 뒤 다시 누르세요';
    if (f.script && !f.sinfo.ok) return `대본 · ${f.sinfo.error || '읽지 못했습니다'}`;
    const a = parseClock(f.start), b = parseClock(f.end), dur = num(f.vinfo.duration);
    if (Number.isNaN(a) || Number.isNaN(b)) return '처리 구간은 12:34 나 754 처럼 적으세요';
    if (a != null && a >= dur) return `시작이 영상 길이(${fmtClock(dur)})를 넘습니다`;
    if (b != null && b > dur + 0.5) return `끝은 영상 길이(${fmtClock(dur)})까지입니다`;
    const g = f.vinfo.guard;
    if (g && Math.min(b ?? dur, dur) > g.dev_end + 1e-6) return `연구 규칙: ${g.title}은 개발 회차(00:00~${fmtClock(g.dev_end)})까지만 처리합니다`;
    if (a != null && b != null && b <= a) return '끝이 시작보다 빨라요';
    if (Math.min(b ?? dur, dur) - (a ?? 0) < 10) return '처리 구간이 너무 짧습니다 (10초 넘게)';
    return '';
  }
  async function submitNew(run) {
    const f = S.form;
    if (!f || f.kind !== 'new' || f.sending) return;
    const opts = readOpts(f);
    const err = validate(f);
    showErr(err);
    if (err) return;
    if (run && running()) { showErr('다른 작업을 분석하는 중입니다 · 만들기만 하고 나중에 분석하세요'); return; }
    f.sending = true;
    $$('#new-form [data-act="create"]').forEach((b) => { b.disabled = true; });
    setBusy(true);
    try {
      remember('opts', opts);
      const a = parseClock(f.start), b = parseClock(f.end);
      const r = await api('POST', '/api/projects', {
        video: f.video, script: f.script || null, title: f.title.trim() || null,
        start: a, end: b != null && b < num(f.vinfo.duration) ? b : null, run, options: opts,
      });
      S.sel = r.project.id;
      remember('sel', S.sel);
      closeModal();
      await refresh();
      if (r.warning) toast(r.warning, 'error', 5000);
      else toast(run ? `분석을 시작했습니다 · ${r.project.title}` : `작업을 만들었습니다 · ${r.project.title}`);
      if (run && !r.warning) schedulePoll(400);
    } catch (e) {
      if (S.form === f) {
        f.sending = false;
        $$('#new-form [data-act="create"]').forEach((x) => { x.disabled = false; });
        showErr(e.message || String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------------------------------- run again
  function openRun() {
    const p = current();
    if (!p) return;
    const first = p.status === 'new';
    S.form = { kind: 'run', project: p.id, mode: 'continue', ...defaults() };
    S.modal = 'run';
    const ag = p.agent ? `지금까지 에이전트가 ${p.agent.done}개를 다시 봤다${p.agent.left ? ` · 한도에 걸려 못 본 후보 ${p.agent.left}개 (한도를 올리면 이어서 본다)` : ''}` : '';
    $('#modal-box').innerHTML = `<div class="form">
      <div class="mdl-head"><span class="logo" aria-hidden="true">SDH</span><h3 id="modal-title">분석</h3><span class="muted">${esc(p.title)}</span>
        <span class="spacer"></span><button type="button" class="icon-btn" data-act="close" aria-label="닫기">×</button></div>
      ${first ? '' : `<div class="modes" role="radiogroup" aria-label="분석 방법">
        <label class="mode"><input type="radio" name="mode" value="continue" checked><span><b>이어서 분석</b>
          <span class="muted">끝난 단계는 건너뛰고 남은 단계를 한다 · LLM 을 켜면 비어 있던 근거와 에이전트 재확인을 채운다</span></span></label>
        <label class="mode"><input type="radio" name="mode" value="all"><span><b>처음부터 다시</b>
          <span class="muted">분석 결과를 모두 지우고 다시 한다 · 작가 결정은 남지만, 후보가 바뀌어 맞지 않는 결정은 따로 보관된다</span></span></label>
      </div>`}
      ${optsHtml(S.form)}
      ${ag ? `<div class="finfo">${esc(ag)}</div>` : ''}
      <div class="form-foot"><button type="button" class="btn" data-act="close">취소</button>
        <button type="button" class="btn btn-primary" data-act="run-go">분석 시작</button></div></div>`;
    showModal();
    $('#modal-box [data-act="run-go"]').focus();
  }
  async function startRun(p, mode, opts) {
    setBusy(true);
    try {
      remember('opts', opts);
      await api('POST', `/api/projects/${encodeURIComponent(p.id)}/run`, { mode, options: opts });
      if (S.modal) closeModal();
      await refresh();
      toast(`분석을 시작했습니다 · ${p.title}`);
      schedulePoll(400);
    } catch (e) {
      toast(e.message || String(e), 'error', 4500);
    } finally {
      setBusy(false);
    }
  }
  async function submitRun() {
    const f = S.form;
    if (!f || f.kind !== 'run') return;
    const p = byId(f.project);
    if (!p) { closeModal(); return; }
    const mode = ($('#modal-box input[name="mode"]:checked') || {}).value || 'continue';
    const opts = readOpts(f);
    if (mode === 'all') {
      closeModal();
      const ok = await askConfirm('처음부터 다시 분석할까요?',
        `<b>${esc(p.title)}</b>의 분석 결과(후보 · 근거 · 추천 · 에이전트 재확인)를 지우고 다시 만든다.<br>작가 결정과 내보낸 자막 파일은 지우지 않는다.`,
        '처음부터 다시', true);
      if (!ok) return;
    }
    await startRun(p, mode, opts);
  }
  async function cancelJob() {
    const j = job();
    if (!j || j.state !== 'running') return;
    const ok = await askConfirm('분석을 멈출까요?', '끝난 단계의 결과는 남는다 · 다음에 <b>이어서 분석</b>하면 남은 단계부터 한다.', '멈추기', true);
    if (!ok) return;
    setBusy(true);
    try {
      const r = await api('POST', '/api/job/cancel');
      S.data.job = r.job;
      renderJobOnly();
      schedulePoll(300);
    } catch (e) {
      toast(e.message || String(e), 'error', 4000);
    } finally {
      setBusy(false);
    }
  }
  async function deleteProject() {
    const p = current();
    if (!p) return;
    const ok = await askConfirm('작업을 지울까요?',
      `<b>${esc(p.title)}</b> 작업 폴더를 작업 폴더 안의 <code>.trash</code> 로 옮긴다.<br>영상과 대본 원본은 건드리지 않고, 폴더를 되돌려 놓으면 다시 보인다.`, '지우기', true);
    if (!ok) return;
    setBusy(true);
    try {
      await api('POST', `/api/projects/${encodeURIComponent(p.id)}/delete`);
      S.sel = null;
      await refresh();
      toast(`지웠습니다 · ${p.title}`);
    } catch (e) {
      toast(e.message || String(e), 'error', 4000);
    } finally {
      setBusy(false);
    }
  }
  async function openPath(path) {
    if (!path) return;
    try {
      const bridge = window.pywebview && window.pywebview.api;
      if (bridge && bridge.open_path) { if (!(await bridge.open_path(path))) throw new Error('폴더가 없습니다'); }
      else await api('POST', '/api/open', { path });
    } catch (e) {
      toast(e.message || '열지 못했습니다', 'error');
    }
  }
  function openEnv() {
    const e = S.data.env || {};
    const l = e.llm || {};
    const row = (cls, k, v) => `<div class="envrow"><span class="dot ${cls}"></span><b>${esc(k)}</b><span class="v">${v}</span></div>`;
    const models = Object.entries(e.models || {}).map(([k, ok]) => row(ok ? 'ok' : 'bad', k, ok ? '있음' : '<b>없음</b> · README 의 설치 안내 참고')).join('');
    S.modal = 'env';
    $('#modal-box').innerHTML = `<div class="form">
      <div class="mdl-head"><span class="logo" aria-hidden="true">SDH</span><h3 id="modal-title">환경 점검</h3><span class="spacer"></span>
        <button type="button" class="icon-btn" data-act="close" aria-label="닫기">×</button></div>
      <div class="envlist">
        ${row(e.gpu ? 'ok' : 'warn', 'GPU', e.gpu ? `${esc(e.gpu)} · ${esc(e.device || '')}` : 'NVIDIA(CUDA) · 인텔(XPU) GPU 를 찾지 못했습니다 · CPU 로 분석(많이 느림)')}
        ${row(e.ffmpeg ? 'ok' : 'bad', 'ffmpeg', e.ffmpeg ? '있음' : 'PATH 에서 찾지 못했습니다')}
        ${row(l.logged_in ? 'ok' : 'warn', 'Claude', l.logged_in ? `로그인됨 (${esc(l.method || '')}) · 화면 근거 · 대사 근거 · 재확인 에이전트에 씀`
    : l.cli ? '로그인이 필요합니다 · 터미널에서 <code>claude</code> 를 실행해 로그인' : 'Claude Code CLI 를 찾지 못했습니다 · 규칙으로만 분석')}
        ${row('ok', 'Python', esc(e.python || ''))}
      </div>
      <div class="sec-title">모델 파일</div><div class="envlist">${models}</div>
      <div class="form-foot"><button type="button" class="btn" data-act="env-refresh">다시 점검</button>
        <button type="button" class="btn btn-primary" data-act="close">닫기</button></div></div>`;
    showModal();
  }

  // -------------------------------------------------------------- data, poll
  async function refresh(opts = {}) {
    const data = await api('GET', `/api/studio${opts.env ? '?refresh_env=true' : ''}`);
    S.data = data;
    const ids = new Set(projects().map((p) => p.id));
    if (!S.sel || !ids.has(S.sel)) {
      const j = job();
      S.sel = j && j.state === 'running' && ids.has(j.project) ? j.project : recall('sel', null);
      if (!ids.has(S.sel)) S.sel = projects().length ? projects()[0].id : null;
    }
    render();
    banner('');
  }
  function schedulePoll(ms) {
    clearTimeout(S.poll);
    S.poll = setTimeout(poll, ms ?? (running() ? 1000 : 15000));
  }
  async function poll() {
    try {
      if (running()) {
        const r = await api('GET', '/api/job');
        S.data.job = r.job;
        if (!r.job || r.job.state !== 'running') {
          await refresh();
          finished(r.job);
        } else renderJobOnly();
        banner('');
      } else if (document.visibilityState === 'visible' && !S.modal) {
        await refresh();
      }
    } catch (e) {
      banner(`프로그램과 연결이 끊겼습니다 · ${e.message || e}`);
    }
    schedulePoll();
  }
  function finished(j) {
    if (!j) return;
    if (j.state === 'done') toast(`분석이 끝났습니다 · ${j.title} · 검수를 시작할 수 있습니다`, 'ok', 5200);
    else if (j.state === 'failed') toast(`분석이 실패했습니다 · ${j.title} · 오류는 분석 과정에 있습니다`, 'error', 6500);
    else if (j.state === 'cancelled') toast('분석을 멈췄습니다 · 다음에 이어서 할 수 있습니다', 'ok', 4000);
  }

  function select(id, focus = false) {
    if (!id || !byId(id)) return;
    S.sel = id;
    remember('sel', id);
    for (const row of $$('#plist .prow')) {
      const on = row.dataset.id === id;
      row.classList.toggle('is-sel', on);
      row.setAttribute('aria-selected', String(on));
      row.tabIndex = on ? 0 : -1;
      if (on && focus) { row.focus({ preventScroll: true }); row.scrollIntoView({ block: 'nearest' }); }
    }
    S.logOpen = false;
    renderDetail();
  }
  function openReview(id) {
    const p = byId(id);
    if (!p || !p.review_url || p.status === 'external' || (jobOf(p) && running())) return;
    remember('sel', id);
    location.href = p.review_url;
  }

  // ------------------------------------------------------------------ events
  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-act]');
    if (el && !el.disabled) {
      switch (el.dataset.act) {
        case 'new': openNew(); return;
        case 'close': closeModal(); return;
        case 'confirm-ok': confirmOk(); return;
        case 'pick': pickFile(el.dataset.kind); return;
        case 'clear-script':
          if (S.form) { S.form.script = ''; $('#f-script').value = ''; inspect('script'); }
          return;
        case 'create': ev.preventDefault(); submitNew(el.dataset.run === '1'); return;
        case 'model':
          if (S.form) { S.form.model = el.dataset.model; for (const b of $$('[data-act="model"]')) b.classList.toggle('is-on', b === el); }
          return;
        case 'run': openRun(); return;
        case 'run-go': submitRun(); return;
        case 'cancel': cancelJob(); return;
        case 'delete': deleteProject(); return;
        case 'open-path': openPath(el.dataset.path); return;
        case 'open-ws': openPath(S.data && S.data.workspace); return;
        case 'env': openEnv(); return;
        case 'env-refresh':
          setBusy(true);
          refresh({ env: true }).then(() => { if (S.modal === 'env') openEnv(); toast('다시 점검했습니다'); })
            .catch((e) => toast(e.message || String(e), 'error')).finally(() => setBusy(false));
          return;
        case 'review': remember('sel', S.sel); return;    // the link navigates
        default: break;
      }
    }
    const row = ev.target.closest('.prow');
    if (row) select(row.dataset.id);
  });
  document.addEventListener('dblclick', (ev) => {
    const row = ev.target.closest('.prow');
    if (row) openReview(row.dataset.id);
  });
  document.addEventListener('submit', (ev) => { ev.preventDefault(); if (S.form && S.form.kind === 'new') submitNew(true); });
  document.addEventListener('input', (ev) => {
    const f = S.form;
    if (!f) return;
    const id = ev.target.id;
    if (f.kind === 'new' && id && id.startsWith('f-')) showErr('');
    if (id === 'f-video') {
      f.video = unquote(ev.target.value); f.vinfo = null; f.vseq++;
      if (f.endAuto) { f.end = ''; f.endAuto = false; $('#f-end').value = ''; }   // filled for the old video
      updVideo(); updEst();
    }
    else if (id === 'f-script') { f.script = unquote(ev.target.value); f.sinfo = null; f.sseq++; updScript(); }
    else if (id === 'f-title') { f.title = ev.target.value; f.titleAuto = !f.title.trim(); }
    else if (id === 'f-start') { f.start = ev.target.value; updEst(); }
    else if (id === 'f-end') { f.end = ev.target.value; f.endAuto = false; updEst(); }
    else if (id === 'o-llm') { f.llm = ev.target.checked; $('#o-grid').classList.toggle('is-off', !f.llm); updEst(); }
  });
  document.addEventListener('change', (ev) => {
    const f = S.form;
    if (!f || f.kind !== 'new') return;
    if (ev.target.id === 'f-video') { ev.target.value = f.video; inspect('video'); }
    if (ev.target.id === 'f-script') { ev.target.value = f.script; inspect('script'); }
  });
  // the log box keeps its open state across the 1 s re-render
  document.addEventListener('toggle', (ev) => { if (ev.target.matches && ev.target.matches('.logbox')) S.logOpen = ev.target.open; }, true);
  document.addEventListener('keydown', (ev) => {
    if (!$('#modal').hidden) {
      if (ev.key === 'Escape') { ev.preventDefault(); closeModal(); }
      // Enter in a path box reads the file instead of submitting the form
      else if (ev.key === 'Enter' && ev.target.matches && ev.target.matches('input.path')) { ev.preventDefault(); ev.target.blur(); }
      return;
    }
    if (ev.target.closest && ev.target.closest('input, textarea, select')) return;
    const ps = projects();
    if (!ps.length) return;
    const i = Math.max(0, ps.findIndex((p) => p.id === S.sel));
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      const n = Math.min(Math.max(i + (ev.key === 'ArrowDown' ? 1 : -1), 0), ps.length - 1);
      select(ps[n].id, true);
    } else if (ev.key === 'Enter' && ev.target.closest && ev.target.closest('.prow')) {
      ev.preventDefault();
      openReview(S.sel);
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !running()) schedulePoll(50);
  });

  // -------------------------------------------------------------------- init
  async function init() {
    setBusy(true);
    try {
      await refresh();
    } catch (e) {
      banner(`목록을 불러오지 못했습니다 · ${e.message || e}`);
    } finally {
      setBusy(false);
    }
    schedulePoll();
  }
  init();
})();
