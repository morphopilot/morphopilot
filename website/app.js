// SPDX-License-Identifier: Apache-2.0
'use strict';
const $ = (s) => document.querySelector(s);
const all = (s) => [...document.querySelectorAll(s)];
const stack = window.MORPHOPILOT_STACK;
const asset = name => window.MORPHOPILOT_ASSET_URLS?.[name] || name;
const frameCount = Math.max(1, Number(stack?.frame_count) || 25);
let dataset = 'neurite', frame = Math.min(frameCount - 1, Math.max(0, Number(stack?.initial_z) || 0)), timer = null;
const raw = $('#raw-view'), mask = $('#mask-view'), playButton = $('#play-stack');
const rawCanvas = document.createElement('canvas'), maskCanvas = document.createElement('canvas');
for (const canvas of [rawCanvas, maskCanvas]) { canvas.width=768; canvas.height=768; canvas.setAttribute('aria-hidden','true'); }
raw.append(rawCanvas); mask.append(maskCanvas);
const sprites = {
 neurite: [new Image(), new Image()],
 spot: [new Image(), new Image()],
};
const failedSprites = new Set();
function isReady() { return Boolean(stack) && sprites[dataset].every(sprite => sprite.complete && sprite.naturalWidth > 0 && !failedSprites.has(sprite)); }
function updatePlayback() {
 const ready=isReady(), playing=timer!==null, failed=sprites[dataset].some(sprite=>failedSprites.has(sprite));
 playButton.disabled=!ready;
 playButton.textContent=playing?'Ⅱ Pause':ready?'▷ Play':failed?'Unavailable':'Loading…';
 playButton.setAttribute('aria-label',playing?'Pause Z slices':'Play Z slices');
 playButton.setAttribute('aria-pressed',String(playing));
 $('#z-range').disabled=!ready;
}
function paint(canvas,sprite) {
 const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
 if(!sprite.complete||!sprite.naturalWidth||!stack||failedSprites.has(sprite))return;
 ctx.imageSmoothingEnabled=true;
 ctx.drawImage(sprite,(frame%stack.columns)*stack.frame_width,Math.floor(frame/stack.columns)*stack.frame_height,stack.frame_width,stack.frame_height,0,0,canvas.width,canvas.height);

}
function renderFrame(){
 $('#z-output').textContent=`${String(frame+1).padStart(2,'0')} / ${frameCount}`;
 $('#z-range').value=String(frame);
 const neuron=dataset==='neurite';
 // Both views use exactly the same Z plane and image coordinates.
 paint(rawCanvas,sprites[dataset][0]);paint(maskCanvas,sprites[dataset][1]);
 const failed=sprites[dataset].some(sprite=>failedSprites.has(sprite));
 $('#mask-legend-swatch').style.backgroundColor=neuron?'#b6f278':'#ff781f';
 $('#mask-legend-text').textContent=neuron?'Reference mask · lime':'Reference mask · orange';
 $('#example-caption').textContent=failed?'Synthetic preview asset could not load. Reload the page to try again.':`SYNTHETIC ${neuron?'branching structures':'spots'} · Z ${frame+1}/${frameCount}. Full geometric reference in ${neuron?'lime green':'orange'}; no model runs in this preview.`;
 updatePlayback();
}
function stop(){if(timer!==null)clearInterval(timer);timer=null;updatePlayback();}
function nextFrame(){frame=(frame+1)%frameCount;renderFrame();}
function setComparison(value){
 const position=Math.max(0,Math.min(100,Number(value)||0));
 mask.style.clipPath=`inset(0 0 0 ${position}%)`;
 $('#divider').style.left=position+'%';
 $('#divider').hidden=position===0||position===100;
 $('#comparison-range').value=String(position);
 $('#comparison-range').setAttribute('aria-valuetext',position===0?'Full reference mask':position===100?'Original image only':`Original image left; reference mask right; divider at ${position}%`);
 $('#comparison-state').textContent=position===0?'Full reference mask':position===100?'Original image only':'Original image ← → Reference mask';
 $('#raw-label').hidden=position===0;
 $('#mask-label').hidden=position===100;
 all('[data-comparison]').forEach(button=>{
  const active=Number(button.dataset.comparison)===position;
  button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
 });
}
playButton.addEventListener('click',()=>{
 if(timer!==null){stop();return;}
 if(!isReady())return;
 // Advance immediately so the click has visible feedback even during slow playback.
 timer=setInterval(nextFrame,1050);nextFrame();
});
$('#z-range').max=String(frameCount-1);
$('#z-range').addEventListener('input',e=>{stop();frame=Math.max(0,Math.min(frameCount-1,Number(e.target.value)||0));renderFrame();});
$('#comparison-range').addEventListener('input',e=>setComparison(e.target.value));
all('[data-comparison]').forEach(button=>button.addEventListener('click',()=>setComparison(button.dataset.comparison)));
all('[data-dataset]').forEach(button=>button.addEventListener('click',()=>{
 if(!sprites[button.dataset.dataset])return;
 stop();dataset=button.dataset.dataset;all('[data-dataset]').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
 raw.style.backgroundImage=`url('${asset(`assets/${dataset==='neurite'?'neurite':'spot'}-raw.webp`)}')`;mask.style.backgroundImage=`url('${asset(`assets/${dataset==='neurite'?'neurite':'spot'}-mask.webp`)}')`;
 renderFrame();
}));
for(const [name,pair] of Object.entries(sprites))pair.forEach((sprite,index)=>{
 // Register handlers before assigning src, including synchronous cached data URLs.
 sprite.onload=renderFrame;
 sprite.onerror=()=>{failedSprites.add(sprite);if(sprites[dataset].includes(sprite))stop();renderFrame();};
 const key=name==='neurite'?(index?'mask_sprite':'raw_sprite'):(index?'spot_mask_sprite':'spot_raw_sprite');
 sprite.src=asset(stack?.[key]||`assets/${name==='spot'?'spot-':''}stack-${index?'mask':'raw'}.webp`);
});
setComparison($('#comparison-range').value);
const steps=[
 ['LARGE VOLUME → MANAGEABLE TILES','Start with the<br>whole picture.','Upload a single-channel 3D image. MorphoPilot splits it into annotation tiles, keeps their original coordinates, and saves your work in a persistent data library.',['TIFF, BigTIFF, NPY and NPZ','Default tiles: 25 × 192 × 192 voxels','Resume each image where you left off'],'hero-neurites.webp','ILLUSTRATION / COORDINATE-PRESERVING TILING'],
 ['EXPERT GUIDANCE → REVIEWED MASKS','Keep your expertise<br>in every decision.','Place positive prompts on the structures you want and negative prompts on clear background. Inspect the Z stack, refine the segmentation, and confirm the full tile.',['Pretrained interactive segmentation model','Paint, erase, undo and adjust the view','Only explicitly confirmed masks enter training'],'neurite-mask.webp','SYNTHETIC INPUT + KNOWN GEOMETRIC REFERENCE'],
 ['CONFIRMED ANNOTATIONS → UPDATED WEIGHTS','Let the next model<br>learn from your work.','Pair each source image with its confirmed binary mask, then fine-tune the category model on those examples. Use the updated model on the next image and inspect its colored mask overlay.',['One independent model per data category','Prompt stem retained from original weights','Auto stem inactive during Prompt training'],'training-pairs.png','TRAINING SCHEMATIC / IMAGE + BINARY MASK PAIRS'],
 ['REVIEWED TILES → THE COMPLETE VOLUME','Bring the full<br>picture together.','Review every tile, including empty and edge tiles. Assemble saved masks at their original coordinates and export a full-resolution mask for downstream analysis.',['Global image and mask visualization','Visible review status for every tile','Original volume dimensions preserved'],'hero-neurites.webp','SYNTHETIC FULL-VOLUME PROJECTION / TILING SCHEMATIC']
];
function selectStep(i,focus=false){const data=steps[i];all('[data-step]').forEach((b,j)=>{b.setAttribute('aria-selected',String(j===i));b.tabIndex=j===i?0:-1;if(j===i&&focus)b.focus();});$('#workflow-panel').setAttribute('aria-labelledby','step-'+i);$('#workflow-label').textContent=data[0];$('#workflow-title').innerHTML=data[1];$('#workflow-copy').textContent=data[2];$('#workflow-points').replaceChildren(...data[3].map(t=>{const li=document.createElement('li');li.textContent=t;return li;}));$('#workflow-art').dataset.step=String(i);$('#workflow-art img').src=asset('assets/'+data[4]);$('#workflow-art img').alt=data[5];$('#workflow-art .art-caption').textContent=data[5];}
all('[data-step]').forEach((button,i)=>{button.addEventListener('click',()=>selectStep(i));button.addEventListener('keydown',e=>{let j=i;if(e.key==='ArrowRight')j=(i+1)%4;else if(e.key==='ArrowLeft')j=(i+3)%4;else if(e.key==='Home')j=0;else if(e.key==='End')j=3;else return;e.preventDefault();selectStep(j,true);});});
all('[data-mode]').filter(x=>x.tagName==='BUTTON').forEach(button=>button.addEventListener('click',()=>{const auto=button.dataset.mode==='auto';all('.mode-buttons button').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});$('.model-graphic').dataset.mode=auto?'auto':'prompt';$('#mode-title').textContent=auto?'Move toward image-only segmentation.':'Keep the expert in the loop.';$('#mode-copy').textContent=auto?'Convert the adapted model, then train the Auto pathway with reviewed image–mask pairs. It predicts without prompts. Evaluate it on held-out data before relying on its output.':'The original prompt pathway takes the image and your interactions. Confirmed masks become training data. The Auto stem stays inactive during this stage.';}));
function openDialog(id){stop();$(id).showModal();document.body.classList.add('modal-open');}
$('#expand-screenshot').addEventListener('click',()=>openDialog('#image-dialog'));$('#deploy-guide').addEventListener('click',()=>openDialog('#guide-dialog'));
all('dialog').forEach(dialog=>{dialog.querySelector('.dialog-close').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>document.body.classList.remove('modal-open'));dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});});
$('.menu').addEventListener('click',()=>{const open=$('.header nav').classList.toggle('open');$('.menu').setAttribute('aria-expanded',String(open));$('.menu').setAttribute('aria-label',open?'Close navigation':'Open navigation');$('.menu').textContent=open?'×':'☰';});all('.header nav a').forEach(a=>a.addEventListener('click',()=>{$('.header nav').classList.remove('open');$('.menu').setAttribute('aria-expanded','false');$('.menu').setAttribute('aria-label','Open navigation');$('.menu').textContent='☰';}));
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});window.addEventListener('pagehide',stop);
// Optional public links, with protocol validation and no embedded credentials.
function publicUrl(value){try{const u=new URL(value);return /^https?:$/.test(u.protocol)&&!u.username&&!u.password?u.href:null;}catch{return null;}}
const config=window.MORPHOPILOT_CONFIG||{};
const repository=publicUrl(config.repositoryUrl);if(repository)all('[data-repository-link]').forEach(link=>{link.href=repository;link.textContent='Source repository ↗';link.target='_blank';link.rel='noopener noreferrer';});
if(repository){
 const base=repository.replace(/\/$/,'');const branch=encodeURIComponent(config.repositoryBranch||'main');
 all('a[href^="../"]').forEach(link=>{const path=link.getAttribute('href').slice(3);link.href=base+(path.endsWith('.tif')?'/raw/':'/blob/')+branch+'/'+path;link.removeAttribute('download');link.target='_blank';link.rel='noopener noreferrer';});
}
// A website-only public deployment has no parent repository files. If its
// source URL is not configured yet, show an explicit notice instead of a 404.
const localPreview=location.protocol==='file:'||['localhost','127.0.0.1','[::1]'].includes(location.hostname);
if(!repository&&!localPreview)all('a[href^="../"]').forEach(link=>{
 link.dataset.repositoryRequired='true';
 link.addEventListener('click',event=>{
  if(link.dataset.studioConfigured==='true')return;
  event.preventDefault();
  const notice=$('#repository-notice');notice.hidden=false;
  notice.textContent='The project repository link has not been configured for this website yet. The site maintainer must set repositoryUrl in config.js to enable source, documentation and fixture downloads. The setup overview is available below.';
  openDialog('#guide-dialog');
 });
});
const studio=publicUrl(config.studioUrl);if(studio)all('[data-studio-link]').forEach(link=>{link.dataset.studioConfigured='true';link.href=studio;link.textContent='Open Studio ↗';link.target='_blank';link.rel='noopener noreferrer';});
renderFrame();
