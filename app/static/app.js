/* CareerSafari 前端逻辑 */
let Q = null;
const STEP_TITLES = ['硬约束','能力','兴趣','价值观','未知项'];
let state = { tab:'profile', step:1, src:'manual', rawText:'',
              sourceType:'manual_paste', sourceUrl:'', lastParse:null, ratings:{}, verified:{} };

const $ = id => document.getElementById(id);
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(path, opts){
  const tok = localStorage.getItem('cs_token') || '';
  const o = Object.assign({headers:{'Content-Type':'application/json','X-Admin-Token':tok}}, opts||{});
  const r = await fetch(path, o);
  return r.json();
}

/* ---------------- 视图切换 ---------------- */
function showTab(t){
  state.tab = t;
  $('sec-profile').classList.toggle('on', t==='profile');
  $('sec-jd').classList.toggle('on', t==='jd');
  $('tab-profile').classList.toggle('on', t==='profile');
  $('tab-jd').classList.toggle('on', t==='jd');
  if(t==='jd'){ refreshPositions(); refreshJds(); }
}

/* ---------------- 分步向导 ---------------- */
function renderSteps(){
  $('steps').innerHTML = STEP_TITLES.map((t,i)=>{
    const n=i+1, cls = n===state.step?'on':(n<state.step?'done':'');
    return `<button class="step ${cls}" onclick="stepJump(${n})">
      <span class="n"><span>${n}</span></span><span class="t">${esc(t)}</span></button>`;
  }).join('');
}
function stepJump(n){ state.step=n; renderSteps(); renderPanes(); }
function stepGo(d){
  const n = state.step + d;
  if(n<1||n>STEP_TITLES.length) return;
  state.step=n; renderSteps(); renderPanes();
  window.scrollTo({top:0,behavior:'smooth'});
}
function renderPanes(){
  document.querySelectorAll('.step-pane').forEach(p=>{
    p.hidden = (+p.dataset.step !== state.step);
  });
  $('btn-prev').hidden = state.step===1;
  $('btn-next').hidden = state.step===STEP_TITLES.length;
  $('btn-submit').hidden = state.step!==STEP_TITLES.length;
}

/* ---------------- 初始化 ---------------- */
async function init(){
  let h; try{ h = await api('/api/health'); }catch(e){ h={ok:false}; }
  const t=$('status-t'), d=$('status').querySelector('.dot');
  if(!h.ok){ t.textContent='后端未连接'; d.className='dot bad'; }
  else if(!h.llm_configured){ t.textContent='LLM 未配置 · 走降级'; d.className='dot warn'; }
  else { t.textContent='LLM 就绪'; d.className='dot ok'; }
  if(h.ok && h.allowlist_mode && h.allowlist_mode!=='off'){
    t.textContent += ' · 白名单'+h.allowlist_mode;
  }
  $('ver').textContent = 'schema ' + (h.schema_version||'—');

  Q = await api('/api/questionnaire');

  $('f-persona').innerHTML = Q.persona_options.map(o=>`<option>${esc(o)}</option>`).join('');
  if(Q.persona_options.includes('研一 · 方向未定型')) $('f-persona').value='研一 · 方向未定型';
  $('f-region').innerHTML = '<option value="">— 请选择 —</option>' +
    Q.regions.map(o=>`<option>${esc(o)}</option>`).join('') +
    '<option value="__unknown__">我不确定（登记为未知）</option>';
  $('f-dealbreaker').innerHTML = '<option value="">— 无特殊不可接受项 —</option>' +
    Q.dealbreakers.map(o=>`<option>${esc(o)}</option>`).join('');
  $('f-dir').innerHTML = '<option value="">— 未定 —</option>' +
    Q.major_directions.map(d=>`<option>${esc(d.name)}</option>`).join('');
  $('mbti-caveat-text').textContent = Q.mbti_caveat;

  renderSteps(); renderPanes();
  renderSkills(); renderRiasec(); renderValues(); renderUnknownSuggest();
}

/* ---------------- 能力 ---------------- */
function skillHTML(name, col){
  const key = col+'::'+name;
  const r = state.ratings[key] || 0;
  const v = !!state.verified[key];
  return `<div class="sk ${v?'open':''}" data-key="${esc(key)}" data-col="${col}">
    <div class="skwrap" style="flex:1">
      <div style="display:flex;align-items:center;gap:10px">
        <span class="nm">${esc(name)}</span>
        <span class="stars">${[1,2,3,4,5].map(n=>
          `<button type="button" class="${n<=r?'on':''}" onclick="setRating(this,${n})">★</button>`).join('')}</span>
        <button type="button" class="ev-btn ${v?'on':''}" onclick="toggleEv(this)">
          ${v?'补充经历':'有经历可补充'}</button>
      </div>
      <div class="ev">
        <select class="ev-source" onchange="state.evSources=state.evSources||{};state.evSources[this.closest('.sk').dataset.key]=this.value">${[['self_report','自述'],['course','课程学习'],['practical','实践经历']].map(([val,label])=>`<option value="${val}" ${(state.evSources?.[key]||'self_report')===val?'selected':''}>${label}</option>`).join('')}</select>
        <input class="artifact-ref" type="text" placeholder="作品链接或材料编号（可选）" value="${esc(state.artifacts?.[key]||'')}" oninput="state.artifacts=state.artifacts||{};state.artifacts[this.closest('.sk').dataset.key]=this.value">
        <input type="text" placeholder="做过什么、负责哪一步、产出是什么"
        value="${esc(state.evText&&state.evText[key]||'')}"
        oninput="state.evText=state.evText||{};state.evText['${esc(key)}']=this.value"></div>
    </div></div>`;
}
function setRating(btn,n){
  const box=btn.closest('.sk'), key=box.dataset.key;
  state.ratings[key]=n;
  box.querySelectorAll('.stars button').forEach((b,i)=>b.classList.toggle('on', i<n));
}
function toggleEv(btn){
  const box=btn.closest('.sk'), key=box.dataset.key;
  state.verified[key]=!state.verified[key];
  box.classList.toggle('open', state.verified[key]);
  btn.classList.toggle('on', state.verified[key]);
  btn.textContent = state.verified[key] ? '补充经历' : '有经历可补充';
}
function renderSkills(){
  $('skills-transferable').innerHTML = Q.transferable_skills.map(n=>skillHTML(n,'transferable')).join('');
  const dir=$('f-dir').value;
  const code=(Q.major_directions.find(d=>d.name===dir)||{}).code || 'MSE-BIGDATA';
  $('skills-specific').innerHTML =
    (Q.job_specific_skills[code]||Q.job_specific_skills['MSE-BIGDATA']).map(n=>skillHTML(n,'job_specific')).join('');
}

/* ---------------- RIASEC ---------------- */
const RIASEC_META={R:['现实型','Realistic'],I:['研究型','Investigative'],A:['艺术型','Artistic'],
  S:['社会型','Social'],E:['企业型','Enterprising'],C:['常规型','Conventional']};
function renderRiasec(){
  const byDim={};
  Q.riasec_questions.forEach((q,i)=>{ (byDim[q.dim]=byDim[q.dim]||[]).push({...q,i}); });
  $('riasec').innerHTML = Object.keys(RIASEC_META).map(d=>{
    const [nm,en]=RIASEC_META[d];
    const stmts=(byDim[d]||[]).map(q=>`
      <div class="stmt">
        <div class="q">${esc(q.text)}</div>
        <div class="scale">${[1,2,3,4,5].map(n=>
          `<button type="button" onclick="setScale(this,${q.i},${n})">${n}</button>`).join('')}</div>
        <div class="scale-lbl"><span>不符合</span><span>符合</span></div>
      </div>`).join('');
    return `<div class="rtype"><div class="hd"><span class="b">${d}</span>
      <span class="nm">${nm}</span><span class="en">${en}</span></div>${stmts}</div>`;
  }).join('');
}
function setScale(btn,qi,n){
  const box=btn.closest('.stmt');
  box.querySelectorAll('.scale button').forEach((b,i)=>b.classList.toggle('on', i+1===n));
  box.dataset.v=n;
}

/* ---------------- 价值观 ---------------- */
function renderValues(){
  $('values').innerHTML = Q.value_dimensions.map(([k,label])=>`
    <div class="vrow"><span class="lb">${esc(label)}</span>
      <input type="range" min="0" max="100" value="0" data-k="${k}" oninput="updateVSum()">
      <span class="num" id="vn-${k}">0</span></div>`).join('');
  updateVSum();
}
function updateVSum(){
  let t=0;
  document.querySelectorAll('.vrow input[type=range]').forEach(el=>{
    const v=+el.value; t+=v; $('vn-'+el.dataset.k).textContent=v;
  });
  $('vsum').textContent=t;
  const box=$('vsum-box');
  box.classList.remove('ok','over','under');
  if(t===100){ box.classList.add('ok'); $('vsum-msg').textContent='正好 100 分，取舍明确'; }
  else if(t>100){ box.classList.add('over'); $('vsum-msg').textContent='超出 '+(t-100)+' 分，需要让渡一些'; }
  else { box.classList.add('under'); $('vsum-msg').textContent='还剩 '+(100-t)+' 分没分配'; }
}

/* ---------------- 未知项 ---------------- */
const UNKNOWN_SUGGESTIONS=[
  '我是否真的喜欢这个方向（只有名称认知，没有真实体验）',
  '我的专业在目标岗位上是否有竞争力',
  '目标岗位日常真实在做什么',
  '是否需要长期出差或高频加班',
  '我这个年级开始补技能是否来得及',
  '家庭/地域对我择业的约束',
];
function renderUnknownSuggest(){
  $('unknown-suggest').innerHTML = UNKNOWN_SUGGESTIONS.map(t=>`
    <label style="display:flex;gap:9px;align-items:flex-start;padding:7px 0;font-size:13.5px;
      border-bottom:1px dashed var(--line);cursor:pointer">
      <input type="checkbox" class="u-sug" data-t="${esc(t)}" style="margin-top:3px;flex:none">
      <span>${esc(t)}</span></label>`).join('');
}
function addUnknown(){
  const d=document.createElement('div');
  d.className='grid g2'; d.style.marginBottom='8px';
  d.innerHTML=`<div class="f"><label>未知项</label>
      <input type="text" class="u-field" placeholder="如：是否接受长期出差"></div>
    <div class="f"><label>为什么不确定</label>
      <input type="text" class="u-reason" placeholder="如：没实习过，无法判断"></div>`;
  $('unknowns').appendChild(d);
}

/* ---------------- 采集答案 ---------------- */
function collectAnswers(){
  const riasec={R:0,I:0,A:0,S:0,E:0,C:0};
  document.querySelectorAll('.stmt[data-v]').forEach(box=>{
    const dim=box.closest('.rtype').querySelector('.b').textContent.trim();
    riasec[dim]+= (+box.dataset.v);
  });
  const skills=[];
  document.querySelectorAll('.sk').forEach(box=>{
    const name=box.querySelector('.nm').textContent;
    const col=box.dataset.col, key=box.dataset.key;
    const verified=!!state.verified[key];
    const ev=(state.evText&&state.evText[key]||'').trim();
    if(!state.ratings[key] && !ev) return;
    skills.push({name, column:col, self_rating:state.ratings[key]||null,
                 verified:false, evidence_text:ev,
                 evidence_source:box.querySelector('.ev-source').value,
                 artifact_reference:box.querySelector('.artifact-ref').value.trim()});
  });
  const values={};
  document.querySelectorAll('.vrow input[type=range]').forEach(el=>{
    values[el.dataset.k]=+el.value;
  });
  const unknowns=[];
  document.querySelectorAll('.u-sug:checked').forEach(el=>
    unknowns.push({field:el.dataset.t, reason:'从候选中勾选'}));
  document.querySelectorAll('#unknowns .grid').forEach(r=>{
    const f=r.querySelector('.u-field').value.trim();
    if(f) unknowns.push({field:f, reason:r.querySelector('.u-reason').value.trim()});
  });

  return { target_stage:$('f-stage').value, attained_degree:$('f-degree').value, persona:$('f-persona').value, major:$('f-major').value, grade:$('f-grade').value,
    direction_interest:$('f-dir').value, region:$('f-region').value, timeline:$('f-timeline').value,
    dealbreaker:$('f-dealbreaker').value, intern_goal:$('f-intern').value, skills, riasec,
    mbti:$('f-mbti').value.trim().toUpperCase(), mbti_caveat_ack:$('f-mbti-ack').checked,
    values, unknowns };
}

/* ---------------- 提交画像 ---------------- */
async function submitProfile(){
  const out=$('card-out'); out.hidden=false;
  out.innerHTML='<div class="card"><span class="spin"></span> 生成中…</div>';
  const r=await api('/api/profile',{method:'POST',
    body:JSON.stringify({user_id:'demo-user', answers:collectAnswers()})});
  if(!r.ok){ out.innerHTML=`<div class="note bad"><span class="ic">✕</span><div>失败：${esc(r.error)}</div></div>`; return; }
  window.__lastAdvice = r.advice || null;
  renderCard(r.card);
}

function renderCard(c){
  const consRows=c.hard_constraints.map(k=>`<div class="kv">
      <span class="k">${esc(k.label)}</span><span class="v">${esc(k.value)}</span>
      <span class="tag ${k.evidence==='unknown'?'unk':'info'}">${esc(k.evidence)}</span></div>`).join('');
  const vsk=c.skills.verified.map(s=>`<span class="tag ok">${esc(s.name)}</span>`).join('') || '<span class="muted">无</span>';
  const usk=c.skills.unverified.map(s=>`<span class="tag warn">${esc(s.name)}</span>`).join('') || '<span class="muted">无</span>';
  const tv=c.top_value;
  const tvLine = tv.note && !tv.dimension
    ? `<span class="tag warn">并列：${esc(tv.tied.join('、'))}</span>`
    : (tv.dimension ? `<span class="tag info">${esc(tv.dimension)}</span> <b>${tv.weight}</b>` : '<span class="muted">未分配</span>');
  const warns=(c.consistency_warnings||[]).length
    ? `<div class="note warn"><span class="ic">⚠</span><div><b>输入一致性警告</b><br>${
        c.consistency_warnings.map(esc).join('<br>')}</div></div>` : '';

  $('card-out').innerHTML=`
  <div class="result fade">
    <div class="rh">
      <div class="eyebrow">求职意向定义卡</div>
      <h2>${esc(c.persona||'未命名人设')}</h2>
      <p class="muted" style="font-size:13px">profile <span class="mono">${esc(c.profile_id)}</span>
        · response <span class="mono">${esc(c.response_id)}</span>
        · schema <span class="mono">${esc(c.schema_version)}</span></p>
    </div>
    <div class="rb">
      ${warns}
      <div class="sec-t">硬约束 · 客观事实，划定可行范围</div>
      ${consRows}
      <div class="sec-t">兴趣</div>
      <div class="chips">
        ${c.interest.riasec_code
          ? `<span class="tag info">RIASEC ${esc(c.interest.riasec_code)}</span>`
          : `<span class="tag unk">兴趣码未生成 · 各维度未分化</span>`}
        ${c.interest.mbti_hint?`<span class="tag warn">MBTI ${esc(c.interest.mbti_hint)}（辅助）</span>`:''}
      </div>
      ${c.interest.mbti_caveat?`<p class="muted" style="font-size:12px;margin-top:7px">${esc(c.interest.mbti_caveat)}</p>`:''}
      <div class="sec-t">能力 · 提供作品引用（贡献与熟练度待核实）</div>
      <div class="chips">${vsk}</div>
      <div class="sec-t">能力 · 课程学习或自述</div>
      <div class="chips">${usk}</div>
      <div class="sec-t">价值观最高项</div>
      <div>${tvLine}</div>
      ${tv.note?`<p class="muted" style="font-size:12.5px;margin-top:7px">${esc(tv.note)}</p>`:''}
      <div class="sec-t">显式未知</div>
      <div class="chips">${c.unknowns.length
        ? c.unknowns.map(u=>`<span class="tag unk">${esc(u.field)}</span>`).join('')
        : '<span class="muted">未登记任何未知项</span>'}</div>
      <div class="sec-t">诚实性摘要</div>
      <div class="note ok"><span class="ic">✓</span><div>${
        c.honesty_summary.map(esc).join('<br>')}</div></div>
      <details><summary>机器可读（完整 JSON）</summary>
        <pre class="out">${esc(JSON.stringify(c,null,2))}</pre></details>
    </div>
  </div>`;
  if(window.__lastAdvice) renderAdvice(window.__lastAdvice);
}

/* ---------------- JD 端 ---------------- */
function setSrc(s){
  state.src=s;
  $('src-manual-box').hidden = s!=='manual';
  $('src-url-box').hidden    = s!=='url';
  $('src-file-box').hidden   = s!=='file';
  $('src-manual').classList.toggle('on',s==='manual');
  $('src-url').classList.toggle('on',s==='url');
  $('src-file').classList.toggle('on',s==='file');
}
function warnBox(ws){
  if(!ws||!ws.length) return '';
  return `<div class="note warn" style="margin-top:10px"><span class="ic">⚠</span>
    <div><b>采集提示</b><br>${ws.map(esc).join('<br>')}</div></div>`;
}
async function fetchUrl(){
  const url=$('jd-url').value.trim();
  $('url-msg').innerHTML='<span class="spin"></span> 抓取中…';
  const r=await api('/api/jd/fetch',{method:'POST',body:JSON.stringify({url,user_id:'demo-user'})});
  if(!r.ok){ $('url-msg').innerHTML=`<div class="note bad"><span class="ic">✕</span><div>${esc(r.error)}</div></div>`+warnBox(r.warnings); refreshJds(); return; }
  $('url-msg').innerHTML=`<div class="note ok"><span class="ic">✓</span><div>正文 ${r.chars} 字符</div></div>`+warnBox(r.warnings);
  state.rawText=r.raw_text; state.sourceType='url_fetch'; state.sourceUrl=r.source_url;
  $('jd-text').value=r.raw_text; $('jd-source-url').value=state.sourceUrl; setSrc('manual');
}
async function uploadFile(){
  const f=$('jd-file').files[0];
  if(!f){ $('file-msg').textContent='请先选择文件'; return; }
  const fd=new FormData(); fd.append('file',f); fd.append('user_id','demo-user');
  $('file-msg').innerHTML='<span class="spin"></span> 解析中…';
  const tok=localStorage.getItem('cs_token')||'';
  const resp=await fetch('/api/jd/upload',{method:'POST',headers:{'X-Admin-Token':tok},body:fd});
  const r=await resp.json();
  if(!r.ok){ $('file-msg').innerHTML=`<div class="note bad"><span class="ic">✕</span><div>${esc(r.error)}</div></div>`; return; }
  $('file-msg').innerHTML=`<div class="note ok"><span class="ic">✓</span><div>${esc(r.filename)} · ${r.chars} 字符</div></div>`;
  state.rawText=r.raw_text; state.sourceType='file_upload'; state.sourceUrl='';
  $('jd-text').value=r.raw_text; $('jd-source-url').value=state.sourceUrl; setSrc('manual');
}
async function parseJd(){
  const raw=$('jd-text').value.trim();
  if(raw.length<50){ $('parse-out').textContent='JD 原文至少需要 50 字符。'; return; }
  $('parse-out').innerHTML='<div class="card"><span class="spin"></span> 正在检查来源并拆解，完成后自动保存结果…</div>';
  const r=await api('/api/jd/ingest',{method:'POST',body:JSON.stringify({
    raw_text:raw, source_type:state.sourceType, source_url:$('jd-source-url').value.trim(), user_id:'demo-user'})});
  renderIngestResult(r);
  refreshPositions(); refreshJds();
}
function renderIngestResult(r){
  if(!r.ok){ $('parse-out').innerHTML=`<div class="note bad">${esc(r.error)}</div>`; return; }
  const labels={ready:'自动检查通过，已保存岗位',review:'已保存原文，进入待确认队列',excluded:'已保存原文，该招聘阶段不在首批范围'};
  $('parse-out').innerHTML=`<div class="note ${r.state==='ready'?'ok':'warn'}"><div>
    <b>${esc(labels[r.state]||r.state)}</b>${r.cached?'（复用已有检查）':''}
    <p>${(r.reasons||[]).map(esc).join('<br>')}</p>
    <p>${esc(r.source_note||'')}</p>
    <span class="mono">${esc(r.jd_id)}</span>
    ${r.state==='review'?`<button class="btn" onclick="retryJd('${esc(r.jd_id)}')">重新检查</button>`:''}
    </div></div>`;
}
async function retryJd(id){
  $('parse-out').textContent='重新检查中…';
  renderIngestResult(await api('/api/jd/'+encodeURIComponent(id)+'/retry',{method:'POST'}));
  refreshPositions(); refreshJds();
}
let POS_CACHE=[], FAM_FILTER='';

function familyOf(p){ return p.admission?.family || '待重新检查'; }

async function refreshPositions(){
  const r=await api('/api/positions'); const box=$('positions');
  if(!r.ok||!r.positions.length){
    box.innerHTML='<div class="empty">还没有沉淀任何岗位。先去上面拆解一条 JD。</div>'; return; }
  POS_CACHE=r.positions;
  const fams=[...new Set(r.positions.map(p=>familyOf(p)))].sort();
  $('fam-chips').innerHTML =
    `<button class="${FAM_FILTER===''?'on':''}" onclick="FAM_FILTER='';renderPositions()">全部 ${r.positions.length}</button>` +
    fams.map(f=>{const c=r.positions.filter(p=>familyOf(p)===f).length;
      return `<button class="${FAM_FILTER===f?'on':''}" onclick="FAM_FILTER='${esc(f)}';renderPositions()">${esc(f)} ${c}</button>`;}).join('');
  renderPositions();
}

function renderPositions(){
  const box=$('positions');
  if(!POS_CACHE.length) return;
  const q=($('pos-q').value||'').trim().toLowerCase();
  const kind=$('pos-kind').value, conf=$('pos-conf').value;
  let list=POS_CACHE;
  if(FAM_FILTER) list=list.filter(p=>familyOf(p)===FAM_FILTER);
  if(q) list=list.filter(p=>
    (p.canonical_name+' '+(p.industry||'')+' '+p.requirements.map(r=>r.text).join(' '))
      .toLowerCase().includes(q));
  if(kind||conf) list=list.map(p=>({...p,
    requirements:p.requirements.filter(r=>(!kind||r.kind===kind)&&(!conf||r.confidence===conf))}))
    .filter(p=>p.requirements.length);

  if(!list.length){ box.innerHTML='<div class="empty">没有匹配的岗位。</div>'; return; }
  box.innerHTML=list.map(p=>{
    const hard=p.requirements.filter(q=>q.kind==='hard').length;
    const soft=p.requirements.length-hard;
    const ext=p.requirements.filter(q=>q.confidence==='external').length;
    return `<div class="pos fade"><div class="ph">
      <b>${esc(p.canonical_name)}</b>
      <span class="tag">${esc(familyOf(p))}</span>
      ${p.industry?`<span class="tag">${esc(p.industry)}</span>`:''}
      <span class="tag info">${hard} 硬</span><span class="tag warn">${soft} 软</span>
      <span class="tag ok">${ext} 有原文引用</span>
      <span class="tag ${p.admission?.current?'info':'warn'}">${p.admission?.current?'通过自动检查':'历史数据待检查'}</span>
      <span class="mono faint">${esc(p.position_id)}</span></div>
      <div class="chips">${p.requirements.map(q=>
        `<span class="tag ${q.confidence==='external'?'ok':'warn'}" title="${esc((q.evidence_spans||[]).join('\n')||q.evidence_span||'')}">${esc(q.text)}</span>`).join('')}</div>
    </div>`;}).join('');
}

async function refreshJds(){
  const coverage=await api('/api/library/coverage');
  if(coverage.ok){
    const current=$('knowledge-family').value;
    $('knowledge-family').innerHTML=[...new Set(coverage.rows.map(r=>r.family))].map(f=>`<option value="${esc(f)}">${esc(f)}</option>`).join('');
    if([...$('knowledge-family').options].some(o=>o.value===current)) $('knowledge-family').value=current;
  }
  $('coverage-status').innerHTML=coverage.ok ? `<p>${coverage.eligible_count} / ${coverage.target_count} 个岗位方向与招聘阶段已有足够证据。</p>
    <p class="muted">${esc(coverage.note)}</p><details><summary>查看全部方向与缺口</summary>
    <table class="tbl"><tr><th>方向</th><th>阶段</th><th>有效 JD</th><th>企业</th><th>进度</th></tr>
    ${coverage.rows.map(r=>`<tr><td>${esc(r.family)}</td><td>${esc(r.stage)}</td><td>${r.jd_count} / 6</td><td>${r.employer_count} / 3</td><td>${r.eligible?'可比对':'待补齐'}</td></tr>`).join('')}</table></details>`
    : '<p class="muted">填写管理令牌后可查看覆盖进度</p>';
  const queue=await api('/api/jd/reviews');
  const humanReason=s=>String(s).replace(/company/g,'公司信息').replace(/family/g,'岗位分类').replace(/stage/g,'招聘阶段')
    .replace(/req-(\d+)/g,(_,n)=>'第'+(+n+1)+'项要求').replace(/evidence_span/g,'原文引用');
  $('reviews').innerHTML=queue.ok ? (queue.reviews.map(j=>`<div class="fam"><b>${esc(j.title)} · ${esc(j.company)}</b>
    <p>${(j.details?.result?.reasons||[]).map(s=>esc(humanReason(s))).join('；')}</p>
    <p>${sourceLink(j.source_url)} <small>${esc(j.jd_id)}</small></p>
    <button class="btn" onclick="retryJd('${esc(j.jd_id)}')">重新检查</button>
    <button class="btn" data-recollect-url="${esc(j.source_url)}">重新采集来源</button></div>`).join('')
    + (queue.source_failures||[]).map(s=>`<div class="fam"><b>来源采集失败</b><p>${esc(s.error)}</p>
      <p>${sourceLink(s.source_url)}</p><button class="btn" data-recollect-url="${esc(s.source_url)}">重新采集来源</button></div>`).join('')
    || '<p class="muted">暂无待确认或采集失败项</p>') : '<p class="muted">填写管理令牌后可查看待确认队列</p>';
  const r=await api('/api/jds'); const box=$('jds');
  if(!r.ok||!r.jds.length){ box.innerHTML='<div class="empty">暂无 JD 原文。</div>'; return; }
  box.innerHTML=`<table class="tbl"><tr><th>JD ID</th><th>来源</th><th>可信度</th>
    <th>状态</th><th>字符</th><th>URL</th></tr>${r.jds.map(j=>`<tr>
    <td class="mono">${esc(j.jd_id)}</td><td>${esc(j.source_type)}</td>
    <td><span class="tag info">${esc(j.credibility)}</span></td><td>${esc({raw:'已保存，待检查',checking:'正在检查',ready:'检查通过',review:'待确认',excluded:'范围外'}[j.status]||j.status)}</td>
    <td class="mono">${j.raw_len}</td>
    <td class="mono faint">${esc(j.source_url||'—')}</td></tr>`).join('')}</table>`;
}

$('reviews').addEventListener('click',event=>{
  const button=event.target.closest('[data-recollect-url]');
  if(button){$('jd-url').value=button.dataset.recollectUrl;setSrc('url');fetchUrl();}
});

$('f-dir').addEventListener('change', renderSkills);
init();


/* ---------------- 求职 / 学习建议 ---------------- */
function verdictTag(v){
  const m={'证据充分':'ok','可行，需补证':'info','差距较大':'warn','已排除':'bad','样本不足':'unk'};
  return `<span class="tag ${m[v]||''}">${esc(v)}</span>`;
}
function sourceLink(url){
  try {
    const u=new URL(url);
    const jd=u.hostname==='campus.jd.com' && u.pathname.match(/^\/api\/wx\/position\/detail\/(\d+)$/);
    if(jd) return `<a href="https://campus.jd.com/#/jobs" title="原始接口：${esc(u.href)}" target="_blank" rel="noopener noreferrer">京东校招官网 · 岗位编号 ${esc(jd[1])}</a>`;
    if(['http:','https:'].includes(u.protocol)) return `<a href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">查看招聘原文</a>`;
  } catch(e) {}
  return '来源未提供';
}
function renderAdvice(a){
  if(!a) return;
  const box=document.createElement('div'); box.className='result fade';
  const directions=(a.positions||[]).map(p=>`<div class="fam">
    <div class="fh"><b>${esc(p.job_family)} · ${esc(p.stage)}</b><span class="tag info">${esc(p.verdict)}</span></div>
    ${(p.why||[]).map(w=>`<p class="muted">${esc(w)}</p>`).join('')}
    <details><summary>能力与依据</summary>${(p.matrix||[]).map(m=>`<div class="fam">
      <b>${esc(m.capability_name)}</b> <span class="tag">${esc({matched:'提供作品引用',unverified:'课程或自述',unknown:'未知',gaps:'明确缺口'}[m.status])}</span>
      <p class="muted">${esc(m.note||'')}</p>
      ${(m.evidence||[]).map(e=>`<p>${esc(e.company)}：${esc(e.text)}<br><small>${esc(e.published_date)} · ${sourceLink(e.source_url)}</small></p>`).join('')}
    </div>`).join('')}
    <p class="muted">${esc(p.note)}</p></details>
    ${p.scenario_specific?.length?`<details><summary>单企业场景补充</summary>${p.scenario_specific.map(m=>`<p>${esc(m.capability_name)}：${m.evidence.map(e=>esc(e.company)+' · '+esc(e.text)).join('；')}</p>`).join('')}</details>`:''}
    ${renderOccupationalReference(p.occupational_reference)}
    </div>`).join('');
  const candidates=(a.candidate_families||[]).map(p=>`<div class="fam"><b>${esc(p.job_family)} · ${esc(p.stage)}</b>
    <p class="muted">${(p.reasons||[]).map(esc).join('；')}。未进入正式匹配。</p>${renderOccupationalReference(p.occupational_reference)}</div>`).join('');
  const actions=(a.two_week_actions||[]).map(x=>`<div class="fam"><b>${esc(x.action)}</b><p class="muted">${esc(x.validates)}</p></div>`).join('');
  box.innerHTML=`<div class="card"><h2>岗位探索报告</h2><p>${esc(a.headline)}</p>
    ${a.coverage_note?`<p class="muted">${esc(a.coverage_note)}</p>`:''}
    <p class="muted">${esc(a.evidence_strength?.note||'作品引用不等于掌握，未知不等于能力不足。')}</p>
    ${directions||'<div class="empty">当前暂无正式匹配结果</div>'}</div>
    ${candidates?`<div class="card"><h2>证据待补充的候选方向</h2>${candidates}</div>`:''}
    ${actions?`<div class="card"><h2>下一步补充什么</h2>${actions}</div>`:''}
    ${(a.excluded||[]).length?`<div class="card"><h2>明确约束冲突</h2>${a.excluded.map(e=>`<p>${esc(e.name)}：${esc(e.reason)}</p>`).join('')}</div>`:''}`;
  $('card-out').appendChild(box);
}

function renderOccupationalReference(ref){
  if(!ref?.available) return '';
  return `<details><summary>美国职业参考 · O*NET ${esc(ref.version)}</summary>
    <p class="muted">${esc(ref.limitation)}</p>
    ${(ref.references||[]).map(r=>`<div class="fam"><b>${esc(r.title)}</b><p>${esc(r.excerpt)}</p>
      <small>${esc(r.citation_id)} · <a href="https://www.onetonline.org/link/summary/${encodeURIComponent(r.soc)}" target="_blank" rel="noopener noreferrer">职业原文</a></small></div>`).join('')}
    <p class="muted">${esc(ref.attribution)}</p></details>`;
}

async function explainOccupation(){
  const question=$('knowledge-question').value.trim();
  if(!question){$('knowledge-result').innerHTML='<p class="muted">请填写想了解的问题。</p>';return;}
  $('knowledge-button').disabled=true;
  $('knowledge-result').innerHTML='<p class="muted">正在查阅职业资料并核对引用…</p>';
  try{
    const r=await api('/api/knowledge/explain',{method:'POST',body:JSON.stringify({family:$('knowledge-family').value,question})});
    $('knowledge-result').innerHTML=r.ok ? `<p class="muted">${esc(r.limitation||'')}</p>
      ${(r.statements||[]).map(s=>`<p>${esc(s.text)}<br><small>${s.citation_ids.map(esc).join('；')}</small></p>`).join('')||`<p>${esc(r.reason||'暂时无法形成有依据的解释。')}</p>`}
      ${renderOccupationalReference({available:(r.references||[]).length>0,version:'31.0',references:r.references,limitation:r.limitation,attribution:r.attribution})}`
      : `<p class="muted">${esc(r.error||'请确认管理令牌后重试。')}</p>`;
  }catch(e){$('knowledge-result').innerHTML='<p class="muted">请求未完成，请稍后重试。</p>';}
  finally{$('knowledge-button').disabled=false;}
}
