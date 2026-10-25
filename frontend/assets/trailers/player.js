// Click-to-play previews and shared HLS loading.
export function mountTrailers({api,getState,performAction}) {
const style = document.createElement('style');
style.textContent = `.trailer-dialog{width:min(900px,calc(100vw - 28px));padding:20px;border:1px solid #454052;border-radius:14px;background:#181b24;color:#edf0f8}.trailer-dialog::backdrop{background:#000b}.trailer-dialog header{display:flex;justify-content:space-between;gap:16px;align-items:center}.trailer-dialog h2{font-size:20px}.trailer-dialog video{display:block;width:100%;aspect-ratio:16/9;max-height:65vh;background:#000;margin:16px 0}.trailer-dialog select{max-width:100%;padding:10px;background:#101218;color:inherit;border:1px solid #454052;border-radius:8px}.trailer-dialog p{color:#9297aa;font-size:13px}.trailer-preview-link{margin-top:12px}`;
document.head.append(style);
const dialog = document.createElement('dialog'); dialog.className = 'trailer-dialog'; dialog.setAttribute('aria-labelledby','trailer-preview-title');
const header = document.createElement('header'), title = document.createElement('h2'); title.id = 'trailer-preview-title';
const close = document.createElement('button'); close.type='button'; close.className='button secondary';close.textContent='Close trailer';close.addEventListener('click',()=>dialog.close());
header.append(title,close);
const choices=document.createElement('select');choices.setAttribute('aria-label','Preview a game trailer');
const video=document.createElement('video'); video.controls=true;video.muted=true;video.playsInline=true;video.preload='none';
const status=document.createElement('p');status.setAttribute('role','status');
dialog.append(header,video,status);document.body.append(dialog);
let player=null, sequence=0, entries={}, scriptPromise;
function stop(){sequence++;video.pause();if(player){player.destroy();player=null;}video.removeAttribute('src');video.removeAttribute('poster');video.load();}
dialog.addEventListener('close',stop);
dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden)video.pause();});
function allowed(url){try{const u=new URL(url);return u.protocol==='https:'&&['video.akamai.steamstatic.com','video.cloudflare.steamstatic.com','cdn.akamai.steamstatic.com','cdn.cloudflare.steamstatic.com','shared.akamai.steamstatic.com','shared.cloudflare.steamstatic.com'].includes(u.hostname)&&!u.username&&!u.password;}catch{return false;}}
function loadPlayer(){return scriptPromise ||= new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=new URL('./hls.min.js',import.meta.url);script.onload=()=>resolve(window.Hls);script.onerror=()=>reject(new Error('Video player could not load.'));document.head.append(script);});}
async function play(id){
 stop();const run=sequence,item=entries[id];if(!item||!allowed(item.url))return;
 choices.value=id;title.textContent=item.title;status.textContent='Loading trailer…';video.muted=true;
 if(allowed(item.thumbnail))video.poster=item.thumbnail;
 try{
  if(item.format==='hls'&&!video.canPlayType('application/vnd.apple.mpegurl')){
   const Hls=await loadPlayer();if(run!==sequence||!dialog.open)return;
   if(!Hls.isSupported())throw new Error('This browser cannot play this trailer.');
   player=new Hls({maxBufferLength:15,maxMaxBufferLength:30});
   player.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal&&run===sequence){status.textContent='Trailer unavailable. Close it and keep browsing.';player?.destroy();player=null;}});
   player.loadSource(item.url);player.attachMedia(video);
  }else video.src=item.url;
  video.play().catch(()=>{if(run===sequence)status.textContent='Press play to watch. Sound starts muted.';});
 }catch(error){if(run===sequence)status.textContent=error.message;}
}
video.addEventListener('playing',()=>status.textContent='Sound starts muted. Use the player controls to enable it.');
video.addEventListener('error',()=>{if(dialog.open)status.textContent='Trailer unavailable. Close it and keep browsing.';});
choices.addEventListener('change',()=>play(choices.value));
function open(id){if(!dialog.open)dialog.showModal();play(id);}
const results=document.getElementById('recommendation-results');
function update(){results.querySelectorAll('article').forEach(card=>{
 const item=card._trailerRecommendation;
 if(!item?.trailer_url||card.querySelector('.watch-trailer'))return;
 entries[String(item.game_id)]={title:item.title,url:item.trailer_url,thumbnail:item.trailer_thumbnail||item.cover_url,format:item.trailer_format};
 const button=document.createElement('button');button.type='button';button.className='button secondary watch-trailer';button.textContent='Watch trailer';button.onclick=()=>open(String(item.game_id));card.querySelector('.recommendation-actions')?.append(button);
});if(document.getElementById('recommendation-form').hidden&&dialog.open)dialog.close();}
new MutationObserver(update).observe(results,{childList:true,subtree:true});
import('./deck.js').then(({mountDeck})=>mountDeck({api,getState,performAction,loadPlayer,allowed,stopPreview:()=>{if(dialog.open)dialog.close();stop();}}));
}
