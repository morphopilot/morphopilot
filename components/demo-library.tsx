"use client";
import {useEffect,useLayoutEffect,useRef,useState,type KeyboardEvent} from "react";
import {ArrowLeft,ArrowRight,Eye,FolderOpen,LoaderCircle,Play,X} from "lucide-react";
import {requestApi} from "@/lib/studio-request";
import {renderSlice} from "@/lib/volume-view";
import {createSliceWheel} from "@/lib/slice-navigation";
import {demoAnnotationHref,demoMaskDescription,demoSampleLabel,type DemoJob,type DemoLoad,type DemoSlice,type LibraryDemo} from "@/lib/demo-library";
import type {DataCategoryId} from "@/lib/data-categories";

const decode=(value:string)=>Uint8Array.from(atob(value),character=>character.charCodeAt(0));
function drawDemo(canvas:HTMLCanvasElement|null,slice:DemoSlice,overlay:boolean,prompts:boolean,brightness:number) {
  if(!canvas)return;const [height,width]=slice.shape;canvas.width=width;canvas.height=height;
  const ctx=canvas.getContext("2d");if(!ctx)return;
  const pixels=ctx.createImageData(width,height);pixels.data.set(renderSlice(decode(slice.image),decode(slice.mask),[1,height,width],0,overlay?48:0,brightness,100));ctx.putImageData(pixels,0,0);
  if(!prompts)return;
  for(const prompt of slice.prompts||[]) {
    const points=prompt.points.filter(point=>point[0]===slice.z);if(!points.length)continue;
    ctx.save();ctx.strokeStyle=prompt.include?"#56f3c2":"#ff806a";ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=Math.max(.9,width/450);ctx.shadowColor="#000";ctx.shadowBlur=3;
    const first=points[0],last=points[points.length-1],x=first[2]/slice.stride,y=first[1]/slice.stride,r=Math.max(3,width/90);ctx.beginPath();
    if(prompt.tool==="point") {ctx.arc(x,y,r,0,Math.PI*2);ctx.stroke();ctx.beginPath();ctx.moveTo(x-r*1.6,y);ctx.lineTo(x+r*1.6,y);ctx.moveTo(x,y-r*1.6);ctx.lineTo(x,y+r*1.6);ctx.stroke();ctx.font=`bold ${Math.max(8,width/32)}px sans-serif`;ctx.fillText(prompt.include?"P":"N",Math.min(width-10,x+r*1.8),Math.max(10,y-r));}
    else if(prompt.tool==="bbox")ctx.strokeRect(x,y,(last[2]-first[2])/slice.stride,(last[1]-first[1])/slice.stride);
    else {points.forEach((point,index)=>index?ctx.lineTo(point[2]/slice.stride,point[1]/slice.stride):ctx.moveTo(point[2]/slice.stride,point[1]/slice.stride));if(prompt.tool==="lasso")ctx.closePath();ctx.stroke();}
    ctx.restore();
  }
}
function useDemoSlice(endpoint:string,demo:LibraryDemo,sampleId:string,z:number,maxSize:number) {
  const [slice,setSlice]=useState<DemoSlice|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[attempt,setAttempt]=useState(0);
  const revision=demo.samples.find(sample=>sample.id===sampleId)?.revision??0;
  const key=`${endpoint}:${demo.id}:${sampleId}:${z}:${revision}:${attempt}`;
  const [loadedKey,setLoadedKey]=useState("");
  useEffect(()=>{const controller=new AbortController();const path=`/demos/${encodeURIComponent(demo.id)}/slice?${new URLSearchParams({sample_id:sampleId,z:String(z),max_size:String(maxSize)})}`;
    requestApi<DemoSlice>(path,undefined,endpoint,controller.signal).then(value=>{
      if(value.category_id!==demo.category_id||value.demo_id!==demo.id||value.sample.id!==sampleId||value.z!==z)throw new Error("The preview returned another ROI or category. Reload the demo.");
      // Validate every byte before committing a preview; malformed responses must
      // never reach a canvas effect or leave a previous ROI visible under a new label.
      renderSlice(decode(value.image),decode(value.mask),[1,...value.shape],0,0);
      if(!controller.signal.aborted){setSlice(value);setLoadedKey(key);setFailure(null);}
    }).catch(cause=>{if(!controller.signal.aborted)setFailure({key,message:(cause as Error).message});});return()=>controller.abort();
  },[endpoint,demo.id,demo.category_id,sampleId,z,maxSize,revision,attempt,key]);
  return {slice:loadedKey===key?slice:null,error:failure?.key===key?failure.message:"",retry:()=>setAttempt(value=>value+1)};
}
function DemoImages({slice,showPrompts=false,brightness=100}:{slice:DemoSlice;showPrompts?:boolean;brightness?:number}) {
  const raw=useRef<HTMLCanvasElement>(null),mask=useRef<HTMLCanvasElement>(null);
  useEffect(()=>{drawDemo(raw.current,slice,false,showPrompts,brightness);drawDemo(mask.current,slice,true,false,brightness);},[slice,showPrompts,brightness]);
  return <><figure><figcaption>Original image{showPrompts?" + recorded prompts":""}</figcaption><canvas ref={raw} aria-label="Demo original image"/></figure><figure><figcaption>Saved mask overlay</figcaption><canvas ref={mask} aria-label="Demo saved mask overlay"/></figure></>;
}
export function DemoLibraryCard({demo,endpoint,category,onView,onLoad,loading}:{demo:LibraryDemo;endpoint:string;category:DataCategoryId;onView:()=>void;onLoad:(sampleId:string)=>void;loading:boolean}) {
  const {slice,error,retry}=useDemoSlice(endpoint,demo,demo.preview_sample_id,demo.preview_z,384);
  const annotations=demo.samples.filter(sample=>sample.prompt_count>0||sample.events>0).length;
  return <article className="dl-demo-card" aria-label={`${category} saved demo`}><div className="dl-demo-card-images">{slice?<DemoImages slice={slice}/>:<div className="dl-demo-placeholder">{error?<div><p>{error}</p><button type="button" className="dl-button dl-outline" onClick={retry}>Retry preview</button></div>:<><LoaderCircle className="dl-spin" size={22}/>Loading saved images…</>}</div>}<span className="dl-demo-preview-caption">{slice?`${slice.sample.round?`Round ${String(slice.sample.round).padStart(2,"0")} · `:""}Z ${slice.z+1} / ${slice.source_shape[0]}`:"3D image preview"}</span></div><div className="dl-demo-card-body"><span className="dl-badge dl-complete">Saved experiment demo</span><h2>{demo.title}</h2><p>{demo.description}</p><div className="dl-demo-counts"><strong>{demo.samples.length} saved ROIs</strong><span>{annotations} with recorded annotations</span></div><div className="dl-demo-model"><span>Demo model · next inference</span><strong>{demo.model.label}</strong></div><div className="dl-demo-actions"><button type="button" className="dl-button dl-primary" disabled={loading} onClick={()=>onLoad(demo.preview_sample_id)}>{loading?<LoaderCircle size={16} className="dl-spin"/>:<FolderOpen size={16}/>}Load demo</button><button type="button" className="dl-button dl-outline" disabled={loading} onClick={onView}><Eye size={16}/>View demo</button></div><small>Load restores this demo’s model. Its saved prompts and masks stay unchanged.</small></div></article>;
}
function trapFocus(event:KeyboardEvent<HTMLDivElement>) {
  if(event.key!=="Tab")return;const items=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]')).filter(node=>node.getClientRects().length>0),first=items[0],last=items.at(-1);if(!first||!last)return;
  if(event.shiftKey&&(document.activeElement===first||document.activeElement===event.currentTarget)){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
}
export function DemoViewer({demo,endpoint,onClose,onLoad,loading,loadError}:{demo:LibraryDemo;endpoint:string;onClose:()=>void;onLoad:(sampleId:string,z:number)=>void;loading:boolean;loadError:string}) {
  const [sampleId,setSampleId]=useState(demo.preview_sample_id),[z,setZ]=useState(demo.preview_z),[showPrompts,setShowPrompts]=useState(true),[brightness,setBrightness]=useState(100);
  const sample=demo.samples.find(item=>item.id===sampleId)||demo.samples[0],depth=sample.shape[0],index=demo.samples.findIndex(item=>item.id===sample.id);
  const {slice,error,retry}=useDemoSlice(endpoint,demo,sample.id,z,1024),dialog=useRef<HTMLDivElement>(null),viewport=useRef<HTMLDivElement>(null);
  const ready=!!slice&&!error,view=useRef({z,depth,ready,drawing:false});useLayoutEffect(()=>{view.current={z,depth,ready,drawing:false};},[z,depth,ready]);
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null,overflow=document.body.style.overflow;document.body.style.overflow="hidden";dialog.current?.focus();return()=>{document.body.style.overflow=overflow;previous?.focus();};},[]);
  useEffect(()=>{const node=viewport.current;if(!node)return;const wheel=createSliceWheel(()=>view.current,setZ);node.addEventListener("wheel",wheel,{passive:false});return()=>node.removeEventListener("wheel",wheel);},[]);
  function select(id:string){const next=demo.samples.find(item=>item.id===id);if(next){setSampleId(id);setZ(Math.max(0,Math.min(next.shape[0]-1,next.preferred_z)));}}
  return <div className="dl-modal-backdrop" onClick={event=>{if(event.target===event.currentTarget&&!loading)onClose();}}><div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="demo-viewer-title" tabIndex={-1} className="dl-global dl-demo-viewer" onKeyDown={event=>{trapFocus(event);if(event.key==="Escape"&&!loading)onClose();}}><header className="dl-global-header"><div><span className="dl-eyebrow">SAVED EXPERIMENT DEMO</span><h2 id="demo-viewer-title">{demo.title}</h2><p>{demo.source_label} · Each ROI is a separate crop, shown in its original Z order.</p></div><div className="dl-global-actions"><button type="button" className="dl-button dl-primary" disabled={loading||!ready} onClick={()=>onLoad(sample.id,z)}>{loading?<LoaderCircle size={16} className="dl-spin"/>:<Play size={16}/>}Load this ROI</button><button type="button" className="dl-button dl-outline" aria-label="Close demo preview" disabled={loading} onClick={onClose}><X size={17}/>Close</button></div></header><div className="dl-demo-select-row"><label>Saved ROI<select aria-label="Select demo ROI" value={sample.id} disabled={loading} onChange={event=>select(event.target.value)}>{demo.samples.map(item=><option key={item.id} value={item.id}>{demoSampleLabel(item)}</option>)}</select></label><div><button type="button" className="dl-button dl-outline" aria-label="Previous demo ROI" disabled={index<=0||loading} onClick={()=>select(demo.samples[index-1].id)}><ArrowLeft size={16}/></button><span>{index+1} / {demo.samples.length}</span><button type="button" className="dl-button dl-outline" aria-label="Next demo ROI" disabled={index>=demo.samples.length-1||loading} onClick={()=>select(demo.samples[index+1].id)}><ArrowRight size={16}/></button></div></div><div className="dl-global-controls"><label className="dl-checkbox"><input type="checkbox" checked={showPrompts} onChange={event=>setShowPrompts(event.target.checked)}/>Show recorded P / N prompts</label><label>Brightness<input aria-label="Demo brightness" type="range" min={25} max={400} step={5} value={brightness} onChange={event=>setBrightness(event.currentTarget.valueAsNumber)}/><span>{brightness}%</span></label><span className="dl-demo-prompt-legend"><i/>Positive · <b/>Negative</span><span>{sample.positive_clicks} positive · {sample.negative_clicks} negative clicks across Z</span></div><div className="dl-demo-provenance"><strong>{demoMaskDescription(slice?.sample||sample)}</strong><span>Model for new predictions: {demo.model.label}. Loading a model does not regenerate this saved mask.</span></div>{(loadError||error)&&<div className="dl-error" role="alert">{loadError||error}{error&&<button type="button" onClick={retry}>Retry preview</button>}</div>}<div className="dl-demo-viewer-images" ref={viewport}>{slice&&!error?<DemoImages slice={slice} showPrompts={showPrompts} brightness={brightness}/>:<div className="dl-demo-placeholder">{error?"Preview unavailable. Retry above or choose another ROI.":<><LoaderCircle size={24} className="dl-spin"/>Loading Z slice…</>}</div>}</div><div className="dl-global-z"><label htmlFor="demo-z">Z slice</label><button type="button" className="dl-slice-step" aria-label="Previous demo Z slice" disabled={z===0} onClick={()=>setZ(z-1)}>−</button><input id="demo-z" aria-label="Demo Z slice" type="range" min={0} max={Math.max(1,depth-1)} value={z} disabled={depth<2} onChange={event=>setZ(event.currentTarget.valueAsNumber)}/><button type="button" className="dl-slice-step" aria-label="Next demo Z slice" disabled={z===depth-1} onClick={()=>setZ(z+1)}>+</button><span>{z+1} / {depth}</span></div><footer className="dl-global-footer"><div><strong>Green overlay = saved segmentation · P / N = recorded prompts on this slice</strong><p>Scroll over an image to change Z. These independent ROIs are not a stitched full volume.</p></div></footer></div></div>;
}

export async function loadDemoSelection(endpoint:string,category:DataCategoryId,demo:LibraryDemo,sampleId:string,signal:AbortSignal,selectedZ?:number) {
  // Loading from the preview should open exactly the Z slice being inspected.
  // Card loading has no explicit slice and keeps the server’s preferred Z.
  if(selectedZ!==undefined){const sample=demo.samples.find(item=>item.id===sampleId);if(!sample||!Number.isInteger(selectedZ)||selectedZ<0||selectedZ>=sample.shape[0])throw new Error("The selected preview slice is unavailable. Reopen the demo.");}
  const response=await requestApi<DemoLoad>(`/demos/${encodeURIComponent(demo.id)}/load`,{sample_id:sampleId},endpoint,signal);
  if(response.category_id!==category||response.demo_id!==demo.id||response.sample_id!==sampleId||response.model_id!==demo.model.id)throw new Error("The server selected a different demo. Reload the Data library.");
  let job:DemoJob|undefined=response.job;
  while(job&&["queued","running"].includes(job.status)) {await new Promise<void>((resolve,reject)=>{signal.throwIfAborted();const abort=()=>{clearTimeout(timer);reject(signal.reason);};const timer=setTimeout(()=>{signal.removeEventListener("abort",abort);resolve();},1200);signal.addEventListener("abort",abort,{once:true});});job=await requestApi<DemoJob>(`/jobs/${encodeURIComponent(job.id)}`,undefined,endpoint,signal);}
  if(job&&job.status!=="completed")throw new Error(job.error||job.message||"The demo model could not be activated.");
  signal.throwIfAborted();return demoAnnotationHref(category,demo.id,response.sample_id,selectedZ??response.z);
}
