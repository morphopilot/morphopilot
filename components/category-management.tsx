"use client";
import {useCallback,useEffect,useRef,useState,type FormEvent,type KeyboardEvent} from "react";
import {LoaderCircle,Plus,X} from "lucide-react";
import {ApiRequestError,requestApi} from "@/lib/studio-request";
import {categoryOptions,normalizeCategoryLabel,type CategoryAvailability,type CategoryIndex,type DataCategoryId} from "@/lib/data-categories";

export function useCategoryRegistry(fallback?:CategoryAvailability[]){
  const [index,setIndex]=useState<CategoryIndex|null>(null),[error,setError]=useState("");
  const sequence=useRef(0);
  const refresh=useCallback(async(signal?:AbortSignal)=>{const current=++sequence.current;try{const next=await requestApi<CategoryIndex>("/categories",undefined,"/api/gpu",signal);if(current===sequence.current&&!signal?.aborted){setIndex(next);setError("");}return next;}catch(cause){if(current===sequence.current&&!signal?.aborted)setError((cause as Error).message);return null;}},[]);
  useEffect(()=>{const counter=sequence,controller=new AbortController();const initial=setTimeout(()=>void refresh(controller.signal),0);const timer=setInterval(()=>{if(document.visibilityState==="visible")void refresh(controller.signal);},10000);return()=>{counter.current++;controller.abort();clearTimeout(initial);clearInterval(timer);};},[refresh]);
  return {categories:categoryOptions(index?.categories||fallback),availability:index?.categories||fallback,canCreate:index?.can_create===true,error,refresh};
}
function focusWithin(event:KeyboardEvent<HTMLDivElement>){if(event.key!=="Tab")return;const items=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),a[href]'));const first=items[0],last=items[items.length-1];if(!first)return;if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
export function CreateCategory({onClose,onCreated}:{onClose:()=>void;onCreated:(id:DataCategoryId)=>void}){
  const [name,setName]=useState(""),[error,setError]=useState(""),[running,setRunning]=useState(false),[created,setCreated]=useState<CategoryAvailability|null>(null);
  const control=useRef<AbortController|null>(null),busy=useRef(false);
  useEffect(()=>()=>control.current?.abort(),[]);
  async function submit(event:FormEvent){event.preventDefault();const label=normalizeCategoryLabel(name);if(busy.current||!label||label.length>48)return;busy.current=true;setRunning(true);setError("");const controller=new AbortController();control.current=controller;let acknowledgedCategory=created;
    try{
      const category=created?.status==="failed"?(await requestApi<{category:CategoryAvailability}>(`/categories/${created.id}/retry`,{},"/api/gpu",controller.signal)).category:created||(await requestApi<{category:CategoryAvailability}>("/categories",{label},"/api/gpu",controller.signal)).category;
      acknowledgedCategory=category;setCreated(category);
      for(let attempt=0;attempt<90;attempt++){
        const index=await requestApi<CategoryIndex>("/categories",undefined,"/api/gpu",controller.signal);const current=index.categories.find(item=>item.id===category.id);
        if(current?.available){onCreated(category.id);return;}
        if(current?.status==="failed"){setCreated(current);throw new Error(current.error||"This category could not start. Ask the server administrator to check its worker, then retry.");}
        await new Promise<void>((resolve,reject)=>{const abort=()=>{clearTimeout(timer);reject(controller.signal.reason);};const timer=setTimeout(()=>{controller.signal.removeEventListener("abort",abort);resolve();},1000);controller.signal.addEventListener("abort",abort,{once:true});});
      }
      throw new Error("The category was saved and is still starting. Retry to check it again, or close this dialog and refresh the library.");
    }catch(cause){
      if(!controller.signal.aborted){
        // A lost POST response can still have created the category. Read back only;
        // never replay the mutation. An existing name requires an explicit Open.
        const ambiguous=!(cause instanceof ApiRequestError)||cause.status===409||cause.status>=500;
        if(!acknowledgedCategory&&ambiguous){
          const index=await requestApi<CategoryIndex>("/categories",undefined,"/api/gpu",controller.signal).catch(()=>null);
          const nameKey=(value:string)=>normalizeCategoryLabel(value).normalize("NFKC").toLocaleLowerCase();
          const recovered=index?.categories.find(item=>nameKey(item.label)===nameKey(label));
          if(!controller.signal.aborted&&recovered){setCreated(recovered);setError(`A category named “${recovered.label}” is already saved. ${recovered.status==="failed"?"Select Retry category to restart it.":"Select Check & open category to use it."}`);return;}
        }
        if(!controller.signal.aborted)setError((cause as Error).message);
      }
    }finally{busy.current=false;if(!controller.signal.aborted)setRunning(false);}
  }
  const label=normalizeCategoryLabel(name);
  return <div className="dl-dialog-backdrop" onClick={event=>{if(event.target===event.currentTarget&&!running)onClose();}}><div className="dl-management-dialog" role="dialog" aria-modal="true" aria-labelledby="create-category-title" onKeyDown={event=>{focusWithin(event);if(event.key==="Escape"&&!running)onClose();}}><div className="dl-dialog-heading"><div><span className="dl-eyebrow">INDEPENDENT DATA & MODEL</span><h2 id="create-category-title">Create a category</h2></div><button className="dl-icon-button" aria-label="Close create category" disabled={running} onClick={onClose}><X size={20}/></button></div><p>Give your data type a name. Its images, annotations and training history stay separate from every other category.</p><div className="dl-new-model-note"><strong>Starts with Original pretrained model</strong><span>No trained weights or annotations are copied from your existing categories.</span></div><form onSubmit={event=>void submit(event)}><label className="dl-input-label">Category name<input aria-label="New category name" autoFocus maxLength={48} value={name} disabled={running||!!created} onChange={event=>setName(event.target.value)} placeholder="e.g. Blood vessels" required/></label>{error&&<p className="dl-management-error" role="alert">{error}</p>}{running&&<p role="status">{created?"Preparing this category’s independent workspace…":"Creating category…"}</p>}<div className="dl-dialog-actions"><button type="button" className="dl-button dl-outline" disabled={running} onClick={onClose}>Cancel</button><button type="submit" className="dl-button dl-primary" disabled={running||!label||label.length>48}>{running?<LoaderCircle size={17} className="dl-spin"/>:<Plus size={17}/>} {created?.status==="failed"?"Retry category":created?"Check & open category":"Create & upload data"}</button></div></form></div></div>;
}
