import type {CategoryAvailability} from "./data-categories";
export type VolumeTile = {
  id:string; title:string; shape:number[]; confirmed:boolean; revision:number;
  volume_id:string; tile_index:number; origin_zyx:number[];
  bounds_zyx?:number[][]; events?:number; empty_background?:boolean;
  inference_eligible?:boolean; split?:string;
};
export type LibraryVolume = {
  id:string; name:string; status:string; shape?:number[]; tile_shape:number[];
  total_tiles:number; confirmed_tiles:number; revision:number; merge_status:string;
  merge_revision?:number; error?:string; import_error?:string; merge_error?:string;
  voxel_spacing?:number[];
};
export type VolumeDetail = {volume:LibraryVolume;tiles:VolumeTile[]};
export type PreviewSlice = {
  image:string;mask:string;shape:number[];source_shape:number[];stride:number;z:number;partial:boolean;revision:number;
  tiles:Array<{id:string;bounds_zyx:number[][];confirmed:boolean;empty_background?:boolean;tile_index?:number;events?:number;revision?:number}>;
};
export type UploadSession = {id:string;offset:number;size:number;status:string;chunk_size:number;volume_id?:string};
export type LargeVolumeCapabilities = {
  categories?:CategoryAvailability[];
  large_volume_upload?:boolean; chunk_size?:number; default_tile_shape?:number[];
  max_upload_bytes?:number; available_workspaces?:Array<{id:string;label:string}>; workspaces?:Array<{id:string;label:string}>;
};
export function tileStatus(tile:{confirmed:boolean;empty_background?:boolean;events?:number;revision?:number}) {
  return tile.confirmed?(tile.empty_background?"Confirmed empty":"Confirmed"):(tile.events||0)>0||(tile.revision||0)>0?"In progress":"Unreviewed";
}
/** Review belongs to the whole 3D tile, not the foreground visible on one slice. */
export function tileReviewAppearance(tile:{confirmed:boolean;empty_background?:boolean;events?:number;revision?:number}) {
  if(tile.confirmed)return {label:tile.empty_background?"Confirmed empty":"Confirmed",color:"#63e0b0",hatch:false};
  if((tile.events||0)>0||(tile.revision||0)>0)return {label:"In progress · not confirmed",color:"#74b9ff",hatch:false};
  // An older server may omit activity metadata. Do not label an unknown tile as untouched.
  if(tile.events===0&&tile.revision===0)return {label:"Not started",color:"#efb456",hatch:true};
  return {label:"Not confirmed",color:"#74b9ff",hatch:false};
}
export function nextUnreviewed(tiles:VolumeTile[],currentId?:string) {
  const ordered=[...tiles].sort((a,b)=>a.tile_index-b.tile_index),index=ordered.findIndex(t=>t.id===currentId);
  return [...ordered.slice(index+1),...ordered.slice(0,index+1)].find(t=>!t.confirmed);
}
export function uploadStorageKey(endpoint:string,file:Pick<File,"name"|"size"|"lastModified">,tileShape:number[],spacing:number[]) {
  return `morphopilot-upload:${endpoint}:${JSON.stringify([file.name,file.size,file.lastModified,tileShape,spacing])}`;
}
/** Chunks are idempotent by offset. On a lost response, query the server before sending any more bytes. */
export async function sendUploadChunks(options:{endpoint:string;file:File;session:UploadSession;signal:AbortSignal;onProgress:(session:UploadSession)=>void;readStatus:(id:string)=>Promise<UploadSession>}) {
  const {endpoint,file,signal,onProgress,readStatus}=options;
  let session=options.session;
  const chunkSize=Math.min(session.chunk_size||8*1024*1024,8*1024*1024);
  if(!Number.isInteger(session.offset)||session.offset<0||session.offset>file.size)throw new Error("The saved upload offset is invalid.");
  while(session.offset<file.size) {
    signal.throwIfAborted();const offset=session.offset,end=Math.min(offset+chunkSize,file.size);
    let complete=false;
    for(let attempt=0;attempt<3&&!complete;attempt++) {
      signal.throwIfAborted();
      try {
        const response=await fetch(`${endpoint}/volumes/uploads/${encodeURIComponent(session.id)}/chunk?offset=${offset}`,{method:"POST",body:file.slice(offset,end),headers:{"Content-Type":"application/octet-stream"},signal});
        if(!response.ok) {
          const data=await response.json().catch(()=>({})) as {detail?:string};
          if(response.status<500)throw new UploadRejected(data.detail||`Upload rejected (${response.status}).`);
          throw new Error(data.detail||`Upload interrupted (${response.status}).`);
        }
        const data=await response.json() as {upload:UploadSession};session=data.upload;
        if(session.offset!==end)throw new UploadRejected("The server did not acknowledge the expected upload chunk.");
        complete=true;
      } catch(error) {
        if(signal.aborted||error instanceof UploadRejected)throw error;
        // Never guess whether the previous request succeeded.
        const saved=await readStatus(session.id);
        if(saved.offset===end){session=saved;complete=true;}
        else if(saved.offset!==offset)throw new Error("The upload changed in another session. Reselect this file to resume safely.",{cause:error});
        else if(attempt===2)throw new Error("Upload paused by a connection problem. Reselect the same file to resume.",{cause:error});
      }
    }
    onProgress(session);
  }
  return session;
}
class UploadRejected extends Error {}
export function previewTileAt(slice:PreviewSlice,y:number,x:number) {
  const sourceY=y*slice.stride,sourceX=x*slice.stride;
  return slice.tiles.find(t=>slice.z>=t.bounds_zyx[0][0]&&slice.z<t.bounds_zyx[0][1]&&sourceY>=t.bounds_zyx[1][0]&&sourceY<t.bounds_zyx[1][1]&&sourceX>=t.bounds_zyx[2][0]&&sourceX<t.bounds_zyx[2][1]);
}
