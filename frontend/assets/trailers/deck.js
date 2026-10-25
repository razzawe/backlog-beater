import { historyInterest, visibleReason } from '../history-interest.js';
// Paginated recommendation trailers; video is delivered directly by Steam.
export function mountDeck({loadPlayer,allowed,stopPreview,api,getState,performAction}) {
 const form=document.getElementById('recommendation-form');
 const element=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
 const css=element('style');css.textContent=`.trailer-deck{width:min(900px,100vw - 20px);max-height:96dvh;padding:0;border:1px solid #454052;border-radius:18px;background:#101218;color:#edf0f8;overflow:hidden}.trailer-deck[open]{display:flex;flex-direction:column}.trailer-deck::backdrop{background:#000d}.deck-header{padding:16px 20px;display:flex;justify-content:space-between;align-items:center;gap:12px}.deck-header h2{margin:0;font-size:20px}.deck-preferences{flex-shrink:0;max-height:90px;overflow:auto;display:flex;flex-wrap:wrap;gap:7px;padding:0 20px 14px}.deck-chip{background:#baa2ff18;color:#d3c3ff;border:1px solid #baa2ff33;border-radius:20px;padding:5px 10px;font-size:12px}.deck-scroll{flex:1 1 auto;min-height:180px;height:min(68dvh,680px);overflow-y:auto;overscroll-behavior:contain;scroll-snap-type:y mandatory;scrollbar-width:thin}.deck-card{height:100%;min-height:100%;scroll-snap-align:start;scroll-snap-stop:always;display:flex;flex-direction:column;justify-content:center;background:linear-gradient(180deg,#171922,#101218);padding:16px 24px;box-sizing:border-box;gap:12px}.deck-card video{width:100%;min-height:0;max-height:43dvh;aspect-ratio:16/9;background:#000;border-radius:10px}.deck-card h3{margin:0;font-size:24px}.deck-card p{margin:0;color:#aeb4c6;line-height:1.5;font-size:13px}.deck-actions{display:flex;flex-wrap:wrap;gap:8px}.deck-actions button{font-size:12px;padding:9px 12px}.deck-footer{flex-shrink:0;display:flex;align-items:center;justify-content:center;gap:14px;padding:12px 20px;border-top:1px solid #282c38}.deck-message{padding:20px;color:#aeb4c6}.deck-hint{font-size:11px;color:#9297aa}.deck-card .deck-status{min-height:18px;font-size:11px}@media(max-width:600px){.deck-card{padding:12px}.deck-card h3{font-size:20px}.deck-header{padding:12px}.deck-preferences{padding:0 12px 10px}.deck-scroll{flex:1 1 auto;min-height:180px;height:65dvh}.deck-footer{padding:10px}.deck-footer button{padding:8px 12px}}@media(prefers-reduced-motion:reduce){.deck-scroll{scroll-behavior:auto}}`;
 css.textContent += `.deck-preference-bar{align-items:center;gap:10px 16px;margin:0 20px 14px;padding:11px 13px;max-height:110px;border:1px solid #282c38;border-radius:11px;background:#171a22}.deck-preference-summary{display:flex;flex-wrap:wrap;align-items:center;gap:5px 0;flex:1;color:#d4d8e4;font-size:12px;line-height:1.6}.deck-setting{white-space:nowrap}.deck-setting+.deck-setting::before{content:'·';padding:0 10px;color:#60677b}.deck-edit{flex-shrink:0;margin-left:auto;padding:5px 2px;border:0;background:none;color:#baa2ff;font-size:12px;cursor:pointer}.deck-edit:hover{color:#e2d8ff;text-decoration:underline;text-underline-offset:3px}.deck-preference-genres{display:flex;flex-wrap:wrap;gap:6px;flex-basis:100%}.deck-preference-bar .deck-chip{padding:3px 9px;border-color:transparent;background:#baa2ff10;font-size:11px;line-height:1.5}@media(max-width:600px){.deck-preference-bar{margin:0 12px 10px;padding:9px 11px;gap:7px 10px}.deck-preference-summary{font-size:11px}.deck-setting+.deck-setting::before{padding:0 7px}.deck-edit{font-size:11px}}`;
 document.head.append(css);
 const dialog=element('dialog',null,'trailer-deck');dialog.setAttribute('aria-labelledby','trailer-deck-title');
 const header=element('header',null,'deck-header'),heading=element('h2','Explore trailers');heading.id='trailer-deck-title';
 const exit=element('button','Back to recommendations','button secondary');exit.type='button';exit.onclick=()=>{setView('tiles');dialog.close();};header.append(heading,exit);
 const chips=element('div',null,'deck-preferences deck-preference-bar');
 const edit=element('button','Edit preferences','deck-edit');edit.type='button';edit.onclick=()=>{dialog.close();form.scrollIntoView({block:'center'});form.elements.weekly_hours.focus();};
 const scroller=element('div',null,'deck-scroll');scroller.tabIndex=0;scroller.setAttribute('aria-label','Recommended trailer deck. Scroll or use arrow keys to browse.');
 const footer=element('nav',null,'deck-footer');footer.setAttribute('aria-label','Trailer navigation');
 const prev=element('button','↑ Previous','button secondary'),next=element('button','Next ↓','button secondary'),counter=element('span','Loading…');prev.type=next.type='button';counter.setAttribute('role','status');footer.append(prev,counter,next);
 dialog.append(header,chips,scroller,footer);document.body.append(dialog);
 let items=[],cards=[],active=-1,player=null,controller=null,run=0,mediaRun=0,observer=null,accountToken=null,preferences=null,nextOffset=0,hasMore=false,loadingNext=false,total=0;
 function stop(){mediaRun++;player?.destroy();player=null;cards.forEach(c=>{c.video.pause();c.video.removeAttribute('src');c.video.load();});}
 function go(index){if(index>=cards.length&&hasMore){loadNext().then(()=>{if(cards[index])go(index);});return;}if(cards[index])scroller.scrollTo({top:cards[index].article.offsetTop-cards[0].article.offsetTop,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});}
 new ResizeObserver(()=>{if(dialog.open&&active>=0&&cards[active])scroller.scrollTop=cards[active].article.offsetTop-cards[0].article.offsetTop;}).observe(scroller);
 prev.onclick=()=>go(active-1);next.onclick=()=>go(active+1);
 scroller.addEventListener('keydown',e=>{if(e.target.tagName==='VIDEO'||e.target.tagName==='BUTTON')return;if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();go(active+(e.key==='ArrowDown'?1:-1));}});
 async function activate(index,manual=false){
  if((index===active&&!manual)||!dialog.open)return;stop();active=index;const ownRun=mediaRun,c=cards[index],item=items[index];
  prev.disabled=index===0;next.disabled=index===cards.length-1&&(!hasMore||loadingNext);counter.textContent=`${index+1} of ${total} games`;
  if(index>=cards.length-2&&hasMore)loadNext();
  c.status.textContent=matchMedia('(prefers-reduced-motion: reduce)').matches||navigator.connection?.saveData?'Press play to preview.':'Loading trailer…';
  c.playButton.hidden=true;
  if(!allowed(item.url)){c.status.textContent='No trailer available. Cover art shown instead.';return;}
  if(!manual&&(matchMedia('(prefers-reduced-motion: reduce)').matches||navigator.connection?.saveData)){c.playButton.hidden=false;return;}
  try{
   if(item.format==='hls'&&!c.video.canPlayType('application/vnd.apple.mpegurl')){
    const Hls=await loadPlayer();if(ownRun!==mediaRun||!dialog.open)return;
    if(!Hls.isSupported())throw Error('Trailer playback is unavailable in this browser.');
    player=new Hls({maxBufferLength:10,maxMaxBufferLength:20});
    player.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal&&ownRun===mediaRun)c.status.textContent='Trailer unavailable. You can still browse this game.';});
    player.loadSource(item.url);player.attachMedia(c.video);
   }else c.video.src=item.url;
   if(manual||(!matchMedia('(prefers-reduced-motion: reduce)').matches&&!navigator.connection?.saveData))c.video.play().catch(()=>{if(ownRun===mediaRun)c.status.textContent='Press play to watch.';});
  }catch(error){if(ownRun===mediaRun)c.status.textContent=error.message;}
 }
 function show(itemsToShow,totalCount,append=false){
  total=totalCount;const startIndex=append?cards.length:0;
  if(append)items.push(...itemsToShow);else{stop();items=itemsToShow;active=-1;observer?.disconnect();cards=[];scroller.replaceChildren();}
  if(!items.length){scroller.append(element('p','No games match these preferences. Edit your time window or completion goal and try again.','deck-message'));counter.textContent='No matches';prev.disabled=next.disabled=true;return;}
  itemsToShow.forEach((item,localIndex)=>{const index=startIndex+localIndex;
   const article=element('article',null,'deck-card');article.dataset.index=index;article.setAttribute('aria-label',`${index+1}: ${item.title}`);
   const video=element('video');video.controls=true;video.muted=true;video.playsInline=true;video.preload='none';if(!item.url)video.controls=false;if(allowed(item.thumbnail))video.poster=item.thumbnail;
   const status=element('p','', 'deck-status');status.setAttribute('role','status');video.onplaying=()=>status.textContent='Muted initially · use the video controls for sound.';
   const playButton=element('button','Play trailer','button secondary');playButton.type='button';playButton.hidden=true;playButton.onclick=()=>activate(index,true);
   article.append(video,playButton,element('h3',item.title),element('p',`${item.estimated_hours} hours · About ${Number(item.estimated_weeks.toFixed(1))} weeks at your pace`));
   const matches=element('div',null,'deck-preferences');matches.style.padding='0';item.matched_genres.forEach(id=>matches.append(element('span','#'+genreName(id),'deck-chip')));article.append(matches);
   const history=historyInterest(item);if(history)article.append(history);
   article.append(element('p',item.reasons.filter(r=>visibleReason(r, item)).join(' ')));
   const actions=element('div',null,'deck-actions');
   for(const [action,label] of [['start','Start Playing'],['queue','Play Next'],['exclude','Exclude']]){
    const button=element('button',label,action==='start'?'button primary':'button secondary');button.type='button';
    button.onclick=async()=>{if(sessionStorage.getItem('backlog-token')!==accountToken){dialog.close();return;}actions.querySelectorAll('button').forEach(b=>b.disabled=true);
     const result=await performAction(action,Number(item.game_id));
     if(!dialog.open)return;status.textContent=result?.ok?(action==='start'?'Game started.':action==='queue'?'Added to Play Next.':'Game excluded.'):'Could not save. Check the message on your library.';
     if(result?.ok&&action!=='queue')dialog.close();else actions.querySelectorAll('button').forEach(b=>b.disabled=false);
    };actions.append(button);
   }
   article.append(actions,status,element('p',index===total-1?'End of your recommendations. Your queue only changes when you choose an action.':'Scroll for the next game · passing does not exclude it.','deck-hint'));scroller.append(article);cards.push({article,video,status,playButton});
  });
  if(!append){observer=new IntersectionObserver(rows=>{const visible=rows.filter(r=>r.isIntersecting&&r.intersectionRatio>=.6).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];if(visible)activate(Number(visible.target.dataset.index));},{root:scroller,threshold:[.6,.8]});scroller.scrollTop=0;}
  cards.slice(startIndex).forEach(c=>observer.observe(c.article));counter.textContent=`${Math.max(1,active+1)} of ${total} games`;prev.disabled=active<=0;next.disabled=active>=cards.length-1&&!hasMore;

 }
 function genreName(id){return [...form.querySelectorAll('input[name="genre"]')].find(c=>c.value===id)?.dataset.name||id;}
 async function open(){
  if(!sessionStorage.getItem('backlog-token')||form.hidden){form.closest('section').scrollIntoView();return;}
  if(!form.reportValidity())return;
  stopPreview();setView('trailers');dialog.showModal();const version=++run;controller=new AbortController();accountToken=sessionStorage.getItem('backlog-token');chips.replaceChildren();scroller.replaceChildren(element('p','Finding trailers for your preferences…','deck-message'));counter.textContent='Loading…';prev.disabled=next.disabled=true;
  const data=new FormData(form),body={weekly_hours:Number(data.get('weekly_hours')),target_weeks:Number(data.get('target_weeks')),completion_goal:data.get('completion_goal'),preferred_genres:data.getAll('genre'),include_free_games:data.get('include_free')==='on'};
  const summary=element('div',null,'deck-preference-summary');
  for(const text of [`${body.weekly_hours}h / week`,`${body.target_weeks}-week window`,form.elements.completion_goal.selectedOptions[0].textContent])summary.append(element('span',text,'deck-setting'));
  chips.append(summary,edit);
  if(body.preferred_genres.length){const genres=element('div',null,'deck-preference-genres');body.preferred_genres.forEach(id=>genres.append(element('span','#'+genreName(id),'deck-chip')));chips.append(genres);}

  preferences=body;nextOffset=0;hasMore=true;loadingNext=false;items=[];cards=[];active=-1;observer?.disconnect();
  await loadNext();
 }
 async function loadNext(){
  if(loadingNext||!hasMore||!dialog.open)return;
  loadingNext=true;const version=run,session=getState().generation;
  try{
   const result=await api('/recommendations',{method:'POST',body:{...preferences,offset:nextOffset}});
   if(version!==run||!dialog.open||session!==getState().generation)return;
   nextOffset=result.pagination.offset+result.pagination.limit;hasMore=result.pagination.has_more;
   show(result.recommendations.map(game=>({...game,url:game.trailer_url,thumbnail:game.trailer_thumbnail||game.cover_url,format:game.trailer_format})),result.pagination.total,cards.length>0);
  }catch(error){if(version===run&&dialog.open){if(cards.length){cards[Math.max(0,active)].status.textContent='Could not load more games. Press Next to retry.';next.disabled=false;}else{scroller.replaceChildren(element('p',error.message,'deck-message'));counter.textContent='Unavailable';}}}
  finally{if(version===run){loadingNext=false;next.disabled=active>=cards.length-1&&!hasMore;}}
 }

 new MutationObserver(()=>{if(form.hidden){stop();if(dialog.open)dialog.close();items=[];cards=[];scroller.replaceChildren();}}).observe(form,{attributes:true,attributeFilter:['hidden']});
 dialog.addEventListener('cancel',()=>setView('tiles'));
 dialog.addEventListener('close',()=>{setView('tiles');run++;controller?.abort();observer?.disconnect();stop();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)cards.forEach(c=>c.video.pause());});
 const switcher=element('div',null,'deck-view-switch');
 const label=element('span','Browse as','deck-view-label');
 const group=element('div',null,'deck-view-segments');group.setAttribute('role','group');group.setAttribute('aria-label','Recommendation view');
 const tilesButton=element('button','Tiles'),trailersButton=element('button','Trailers');
 tilesButton.type=trailersButton.type='button';tilesButton.setAttribute('aria-pressed','true');trailersButton.setAttribute('aria-pressed','false');
 tilesButton.onclick=()=>{if(dialog.open)dialog.close();setView('tiles');};trailersButton.onclick=open;
 group.append(tilesButton,trailersButton);switcher.append(label,group);
 form.querySelector('.recommendation-submit').before(switcher);
 function setView(view){tilesButton.setAttribute('aria-pressed',String(view==='tiles'));trailersButton.setAttribute('aria-pressed',String(view==='trailers'));}
 const switchCSS=element('style');switchCSS.textContent=`.deck-view-switch{display:flex;align-items:center;gap:12px;margin:18px 0}.deck-view-label{font-size:12px;color:#9297aa}.deck-view-segments{display:inline-flex;padding:4px;border:1px solid #303543;border-radius:10px;background:#101218;gap:3px}.deck-view-segments button{border:0;background:transparent;border-radius:7px;padding:8px 20px;color:#9297aa;cursor:pointer;font-size:12px;min-width:88px}.deck-view-segments button[aria-pressed="true"]{background:#baa2ff22;color:#dfd2ff}.deck-view-segments button:hover{color:#edf0f8}`;document.head.append(switchCSS);
}
