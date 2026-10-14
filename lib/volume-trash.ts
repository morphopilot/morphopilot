import {requestApi} from "./studio-request";
import type {LibraryVolume} from "./large-volume";
export type TrashedVolume={category_id:string;volume_id:string;trash_id:string;name:string;total_tiles:number;confirmed_tiles:number;status:"trashed";deleted_at:number;preserved_training_snapshots:boolean};
/** Clear only this category's deleted selection, preserving all other categories. */
export function forgetDeletedVolume(storage:Pick<Storage,"getItem"|"removeItem">,endpoint:string,volumeId:string,sampleIds:readonly string[]){
  const key=`morphopilot-last-sample:${endpoint}`;const remembered=storage.getItem(key),parentKey=`${key}:${volumeId}`;const parentSample=storage.getItem(parentKey);
  if(remembered&&(sampleIds.includes(remembered)||remembered===parentSample))storage.removeItem(key);
  storage.removeItem(parentKey);
}
export async function trashVolume(endpoint:string,category:string,volume:LibraryVolume,signal?:AbortSignal){
  return requestApi<TrashedVolume>(`/volumes/${encodeURIComponent(volume.id)}/trash`,{name:volume.name,revision:volume.revision,category},endpoint,signal);
}
export async function restoreVolume(endpoint:string,trashId:string,signal?:AbortSignal){return requestApi<{category_id:string;volume:LibraryVolume;restored:true}>(`/volumes/trash/${encodeURIComponent(trashId)}/restore`,{},endpoint,signal);}
