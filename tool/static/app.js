'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  play:'<path d="m9 5 11 7-11 7Z"/>', pause:'<path d="M8 5v14M16 5v14"/>',
  back:'<path d="m14 6-6 6 6 6"/>', next:'<path d="m10 6 6 6-6 6"/>',
  repeat:'<path d="M20 8H7a4 4 0 0 0-4 4v1M4 16h13a4 4 0 0 0 4-4v-1M17 5l3 3-3 3M7 13l-3 3 3 3"/>',
  replay:'<path d="M4 9a8 8 0 1 1 0 6M4 3v6h6"/>',
  star:'<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.3-5.7-3-5.7 3 1.1-6.3-4.6-4.5 6.4-.9Z"/>',
  check:'<path d="m5 12 4 4L19 6"/>', search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  book:'<path d="M12 5v15M12 5C9 3 5 3 2 4v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Z"/>',
  sound:'<path d="M5 9H2v6h3l5 4V5ZM14 8c3 2 3 6 0 8M17 4c6 4 6 12 0 16"/>',
  muted:'<path d="M5 9H2v6h3l5 4V5ZM16 9l6 6M22 9l-6 6"/>',
  expand:'<path d="M8 3H3v5M16 3h5v5M21 16v5h-5M3 16v5h5"/>',
  chevron:'<path d="m6 9 6 6 6-6"/>', link:'<path d="m9 15 6-6M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(1 0) scale(.9)"/>',
  folder:'<path d="M3 7V5h6l2 2h10v13H3ZM3 10h18"/>',
  pen:'<path d="m4 16-1 5 5-1L21 7l-4-4ZM14 6l4 4"/>',
  lines:'<path d="M4 6h16M4 12h12M4 18h16"/>',
  cloud:'<path d="M6 17a5 5 0 1 1 1-10 6 6 0 0 1 11 2 4 4 0 0 1 0 8M9 15l3 3 5-5"/>',
};
const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.book}</svg>`;
const fmt = sec => {sec=Math.max(0,Math.floor(Number(sec)||0));return `${sec>=3600?Math.floor(sec/3600)+':':''}${String(Math.floor(sec/60)%60).padStart(2,'0')}:${String(sec%60).padStart(2,'0')}`;};
const num = n => String(n).padStart(2,'0');
const state = { token:'', packages:[], jobs:[], page:'library', route:null, pkg:null, progress:{}, current:0, mode:'watch', subtitles:'en', loop:false, analysisOpen:false, reveal:false, filter:'all', search:'', frame:0, loopTimer:0, saveTimer:0, jobSignature:'', loading:false, reviews:[], reviewFilter:'all', reviewCategory:'all', reviewSearch:'', reviewExpanded:new Set(), reviewDrafts:{}, reviewDelete:null, selection:null, reviewSaving:false };
let toastTimer, saveRevision=0, routeRevision=0, reviewRevision=0, progressQueue=Promise.resolve(true), teardownPending=null;
const listening={items:[],loaded:false,offset:0,mode:'due',queue:[],index:0,active:null,attempts:new Map(),completed:new Set(),skipped:new Set(),audio:null,epoch:0,frame:0,timer:0,feedback:'',markTarget:null,markBusy:false};
state.reviewMode='text';
const difficultyLabels={meaning:'看字幕也不懂',sound:'看得懂但听不出',speed:'慢一点能懂，原速跟不上'};
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),4000);}
async function api(path, data, options={}){
  const response=await fetch(path,{...options,method:data!==undefined?'POST':options.method||'GET',headers:{...(data!==undefined?{'Content-Type':'application/json','X-Local-Token':state.token}:{}),...options.headers},body:data!==undefined?JSON.stringify(data):options.body});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'操作失败，请重试');
  return result;
}
const media = (pkg, path) => `/media/${encodeURIComponent(pkg.folder)}/${path.split('/').map(encodeURIComponent).join('/')}`;
const current = () => state.pkg?.segments[state.current];
function showImport(mode='url'){ $('#download-error').textContent='';if(!localVideo&&!localCopyBusy){$('#local-copy-status').textContent='';$('#local-import-error').textContent='';}setImportMode(mode);if(!$('#import-dialog').open)$('#import-dialog').showModal();if(mode==='url')setTimeout(()=>$('#video-url').focus(),30); }
async function teardown(){
  stopListening();
  if(teardownPending)return teardownPending;
  clearSelection();cancelAnimationFrame(state.frame);clearTimeout(state.loopTimer);
  const video=$('#video');if(!video)return true;
  video.pause();
  teardownPending=(async()=>{let revision;do{const pending=persist(true);revision=saveRevision;if(!await pending)return false;}while(revision!==saveRevision);return true;})();
  try{return await teardownPending;}finally{teardownPending=null;}
}
function keepUnsavedStudy(){setRoute(state.pkg?.folder||'library');toast('学习进度还没有保存成功，已留在当前页面。请恢复本地连接后再试，收藏和笔记暂时保留在这里。');}
function setNavigation(page){$('#library-nav').classList.toggle('active',page!=='review');$('#review-nav').classList.toggle('active',page==='review');}
function setRoute(route){state.route=route;const hash=route==='library'?'':route==='review'?'review':encodeURIComponent(route);if(location.hash.slice(1)!==hash)location.hash=hash;}
async function home(){const revision=++routeRevision;setRoute('library');const saved=await teardown();if(revision!==routeRevision)return;if(!saved){keepUnsavedStudy();return;}state.page='library';state.pkg=null;setNavigation('library');await loadLibrary();}

function jobCards(){
  return state.jobs.filter(j=>!['complete','cancelled'].includes(j.status)).slice(0,3).map(j=>{
    const active=['running','queued'].includes(j.status), title=j.title||'制作新的学习素材包';
    return `<div class="job-card ${active?'':'error'}"><div><div class="job-title">${active?'<span class="status-dot"></span> ':''}${esc(title)}</div><div class="job-message">${esc(j.message)}</div></div><div class="job-actions">${j.folder?`<button class="button small" data-open="${esc(j.folder)}">先开始学习</button>`:''}${active?`<button class="text-button" data-cancel="${esc(j.id)}">取消</button>`:`<button class="button small" data-retry="${esc(j.id)}">重试</button><button class="text-button" data-dismiss="${esc(j.id)}">收起</button>`}</div>${active?`<div class="job-track"><span style="width:${Math.max(0,Math.min(100,j.progress||0))}%"></span></div><div class="job-subtle">制作完成后，视频、字幕与讲解都会保存在素材包中。</div>`:''}</div>`;
  }).join('');
}
const catalogKey=value=>String(value).normalize('NFKC').toLocaleLowerCase().trim().replace(/\s+/g,' ');
const libraryView={query:'',category:'',tags:[],sort:'recent',expanded:false,data:null,revision:0,controller:null,timer:null,editing:null,busy:false};
function libraryShell(){
  $('#app').innerHTML=`<section class="library"><div class="hero"><div><div class="eyebrow">YOUR EVERYDAY ENGLISH, SLOWLY.</div><h1>把喜欢的视频，<br>听成<em>自己的英语。</em></h1><p class="intro">从一段日常开始。看见生活，听懂表达，<br>再把喜欢的那一句，变成你自己的声音。</p><div class="hero-note">Less rushing. More listening.</div></div><div class="new-card"><div class="new-card-top"><h3>收藏一个新故事</h3><span class="tiny-tag">LINKS & LOCAL STORIES</span></div><button class="url-starter" data-new>${icon('link')}<span>粘贴你想学习的视频链接</span><span class="arrow-box">↗</span></button><button class="local-starter" data-local>${icon('folder')}<span>导入本地英文视频<small>电影、访谈或课程，从原声重新识别</small></span><span>↗</span></button><button class="drop-zone" data-import><span class="folder-glyph">↳</span><span>或拖入一个素材包<small>选择整个文件夹，让学习接着发生</small></span></button></div></div><div id="jobs-area">${jobCards()}</div><div class="collection-heading"><div class="collection-title"><h2>我的素材库</h2><span class="count-badge" id="library-total">…</span></div><div class="collection-actions"><button class="text-button" data-refresh>${icon('replay')}刷新</button><button class="button small" data-new>＋ 新建素材包</button></div></div><section class="library-tools" aria-label="查找素材"><div class="library-search-row"><div class="search-box library-search">${icon('search')}<input id="library-search" type="search" maxlength="200" value="${esc(libraryView.query)}" placeholder="搜索标题、作者、标签或中英文字幕…" aria-label="搜索素材"></div><select id="library-sort" aria-label="素材排序"><option value="recent">最近添加</option><option value="relevance">相关程度</option><option value="title">标题 A–Z</option><option value="short">时长从短到长</option><option value="long">时长从长到短</option></select></div><div id="library-filters"></div><div class="library-results-line"><span id="library-result-count" role="status" aria-live="polite">正在整理素材…</span><button class="text-button" data-library-clear>清除筛选</button></div><p id="library-error" class="inline-error" role="alert"></p></section><div class="library-grid" id="library-results"></div><p class="library-note">${icon('folder')}所有素材保存在本地。分类与标签可以随时调整。</p></section>`;
  $('#library-sort').value=libraryView.sort;
  $('#library-search').oninput=e=>{const previous=libraryView.query;libraryView.query=e.target.value;libraryView.revision++;libraryView.controller?.abort();$('#library-result-count').textContent='正在查找…';if(!previous&&libraryView.query&&libraryView.sort==='recent'){libraryView.sort='relevance';$('#library-sort').value='relevance';}clearTimeout(libraryView.timer);libraryView.timer=setTimeout(loadLibrary,220);};
  $('#library-sort').onchange=e=>{libraryView.sort=e.target.value;loadLibrary();};
}
async function loadLibrary(){
  if(state.page!=='library')return;
  if(!$('#library-results'))libraryShell();
  const revision=++libraryView.revision;
  libraryView.controller?.abort();libraryView.controller=new AbortController();
  const query=new URLSearchParams({q:libraryView.query,category:libraryView.category,sort:libraryView.sort});libraryView.tags.forEach(tag=>query.append('tag',tag));
  try{
    const result=await api(`/api/library?${query}`,undefined,{signal:libraryView.controller.signal});
    if(revision!==libraryView.revision||state.page!=='library')return;
    state.packages=result.packages;libraryView.data=result;
    $('#library-error').textContent='';$('#library-total').textContent=num(result.total);
    renderLibraryFilters();renderLibraryResults();
  }catch(e){if(e.name==='AbortError'||revision!==libraryView.revision||state.page!=='library')return;$('#library-error').innerHTML=`素材检索暂未完成。${esc(e.message)}<button class="text-button" data-refresh>重试检索</button>`;$('#library-result-count').textContent='检索失败';}
}
function renderLibraryFilters(){
  const data=libraryView.data;if(!data||!$('#library-filters'))return;
  const focused=document.activeElement,focusCategory=focused?.dataset.libraryCategory,focusTag=focused?.dataset.libraryTag;
  const categories=[...data.categories],tags=[...data.tags];
  if(libraryView.category&&!categories.some(c=>catalogKey(c.name)===catalogKey(libraryView.category)))categories.push({name:libraryView.category,count:0});
  for(const name of libraryView.tags)if(!tags.some(t=>catalogKey(t.name)===catalogKey(name)))tags.push({name,count:0});
  const shown=libraryView.expanded?tags:tags.filter((t,i)=>i<10||libraryView.tags.some(value=>catalogKey(value)===catalogKey(t.name)));
  const html=`<div class="library-filter-row"><span class="filter-label">分类</span><div class="catalog-chips"><button class="catalog-chip" data-library-category="" aria-pressed="${!libraryView.category}">全部 <small>${data.total}</small></button>${categories.map(c=>`<button class="catalog-chip" data-library-category="${esc(c.name)}" aria-pressed="${catalogKey(libraryView.category)===catalogKey(c.name)}">${esc(c.name)} <small>${c.count}</small></button>`).join('')}</div></div><div class="library-filter-row"><span class="filter-label">标签</span><div class="catalog-chips">${shown.map(t=>`<button class="catalog-chip tag-chip" data-library-tag="${esc(t.name)}" aria-pressed="${libraryView.tags.some(value=>catalogKey(value)===catalogKey(t.name))}">${esc(t.name)} <small>${t.count}</small></button>`).join('')||'<span class="fine-print">整理素材后，标签会显示在这里。</span>'}${tags.length>10?`<button class="text-button" data-library-expand>${libraryView.expanded?'收起标签':`全部标签 · ${tags.length}`}</button>`:''}</div></div>`;
  if($('#library-filters').innerHTML!==html){
    $('#library-filters').innerHTML=html;
    if(focusCategory!==undefined)Array.from($('#library-filters').querySelectorAll('[data-library-category]')).find(b=>b.dataset.libraryCategory===focusCategory)?.focus();
    else if(focusTag!==undefined)Array.from($('#library-filters').querySelectorAll('[data-library-tag]')).find(b=>b.dataset.libraryTag===focusTag)?.focus();
  }
  $('#library-result-count').textContent=`${data.matched} / ${data.total} 份素材${libraryView.tags.length>1?' · 同时符合所选标签':''}`;
  $('[data-library-clear]').hidden=!(libraryView.query||libraryView.category||libraryView.tags.length);
}
function renderLibraryResults(){
  const data=libraryView.data;
  $('#library-results').innerHTML=data.packages.map(p=>`<article class="video-card"><button class="card-open" data-open="${esc(p.folder)}" aria-label="学习 ${esc(p.title)}"><div class="card-image">${p.poster?`<img src="${media(p,p.poster)}" alt="" loading="lazy">`:''}<span class="card-play">${icon('play')}</span><span class="duration">${fmt(p.duration)}</span></div><div class="card-body"><div class="creator">${esc(p.creator||'LOCAL COLLECTION')}</div><h3>${esc(p.title)}</h3><div class="card-meta"><span>${p.count} 个精听片段</span><span class="ready-pill">${p.count&&p.analyzed===p.count?'讲解已就绪':p.analyzed?`${p.analyzed}/${p.count} 句已解析`:p.count?'英文字幕就绪':'待补充字幕'}</span></div></div></button><div class="card-organize"><button class="card-category" data-library-category="${esc(p.category)}">${esc(p.category)}</button><button class="text-button" data-organize="${esc(p.folder)}" aria-label="整理 ${esc(p.title)}">${icon('pen')}整理</button></div>${p.tags.length?`<div class="card-tags">${p.tags.slice(0,4).map(t=>`<button data-library-tag="${esc(t)}">${esc(t)}</button>`).join('')}${p.tags.length>4?`<span title="${esc(p.tags.join('，'))}">+${p.tags.length-4}</span>`:''}</div>`:''}${p.catalog_error?'<p class="catalog-warning">整理信息需要检查，请点击「整理」。</p>':''}${p.match?`<button class="catalog-match" data-library-match="${esc(p.folder)}"><span>字幕命中 · ${fmt(p.match.start)}${p.match_count>1?` · 共 ${p.match_count} 句`:''}</span><q lang="en">${esc(p.match.en)}</q>${p.match.zh?`<small>${esc(p.match.zh)}</small>`:''}<em>去听这一句 ↗</em></button>`:''}</article>`).join('')+(data.matched?'':`<div class="library-empty"><span>↳</span><h3>${data.total?'没有找到符合条件的素材':'收藏你的第一段故事'}</h3><p>${data.total?'换个关键词，或放宽分类与标签，试试再找一次。':'添加视频后，会自动获得来源、时长等标签。'}</p><button class="button small" ${data.total?'data-library-clear':'data-new'}>${data.total?'清除筛选':'制作学习素材包'}</button></div>`);
}
function setLibraryCategory(value){libraryView.category=value;loadLibrary();}
function toggleLibraryTag(value){if(libraryView.tags.some(t=>catalogKey(t)===catalogKey(value)))libraryView.tags=libraryView.tags.filter(t=>catalogKey(t)!==catalogKey(value));else if(libraryView.tags.length<16)libraryView.tags.push(value);else return toast('最多同时筛选 16 个标签');loadLibrary();}
function clearLibraryFilters(){libraryView.query='';libraryView.category='';libraryView.tags=[];$('#library-search').value='';clearTimeout(libraryView.timer);loadLibrary();}
function openOrganization(folder){
  const p=libraryView.data?.packages.find(p=>p.folder===folder);if(!p||libraryView.busy)return;
  libraryView.editing=p;$('#catalog-title').textContent=p.title;$('#catalog-category').value=p.category;$('#catalog-tags').value=p.tags.join('，');
  $('#catalog-categories').innerHTML=[...new Set([...libraryView.data.presets,...libraryView.data.categories.map(c=>c.name)])].map(name=>`<option value="${esc(name)}"></option>`).join('');
  const suggestions=[...new Set([...p.suggested.tags,...libraryView.data.tags.map(t=>t.name)])].slice(0,18);
  $('#catalog-suggestions').innerHTML=suggestions.map(tag=>`<button class="catalog-chip" type="button" data-catalog-suggest="${esc(tag)}">＋ ${esc(tag)}</button>`).join('');
  $('#catalog-origin').textContent=p.automatic?'当前使用自动建议。你可以直接修改。':'当前使用你保存的分类和标签。自动更新不会覆盖它们。';
  $('#catalog-error').textContent=p.catalog_error||'';$('#catalog-dialog').showModal();
}
async function saveOrganization(reset=false){
  const p=libraryView.editing;if(!p||libraryView.busy)return;
  libraryView.busy=true;$('#catalog-error').textContent='';$('#catalog-dialog').querySelectorAll('input,textarea,button').forEach(el=>el.disabled=true);
  try{
    const tags=$('#catalog-tags').value.split(/[,，;；\n]+/).map(t=>t.trim()).filter(Boolean);
    await api('/api/library/organize',{folder:p.folder,package_id:p.id,...(reset?{reset:true}:{category:$('#catalog-category').value,tags})});
    $('#catalog-dialog').close();await loadLibrary();toast(reset?'已恢复自动整理':'分类和标签已保存');
  }catch(e){$('#catalog-error').textContent=e.message;}
  finally{libraryView.busy=false;$('#catalog-dialog').querySelectorAll('input,textarea,button').forEach(el=>el.disabled=false);}
}
$('#catalog-form').onsubmit=e=>{e.preventDefault();saveOrganization();};
$('#catalog-reset').onclick=()=>saveOrganization(true);
$('#catalog-dialog').addEventListener('cancel',e=>{if(libraryView.busy)e.preventDefault();});

async function openPackage(folder, target=null){
  const revision=++routeRevision;state.loading=true;setRoute(folder);const saved=await teardown();
  if(revision!==routeRevision)return;
  if(!saved){state.loading=false;keepUnsavedStudy();return;}
  try{
    const [pkg,progress,reviews]=await Promise.all([api(`/api/package?folder=${encodeURIComponent(folder)}`),api(`/api/progress?folder=${encodeURIComponent(folder)}`),api('/api/reviews')]);
    if(revision!==routeRevision)return;
    if(target&&String(pkg.id)!==String(target.package_id))throw new Error('找到了同名素材，但它不是这条摘录的原视频。请重新导入原素材包。');
    if(target&&!pkg.segments.some(s=>s.id===target.segment_id))throw new Error('原素材中的这句话已变更。摘录仍保存在复习库中。');
    state.reviews=reviews.items;
    state.pkg=pkg;state.progress={favorites:[],mastered:[],notes:{},dictations:{},...progress};
    for(const key of ['favorites','mastered'])if(!Array.isArray(state.progress[key]))state.progress[key]=[];
    for(const key of ['notes','dictations'])if(!state.progress[key]||typeof state.progress[key]!=='object')state.progress[key]={};
    state.current=Math.max(0,pkg.segments.findIndex(s=>s.id===progress.lastSegment));
    state.mode=progress.mode==='focus'?'focus':'watch';state.subtitles=['none','en','both'].includes(progress.subtitles)?progress.subtitles:'en';
    state.loop=!!progress.loop;state.analysisOpen=false;state.reveal=false;state.filter='all';state.search='';state.page='study';
    if(target){state.current=pkg.segments.findIndex(s=>s.id===target.segment_id);state.mode='focus';state.subtitles='both';state.analysisOpen=true;state.progress.lastTime=current().start;}
    setNavigation('study');renderStudy();
  }catch(e){if(revision===routeRevision){setRoute(state.page==='review'?'review':state.pkg?.folder||'library');toast(target?`暂时无法回到原句。${e.message}`:e.message);}}finally{if(revision===routeRevision)state.loading=false;}
}

function renderStudy(){
  const p=state.pkg;
  $('#app').innerHTML=`<section class="study"><div class="study-top"><div class="breadcrumb"><button data-home>${icon('back')}素材库</button><span>/</span><span>学习空间</span></div><span id="save-status" class="saved-indicator">${icon('check')}进度自动保存</span></div><h1 class="study-title">${esc(p.title)}</h1><div class="study-meta"><span>${esc(p.creator)}</span><span>·</span><span>${fmt(p.duration)}</span><span>·</span><span>${p.segments.length} 个精听片段</span><span>·</span><span>${esc(p.transcription?'本地原音识别':(p.caption_source||'英文字幕'))}</span></div><div id="study-jobs"></div>${p.notice?`<div class="study-notice">${esc(p.notice)}</div>`:''}<div class="study-layout"><div class="study-main"><div class="mode-row"><div class="segmented" aria-label="播放模式"><button data-mode="watch">完整观看</button><button data-mode="focus" ${p.segments.length?'':'disabled'}>逐句精听</button></div><div class="subtitle-select"><span>字幕</span><div class="segmented" aria-label="字幕模式"><button data-sub="none">无字幕</button><button data-sub="en">英文</button><button data-sub="both">中英对照</button></div></div></div><div class="player-wrap" id="player-wrap"><video id="video" src="${media(p,p.video)}" ${p.poster?`poster="${media(p,p.poster)}"`:''} preload="metadata" playsinline aria-label="${esc(p.title)}"></video><div class="player-overlay" id="player-overlay"><button class="big-play" id="big-play" aria-label="播放视频">${icon('play')}</button></div><div class="video-subtitles" aria-live="off"><div class="en" id="sub-en"></div><div class="zh" id="sub-zh"></div></div><button class="fullscreen-exit" data-exit-fullscreen>退出全屏</button></div><div class="player-controls"><input class="timeline" id="timeline" type="range" min="0" max="${p.duration||1}" step="0.05" value="0" aria-label="视频播放进度"><div class="controls-row"><button class="icon-button" id="play-toggle" aria-label="播放">${icon('play')}</button><button class="icon-button" id="replay" aria-label="重听本句 (R)" title="重听本句 · R">${icon('replay')}</button><span class="timecode" id="timecode">00:00 / ${fmt(p.duration)}</span><div class="controls-right"><button class="loop-button" id="loop-toggle" aria-label="循环本句 (L)" title="循环本句 · L">${icon('repeat')}<span>循环</span></button><select id="rate" class="rate-select" aria-label="播放速度">${[.5,.75,.85,1,1.25,1.5].map(n=>`<option value="${n}">${n}×</option>`).join('')}</select><button class="icon-button" id="mute-toggle" aria-label="静音">${icon('sound')}</button><button class="icon-button" id="fullscreen" aria-label="全屏">${icon('expand')}</button></div></div></div><div class="focus-strip"><span id="focus-label">先听懂故事，再慢慢走进每一句。</span><div class="focus-keys"><button class="icon-button" id="prev" aria-label="上一句">${icon('back')}</button><kbd>←</kbd><span id="sentence-counter"></span><kbd>→</kbd><button class="icon-button" id="next" aria-label="下一句">${icon('next')}</button></div></div><div id="lesson"></div></div><aside class="transcript-panel"><div class="panel-header"><div class="panel-title"><h3>故事里的每一句</h3><small id="list-count">${p.segments.length} 个片段</small></div><div class="filter-tabs"><button class="active" data-filter="all">全部语句</button><button data-filter="favorites">已收藏</button><button data-filter="unlearned">待练习</button></div><div class="search-box">${icon('search')}<input id="sentence-search" placeholder="搜索语句或表达…" aria-label="搜索语句"></div></div><div class="sentence-list" id="sentence-list"></div><div class="progress-footer"><span id="mastery-count"></span><div class="mini-progress"><span id="mastery-bar"></span></div></div></aside></div></section>`;
  const video=$('#video');video.playbackRate=[.5,.75,.85,1,1.25,1.5].includes(state.progress.rate)?state.progress.rate:1;$('#rate').value=video.playbackRate;
  video.addEventListener('loadedmetadata',()=>{
    $('#timeline').max=video.duration;
    let t=Number(state.progress.lastTime)||0;
    if(state.mode==='focus'&&current()&&(t<current().start||t>=current().end))t=current().start;
    video.currentTime=Math.min(Math.max(0,t),video.duration||0);tick();
  },{once:true});
  video.addEventListener('play',()=>{playVisual(true);animate();});
  video.addEventListener('pause',()=>{playVisual(false);persist();});
  video.addEventListener('timeupdate',tick);
  video.addEventListener('seeked',()=>{tick();persist();});
  video.addEventListener('ended',()=>{playVisual(false);persist();});
  video.addEventListener('error',()=>toast('视频无法播放，请确认素材包中的视频完整，并使用新版 Chrome 或 Safari。'));
  video.onclick=togglePlay;$('#big-play').onclick=togglePlay;$('#play-toggle').onclick=togglePlay;
  $('#replay').onclick=replay;$('#prev').onclick=()=>selectSentence(state.current-1);$('#next').onclick=()=>selectSentence(state.current+1);
  $('#loop-toggle').onclick=toggleLoop;
  $('#rate').onchange=e=>{video.playbackRate=Number(e.target.value);state.progress.rate=video.playbackRate;persist();};
  $('#mute-toggle').onclick=()=>{video.muted=!video.muted;$('#mute-toggle').innerHTML=icon(video.muted?'muted':'sound');$('#mute-toggle').setAttribute('aria-label',video.muted?'取消静音':'静音');};
  $('#fullscreen').onclick=async()=>{try{await $('#player-wrap').requestFullscreen();}catch{toast('当前浏览器不支持全屏');}};
  $('#timeline').oninput=e=>{clearTimeout(state.loopTimer);if(state.mode==='focus')setMode('watch',false);video.currentTime=Number(e.target.value);tick();};
  $('#sentence-search').oninput=e=>{state.search=e.target.value;renderList();};
  updateModes();renderList();renderLesson();updateMastery();tick();
}

function playVisual(playing){if(!$('#play-toggle'))return;$('#play-toggle').innerHTML=icon(playing?'pause':'play');$('#play-toggle').setAttribute('aria-label',playing?'暂停':'播放');$('#player-overlay').hidden=playing;}
async function play(){try{await $('#video')?.play();}catch(e){if(e.name!=='AbortError')toast('请点击视频上的播放按钮开始');}}
function togglePlay(){const v=$('#video');if(!v)return;clearTimeout(state.loopTimer);if(v.paused){if(state.mode==='focus'&&current()&&(v.currentTime>=current().end-.05||v.currentTime<current().start-.3))v.currentTime=Math.max(0,current().start-.1);play();}else v.pause();}
function setMode(mode,seek=true){state.mode=mode;clearTimeout(state.loopTimer);if(mode==='focus'&&seek&&current()){$('#video').currentTime=Math.max(0,current().start-.1);}updateModes();persist();}
function updateModes(){
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===state.mode)));
  document.querySelectorAll('[data-sub]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.sub===state.subtitles)));
  $('#loop-toggle')?.setAttribute('aria-pressed',String(state.loop));
  if($('#focus-label'))$('#focus-label').innerHTML=state.mode==='focus'?`<b>精听模式</b> · ${state.loop?'循环这一句，直到听清楚':'一句结束后自动暂停'}`:'完整观看 · 跟着故事，慢慢听。';
  if($('#sentence-counter'))$('#sentence-counter').textContent=`${current()?num(state.current+1):'00'} / ${num(state.pkg.segments.length)}`;
  if($('#prev'))$('#prev').disabled=state.current<=0;
  if($('#next'))$('#next').disabled=state.current>=state.pkg.segments.length-1;
}
function setSubtitles(mode){state.subtitles=mode;state.reveal=false;if(mode==='none')state.analysisOpen=false;updateModes();renderList();renderLesson();updateSubtitle();persist();}
function toggleLoop(){state.loop=!state.loop;if(state.loop&&current())setMode('focus');clearTimeout(state.loopTimer);updateModes();persist();}
function selectSentence(index,autoplay=true){
  if(!state.pkg||index<0||index>=state.pkg.segments.length)return;
  clearTimeout(state.loopTimer);state.current=index;state.mode='focus';state.reveal=false;
  if(state.subtitles==='none')state.analysisOpen=false;
  $('#video').currentTime=Math.max(0,current().start-.1);updateModes();renderList();renderLesson();updateSubtitle();persist();if(autoplay)play();
}
function replay(){if(!current()){$('#video').currentTime=0;play();return;}selectSentence(state.current);}
function activeIndex(t){
  const rows=state.pkg?.segments||[];let lo=0,hi=rows.length-1,index=-1;
  while(lo<=hi){const mid=(lo+hi)>>1;if(rows[mid].start<=t){index=mid;lo=mid+1;}else hi=mid-1;}
  return index>=0&&t<rows[index].end?index:-1;
}
function updateSubtitle(){
  const v=$('#video');if(!v)return;const i=activeIndex(v.currentTime),s=state.pkg.segments[i];
  const en=state.subtitles!=='none'&&s?s.en:'',zh=state.subtitles==='both'&&s?s.zh||'':'';
  if($('#sub-en').textContent!==en)$('#sub-en').textContent=en;if($('#sub-zh').textContent!==zh)$('#sub-zh').textContent=zh;
}
function tick(){
  const v=$('#video');if(!v||!state.pkg)return;
  if(state.mode==='focus'&&current()&&!v.paused&&v.currentTime>=current().end){
    v.pause();v.currentTime=Math.max(current().start,current().end-.025);
    if(state.loop){clearTimeout(state.loopTimer);state.loopTimer=setTimeout(()=>{if(state.page==='study'&&state.mode==='focus'&&state.loop){v.currentTime=Math.max(0,current().start-.1);play();}},650);}
  }
  const i=activeIndex(v.currentTime);
  if(state.mode==='watch'&&i>=0&&i!==state.current){
    state.current=i;state.reveal=false;if(state.subtitles==='none')state.analysisOpen=false;
    renderLesson();highlightCurrent();updateModes();
  }
  updateSubtitle();if(document.activeElement!==$('#timeline'))$('#timeline').value=v.currentTime;
  $('#timecode').textContent=`${fmt(v.currentTime)} / ${fmt(Math.ceil(v.duration||state.pkg.duration))}`;
}
function animate(){cancelAnimationFrame(state.frame);function frame(){tick();if($('#video')&&!$('#video').paused)state.frame=requestAnimationFrame(frame);}state.frame=requestAnimationFrame(frame);}

function renderList(){
  if(!state.pkg||!$('#sentence-list'))return;
  const list=state.pkg.segments.map((s,index)=>({s,index})).filter(({s})=>(state.filter!=='favorites'||state.progress.favorites.includes(s.id))&&(state.filter!=='unlearned'||!state.progress.mastered.includes(s.id))&&(!state.search||`${s.en} ${s.zh}`.toLowerCase().includes(state.search.toLowerCase())));
  $('#list-count').textContent=`${list.length} 个片段`;
  $('#sentence-list').innerHTML=list.map(({s,index})=>`<button class="sentence-row ${index===state.current?'active':''}" data-sentence="${index}" aria-label="播放第 ${index+1} 句" ${index===state.current?'aria-current="true"':''}><span class="sentence-number">${num(index+1)}</span><span><span class="sentence-text ${state.subtitles==='none'?'blind':''}">${state.subtitles==='none'?'先听一听这一句':esc(s.en)}</span>${state.subtitles==='both'&&s.zh?`<span class="sentence-zh" style="display:block">${esc(s.zh)}</span>`:''}<span class="sentence-bottom">${fmt(s.start)} — ${fmt(s.end)}<span class="marks">${state.progress.favorites.includes(s.id)?icon('star'):''}${state.progress.mastered.includes(s.id)?icon('check'):''}</span></span></span></button>`).join('')||`<div class="empty">${state.pkg.segments.length?'这里还没有符合条件的语句。':'还没有英文字幕。<br>导入字幕后就可以逐句练习。'}</div>`;
  document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===state.filter));
}
function highlightCurrent(){
  document.querySelectorAll('.sentence-row').forEach(row=>{const active=Number(row.dataset.sentence)===state.current;row.classList.toggle('active',active);if(active)row.setAttribute('aria-current','true');else row.removeAttribute('aria-current');});
  const active=$('.sentence-row.active'),container=$('#sentence-list');if(active&&container){const top=active.offsetTop-container.offsetTop;if(top<container.scrollTop||top+active.offsetHeight>container.scrollTop+container.clientHeight)container.scrollTo({top:top-container.clientHeight/3,behavior:'smooth'});}
}
function updateMastery(){const n=state.pkg.segments.filter(s=>state.progress.mastered.includes(s.id)).length;$('#mastery-count').textContent=`已掌握 ${n} / ${state.pkg.segments.length} 句`;$('#mastery-bar').style.width=`${n/Math.max(1,state.pkg.segments.length)*100}%`;}

function renderLesson(){
  const s=current();if(!$('#lesson'))return;clearSelection();
  if(!s){$('#lesson').innerHTML=`<div class="analysis-empty">视频已经可以完整播放。添加英文字幕后，就能解锁逐句精听与讲解。<br><button class="button" data-caption>导入英文字幕</button> <button class="button" data-analyze>识别语音并生成简单讲解</button><p class="fine-print">本地识别需先运行 tool/安装语音转写.command；首次运行会下载语音模型。</p></div>`;return;}
  const visible=state.subtitles!=='none'||state.reveal, favorite=state.progress.favorites.includes(s.id),mastered=state.progress.mastered.includes(s.id);
  $('#lesson').innerHTML=`<div class="lesson-top"><div class="eyebrow">ONE SENTENCE AT A TIME / ${num(state.current+1)}</div><div class="lesson-actions"><button data-favorite aria-pressed="${favorite}">${icon('star')}${favorite?'已收藏':'收藏'}</button><button data-mastered aria-pressed="${mastered}">${icon('check')}${mastered?'已掌握':'标记掌握'}</button></div></div>${visible?`<p class="current-quote">${esc(s.en)}</p>${state.subtitles==='both'||state.reveal?`<p class="current-translation">${esc(s.zh||'中文翻译尚未生成，可在下方补全讲解。')}</p>`:''}`:`<div class="blind-prompt"><h3>先让耳朵，找到答案。</h3><p>反复听这一句，试着抓住几个熟悉的词。不着急。</p><button class="button small" data-reveal>准备好了，查看原文</button></div>`}<details class="dictation" ${state.progress.dictations[s.id]?'open':''}><summary>${icon('pen')}写下我听到的 · 听写练习</summary><textarea id="dictation" placeholder="Type what you hear…" aria-label="本句听写" spellcheck="false">${esc(state.progress.dictations[s.id]||'')}</textarea><div class="dictation-bottom"><button class="button small" data-check-dictation>对照答案</button><span id="dictation-result"></span></div><p class="fine-print" id="dictation-answer" hidden></p></details><button class="analysis-toggle" id="analysis-toggle" aria-expanded="${state.analysisOpen}"><span>把这一句，听明白<span class="tag">词句讲解</span></span>${icon('chevron')}</button><div id="analysis-content" class="analysis-content" ${state.analysisOpen?'':'hidden'}>${analysisMarkup(s)}</div><label class="note-label" for="personal-note">留给自己的笔记 <span>· 自动保存</span></label><textarea class="personal-note" id="personal-note" placeholder="记下一点发现，或写一个自己的句子…">${esc(state.progress.notes[s.id]||'')}</textarea>`;
  $('.lesson-actions').insertAdjacentHTML('beforeend',`<button data-listening-mark>${icon('sound')}这句没听懂</button>`);
  $('#dictation').oninput=e=>{state.progress.dictations[s.id]=e.target.value;persist();};
  $('#personal-note').oninput=e=>{state.progress.notes[s.id]=e.target.value;persist();};
  $('#analysis-toggle').onclick=()=>{state.analysisOpen=!state.analysisOpen;$('#analysis-toggle').setAttribute('aria-expanded',state.analysisOpen);$('#analysis-content').hidden=!state.analysisOpen;if(!state.analysisOpen)clearSelection();};
  applyHighlights();
}
function analysisMarkup(s){
  const a=s.analysis,detailed=Number.isInteger(s.analysis_version)&&s.analysis_version>=2;
  if(!a)return `<div class="analysis-empty">这句话还没有讲解。先生成简明的翻译与重点词句，需要时再升级这一句。<br><button class="button primary small" data-analyze>补全简单讲解</button><p class="fine-print">使用当前 ChatGPT 账号的 Codex 用量，只补充尚未完成的语句。</p></div>`;
  const leaf=(field,text,tag='span',className='')=>`<${tag} class="review-text ${className}" data-review-field="${esc(field)}">${esc(text)}</${tag}>`;
  const save=(fields)=>`<button class="save-fragment" data-save-fields="${esc(fields.join(','))}" aria-label="将这一条讲解保存到复习库">${icon('pen')}保存这一条</button>`;
  const group=(name,title,key,fields)=>Array.isArray(a[key])&&a[key].length?`<section class="analysis-section"><h3 class="section-label">${icon(name)}${title}<span>${a[key].length}</span></h3>${a[key].map((row,index)=>{const paths=fields.map(([field])=>`analysis.${key}.${index}.${field}`);return `<article class="analysis-item">${fields.map(([field,tag,className])=>leaf(`analysis.${key}.${index}.${field}`,row[field],tag,className)).join('')}${save(paths)}</article>`;}).join('')}</section>`:'';
  return `<div class="analysis-toolbar"><div><span class="level-tag">${detailed?'入门精讲 · 一点点学':'简单讲解'}</span><p>划选想记住的文字，再保存到复习库。也可用每条下方的保存按钮。</p></div><button class="button small" data-save-selection disabled>${icon('pen')}保存划选内容</button></div>${!detailed?`<div class="upgrade-note"><span>这份简单讲解可以直接学习。需要更细的解释时，再升级这一句，会使用 Codex 用量。</span><button class="button small" data-upgrade-sentence>升级这一句精讲</button></div>`:''}<div class="meaning"><span class="meaning-title">先懂这句话在说什么</span>${leaf('analysis.meaning',a.meaning,'div')}${save(['analysis.meaning'])}</div>${group('lines','一句一句拆开','sentence_parts',[['chunk','strong',''],['meaning','p',''],['role','p','part-role']])}<div class="analysis-columns"><div>${group('book',detailed?'基础单词 · 小词也学会':'重点词句','vocabulary',[['term','strong',''],['meaning','p',''],['example','p','example']])}${group('link','常用短语 · 放在一起记','phrases',[['term','strong',''],['meaning','p',''],['example','p','example']])}</div><div>${group('lines','句子怎么组成','grammar',[['pattern','strong',''],['explanation','p','']])}${group('sound','耳朵可以留意的细节','speech',[['text','strong',''],['tip','p','']])}<p class="speech-note">发音提示基于常见语言规律生成，具体读法请对照原音。</p></div></div>${a.paraphrase?`<div class="practice-card"><small>换个简单的说法</small>${leaf('analysis.paraphrase',a.paraphrase,'div')}${save(['analysis.paraphrase'])}</div>`:''}${a.practice?`<div class="practice-card"><small>换你来说 · 小小练习</small>${leaf('analysis.practice',a.practice,'div')}${save(['analysis.practice'])}</div>`:''}`;
}

function clearSelection(){state.selection=null;$('#selection-toolbar').hidden=true;document.querySelectorAll('[data-save-selection]').forEach(b=>b.disabled=true);}
function showSelection(selections,rect){
  if(!selections.length){clearSelection();return;}
  if(selections.length>30||selections.reduce((n,s)=>n+Array.from(s.quote).length,0)+selections.length-1>12000){clearSelection();toast('这次划选有点长，请分成更小的几段保存。');return;}
  state.selection={folder:state.pkg.folder,segment_id:current().id,selections};
  document.querySelectorAll('[data-save-selection]').forEach(b=>b.disabled=false);
  const toolbar=$('#selection-toolbar');toolbar.hidden=false;
  const width=toolbar.offsetWidth;toolbar.style.left=`${Math.max(12,Math.min(rect.left+(rect.width-width)/2,innerWidth-width-12))}px`;
  toolbar.style.top=`${Math.max(12,Math.min(rect.bottom+9,innerHeight-toolbar.offsetHeight-12))}px`;
}
function captureSelection(){
  if(state.page!=='study'||!state.analysisOpen||state.reviewSaving)return;
  const selection=window.getSelection(),content=$('#analysis-content');
  if(!selection?.rangeCount||selection.isCollapsed){if(!document.activeElement?.closest('[data-save-selection]'))clearSelection();return;}
  const range=selection.getRangeAt(0);
  if(!content?.contains(range.startContainer)||!content.contains(range.endContainer)){clearSelection();return;}
  const selections=[];
  for(const leaf of content.querySelectorAll('[data-review-field]')){
    if(!range.intersectsNode(leaf))continue;
    const part=document.createRange();part.selectNodeContents(leaf);
    if(leaf.contains(range.startContainer))part.setStart(range.startContainer,range.startOffset);
    if(leaf.contains(range.endContainer))part.setEnd(range.endContainer,range.endOffset);
    const quote=part.toString();if(!quote.trim())continue;
    const prefix=document.createRange();prefix.selectNodeContents(leaf);prefix.setEnd(part.startContainer,part.startOffset);
    const start=Array.from(prefix.toString()).length;
    selections.push({field:leaf.dataset.reviewField,start,end:start+Array.from(quote).length,quote});
  }
  showSelection(selections,range.getBoundingClientRect());
}
function fullSelections(fields){
  const leaves=[...document.querySelectorAll('#analysis-content [data-review-field]')];
  return fields.map(field=>{const quote=leaves.find(node=>node.dataset.reviewField===field)?.textContent||'';return {field,start:0,end:Array.from(quote).length,quote};}).filter(s=>s.quote.trim());
}
async function saveSelection(payload=state.selection){
  if(!payload?.selections.length){toast('先在讲解中划选文字，或点击某条讲解下方的保存按钮。');return;}
  if(state.reviewSaving)return;
  state.reviewSaving=true;
  try{
    const result=await api('/api/reviews/add',payload);
    reviewRevision++;
    state.reviews=[result.item,...state.reviews.filter(item=>item.id!==result.item.id)];
    clearSelection();window.getSelection()?.removeAllRanges();applyHighlights();
    if(state.page==='review')renderReviewList();
    toast(result.existing?'这段已经在复习库里了':'已保存到复习库，慢慢复习就好。');
  }finally{state.reviewSaving=false;}
}
function applyHighlights(){
  if(!state.pkg||!current())return;
  const items=state.reviews.filter(item=>item.active!==false&&String(item.source.package_id)===String(state.pkg.id)&&item.source.segment_id===current().id);
  for(const leaf of document.querySelectorAll('#analysis-content [data-review-field]')){
    const value=leaf.textContent,chars=Array.from(value),ranges=[];
    for(const item of items)for(const selection of item.selections||[]){
      if(selection.field===leaf.dataset.reviewField&&selection.context===value&&Number.isInteger(selection.start)&&Number.isInteger(selection.end)&&selection.start>=0&&selection.end>selection.start&&selection.end<=chars.length&&chars.slice(selection.start,selection.end).join('')===selection.quote)ranges.push([selection.start,selection.end]);
    }
    ranges.sort((a,b)=>a[0]-b[0]);const merged=[];
    for(const range of ranges){const last=merged.at(-1);if(last&&range[0]<=last[1])last[1]=Math.max(last[1],range[1]);else merged.push([...range]);}
    let cursor=0,markup='';
    for(const [start,end] of merged){markup+=esc(chars.slice(cursor,start).join(''))+`<mark class="review-highlight" title="已保存到复习库">${esc(chars.slice(start,end).join(''))}</mark>`;cursor=end;}
    leaf.innerHTML=markup+esc(chars.slice(cursor).join(''));
  }
}
document.addEventListener('selectionchange',()=>{if(!state.reviewSaving)captureSelection();});
document.addEventListener('pointerup',e=>{if(!e.target.closest('[data-save-selection]'))captureSelection();});
document.addEventListener('pointerdown',e=>{if(e.target.closest('[data-save-selection]'))e.preventDefault();});
window.addEventListener('resize',()=>{if(state.selection)captureSelection();});
document.addEventListener('scroll',()=>{if(state.selection)captureSelection();},true);

const reviewDate=value=>{if(!value)return '还没有复习';const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleDateString('zh-CN',{month:'long',day:'numeric'});};
const reviewCategories={word:'单词',phrase:'短语',sentence:'句子'};
const reviewCategory=item=>item.kind==='favorite'?'sentence':Object.hasOwn(reviewCategories,item.category)?item.category:'sentence';
function reviewEnglish(item){
  const english=text=>(String(text||'').match(/[\p{Script=Latin}\p{N}][\p{Script=Latin}\p{N}\s'’"“”.,!?;:()\/+&\-–—…]*/gu)||[]).map(part=>part.trim().replace(/^[.,;:!?\s]+|[;:,\s]+$/g,'')).filter(part=>/\p{Script=Latin}/u.test(part)).join(' · ');
  if(item.kind==='favorite')return english(item.source.en)||'…';
  if(english(item.english))return english(item.english);
  const selected=(item.selections||[]).map(selection=>({field:selection.field,text:english(selection.quote)})).filter(selection=>selection.text);
  const terms=selected.filter(selection=>/\.(?:term|chunk|pattern|text)$/.test(selection.field));
  const texts=(terms.length?terms:selected).map(selection=>selection.text);
  return [...new Set(texts)].join(' · ')||english(item.source.en)||'…';
}
async function openReviews(){
  const fresh=state.page!=='review',revision=++routeRevision;setRoute('review');const saved=await teardown();if(revision!==routeRevision)return;
  if(!saved){keepUnsavedStudy();return;}
  if(fresh)state.reviewExpanded.clear();
  state.page='review';state.reviewMode='text';state.pkg=null;state.reviewDelete=null;setNavigation('review');
  $('#app').innerHTML='<div class="loading"><span class="spinner"></span>正在翻开你的复习库…</div>';
  try{
    let result,before;
    do{before=reviewRevision;result=await api('/api/reviews');if(revision!==routeRevision)return;}while(before!==reviewRevision);
    state.reviews=result.items;
    try{const catalog=await api('/api/listening');if(revision!==routeRevision)return;acceptListeningCatalog(catalog);}catch{listening.loaded=false;}
    if(revision!==routeRevision)return;renderReviews();
  }
  catch(e){if(revision===routeRevision){$('#app').innerHTML='<div class="empty">暂时无法打开复习库。<br><button class="button small" data-review-refresh>重新加载</button></div>';toast(e.message);}}
}
function renderReviews(){
  $('#app').innerHTML=`<section class="review-page"><div class="review-heading"><div><div class="eyebrow">A LITTLE TO KEEP, A LITTLE TO REVISIT.</div><h1>让记住的，<em>留得久一点。</em></h1><p>划下的表达、收藏的句子，都在这里。先看英语想一想，再点开慢慢复习。</p></div><div class="review-total"><strong>${num(state.reviews.filter(item=>item.active!==false).length)}</strong><span>份小小的积累</span></div></div>${reviewModeMarkup()}<div class="review-category-bar"><div class="segmented review-categories" aria-label="复习内容分类"><button data-review-category="all">全部内容</button>${Object.entries(reviewCategories).map(([value,label])=>`<button data-review-category="${value}">${label}</button>`).join('')}</div></div><div class="review-tools"><div class="filter-tabs review-filters" aria-label="复习状态"><button data-review-filter="all">全部状态</button><button data-review-filter="unmastered">继续练习</button><button data-review-filter="mastered">已经掌握</button></div><div class="search-box review-search">${icon('search')}<input id="review-search" type="search" value="${esc(state.reviewSearch)}" placeholder="找一个词、一条笔记或一个故事…" aria-label="搜索复习内容、笔记和来源"></div><button class="text-button" data-review-refresh aria-label="刷新复习库">${icon('replay')}</button></div><div id="review-summary" class="review-summary" aria-live="polite"></div><div id="review-list" class="review-list"></div><p class="library-note">${icon('folder')}收藏、摘录与笔记保存在本地 review 文件夹。即使原视频暂时不在，也能继续复习。</p></section>`;
  $('#review-search').oninput=e=>{state.reviewSearch=e.target.value;renderReviewList();};renderReviewList();
}
function renderReviewList(){
  if(state.page!=='review'||!$('#review-list'))return;
  document.querySelectorAll('[data-review-card]').forEach(card=>{if(card.open)state.reviewExpanded.add(card.dataset.reviewCard);else state.reviewExpanded.delete(card.dataset.reviewCard);});
  const all=state.reviews.filter(item=>item.active!==false);
  $('.review-total strong').textContent=num(all.length);
  const query=state.reviewSearch.trim().toLocaleLowerCase();
  const items=all.filter(item=>(state.reviewFilter!=='unmastered'||!item.mastered)&&(state.reviewFilter!=='mastered'||item.mastered)&&(state.reviewCategory==='all'||reviewCategory(item)===state.reviewCategory)&&(!query||[reviewEnglish(item),item.quote,item.note,state.reviewDrafts[item.id],item.source.package_title,item.source.en,item.source.zh].join(' ').toLocaleLowerCase().includes(query)));
  document.querySelectorAll('[data-review-filter]').forEach(b=>{const active=b.dataset.reviewFilter===state.reviewFilter;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
  document.querySelectorAll('[data-review-category]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.reviewCategory===state.reviewCategory)));
  $('#review-summary').textContent=`${items.length} 条内容 · ${all.filter(item=>item.mastered).length} 条已掌握`;
  $('#review-list').innerHTML=items.length?items.map(item=>{
    const src=item.source,favorite=item.kind==='favorite',note=Object.hasOwn(state.reviewDrafts,item.id)?state.reviewDrafts[item.id]:item.note||'';
    const contexts=(item.selections||[]).filter((s,i,arr)=>arr.findIndex(row=>row.field===s.field)===i);
    return `<details class="review-card ${item.mastered?'is-mastered':''}" data-review-card="${esc(item.id)}" ${state.reviewExpanded.has(item.id)?'open':''}><summary class="review-card-summary"><span lang="en">${esc(reviewEnglish(item))}</span>${icon('chevron')}</summary><div class="review-card-body"><div class="review-card-top"><span class="review-source-title">${esc(src.package_title)}</span><span class="review-status">${item.mastered?icon('check')+' 已掌握':'慢慢记住'}</span></div><div class="review-item-category">${favorite?`<span class="favorite-label">${icon('star')}收藏的句子</span>`:`<label for="review-category-${esc(item.id)}">这条内容是</label><select id="review-category-${esc(item.id)}" data-review-category-select="${esc(item.id)}" aria-label="这条复习内容的分类">${Object.entries(reviewCategories).map(([value,label])=>`<option value="${value}" ${reviewCategory(item)===value?'selected':''}>${label}</option>`).join('')}</select>`}</div>${favorite?`<p class="review-translation">${esc(src.zh||'这句话暂时还没有中文翻译。')}</p>`:`<blockquote>${esc(item.quote)}</blockquote>`}${contexts.length?`<details class="review-context"><summary>看看摘录前后的讲解</summary>${contexts.map(s=>`<p>${esc(s.context)}</p>`).join('')}</details>`:''}<div class="review-source"><p class="review-source-caption">来自故事里的这一句 · ${fmt(src.start)}</p>${favorite?'':`<p lang="en">${esc(src.en)}</p><p class="muted">${esc(src.zh)}</p>`}<button class="text-button" data-review-source="${esc(item.id)}">${icon('play')}回到原视频这一句</button></div><label class="note-label" for="review-note-${esc(item.id)}">用自己的话记一记</label><textarea class="personal-note review-note" id="review-note-${esc(item.id)}" data-review-note="${esc(item.id)}" placeholder="这个词让我想到什么？下次想怎么用？" maxlength="10000">${esc(note)}</textarea><div class="review-note-actions"><button class="text-button" data-review-save-note="${esc(item.id)}">保存笔记</button><span class="review-note-status" data-note-status="${esc(item.id)}" aria-live="polite">${note!==(item.note||'')?'笔记尚未保存':''}</span></div><div class="review-card-footer"><span>复习 ${Number(item.review_count)||0} 次 · ${item.last_reviewed_at?'上次 '+reviewDate(item.last_reviewed_at):'第一次，从今天开始'}</span><div class="review-actions"><button class="button small" data-review-studied="${esc(item.id)}">今天复习过了</button><button class="button small" aria-pressed="${!!item.mastered}" data-review-mastered="${esc(item.id)}">${icon('check')}${item.mastered?'继续练习':'标记掌握'}</button><button class="text-button review-remove" data-review-remove="${esc(item.id)}">${favorite?'移除并取消收藏':'移除'}</button></div></div>${state.reviewDelete===item.id?`<div class="review-delete-confirm" role="alert"><span>${favorite?'取消收藏后，这张卡片会从复习库隐藏。已保存的笔记与复习记录会保留，再次收藏时可以接着使用。':'移除这条摘录和它的笔记？原素材会保留。'}</span><button class="button small" data-review-keep="${esc(item.id)}">保留</button><button class="button small danger" data-review-delete="${esc(item.id)}">${favorite?'移除并取消收藏':'确认移除'}</button></div>`:''}</div></details>`;
  }).join(''):`<div class="review-empty"><span class="review-empty-mark">“</span><h2>${all.length?'暂时没有这样的内容':'喜欢的一句，值得再见。'}</h2><p>${all.length?'换个关键词、内容分类，或看看其他复习状态。':'收藏视频里的句子，或在精讲中划选想记住的单词、短语和解释。它们都会来到这里。'}</p><button class="button small" ${all.length?'data-review-reset':'data-home'}>${all.length?'查看全部内容':'去素材库听一听'}</button></div>`;
}
document.addEventListener('toggle',e=>{const card=e.target;if(!card.matches?.('[data-review-card]')||!card.isConnected)return;if(card.open)state.reviewExpanded.add(card.dataset.reviewCard);else state.reviewExpanded.delete(card.dataset.reviewCard);},true);

function acceptListeningCatalog(result){listening.items=result.items;listening.loaded=true;const time=Date.parse(result.now);listening.offset=Number.isFinite(time)?time-Date.now():0;}
function listeningDue(item){return !item.due_at||Date.parse(item.due_at)<=Date.now()+listening.offset;}
function reviewModeMarkup(){const due=listening.loaded?listening.items.filter(listeningDue).length:'—';return `<div class="review-mode-tabs segmented" aria-label="复习方式"><button data-review-mode="text" aria-pressed="${state.reviewMode!=='listening'}">${icon('book')}文字复习</button><button data-review-mode="listening" aria-pressed="${state.reviewMode==='listening'}">${icon('sound')}听力复习 <span class="listening-due-count">今日 ${due}</span></button></div>`;}
function stopListening(){
  listening.epoch++;cancelAnimationFrame(listening.frame);clearTimeout(listening.timer);
  if(listening.audio){listening.audio.pause();listening.audio.removeAttribute('src');listening.audio.load();listening.audio=null;}
  if(listening.active){listening.active.playing=false;listening.active.ready=false;}
}
function pauseListening(){cancelAnimationFrame(listening.frame);clearTimeout(listening.timer);listening.audio?.pause();if(listening.active)listening.active.playing=false;updateListeningControls();}
async function openListening(){
  const fresh=state.page!=='review'||state.reviewMode!=='listening',revision=++routeRevision;
  setRoute('review-listening');const saved=await teardown();if(revision!==routeRevision)return;if(!saved){keepUnsavedStudy();return;}
  if(fresh){listening.completed.clear();listening.skipped.clear();listening.attempts.clear();listening.feedback='';listening.mode='due';}
  state.page='review';state.reviewMode='listening';state.pkg=null;setNavigation('review');
  renderListeningShell();$('#listening-workspace').innerHTML='<div class="loading"><span class="spinner"></span>正在整理今天的原声练习…</div>';
  try{const result=await api('/api/listening');if(revision!==routeRevision)return;acceptListeningCatalog(result);for(const id of listening.completed){const item=listening.items.find(row=>row.id===id);if(item&&listeningDue(item)){listening.completed.delete(id);listening.attempts.delete(id);}}buildListeningQueue();}
  catch(error){if(revision===routeRevision){$('#listening-workspace').innerHTML='<div class="listening-empty"><h2>暂时没能打开听力库</h2><p>原来的摘录和笔记都还在。连接恢复后，再试一次就好。</p><button class="button small" data-listening-refresh>重新加载</button></div>';toast(error.message);}}
}
function renderListeningShell(){
  $('#app').innerHTML=`<section class="review-page listening-page"><div class="review-heading"><div><div class="eyebrow">LISTEN FIRST. TAKE YOUR TIME.</div><h1>先用耳朵，<em>再找答案。</em></h1><p>听来源整句原声，试着自己理解。听完后再决定：记住了，还是再见一次。</p></div><div class="listening-emblem" aria-hidden="true">${icon('sound')}</div></div>${reviewModeMarkup()}<div class="listening-filter-row"><div class="filter-tabs"><button data-listening-filter="due">今天待练</button><button data-listening-filter="all">全部听力</button></div><button class="text-button" data-listening-refresh>${icon('replay')}刷新待练</button></div><div id="listening-feedback" class="listening-feedback" role="status"></div><div id="listening-workspace"></div><p class="listening-footnote">每次播放对应素材中的整句原声。同一句的收藏与摘录合并练习；讲解里的新例句不会冒用原视频的声音。</p></section>`;
}
function buildListeningQueue(){
  stopListening();listening.queue=listening.items.filter(item=>!listening.completed.has(item.id)&&(listening.mode==='all'||listeningDue(item)));listening.index=0;
  document.querySelectorAll('[data-listening-filter]').forEach(button=>{const active=button.dataset.listeningFilter===listening.mode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
  document.querySelectorAll('.listening-due-count').forEach(node=>node.textContent=`今日 ${listening.items.filter(listeningDue).length}`);
  renderListeningCard();
}
function renderListeningCard(){
  stopListening();if(state.page!=='review'||state.reviewMode!=='listening'||!$('#listening-workspace'))return;
  document.querySelectorAll('[data-listening-filter], [data-listening-refresh], [data-review-mode="listening"]').forEach(button=>button.disabled=false);
  $('#listening-feedback').textContent=listening.feedback;
  const item=listening.queue[listening.index];
  if(!item){
    listening.active=null;
    const missing=listening.queue.filter(row=>!row.audio?.available).length,unrecorded=listening.queue.filter(row=>!listening.completed.has(row.id)).length;
    const title=missing?'还有原声需要找回来':unrecorded?'这一轮先到这里':listening.completed.size?'今天，耳朵又往前走了一点。':'现在没有到期的练习';
    const detail=missing?`${missing} 句原声暂时不可用，重新导入原素材后再来。这些句子没有记为完成。`:unrecorded?`${unrecorded} 句尚未记录结果，可以稍后再练。`:listening.completed.size?`本次已记录 ${listening.completed.size} 句。它们会按本次的理解情况，在之后再次出现。`:'收藏语句、保存摘录，或在学习时标记「这句没听懂」，就能在这里练习来源整句原声。';
    $('#listening-workspace').innerHTML=`<div class="listening-empty">${icon(missing?'folder':'sound')}<h2>${title}</h2><p>${detail}</p><div><button class="button small" data-listening-refresh>刷新待练</button><button class="text-button" data-review-mode="text">回到文字复习</button></div></div>`;return;
  }
  let attempt=listening.attempts.get(item.id);
  if(!attempt){attempt={item,id:crypto.randomUUID(),assisted:false,listened:false,reveal:'none',rate:1,loop:false,playing:false,ready:false,started:false,available:!!item.audio?.available,grading:false,pendingGrade:null,error:''};listening.attempts.set(item.id,attempt);}
  attempt.item=item;attempt.available=!!item.audio?.available;listening.active=attempt;
  const hint={meaning:'先听完整一句。需要时再看英文，最后把意思弄清楚。',sound:'先抓住熟悉的声音，卡住时再对照英文。',speed:'先试原速，跟不上时可以慢听，再回到原速。'}[item.difficulty]||'先不看文字，听一听声音在表达什么。';
  $('#listening-workspace').innerHTML=`<article class="listening-card"><div class="listening-card-top"><span>来源整句原声</span><span>第 ${listening.index+1} / ${listening.queue.length} 句</span></div><div class="listening-blind"><div class="listening-rings" aria-hidden="true">${icon('sound')}</div><h2>把这一句，交给耳朵。</h2><p>${hint}</p></div><audio id="listening-audio" preload="metadata" aria-hidden="true" hidden></audio><div id="listening-media-status" class="listening-media-status" role="status">${attempt.available?'正在准备原声音频…':'这句的本地原声暂时不可用。请重新导入原素材包，再刷新听力库；本句不会记为已完成。'}</div><div class="listening-player"><button class="button primary" data-listening-play disabled>${icon('play')}播放原声</button><button class="button" data-listening-replay disabled>${icon('replay')}重听</button><div class="segmented listening-rates" aria-label="原声速度"><button data-listening-rate="1" aria-pressed="${attempt.rate===1}">原速 1×</button><button data-listening-rate="0.85" aria-pressed="${attempt.rate===.85}">慢听 0.85×</button></div><button class="button listening-loop" data-listening-loop aria-pressed="${attempt.loop}">${icon('repeat')}循环本句</button></div><div class="listening-reveal-row"><button class="button small" data-listening-reveal="en">需要时，看看英文</button><span>看文字或慢听后，本次会记为使用了提示。</span></div><div id="listening-answer"></div><div id="listening-attempt-status" class="listening-attempt-status" aria-live="polite"></div><div class="listening-grade"><p>完整听过之后，选一个最接近的感受。</p><div><button class="button" data-listening-grade="understood" disabled>${icon('check')}原速听懂了</button><button class="button" data-listening-grade="hinted" disabled>提示后听懂</button><button class="button" data-listening-grade="missed" disabled>还没听清</button></div></div><div id="listening-grade-error" role="alert"></div><div class="listening-bottom"><span>这里只记录听力练习，不改变文字卡片的掌握状态。</span><button class="text-button" data-listening-skip>先跳过</button></div></article>`;
  renderListeningAnswer();updateListeningControls();if(attempt.available)prepareListeningAudio(attempt);
}
function prepareListeningAudio(attempt){
  const audio=$('#listening-audio'),epoch=listening.epoch,clip=attempt.item.audio;
  listening.audio=audio;audio.playbackRate=attempt.rate;audio.src=media({folder:clip.folder},clip.video);
  const valid=()=>epoch===listening.epoch&&listening.active===attempt&&state.reviewMode==='listening'&&state.page==='review';
  const fail=()=>{if(!valid())return;pauseListening();attempt.available=false;clip.available=false;attempt.ready=false;$('#listening-media-status').textContent='这句原声暂时无法播放。请确认原素材完整，再刷新听力库；本句不会记为已完成。';updateListeningControls();};
  audio.addEventListener('loadedmetadata',()=>{if(!valid())return;if(!Number.isFinite(clip.start)||!Number.isFinite(clip.end)||clip.start<0||clip.end<=clip.start||!Number.isFinite(audio.duration)||audio.duration<clip.end-.03){fail();return;}attempt.ready=true;audio.currentTime=clip.start;$('#listening-media-status').textContent=`${Math.max(1,Math.round(clip.end-clip.start))} 秒原声 · 点击开始`;updateListeningControls();});
  audio.addEventListener('error',fail);
  audio.addEventListener('timeupdate',()=>{if(valid())checkListeningEnd();});
  audio.addEventListener('ended',()=>{if(valid()){if(audio.currentTime<clip.end-.08)fail();else checkListeningEnd();}});
  audio.addEventListener('pause',()=>{if(valid()){attempt.playing=false;updateListeningControls();}});
  audio.addEventListener('play',()=>{if(!valid()){audio.pause();return;}attempt.playing=true;attempt.lastPosition=audio.currentTime;attempt.lastObservedAt=performance.now();updateListeningControls();scheduleListeningBoundary();});
  audio.load();
}
function scheduleListeningBoundary(){
  cancelAnimationFrame(listening.frame);clearTimeout(listening.timer);
  const audio=listening.audio,attempt=listening.active,epoch=listening.epoch;if(!audio||!attempt||audio.paused)return;
  const frame=()=>{if(epoch!==listening.epoch)return;checkListeningEnd();if(!audio.paused)listening.frame=requestAnimationFrame(frame);};listening.frame=requestAnimationFrame(frame);
  const remaining=Math.max(.02,(attempt.item.audio.end-audio.currentTime)/audio.playbackRate);
  listening.timer=setTimeout(()=>{if(epoch!==listening.epoch)return;checkListeningEnd();if(!audio.paused)scheduleListeningBoundary();},remaining*1000+15);
}
function checkListeningEnd(){
  const attempt=listening.active,audio=listening.audio;if(!attempt||!audio||!attempt.started)return;
  if(Number.isFinite(attempt.lastPosition)){const now=performance.now(),advanced=audio.currentTime-attempt.lastPosition,elapsed=(now-attempt.lastObservedAt)/1000;if(advanced>=0&&advanced<=elapsed*audio.playbackRate+.2)attempt.heardSeconds+=advanced;else if(advanced>.2)attempt.traversalInvalid=true;attempt.lastPosition=audio.currentTime;attempt.lastObservedAt=now;}
  if(audio.currentTime<attempt.item.audio.end-.025)return;
  const epoch=listening.epoch,complete=!attempt.traversalInvalid&&attempt.heardSeconds>=attempt.item.audio.end-attempt.item.audio.start-.1;
  attempt.started=false;pauseListening();audio.currentTime=attempt.item.audio.end;if(complete)attempt.listened=true;
  $('#listening-media-status').textContent=complete?'这一遍听完了。可以重听，也可以记录你的感受。':'这次没有完整听过原句，请从头重听。';updateListeningControls();
  if(attempt.loop&&!attempt.pendingGrade)listening.timer=setTimeout(()=>{if(epoch===listening.epoch&&listening.active===attempt&&attempt.loop)playListening(true);},650);
}
async function playListening(replay=false){
  const attempt=listening.active,audio=listening.audio,epoch=listening.epoch;
  if(!attempt?.ready||!attempt.available||!audio||attempt.pendingGrade)return;
  clearTimeout(listening.timer);
  if(!replay&&!audio.paused){pauseListening();return;}
  if(replay||!attempt.started||audio.currentTime>=attempt.item.audio.end-.03||audio.currentTime<attempt.item.audio.start){audio.currentTime=attempt.item.audio.start;attempt.started=true;attempt.heardSeconds=0;attempt.traversalInvalid=false;attempt.lastPosition=undefined;}
  try{await audio.play();if(epoch!==listening.epoch||listening.active!==attempt)audio.pause();}
  catch(error){if(epoch===listening.epoch&&error.name!=='AbortError'){attempt.playing=false;$('#listening-media-status').textContent='播放没有开始，请再点击一次播放原声。';updateListeningControls();}}
}
function updateListeningControls(){
  const attempt=listening.active;if(!attempt||!$('#listening-attempt-status')||state.reviewMode!=='listening')return;
  const locked=!!attempt.pendingGrade,playable=attempt.ready&&attempt.available&&!locked;
  for(const selector of ['[data-listening-play]','[data-listening-replay]'])$(selector).disabled=!playable;
  $('[data-listening-play]').innerHTML=icon(attempt.playing?'pause':'play')+(attempt.playing?'暂停':'播放原声');
  document.querySelectorAll('[data-listening-rate]').forEach(button=>{button.disabled=locked||!attempt.available;button.setAttribute('aria-pressed',String(Number(button.dataset.listeningRate)===attempt.rate));});
  $('[data-listening-loop]').disabled=locked||!attempt.available;$('[data-listening-loop]').setAttribute('aria-pressed',String(attempt.loop));
  document.querySelectorAll('[data-listening-reveal], [data-listening-retest], [data-listening-skip]').forEach(button=>button.disabled=locked);
  document.querySelectorAll('[data-listening-filter], [data-listening-refresh], [data-review-mode="listening"]').forEach(button=>button.disabled=locked);
  document.querySelectorAll('[data-listening-grade]').forEach(button=>button.disabled=locked||!attempt.available||!attempt.ready||!attempt.listened||(button.dataset.listeningGrade==='understood'&&attempt.assisted));
  $('#listening-attempt-status').textContent=attempt.assisted?'本次已使用文字或慢听提示。藏起答案重听，也会保留这个记录。':attempt.listened?'已经完整听过，可以记录这次的理解情况。':'先完整听一遍，再记录结果。也可以提前查看提示。';
  $('#listening-grade-error').innerHTML=attempt.error?`<p>${esc(attempt.error)}</p><button class="button small" data-listening-grade-retry ${attempt.grading?'disabled':''}>${attempt.grading?'正在保存…':'重试保存本次结果'}</button>`:'';
}
function renderListeningAnswer(){
  const attempt=listening.active,node=$('#listening-answer');if(!attempt||!node)return;
  if(attempt.reveal==='none'){node.innerHTML='';return;}
  const item=attempt.item;
  node.innerHTML=`<section class="listening-answer"><p class="listening-english" lang="en">${esc(item.source.en)}</p>${attempt.reveal==='both'?`<p class="listening-chinese">${esc(item.source.zh||'这句暂时还没有中文翻译。')}</p>${item.meaning?`<div class="listening-meaning"><span>再把意思弄清楚</span><p>${esc(item.meaning)}</p></div>`:''}`:'<button class="text-button" data-listening-reveal="both">再看中文与已有讲解</button>'}<div class="listening-answer-actions"><button class="button small" data-listening-retest>藏起答案，原速再听</button><button class="text-button" data-listening-source>回到学习空间</button></div><p class="listening-source-caption">${esc(item.source.package_title)} · ${fmt(item.source.start)}</p></section>`;
}
function revealListening(level){const attempt=listening.active;if(!attempt||attempt.pendingGrade)return;attempt.assisted=true;attempt.reveal=level;renderListeningAnswer();updateListeningControls();}
function setListeningRate(rate){const attempt=listening.active;if(!attempt||attempt.pendingGrade)return;attempt.rate=rate;if(rate<1)attempt.assisted=true;if(listening.audio)listening.audio.playbackRate=rate;updateListeningControls();scheduleListeningBoundary();}
async function gradeListening(result){
  const attempt=listening.active;if(!attempt||attempt.grading||!attempt.available||!attempt.ready||!attempt.listened)return;
  if(!attempt.pendingGrade){if(result==='understood'&&attempt.assisted)return;attempt.pendingGrade={id:attempt.item.id,result,attempt_id:attempt.id,assisted:attempt.assisted};}
  attempt.grading=true;attempt.error='';pauseListening();updateListeningControls();
  const epoch=listening.epoch;
  try{
    const response=await api('/api/listening/grade',attempt.pendingGrade),item=response.item;
    if(epoch!==listening.epoch||state.page!=='review'||state.reviewMode!=='listening'||listening.active!==attempt)return;
    listening.items=listening.items.map(row=>row.id===item.id?item:row);listening.completed.add(item.id);listening.attempts.delete(item.id);
    const when=item.last_result==='missed'?'10 分钟后':item.interval_days===1?'明天':`${item.interval_days} 天后`;
    listening.feedback=`已记录这次练习，${when}再见。`;listening.index++;document.querySelectorAll('.listening-due-count').forEach(node=>node.textContent=`今日 ${listening.items.filter(listeningDue).length}`);renderListeningCard();
  }catch(error){if(epoch===listening.epoch&&listening.active===attempt){attempt.error=`结果暂时未确认：${error.message}。重试会使用同一次练习记录。`;}}
  finally{attempt.grading=false;if(epoch===listening.epoch&&listening.active===attempt)updateListeningControls();}
}
async function showDifficulty(){
  if(!state.pkg||!current()||listening.markBusy)return;$('#video')?.pause();listening.markTarget={folder:state.pkg.folder,segment_id:current().id,package_id:state.pkg.id};
  $('#difficulty-error').textContent='';$('#difficulty-current').textContent='选择最接近你现在感受的一项。';$('[data-listening-reason="clear"]').hidden=true;$('#difficulty-dialog').showModal();
  const target=listening.markTarget;
  try{const catalog=await api('/api/listening');if(listening.markTarget!==target||!$('#difficulty-dialog').open)return;acceptListeningCatalog(catalog);const item=listening.items.find(row=>row.source.package_id===target.package_id&&row.source.segment_id===target.segment_id);if(item?.difficulty){$('#difficulty-current').textContent=`当前标记：${difficultyLabels[item.difficulty]}`;$('[data-listening-reason="clear"]').hidden=false;}}
  catch{if($('#difficulty-dialog').open)$('#difficulty-current').textContent='选择后会保存到本地听力库。';}
}
async function markDifficulty(reason){
  const target=listening.markTarget;if(!target||listening.markBusy)return;listening.markBusy=true;
  $('#difficulty-error').textContent='';document.querySelectorAll('[data-listening-reason]').forEach(button=>button.disabled=true);
  try{const response=await api('/api/listening/mark',{folder:target.folder,segment_id:target.segment_id,reason:reason==='clear'?null:reason});listening.loaded=false;$('#difficulty-dialog').close();toast(reason==='clear'?'已清除困难标记；已有收藏、摘录和练习记录会保留':'已加入听力待练。去「我的复习库 → 听力复习」再听一次。');}
  catch(error){$('#difficulty-error').textContent=error.message;}
  finally{listening.markBusy=false;document.querySelectorAll('[data-listening-reason]').forEach(button=>button.disabled=false);}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)pauseListening();});

function updateReviewItem(item){reviewRevision++;state.reviews=state.reviews.map(old=>old.id===item.id?item:old);renderReviewList();}
async function reviewAction(id,change,message){const result=await api('/api/reviews/update',{id,...change});updateReviewItem(result.item);toast(message);}
async function jumpToReviewSource(item){
  const revision=routeRevision,result=await api('/api/packages');
  if(revision!==routeRevision||state.page!=='review')return;
  const matches=result.packages.filter(pkg=>String(pkg.id)===String(item.source.package_id));
  const original=matches.find(pkg=>pkg.folder===item.source.folder)||matches[0];
  if(!original){toast('原素材暂时不在素材库。重新导入这个视频的素材包后，就能回到原句；摘录仍然可以复习。');return;}
  await openPackage(original.folder,item.source);
}
document.addEventListener('input',e=>{const id=e.target.dataset?.reviewNote;if(!id)return;state.reviewDrafts[id]=e.target.value;const status=[...document.querySelectorAll('[data-note-status]')].find(node=>node.dataset.noteStatus===id);if(status)status.textContent='笔记尚未保存';});
document.addEventListener('change',async e=>{
  const id=e.target.dataset?.reviewCategorySelect;if(!id)return;
  const select=e.target,item=state.reviews.find(item=>item.id===id);if(!item)return;
  select.disabled=true;
  try{await reviewAction(id,{category:select.value},'分类已保存');}
  catch(error){select.value=reviewCategory(item);toast(error.message);}
  finally{select.disabled=false;}
});

function mark(kind){const id=current().id,arr=state.progress[kind];state.progress[kind]=arr.includes(id)?arr.filter(x=>x!==id):[...arr,id];renderLesson();renderList();updateMastery();persist();}
function checkDictation(){
  const answer=current().en,user=$('#dictation').value;
  const words=s=>s.toLowerCase().replace(/[’‘]/g,"'").match(/[a-z0-9]+(?:'[a-z]+)?/g)||[];
  const a=words(answer),b=words(user);if(!b.length){toast('先写下你听到的几个词吧');return;}
  if(b.length>500){toast('这一句的听写太长了，请缩短后再对照');return;}
  let prev=Array.from({length:b.length+1},(_,i)=>i);
  for(let i=1;i<=a.length;i++){const row=[i];for(let j=1;j<=b.length;j++)row[j]=Math.min(row[j-1]+1,prev[j]+1,prev[j-1]+(a[i-1]===b[j-1]?0:1));prev=row;}
  const score=Math.max(0,Math.round((1-prev[b.length]/Math.max(a.length,b.length))*100));
  $('#dictation-result').textContent=score===100?'完全一致，听得很仔细！':`词序匹配 ${score}% · 再听一次试试`;
  $('#dictation-answer').textContent=`原文：${answer}`;$('#dictation-answer').hidden=false;
}

function persist(immediate=false){
  if(!state.pkg)return true;const video=$('#video');
  Object.assign(state.progress,{lastTime:video?.currentTime||0,lastSegment:current()?.id,mode:state.mode,subtitles:state.subtitles,loop:state.loop});
  const payload={folder:state.pkg.folder,progress:structuredClone(state.progress)},revision=++saveRevision;
  clearTimeout(state.saveTimer);
  const save=()=>{
    const pending=progressQueue.then(async()=>{try{await api('/api/progress',payload);reviewRevision++;if(revision===saveRevision&&$('#save-status')){$('#save-status').innerHTML=`${icon('check')}进度已保存`;$('#save-status').classList.remove('progress-error');}return true;}catch(e){if(revision===saveRevision&&$('#save-status')){$('#save-status').textContent='进度保存失败';$('#save-status').classList.add('progress-error');}return false;}});
    progressQueue=pending;return pending;
  };
  if(immediate)return save();else state.saveTimer=setTimeout(save,650);
}
setInterval(()=>{if(state.page==='study'&&$('#video')&&!$('#video').paused)persist();},5000);
document.addEventListener('visibilitychange',()=>{if(document.hidden)persist(true);});
window.addEventListener('beforeunload',()=>{if(state.pkg){clearTimeout(state.saveTimer);state.progress.lastTime=$('#video')?.currentTime||0;fetch('/api/progress',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':state.token},body:JSON.stringify({folder:state.pkg.folder,progress:state.progress}),keepalive:true}).catch(()=>{});}});

const dismissed=new Set();
async function pollJobs(){
  try{
    const result=await api('/api/jobs');state.jobs=result.jobs.filter(j=>!dismissed.has(j.id));
    const signature=JSON.stringify(state.jobs.map(j=>[j.id,j.status,j.progress,j.message]));
    if(signature!==state.jobSignature){
      const before=state.jobSignature;state.jobSignature=signature;
      if(state.page==='library'){
        if($('#jobs-area'))$('#jobs-area').innerHTML=jobCards();
        if(before&&state.jobs.some(j=>j.status==='complete'||(j.folder&&!state.packages.some(p=>p.folder===j.folder))))await loadLibrary();
      }else if(state.page==='study'&&state.pkg){
        const folder=state.pkg.folder;
        const matching=state.jobs.find(j=>j.folder===state.pkg.folder&&['running','queued'].includes(j.status));
        if($('#study-jobs'))$('#study-jobs').innerHTML=matching?`<div class="study-notice">${esc(matching.message)} · 可以先练习已经准备好的语句。</div>`:'';
        const updated=await api(`/api/package?folder=${encodeURIComponent(folder)}`);
        if(state.page!=='study'||state.pkg?.folder!==folder)return;
        const changed=JSON.stringify(updated.segments)!==JSON.stringify(state.pkg.segments);
        if(changed){
          const oldId=current()?.id, oldSentence=JSON.stringify(current()), countChanged=updated.segments.length!==state.pkg.segments.length;
          state.progress.lastTime=$('#video')?.currentTime||0;
          state.pkg={...updated,folder:state.pkg.folder};state.current=Math.max(0,state.pkg.segments.findIndex(s=>s.id===oldId));
          if(countChanged)renderStudy();
          else{
            if(JSON.stringify(current())!==oldSentence)renderLesson();
            renderList();
          }
        }
      }
    }
  }catch{/* Server may be restarting. Preserve the active lesson. */}
}

let localVideo=null,localUpload=null,localCopyBusy=false;
function setImportMode(mode){
  if(localCopyBusy)return;
  $('#download-form').hidden=mode==='local';$('#local-video-form').hidden=mode!=='local';
  document.querySelectorAll('[data-import-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.importMode===mode)));
}
function chooseLocalVideo(file){
  if(localCopyBusy){toast('正在复制视频，请等待完成');return;}
  localUpload=null;showImport('local');$('#local-import-error').textContent='';$('#local-copy-status').textContent='';
  if(!/\.(mp4|m4v|mov|mkv|webm|avi)$/i.test(file.name)||!file.size||file.size>32*1024**3){localVideo=null;$('#local-file-name').textContent='选择一个英文视频';$('#local-import-error').textContent='请选择 32 GB 以内的 MP4、MOV、MKV、WebM、M4V 或 AVI 视频';return;}
  localVideo=file;$('#local-file-name').textContent=`${file.name} · ${file.size>=1024**3?(file.size/1024**3).toFixed(2)+' GB':(file.size/1024**2).toFixed(1)+' MB'}`;
}
function copyLocalVideo(id,file){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST',`/api/local/file?id=${encodeURIComponent(id)}`);
    xhr.setRequestHeader('X-Local-Token',state.token);xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.upload.onprogress=e=>{if(e.lengthComputable){const percent=Math.min(99,Math.round(e.loaded/e.total*100));$('#local-copy-progress').value=percent;$('#local-copy-status').textContent=`正在复制到本地素材库 · ${percent}%`;}};
    xhr.onerror=()=>reject(new Error('本地连接中断，请重新选择视频；原文件未改动'));
    xhr.onabort=()=>reject(new Error('复制已停止，原文件未改动'));
    xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300)throw new Error(result.error||'复制失败');resolve(result);}catch(error){reject(error);}};
    xhr.send(file);
  });
}
$('#local-video-picker').onclick=()=>$('#local-video-input').click();
$('#local-video-input').onchange=e=>{if(e.target.files[0])chooseLocalVideo(e.target.files[0]);e.target.value='';};
$('#import-dialog').addEventListener('cancel',e=>{if(localCopyBusy)e.preventDefault();});
$('#local-video-form').onsubmit=async e=>{
  e.preventDefault();if(localCopyBusy)return;
  if(!localVideo){$('#local-import-error').textContent='先选择一个本地英文视频';return;}
  localCopyBusy=true;const controls=[...document.querySelectorAll('#import-dialog button, #local-auto-ai')];controls.forEach(b=>b.disabled=true);
  $('#local-import-error').textContent='';$('#local-copy-progress').hidden=false;$('#local-copy-progress').value=0;
  try{
    if(!localUpload){localUpload=await api('/api/local/start',{name:localVideo.name,size:localVideo.size,analyze:$('#local-auto-ai').checked});}
    if(!localUpload.copied){await copyLocalVideo(localUpload.id,localVideo);localUpload.copied=true;}$('#local-copy-progress').value=100;$('#local-copy-status').textContent='副本已保存，正在开始识别…';
    await api('/api/local/finish',{id:localUpload.id,analyze:$('#local-auto-ai').checked});$('#import-dialog').close();localVideo=null;localUpload=null;$('#local-file-name').textContent='选择一个英文视频';$('#local-copy-status').textContent='';
    await home();await pollJobs();toast('视频副本已保存，正在从原音制作素材包。原文件保持不变。');
  }catch(error){$('#local-import-error').textContent=error.message;if(localUpload?.copied)$('#local-copy-status').textContent='视频副本已保存，再次点击制作即可继续，无需重新复制。';else localUpload=null;}
  finally{localCopyBusy=false;controls.forEach(b=>b.disabled=false);$('#local-copy-progress').hidden=true;}
};

async function importFiles(entries){
  if(!entries.length)return;
  const manifests=entries.filter(e=>e.path.split('/').pop()==='manifest.json');
  if(!manifests.length){const videos=entries.filter(entry=>/\.(mp4|m4v|mov|mkv|webm|avi)$/i.test(entry.file.name));if(videos.length===1){chooseLocalVideo(videos[0].file);return;}toast('请拖入一个本地视频，或一个完整的素材包文件夹');return;}
  if(manifests.length!==1){toast('请一次导入一个素材包');return;}
  const manifestEntry=manifests[0],prefix=manifestEntry.path.slice(0,-'manifest.json'.length);
  try{
    if(manifestEntry.file.size>30*1024*1024)throw new Error('素材包目录文件过大');
    const manifest=JSON.parse(await manifestEntry.file.text());
    const start=await api('/api/import/start',{manifest});
    if(start.existing){$('#import-dialog').close();toast('素材包已在素材库中，直接打开');await openPackage(start.existing);return;}
    for(let i=0;i<start.files.length;i++){
      const path=start.files[i],entry=entries.find(e=>e.path===prefix+path);
      if(!entry)throw new Error(`素材包缺少文件：${path}`);
      toast(`正在导入 ${i+1}/${start.files.length} · ${path}`);
      const response=await fetch(`/api/import/file?id=${start.id}&path=${encodeURIComponent(path)}`,{method:'POST',headers:{'X-Local-Token':state.token,'Content-Type':'application/octet-stream'},body:entry.file});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
    }
    const result=await api('/api/import/finish',{id:start.id});$('#import-dialog').close();toast('素材包已导入');await openPackage(result.folder);
  }catch(e){toast(e.message||'无法读取素材包');}
  finally{$('#folder-input').value='';}
}
async function walkEntry(entry,prefix=''){
  if(entry.isFile){const file=await new Promise((resolve,reject)=>entry.file(resolve,reject));return [{path:prefix+file.name,file}];}
  if(entry.isDirectory){const reader=entry.createReader(),all=[];let batch;do{batch=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));all.push(...batch);}while(batch.length);const results=[];for(const child of all)results.push(...await walkEntry(child,prefix+entry.name+'/'));return results;}
  return [];
}
let dragDepth=0;
document.addEventListener('dragenter',e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();dragDepth++;$('#drag-overlay').hidden=false;}});
document.addEventListener('dragover',e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();e.dataTransfer.dropEffect='copy';}});
document.addEventListener('dragleave',()=>{dragDepth--;if(dragDepth<=0){$('#drag-overlay').hidden=true;dragDepth=0;}});
document.addEventListener('drop',async e=>{
  e.preventDefault();dragDepth=0;$('#drag-overlay').hidden=true;
  const nativeEntries=[...e.dataTransfer.items].map(item=>item.webkitGetAsEntry?.()).filter(Boolean);
  const fallbackFiles=[...e.dataTransfer.files];
  try{const files=nativeEntries.length?(await Promise.all(nativeEntries.map(entry=>walkEntry(entry)))).flat():fallbackFiles.map(file=>({path:file.webkitRelativePath||file.name,file}));await importFiles(files);}catch(e){toast('无法读取文件夹，请使用「选择素材包」');}
});
$('#folder-input').onchange=e=>importFiles([...e.target.files].map(file=>({path:file.webkitRelativePath||file.name,file})));
$('#dialog-import').onclick=()=>$('#folder-input').click();
$('#caption-input').onchange=async e=>{
  const file=e.target.files[0];if(!file||!state.pkg)return;
  try{const ext='.'+file.name.split('.').pop().toLowerCase();const response=await fetch(`/api/captions?folder=${encodeURIComponent(state.pkg.folder)}&ext=${ext}`,{method:'POST',headers:{'X-Local-Token':state.token},body:file});const result=await response.json();if(!response.ok)throw new Error(result.error);await openPackage(state.pkg.folder);toast('英文字幕已导入');}catch(e){toast(e.message);}finally{e.target.value='';}
};
$('#download-form').onsubmit=async e=>{
  e.preventDefault();const button=e.target.querySelector('[type=submit]');button.disabled=true;$('#download-error').textContent='';
  try{await api('/api/download',{url:$('#video-url').value.trim(),analyze:$('#auto-ai').checked,browser:$('#cookie-browser').value});$('#import-dialog').close();await home();await pollJobs();toast('已开始准备素材包，可以在这里查看进度');}catch(e){$('#download-error').textContent=e.message;}finally{button.disabled=false;}
};
$('#home').onclick=home;$('#library-nav').onclick=home;$('#review-nav').onclick=openReviews;$('#guide-nav').onclick=()=>{pauseListening();$('#guide-dialog').showModal();};
async function analyze(button,options={}){button.disabled=true;try{await api('/api/analyze',{folder:state.pkg.folder,...options});toast(options.upgrade?'正在升级这一句精讲，可以先继续学习':'正在补全简单讲解，已有内容会保留');await pollJobs();}finally{button.disabled=false;}}
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  try{
    if(b.hasAttribute('data-new'))showImport();
    else if(b.hasAttribute('data-local'))showImport('local');
    else if(b.dataset.importMode)setImportMode(b.dataset.importMode);
    else if(b.hasAttribute('data-import'))$('#folder-input').click();
    else if(b.hasAttribute('data-library-category'))setLibraryCategory(b.dataset.libraryCategory);
    else if(b.hasAttribute('data-library-tag'))toggleLibraryTag(b.dataset.libraryTag);
    else if(b.hasAttribute('data-library-clear'))clearLibraryFilters();
    else if(b.hasAttribute('data-library-expand')){libraryView.expanded=!libraryView.expanded;renderLibraryFilters();}
    else if(b.dataset.organize)openOrganization(b.dataset.organize);
    else if(b.dataset.catalogSuggest){const input=$('#catalog-tags'),tags=input.value.split(/[,，;；\n]+/).map(t=>t.trim()).filter(Boolean);if(!tags.some(t=>t.toLocaleLowerCase()===b.dataset.catalogSuggest.toLocaleLowerCase()))tags.push(b.dataset.catalogSuggest);input.value=tags.join('，');input.focus();}
    else if(b.dataset.libraryMatch){const p=libraryView.data?.packages.find(p=>p.folder===b.dataset.libraryMatch);if(p?.match)await openPackage(p.folder,{package_id:p.id,segment_id:p.match.id});}
    else if(b.hasAttribute('data-refresh')){await loadLibrary();toast('素材库已刷新');}
    else if(b.hasAttribute('data-home'))await home();
    else if(b.dataset.open)await openPackage(b.dataset.open);
    else if(b.dataset.mode)setMode(b.dataset.mode);
    else if(b.dataset.sub)setSubtitles(b.dataset.sub);
    else if(b.dataset.sentence!==undefined)selectSentence(Number(b.dataset.sentence));
    else if(b.dataset.filter){state.filter=b.dataset.filter;renderList();}
    else if(b.hasAttribute('data-reveal')){state.reveal=true;renderLesson();}
    else if(b.hasAttribute('data-favorite'))mark('favorites');
    else if(b.hasAttribute('data-mastered'))mark('mastered');
    else if(b.hasAttribute('data-listening-mark'))await showDifficulty();
    else if(b.dataset.listeningReason)await markDifficulty(b.dataset.listeningReason);
    else if(b.dataset.reviewMode==='listening')await openListening();
    else if(b.dataset.reviewMode==='text')await openReviews();
    else if(b.hasAttribute('data-listening-refresh'))await openListening();
    else if(b.dataset.listeningFilter){listening.mode=b.dataset.listeningFilter;buildListeningQueue();}
    else if(b.hasAttribute('data-listening-play'))await playListening();
    else if(b.hasAttribute('data-listening-replay'))await playListening(true);
    else if(b.dataset.listeningRate)setListeningRate(Number(b.dataset.listeningRate));
    else if(b.hasAttribute('data-listening-loop')){const attempt=listening.active;if(attempt&&!attempt.pendingGrade){attempt.loop=!attempt.loop;if(!attempt.loop)clearTimeout(listening.timer);updateListeningControls();if(listening.audio&&!listening.audio.paused)scheduleListeningBoundary();}}
    else if(b.dataset.listeningReveal)revealListening(b.dataset.listeningReveal);
    else if(b.hasAttribute('data-listening-retest')){const attempt=listening.active;if(attempt&&!attempt.pendingGrade){attempt.reveal='none';attempt.listened=false;setListeningRate(1);renderListeningAnswer();updateListeningControls();await playListening(true);}}
    else if(b.dataset.listeningGrade)await gradeListening(b.dataset.listeningGrade);
    else if(b.hasAttribute('data-listening-grade-retry'))await gradeListening();
    else if(b.hasAttribute('data-listening-skip')){if(listening.active&&!listening.active.pendingGrade){listening.skipped.add(listening.active.item.id);listening.index++;renderListeningCard();}}
    else if(b.hasAttribute('data-listening-source')){const attempt=listening.active;if(attempt){pauseListening();await jumpToReviewSource(attempt.item);}}
    else if(b.hasAttribute('data-check-dictation'))checkDictation();
    else if(b.hasAttribute('data-caption'))$('#caption-input').click();
    else if(b.hasAttribute('data-analyze'))await analyze(b);
    else if(b.hasAttribute('data-upgrade-sentence'))await analyze(b,{upgrade:true,segment_id:current().id});
    else if(b.hasAttribute('data-save-selection'))await saveSelection();
    else if(b.dataset.saveFields)await saveSelection({folder:state.pkg.folder,segment_id:current().id,selections:fullSelections(b.dataset.saveFields.split(','))});
    else if(b.hasAttribute('data-review-refresh'))await openReviews();
    else if(b.dataset.reviewCategory){state.reviewCategory=b.dataset.reviewCategory;state.reviewDelete=null;renderReviewList();}
    else if(b.dataset.reviewFilter){state.reviewFilter=b.dataset.reviewFilter;state.reviewDelete=null;renderReviewList();}
    else if(b.hasAttribute('data-review-reset')){state.reviewFilter='all';state.reviewCategory='all';state.reviewSearch='';renderReviews();}
    else if(b.dataset.reviewSaveNote){const id=b.dataset.reviewSaveNote,note=state.reviewDrafts[id]??state.reviews.find(item=>item.id===id)?.note??'';b.disabled=true;try{const result=await api('/api/reviews/update',{id,note});if(state.reviewDrafts[id]===note)delete state.reviewDrafts[id];updateReviewItem(result.item);toast(Object.prototype.hasOwnProperty.call(state.reviewDrafts,id)?'笔记已保存，刚刚的新修改仍可继续保存':'笔记已保存');}finally{b.disabled=false;}}
    else if(b.dataset.reviewStudied){b.disabled=true;try{await reviewAction(b.dataset.reviewStudied,{action:'reviewed'},'又见面了一次，记忆会慢慢变牢。');}finally{b.disabled=false;}}
    else if(b.dataset.reviewMastered){const item=state.reviews.find(item=>item.id===b.dataset.reviewMastered);await reviewAction(item.id,{mastered:!item.mastered},item.mastered?'已放回继续练习':'已标记掌握');}
    else if(b.dataset.reviewRemove){state.reviewDelete=b.dataset.reviewRemove;renderReviewList();}
    else if(b.dataset.reviewKeep){state.reviewDelete=null;renderReviewList();}
    else if(b.dataset.reviewDelete){const id=b.dataset.reviewDelete,favorite=state.reviews.find(item=>item.id===id)?.kind==='favorite';b.disabled=true;try{await api('/api/reviews/delete',{id});reviewRevision++;state.reviews=state.reviews.filter(item=>item.id!==id);delete state.reviewDrafts[id];state.reviewExpanded.delete(id);state.reviewDelete=null;renderReviewList();toast(favorite?'已取消收藏，已保存的笔记与复习记录仍会保留':'摘录已移除');}finally{b.disabled=false;}}
    else if(b.dataset.reviewSource){const item=state.reviews.find(item=>item.id===b.dataset.reviewSource);b.disabled=true;try{if(item)await jumpToReviewSource(item);}finally{b.disabled=false;}}
    else if(b.dataset.cancel){await api('/api/cancel',{id:b.dataset.cancel});await pollJobs();}
    else if(b.dataset.dismiss){dismissed.add(b.dataset.dismiss);await pollJobs();if($('#jobs-area'))$('#jobs-area').innerHTML=jobCards();}
    else if(b.dataset.retry){const j=state.jobs.find(x=>x.id===b.dataset.retry);if(j.kind==='local'){if(j.folder)await api('/api/local/resume',{folder:j.folder,analyze:j.analyze!==false});else await api('/api/local/finish',{id:j.import_id});}else if(j.folder&&(j.kind==='analysis'||['analysis','transcribe'].includes(j.stage)))await api('/api/analyze',{folder:j.folder,upgrade:j.upgrade,segment_id:j.segment_id});else await api('/api/download',{url:j.url,analyze:j.analyze,browser:j.browser});dismissed.add(j.id);await pollJobs();}
    else if(b.hasAttribute('data-exit-fullscreen'))document.exitFullscreen();
  }catch(err){toast(err.message);}
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape')clearSelection();
  if(state.page!=='study'||document.querySelector('dialog[open]')||e.ctrlKey||e.metaKey||e.altKey||e.target.closest('input,textarea,select,[contenteditable]'))return;
  if(e.target.closest('button,summary')||window.getSelection()?.toString())return;
  if(e.code==='Space'){e.preventDefault();togglePlay();}
  if(e.code==='ArrowLeft'){e.preventDefault();selectSentence(state.current-1);}
  if(e.code==='ArrowRight'){e.preventDefault();selectSentence(state.current+1);}
  if(e.code==='KeyR')replay();if(e.code==='KeyL')toggleLoop();if(e.code==='KeyC'){const modes=['none','en','both'];setSubtitles(modes[(modes.indexOf(state.subtitles)+1)%3]);}
});

async function boot(){
  try{const status=await api('/api/status');state.token=status.token;$('#connection').innerHTML=`<span class="status-dot" style="${status.ai.ready?'':'background:#bda06e'}"></span>${status.ai.ready?'ChatGPT 已连接 · 本地学习':'本地学习空间'}`;$('#connection').title=status.ai.message;
    await pollJobs();await routeFromHash();setInterval(pollJobs,3000);
  }catch(e){$('#app').innerHTML='<div class="empty">本地服务尚未启动。请双击 tool/启动.command，然后重新打开此页面。</div>';}
}
async function routeFromHash(){
  let route;try{route=decodeURIComponent(location.hash.slice(1))||'library';}catch{route='library';}
  if(route===state.route)return;
  if(route==='review')await openReviews();else if(route==='review-listening')await openListening();else if(route==='library')await home();else await openPackage(route);
}
window.addEventListener('hashchange',()=>{if(state.token)routeFromHash();});
boot();
