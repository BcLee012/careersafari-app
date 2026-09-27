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
  const r = state.ratings[key] || 3;
  const v = !!state.verified[key];
  return `<div class="sk ${v?'open':''}" data-key="${esc(key)}" data-col="${col}">
    <div class="skwrap" style="flex:1">
      <div style="display:flex;align-items:center;gap:10px">
        <span class="nm">${esc(name)}</span>
        <span class="stars">${[1,2,3,4,5].map(n=>
          `<button type="button" class="${n<=r?'on':''}" onclick="setRating(this,${n})">★</button>`).join('')}</span>
        <button type="button" class="ev-btn ${v?'on':''}" onclick="toggleEv(this)">
          ${v?'✓ 有证据':'有证据？'}</button>
      </div>
      <div class="ev"><input type="text" placeholder="哪门课 / 哪个项目 / 哪段实习"
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
  btn.textContent = state.verified[key] ? '✓ 有证据' : '有证据？';
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
    if(verified && !ev) return;              // 说有证据却写不出 → 降级为纯自述
    skills.push({name, column:col, self_rating:state.ratings[key]||3,
                 verified, evidence_text:ev});
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

  return { persona:$('f-persona').value, major:$('f-major').value, grade:$('f-grade').value,
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
      <div class="sec-t">能力 · 有证据支撑</div>
      <div class="chips">${vsk}</div>
      <div class="sec-t">能力 · 仅自述，未经验证</div>
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
  if(!r.ok){ $('url-msg').innerHTML=`<div class="note bad"><span class="ic">✕</span><div>${esc(r.error)}</div></div>`+warnBox(r.warnings); return; }
  $('url-msg').innerHTML=`<div class="note ok"><span class="ic">✓</span><div>正文 ${r.chars} 字符</div></div>`+warnBox(r.warnings);
  state.rawText=r.raw_text; state.sourceType='url_fetch'; state.sourceUrl=r.source_url;
  $('jd-text').value=r.raw_text.slice(0,4000); setSrc('manual');
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
  $('jd-text').value=r.raw_text.slice(0,4000); setSrc('manual');
}
async function parseJd(){
  const raw=$('jd-text').value.trim();
  if(raw.length<50){ $('parse-out').innerHTML='<div class="note warn"><span class="ic">⚠</span><div>JD 原文至少需要 50 字符。</div></div>'; return; }
  $('parse-out').innerHTML='<div class="card"><span class="spin"></span> 拆解中，可能需要 10–60 秒…</div>';
  const r=await api('/api/jd/parse',{method:'POST',body:JSON.stringify({
    raw_text:raw, source_type:state.sourceType, source_url:state.sourceUrl, user_id:'demo-user'})});
  if(!r.ok){ $('parse-out').innerHTML=`<div class="note bad"><span class="ic">✕</span><div>${esc(r.error)}</div></div>`; return; }
  state.lastParse=r;
  if(r.degraded||r.rule_based){
    $('parse-out').innerHTML=`<div class="note warn"><span class="ic">⚠</span>
      <div><b>已降级为规则基线解析</b><br>${esc(r.error||'LLM 不可用')}
      <br><span class="muted">这不是 LLM 分析，是关键词规则的确定性切分：零依赖、可复现，
      但只证明「原文出现过这些词」，不构成语义理解。</span></div></div>` + renderParseBody(r);
    return;
  }
  if(r.reject_all){
    $('parse-out').innerHTML=`<div class="note bad"><span class="ic">✕</span>
      <div><b>已整体拒绝</b>：${r.validation.total} 条需求全部无法在原文中找到支撑，
      疑似模型编造，不予入库。</div></div>`;
    return;
  }
  $('parse-out').innerHTML = renderParseBody(r);
}
function renderParseBody(r){
  const v=r.validation;
  const rows=r.requirements_preview.map(q=>`
    <div class="req ${q.span_ok?'':'bad'}">
      <div><span class="tag ${q.kind==='soft'?'warn':'info'}">${esc(q.kind)}</span>
           <span class="tag">${esc(q.category)}</span></div>
      <div>${esc(q.text)}</div>
      <div class="sp mono">${esc(q.evidence_span||'（无原文支撑）')}</div>
      <div>${q.span_ok?'<span class="tag ok">已验证</span>':'<span class="tag bad">无原文支撑</span>'}</div>
    </div>`).join('');
  return `
    <div class="note ${v.failed?'warn':'ok'}"><span class="ic">${v.failed?'⚠':'✓'}</span>
      <div><b>生成后校验</b>：共 ${v.total} 条，通过 ${v.passed} 条，未通过 ${v.failed} 条。
      ${r.rule_based
        ? '<br><span class="muted">规则基线的 evidence_span 是逐字切片，校验必然通过。</span>'
        : '<br>每条需求的 evidence_span 都回到原文做了子串匹配；匹配不上的标为「无原文支撑」并降级为团队判断，不当作外部证据采信。'}</div></div>
    <div class="grid g2" style="grid-template-columns:1fr 1fr auto;align-items:end;margin-bottom:14px">
      <div class="f"><label>标准化岗位名</label>
        <input type="text" id="c-name" value="${esc(r.extraction.canonical_name||'')}"></div>
      <div class="f"><label>行业</label>
        <input type="text" id="c-industry" value="${esc(r.extraction.industry||'')}"></div>
      <div class="f"><label>&nbsp;</label><button class="btn btn-p" onclick="confirmJd()">确认入库</button></div>
    </div>
    ${rows}`;
}
async function confirmJd(){
  const r=state.lastParse; if(!r){ return; }
  const reqs=r.requirements_preview.map(q=>({kind:q.kind,category:q.category,text:q.text,
    evidence_span:q.evidence_span, confidence:q.span_ok?'external':'team',
    team_confidence:q.span_ok?null:0.3}));
  const out=await api('/api/jd/confirm',{method:'POST',body:JSON.stringify({
    user_id:'demo-user', raw_text:state.rawText||$('jd-text').value,
    source_type:state.sourceType, source_url:state.sourceUrl,
    canonical_name:$('c-name').value, industry:$('c-industry').value, requirements:reqs})});
  $('parse-out').innerHTML = out.ok
    ? `<div class="note ok"><span class="ic">✓</span><div>已入库：position
       <span class="mono">${esc(out.position_id)}</span>，需求 ${out.n_requirements} 条，
       JD <span class="mono">${esc(out.jd_id)}</span></div></div>`
    : `<div class="note bad"><span class="ic">✕</span><div>入库失败：${esc(out.error)}</div></div>`;
  refreshPositions(); refreshJds();
}
let POS_CACHE=[], FAM_FILTER='';

const FAM_RULES=[['数据分析',['数据分析','数据挖掘','风控数据','风险数据','数据运营']],
  ['商业/经营分析',['商业分析','经营分析','商业变现','战略商业']],
  ['战略分析',['战略分析','战略研究','行业研究','商业洞察']],
  ['供应链计划',['供应链计划','计划专员','s&op','需求计划','库存计划']],
  ['采购',['采购','寻源','供应商质量','品类管理']],
  ['物流履约',['物流','仓储','履约','关务','跨境物流']],
  ['产品运营',['运营','用户增长','增长','内容运营']],
  ['产品经理',['产品经理']],
  ['项目管理',['项目管理','项目经理','项目专员']],
  ['风控合规',['风控','合规','反欺诈','审计']]];

function jobFamilyOf(n){
  const s=(n||'').toLowerCase();
  for(const [fam,kws] of FAM_RULES) if(kws.some(k=>s.includes(k.toLowerCase()))) return fam;
  return '其他（未归入受控岗位族）';
}

async function refreshPositions(){
  const r=await api('/api/positions'); const box=$('positions');
  if(!r.ok||!r.positions.length){
    box.innerHTML='<div class="empty">还没有沉淀任何岗位。先去上面拆解一条 JD。</div>'; return; }
  POS_CACHE=r.positions;
  const fams=[...new Set(r.positions.map(p=>jobFamilyOf(p.canonical_name)))].sort();
  $('fam-chips').innerHTML =
    `<button class="${FAM_FILTER===''?'on':''}" onclick="FAM_FILTER='';renderPositions()">全部 ${r.positions.length}</button>` +
    fams.map(f=>{const c=r.positions.filter(p=>jobFamilyOf(p.canonical_name)===f).length;
      return `<button class="${FAM_FILTER===f?'on':''}" onclick="FAM_FILTER='${esc(f)}';renderPositions()">${esc(f)} ${c}</button>`;}).join('');
  renderPositions();
}

function renderPositions(){
  const box=$('positions');
  if(!POS_CACHE.length) return;
  const q=($('pos-q').value||'').trim().toLowerCase();
  const kind=$('pos-kind').value, conf=$('pos-conf').value;
  let list=POS_CACHE;
  if(FAM_FILTER) list=list.filter(p=>jobFamilyOf(p.canonical_name)===FAM_FILTER);
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
      <span class="tag">${esc(jobFamilyOf(p.canonical_name))}</span>
      ${p.industry?`<span class="tag">${esc(p.industry)}</span>`:''}
      <span class="tag info">${hard} 硬</span><span class="tag warn">${soft} 软</span>
      <span class="tag ok">${ext} 有原文支撑</span>
      <span class="mono faint">${esc(p.position_id)}</span></div>
      <div class="chips">${p.requirements.map(q=>
        `<span class="tag ${q.confidence==='external'?'ok':'warn'}" title="${esc(q.evidence_span||'')}">${esc(q.text)}</span>`).join('')}</div>
    </div>`;}).join('');
}

async function refreshJds(){
  const r=await api('/api/jds'); const box=$('jds');
  if(!r.ok||!r.jds.length){ box.innerHTML='<div class="empty">暂无 JD 原文。</div>'; return; }
  box.innerHTML=`<table class="tbl"><tr><th>JD ID</th><th>来源</th><th>可信度</th>
    <th>状态</th><th>字符</th><th>URL</th></tr>${r.jds.map(j=>`<tr>
    <td class="mono">${esc(j.jd_id)}</td><td>${esc(j.source_type)}</td>
    <td><span class="tag info">${esc(j.credibility)}</span></td><td>${esc(j.status)}</td>
    <td class="mono">${j.raw_len}</td>
    <td class="mono faint">${esc(j.source_url||'—')}</td></tr>`).join('')}</table>`;
}

$('f-dir').addEventListener('change', renderSkills);
init();


/* ---------------- 求职 / 学习建议 ---------------- */
function verdictTag(v){
  const m={'证据充分':'ok','可行，需补证':'info','差距较大':'warn','已排除':'bad','样本不足':'unk'};
  return `<span class="tag ${m[v]||''}">${esc(v)}</span>`;
}
function renderAdvice(a){
  if(!a) return;
  const box=document.createElement('div');
  box.className='result fade'; box.style.marginTop='18px';

  const es=a.evidence_strength||{};
  const top=(a.positions||[]).find(p=>p.verdict!=='已排除');
  const nExcluded=(a.positions||[]).filter(p=>p.verdict==='已排除').length;

  // 方向结论卡片：前 3 名高亮，覆盖率用条形可视化
  const fams=(a.positions||[]).map((p,i)=>{
    const cov=p.verified_coverage;
    const pct=cov==null?0:Math.round(cov*100);
    return `<div class="fam ${i<3&&p.verdict!=='已排除'?'top':''}">
      <div class="fh">
        <span class="pill mono faint">${p.rank}</span>
        <b>${esc(p.job_family||p.name)}</b>
        ${verdictTag(p.verdict)}
        ${p.family_size?`<span class="pill">库内 ${p.family_size} 个同族岗位</span>`:''}
        <span style="margin-left:auto" class="cov">
          <span class="track"><span class="fill" style="width:${pct}%"></span></span>
          <span class="num">${pct}%</span>
        </span>
      </div>
      <div class="muted" style="font-size:12px">代表岗位：${esc(p.name)}
        ${p.n_comparable!=null?` · 可比对需求 ${p.n_comparable} 条`:''}
        ${p.industry?` · ${esc(p.industry)}`:''}</div>
      ${(p.why||[]).map(w=>`<div class="fw ${/冲突|不排除|样本不足/.test(w)?'warn':''}">${esc(w)}</div>`).join('')}
    </div>`;}).join('') || '<div class="empty">暂无可比对岗位</div>';

  const hard=(a.learning_hard||[]).map(l=>{
    const cs=l.courses.map(c=>`<span class="tag ${c.mandatory?'unk':'info'}">《${esc(c.course)}》${esc(c.semester)} · ${esc(c.teacher)} · ${esc(c.credits)}学分${c.mandatory?' · 必修':''}</span>`).join(' ');
    return `<div class="fam"><div class="fh"><b>${esc(l.capability_name)}</b>
      <span class="tag">被 ${l.needed_by.length} 个岗位要求</span>
      <span class="pill">${esc(l.needed_by.slice(0,4).join('、'))}${l.needed_by.length>4?' 等':''}</span></div>
      <div class="chips">${cs||'<span class="tag warn">培养方案内无对应课程</span>'}</div>
      ${l.note?`<div class="muted" style="font-size:12px;margin-top:5px">${esc(l.note)}</div>`:''}</div>`;
  }).join('') || '<div class="empty">没有可通过选课弥补的硬缺口</div>';

  const soft=(a.learning_soft||[]).map(l=>`
    <div class="fam"><div class="fh"><b>${esc(l.capability_name)}</b>
      <span class="tag warn">选课无法获得</span>
      <span class="tag">被 ${l.needed_by.length} 个岗位要求</span></div>
      <div class="muted" style="font-size:12.5px">${esc(l.note)}</div></div>`).join('')
    || '<div class="empty">无</div>';

  const acts=(a.two_week_actions||[]).map((x,i)=>`
    <div class="fam"><div style="font-weight:600;font-size:13.5px">
      <span class="pill mono">${i+1}</span> ${esc(x.action)}</div>
      <div class="muted" style="font-size:12.5px;margin-top:4px">验证什么：${esc(x.validates)}</div>
      <div class="muted" style="font-size:11.5px;margin-top:2px">成本：${esc(x.cost)}
        ${x.source?` · <span class="mono">${esc(x.source)}</span>`:''}</div></div>`).join('')
    || '<div class="empty">暂无</div>';

  box.innerHTML=`
    <div class="verdict">
      <div class="vh">求职与学习建议</div>
      <div class="vt">${esc(a.headline||'')}</div>
      <div class="vm">
        <div><b>${esc(es.level||'—')}</b><span class="lbl">整体证据强度</span></div>
        <div><b>${es.matched||0}</b><span class="lbl">有证据的能力</span></div>
        <div><b>${es.unverified||0}</b><span class="lbl">仅自述</span></div>
        <div><b>${es.gaps||0}</b><span class="lbl">能力缺口</span></div>
        <div><b>${(a.positions||[]).length}</b><span class="lbl">候选方向</span></div>
        ${nExcluded?`<div><b>${nExcluded}</b><span class="lbl">被硬约束排除</span></div>`:''}
      </div>
    </div>

    <div class="card">
      <div class="eyebrow">方向结论</div>
      <h2>按岗位族聚合 · 同族只保留证据最强的代表</h2>
      <p class="sub">覆盖率 = 有证据的能力要求 ÷ 可比对的能力要求。同族多个岗位会触发同一条约束提示，
        已去重；样本不足 5 条的方向标记为「样本不足」而不是给一个虚高的百分比。</p>
      ${fams}
    </div>

    <div class="card">
      <div class="eyebrow">学习建议</div>
      <h2>可借培养方案弥补的硬缺口</h2>
      <p class="sub">按「被多少个岗位要求」排序。标注「必修」的是培养方案本身已安排的课程——
        那是既定安排，不构成额外建议，因此排在最后。</p>
      ${hard}
    </div>

    <div class="card">
      <div class="eyebrow">诚实的边界</div>
      <h2>只能靠实践获得的能力</h2>
      <p class="sub">这些能力被大量岗位要求，但培养方案内没有对应课程。把它们列出来而不是
        硬凑一门课，是这套系统「宁可少写，不可编造」的体现。</p>
      ${soft}
    </div>

    <div class="card">
      <div class="eyebrow">行动</div>
      <h2>接下来两周做什么</h2>
      <p class="sub">每一项都说明它要验证什么假设、成本多大、依据是哪条岗位需求。</p>
      ${acts}
    </div>

    ${(a.excluded&&a.excluded.length)?`<div class="card">
      <div class="eyebrow">排除</div><h2>被你的硬约束排除的方向</h2>
      <div class="note bad" style="margin-top:12px"><span class="ic">✕</span><div>${
        a.excluded.map(e=>`<b>${esc(e.name)}</b>：${esc(e.reason)}`).join('<br>')}</div></div>
    </div>`:''}

    <div class="card">
      <details>
        <summary>LLM 状态与机器可读结果</summary>
        <p class="muted" style="font-size:12.5px;margin:8px 0">
          生成方式：${esc(a.generated_by==='llm_polished'
            ?'确定性匹配引擎计算，LLM 仅润色措辞（引用已逐条校验）'
            :'确定性匹配引擎生成（可复现、可逐条解释）')}
          ${a.llm_note?`<br>${esc(a.llm_note)}`:''}</p>
        <pre class="out">${esc(JSON.stringify(a,null,2))}</pre>
      </details>
    </div>`;
  $('card-out').appendChild(box);
  box.scrollIntoView({behavior:'smooth',block:'start'});
}
