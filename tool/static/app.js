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
const state = { token:'', packages:[], jobs:[], page:'library', pkg:null, progress:{}, current:0, mode:'watch', subtitles:'en', loop:false, analysisOpen:false, reveal:false, filter:'all', search:'', frame:0, loopTimer:0, saveTimer:0, jobSignature:'', loading:false };
let toastTimer, saveRevision=0;
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),4000);}
async function api(path, data, options={}){
  const response=await fetch(path,{...options,method:data!==undefined?'POST':options.method||'GET',headers:{...(data!==undefined?{'Content-Type':'application/json','X-Local-Token':state.token}:{}),...options.headers},body:data!==undefined?JSON.stringify(data):options.body});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'操作失败，请重试');
  return result;
}
const media = (pkg, path) => `/media/${encodeURIComponent(pkg.folder)}/${path.split('/').map(encodeURIComponent).join('/')}`;
const current = () => state.pkg?.segments[state.current];
function showImport(){ $('#download-error').textContent='';$('#import-dialog').showModal();setTimeout(()=>$('#video-url').focus(),30); }
function teardown(){cancelAnimationFrame(state.frame);clearTimeout(state.loopTimer);const video=$('#video');if(video){video.pause();persist(true);} }
async function home(){teardown();state.page='library';state.pkg=null;location.hash='';await loadLibrary();}

function jobCards(){
  return state.jobs.filter(j=>!['complete','cancelled'].includes(j.status)).slice(0,3).map(j=>{
    const active=['running','queued'].includes(j.status), title=j.title||'制作新的学习素材包';
    return `<div class="job-card ${active?'':'error'}"><div><div class="job-title">${active?'<span class="status-dot"></span> ':''}${esc(title)}</div><div class="job-message">${esc(j.message)}</div></div><div class="job-actions">${j.folder?`<button class="button small" data-open="${esc(j.folder)}">先开始学习</button>`:''}${active?`<button class="text-button" data-cancel="${esc(j.id)}">取消</button>`:`<button class="button small" data-retry="${esc(j.id)}">重试</button><button class="text-button" data-dismiss="${esc(j.id)}">收起</button>`}</div>${active?`<div class="job-track"><span style="width:${Math.max(0,Math.min(100,j.progress||0))}%"></span></div><div class="job-subtle">制作完成后，视频、字幕与讲解都会保存在素材包中。</div>`:''}</div>`;
  }).join('');
}
async function loadLibrary(){
  try{
    const result=await api('/api/packages');state.packages=result.packages;
    if(state.page!=='library')return;
    $('#app').innerHTML=`<section class="library"><div class="hero"><div><div class="eyebrow">YOUR EVERYDAY ENGLISH, SLOWLY.</div><h1>把喜欢的视频，<br>听成<em>自己的英语。</em></h1><p class="intro">从一段日常开始。看见生活，听懂表达，<br>再把喜欢的那一句，变成你自己的声音。</p><div class="hero-note">Less rushing. More listening.</div></div><div class="new-card"><div class="new-card-top"><h3>收藏一个新故事</h3><span class="tiny-tag">YOUTUBE / BILIBILI</span></div><button class="url-starter" data-new>${icon('link')}<span>粘贴你想学习的视频链接</span><span class="arrow-box">↗</span></button><button class="drop-zone" data-import><span class="folder-glyph">↳</span><span>或拖入一个素材包<small>选择整个文件夹，让学习接着发生</small></span></button></div></div><div id="jobs-area">${jobCards()}</div><div class="collection-heading"><div class="collection-title"><h2>我的素材库</h2><span class="count-badge">${num(state.packages.length)}</span></div><div class="collection-actions"><button class="text-button" data-refresh>${icon('replay')}刷新素材库</button><button class="button small" data-new>＋ 新建素材包</button></div></div><div class="library-grid">${state.packages.map(p=>`<button class="video-card" data-open="${esc(p.folder)}"><div class="card-image">${p.poster?`<img src="${media(p,p.poster)}" alt="${esc(p.title)}" loading="lazy">`:''}<span class="card-play">${icon('play')}</span><span class="duration">${fmt(p.duration)}</span></div><div class="card-body"><div class="creator">${esc(p.creator||'LOCAL COLLECTION')}</div><h3>${esc(p.title)}</h3><div class="card-meta"><span>${p.count} 个精听片段</span><span class="ready-pill">${p.count && p.analyzed===p.count?'讲解已就绪':p.analyzed?`${p.analyzed}/${p.count} 句已解析`:p.count?'英文字幕就绪':'待补充字幕'}</span></div></div></button>`).join('')}<button class="add-card" data-new><span class="plus">＋</span><span>下一段，听点什么？</span><small>让你喜欢的内容成为教材</small></button></div><p class="library-note">${icon('folder')}所有素材保存在本地。准备好一次，就可以练习很多次。</p></section>`;
    $('#library-nav').classList.add('active');
  }catch(e){toast(e.message);if(!state.packages.length)$('#app').innerHTML='<div class="empty">无法连接本地工具。请保持启动窗口打开，然后刷新页面。</div>';}
}

async function openPackage(folder){
  if(state.loading)return;
  state.loading=true;teardown();
  try{
    const [pkg,progress]=await Promise.all([api(`/api/package?folder=${encodeURIComponent(folder)}`),api(`/api/progress?folder=${encodeURIComponent(folder)}`)]);
    state.pkg=pkg;state.progress={favorites:[],mastered:[],notes:{},dictations:{},...progress};
    for(const key of ['favorites','mastered'])if(!Array.isArray(state.progress[key]))state.progress[key]=[];
    for(const key of ['notes','dictations'])if(!state.progress[key]||typeof state.progress[key]!=='object')state.progress[key]={};
    state.current=Math.max(0,pkg.segments.findIndex(s=>s.id===progress.lastSegment));
    state.mode=progress.mode==='focus'?'focus':'watch';state.subtitles=['none','en','both'].includes(progress.subtitles)?progress.subtitles:'en';
    state.loop=!!progress.loop;state.analysisOpen=false;state.reveal=false;state.filter='all';state.search='';state.page='study';
    location.hash=encodeURIComponent(folder);renderStudy();
  }catch(e){toast(e.message);}finally{state.loading=false;}
}

function renderStudy(){
  const p=state.pkg;
  $('#app').innerHTML=`<section class="study"><div class="study-top"><div class="breadcrumb"><button data-home>${icon('back')}素材库</button><span>/</span><span>学习空间</span></div><span id="save-status" class="saved-indicator">${icon('check')}进度自动保存</span></div><h1 class="study-title">${esc(p.title)}</h1><div class="study-meta"><span>${esc(p.creator)}</span><span>·</span><span>${fmt(p.duration)}</span><span>·</span><span>${p.segments.length} 个精听片段</span><span>·</span><span>${esc(p.caption_source||'英文字幕')}</span></div><div id="study-jobs"></div>${p.notice?`<div class="study-notice">${esc(p.notice)}</div>`:''}<div class="study-layout"><div class="study-main"><div class="mode-row"><div class="segmented" aria-label="播放模式"><button data-mode="watch">完整观看</button><button data-mode="focus" ${p.segments.length?'':'disabled'}>逐句精听</button></div><div class="subtitle-select"><span>字幕</span><div class="segmented" aria-label="字幕模式"><button data-sub="none">无字幕</button><button data-sub="en">英文</button><button data-sub="both">中英对照</button></div></div></div><div class="player-wrap" id="player-wrap"><video id="video" src="${media(p,p.video)}" ${p.poster?`poster="${media(p,p.poster)}"`:''} preload="metadata" playsinline aria-label="${esc(p.title)}"></video><div class="player-overlay" id="player-overlay"><button class="big-play" id="big-play" aria-label="播放视频">${icon('play')}</button></div><div class="video-subtitles" aria-live="off"><div class="en" id="sub-en"></div><div class="zh" id="sub-zh"></div></div><button class="fullscreen-exit" data-exit-fullscreen>退出全屏</button></div><div class="player-controls"><input class="timeline" id="timeline" type="range" min="0" max="${p.duration||1}" step="0.05" value="0" aria-label="视频播放进度"><div class="controls-row"><button class="icon-button" id="play-toggle" aria-label="播放">${icon('play')}</button><button class="icon-button" id="replay" aria-label="重听本句 (R)" title="重听本句 · R">${icon('replay')}</button><span class="timecode" id="timecode">00:00 / ${fmt(p.duration)}</span><div class="controls-right"><button class="loop-button" id="loop-toggle" aria-label="循环本句 (L)" title="循环本句 · L">${icon('repeat')}<span>循环</span></button><select id="rate" class="rate-select" aria-label="播放速度">${[.5,.75,.85,1,1.25,1.5].map(n=>`<option value="${n}">${n}×</option>`).join('')}</select><button class="icon-button" id="mute-toggle" aria-label="静音">${icon('sound')}</button><button class="icon-button" id="fullscreen" aria-label="全屏">${icon('expand')}</button></div></div></div><div class="focus-strip"><span id="focus-label">先听懂故事，再慢慢走进每一句。</span><div class="focus-keys"><button class="icon-button" id="prev" aria-label="上一句">${icon('back')}</button><kbd>←</kbd><span id="sentence-counter"></span><kbd>→</kbd><button class="icon-button" id="next" aria-label="下一句">${icon('next')}</button></div></div><div id="lesson"></div></div><aside class="transcript-panel"><div class="panel-header"><div class="panel-title"><h3>故事里的每一句</h3><small id="list-count">${p.segments.length} 个片段</small></div><div class="filter-tabs"><button class="active" data-filter="all">全部语句</button><button data-filter="favorites">已收藏</button><button data-filter="unlearned">待练习</button></div><div class="search-box">${icon('search')}<input id="sentence-search" placeholder="搜索语句或表达…" aria-label="搜索语句"></div></div><div class="sentence-list" id="sentence-list"></div><div class="progress-footer"><span id="mastery-count"></span><div class="mini-progress"><span id="mastery-bar"></span></div></div></aside></div></section>`;
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
  const s=current();if(!$('#lesson'))return;
  if(!s){$('#lesson').innerHTML=`<div class="analysis-empty">视频已经可以完整播放。添加英文字幕后，就能解锁逐句精听与讲解。<br><button class="button" data-caption>导入英文字幕</button> <button class="button" data-analyze>识别语音并生成讲解</button><p class="fine-print">本地识别需先运行 tool/安装语音转写.command；首次运行会下载语音模型。</p></div>`;return;}
  const visible=state.subtitles!=='none'||state.reveal, favorite=state.progress.favorites.includes(s.id),mastered=state.progress.mastered.includes(s.id);
  $('#lesson').innerHTML=`<div class="lesson-top"><div class="eyebrow">ONE SENTENCE AT A TIME / ${num(state.current+1)}</div><div class="lesson-actions"><button data-favorite aria-pressed="${favorite}">${icon('star')}${favorite?'已收藏':'收藏'}</button><button data-mastered aria-pressed="${mastered}">${icon('check')}${mastered?'已掌握':'标记掌握'}</button></div></div>${visible?`<p class="current-quote">${esc(s.en)}</p>${state.subtitles==='both'||state.reveal?`<p class="current-translation">${esc(s.zh||'中文翻译尚未生成，可在下方补全讲解。')}</p>`:''}`:`<div class="blind-prompt"><h3>先让耳朵，找到答案。</h3><p>反复听这一句，试着抓住几个熟悉的词。不着急。</p><button class="button small" data-reveal>准备好了，查看原文</button></div>`}<details class="dictation" ${state.progress.dictations[s.id]?'open':''}><summary>${icon('pen')}写下我听到的 · 听写练习</summary><textarea id="dictation" placeholder="Type what you hear…" aria-label="本句听写" spellcheck="false">${esc(state.progress.dictations[s.id]||'')}</textarea><div class="dictation-bottom"><button class="button small" data-check-dictation>对照答案</button><span id="dictation-result"></span></div><p class="fine-print" id="dictation-answer" hidden></p></details><button class="analysis-toggle" id="analysis-toggle" aria-expanded="${state.analysisOpen}"><span>把这一句，听明白<span class="tag">词句讲解</span></span>${icon('chevron')}</button><div id="analysis-content" class="analysis-content" ${state.analysisOpen?'':'hidden'}>${analysisMarkup(s)}</div><label class="note-label" for="personal-note">留给自己的笔记 <span>· 自动保存</span></label><textarea class="personal-note" id="personal-note" placeholder="记下一点发现，或写一个自己的句子…">${esc(state.progress.notes[s.id]||'')}</textarea>`;
  $('#dictation').oninput=e=>{state.progress.dictations[s.id]=e.target.value;persist();};
  $('#personal-note').oninput=e=>{state.progress.notes[s.id]=e.target.value;persist();};
  $('#analysis-toggle').onclick=()=>{state.analysisOpen=!state.analysisOpen;$('#analysis-toggle').setAttribute('aria-expanded',state.analysisOpen);$('#analysis-content').hidden=!state.analysisOpen;};
}
function analysisMarkup(s){
  const a=s.analysis;
  if(!a)return `<div class="analysis-empty">这句话的讲解还没有生成。准备好后，就能查看翻译、表达用法和听音提示。<br><button class="button primary small" data-analyze>补全 AI 讲解</button><p class="fine-print">使用当前 ChatGPT 账号的 Codex 用量，只补充尚未完成的语句。</p></div>`;
  const group=(name,title,rows,key,body)=>Array.isArray(rows)&&rows.length?`<section class="analysis-section"><h3 class="section-label">${icon(name)}${title}</h3>${rows.map(row=>`<div class="analysis-item"><strong>${esc(row[key])}</strong>${body(row)}</div>`).join('')}</section>`:'';
  return `<div class="meaning"><span class="meaning-title">The meaning, simply.</span>${esc(a.meaning)}${s.zh?`<div style="margin-top:9px;color:#92977f;font-size:11px">${esc(s.zh)}</div>`:''}</div><div class="analysis-columns"><div>${group('book','把表达装进口袋',a.vocabulary,'term',r=>`${esc(r.meaning)}<div class="example">${esc(r.example)}</div>`)}</div><div>${group('lines','句子是怎样组成的',a.grammar,'pattern',r=>esc(r.explanation))}${group('sound','耳朵可以留意的细节',a.speech,'text',r=>esc(r.tip))}<p class="speech-note">发音提示基于常见语言规律生成，具体读法请对照原音。</p></div></div>${a.paraphrase?`<div class="practice-card"><small>IN OTHER WORDS</small>${esc(a.paraphrase)}</div>`:''}${a.practice?`<div class="practice-card"><small>YOUR TURN / 换你来说</small>${esc(a.practice)}</div>`:''}`;
}
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
  if(!state.pkg)return;const video=$('#video');
  Object.assign(state.progress,{lastTime:video?.currentTime||0,lastSegment:current()?.id,mode:state.mode,subtitles:state.subtitles,loop:state.loop});
  const payload={folder:state.pkg.folder,progress:structuredClone(state.progress)},revision=++saveRevision;
  clearTimeout(state.saveTimer);
  const save=async()=>{try{await api('/api/progress',payload);if(revision===saveRevision&&$('#save-status')){$('#save-status').innerHTML=`${icon('check')}进度已保存`;$('#save-status').classList.remove('progress-error');}}catch(e){if($('#save-status')){$('#save-status').textContent='进度保存失败';$('#save-status').classList.add('progress-error');}}};
  if(immediate)save();else state.saveTimer=setTimeout(save,650);
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
        if(before&&state.jobs.some(j=>j.status==='complete'))await loadLibrary();
      }else if(state.pkg){
        const matching=state.jobs.find(j=>j.folder===state.pkg.folder&&['running','queued'].includes(j.status));
        if($('#study-jobs'))$('#study-jobs').innerHTML=matching?`<div class="study-notice">${esc(matching.message)} · 可以先练习已经准备好的语句。</div>`:'';
        const updated=await api(`/api/package?folder=${encodeURIComponent(state.pkg.folder)}`);
        const changed=updated.segments.length!==state.pkg.segments.length||updated.segments.filter(s=>s.analysis).length!==state.pkg.segments.filter(s=>s.analysis).length;
        if(changed){
          const oldId=current()?.id, countChanged=updated.segments.length!==state.pkg.segments.length;
          state.progress.lastTime=$('#video')?.currentTime||0;
          state.pkg={...updated,folder:state.pkg.folder};state.current=Math.max(0,state.pkg.segments.findIndex(s=>s.id===oldId));
          if(countChanged)renderStudy();
          else{
            if($('#analysis-content')&&current())$('#analysis-content').innerHTML=analysisMarkup(current());
            if($('.current-translation'))$('.current-translation').textContent=current()?.zh||'中文翻译尚未生成，可在下方补全讲解。';
            renderList();
          }
        }
      }
    }
  }catch{/* Server may be restarting. Preserve the active lesson. */}
}

async function importFiles(entries){
  if(!entries.length)return;
  const manifests=entries.filter(e=>e.path.split('/').pop()==='manifest.json');
  if(manifests.length!==1){toast('请选择一个包含 manifest.json 的完整素材包文件夹');return;}
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
$('#home').onclick=home;$('#library-nav').onclick=home;$('#guide-nav').onclick=()=>$('#guide-dialog').showModal();
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  try{
    if(b.hasAttribute('data-new'))showImport();
    else if(b.hasAttribute('data-import'))$('#folder-input').click();
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
    else if(b.hasAttribute('data-check-dictation'))checkDictation();
    else if(b.hasAttribute('data-caption'))$('#caption-input').click();
    else if(b.hasAttribute('data-analyze')){await api('/api/analyze',{folder:state.pkg.folder});toast('正在补全未完成的讲解');await pollJobs();}
    else if(b.dataset.cancel){await api('/api/cancel',{id:b.dataset.cancel});await pollJobs();}
    else if(b.dataset.dismiss){dismissed.add(b.dataset.dismiss);await pollJobs();if($('#jobs-area'))$('#jobs-area').innerHTML=jobCards();}
    else if(b.dataset.retry){const j=state.jobs.find(x=>x.id===b.dataset.retry);if(j.folder&&(j.kind==='analysis'||['analysis','transcribe'].includes(j.stage)))await api('/api/analyze',{folder:j.folder});else await api('/api/download',{url:j.url,analyze:j.analyze,browser:j.browser});dismissed.add(j.id);await pollJobs();}
    else if(b.hasAttribute('data-exit-fullscreen'))document.exitFullscreen();
  }catch(err){toast(err.message);}
});
document.addEventListener('keydown',e=>{
  if(state.page!=='study'||document.querySelector('dialog[open]')||e.ctrlKey||e.metaKey||e.altKey||e.target.closest('input,textarea,select,[contenteditable]'))return;
  if(e.code==='Space'&&e.target.closest('button'))return;
  if(e.code==='Space'){e.preventDefault();togglePlay();}
  if(e.code==='ArrowLeft'){e.preventDefault();selectSentence(state.current-1);}
  if(e.code==='ArrowRight'){e.preventDefault();selectSentence(state.current+1);}
  if(e.code==='KeyR')replay();if(e.code==='KeyL')toggleLoop();if(e.code==='KeyC'){const modes=['none','en','both'];setSubtitles(modes[(modes.indexOf(state.subtitles)+1)%3]);}
});

async function boot(){
  try{const status=await api('/api/status');state.token=status.token;$('#connection').innerHTML=`<span class="status-dot" style="${status.ai.ready?'':'background:#bda06e'}"></span>${status.ai.ready?'ChatGPT 已连接 · 本地学习':'本地学习空间'}`;$('#connection').title=status.ai.message;
    await pollJobs();const folder=decodeURIComponent(location.hash.slice(1));if(folder)await openPackage(folder);else await loadLibrary();setInterval(pollJobs,3000);
  }catch(e){$('#app').innerHTML='<div class="empty">本地服务尚未启动。请双击 tool/启动.command，然后重新打开此页面。</div>';}
}
boot();
