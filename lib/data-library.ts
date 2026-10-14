import type {LibraryVolume} from "./large-volume";
import type {DataCategoryId} from "./data-categories";

export type LibraryWorkspace = DataCategoryId;
export type LibraryFilter = "all" | "reviewing" | "reviewed" | "preparing";
export type LibrarySort = "newest" | "name" | "progress";
export type ListedVolume = LibraryVolume & {created_at?:number;imported_at?:number};

export function annotationHref(workspace:LibraryWorkspace,volumeId?:string,tileId?:string,localZ?:number) {
  const query=new URLSearchParams({category:workspace});
  if(volumeId)query.set("volume",volumeId);
  if(tileId)query.set("tile",tileId);
  if(tileId&&Number.isInteger(localZ)&&localZ!>=0)query.set("z",String(localZ!+1));
  return `/?${query.toString()}`;
}

export function allTilesReviewed(volume:LibraryVolume) {
  return volume.status==="ready"&&volume.total_tiles>0&&volume.confirmed_tiles===volume.total_tiles;
}

/** Canvas uses object-fit: contain. Exclude letterboxing when locating a tile. */
export function containedImagePoint(clientX:number,clientY:number,rect:{left:number;top:number;width:number;height:number},shape:readonly number[]) {
  const [height,width]=shape;
  if(!width||!height||rect.width<=0||rect.height<=0)return null;
  const scale=Math.min(rect.width/width,rect.height/height);
  const x=(clientX-rect.left-(rect.width-width*scale)/2)/scale;
  const y=(clientY-rect.top-(rect.height-height*scale)/2)/scale;
  return x>=0&&y>=0&&x<width&&y<height?{x,y}:null;
}

export function volumeMaskState(volume:LibraryVolume):{label:string;tone:"neutral"|"working"|"complete"|"error";description:string} {
  if(volume.status==="failed")return {label:"Import failed",tone:"error",description:volume.import_error||volume.error||"The image could not be imported."};
  if(volume.status!=="ready")return {label:"Preparing tiles",tone:"working",description:"The image is being prepared on the server."};
  if(volume.merge_status==="ready"&&volume.merge_revision===volume.revision&&allTilesReviewed(volume))return {label:"Merged mask ready",tone:"complete",description:"All tiles are reviewed and the full mask export is current."};
  if(allTilesReviewed(volume))return {label:"All tiles reviewed",tone:"complete",description:volume.merge_status==="stale"?"Annotations changed after the last export. Merge again to download the current full mask.":"The current masks can be viewed together. Merge in the annotation workspace to export."};
  if(volume.revision>0||volume.confirmed_tiles>0)return {label:"Annotation in progress",tone:"working",description:"Saved masks are shown in place. Unreviewed areas still require inspection."};
  return {label:"Not annotated yet",tone:"neutral",description:"The original image is ready. No tile annotations have been saved."};
}

export function selectLibraryVolumes(volumes:ListedVolume[],search:string,filter:LibraryFilter,sort:LibrarySort) {
  const query=search.trim().toLocaleLowerCase();
  const selected=volumes.filter(volume=>{
    if(query&&!volume.name.toLocaleLowerCase().includes(query)&&!volume.id.toLocaleLowerCase().includes(query))return false;
    if(filter==="reviewed")return allTilesReviewed(volume);
    if(filter==="reviewing")return volume.status==="ready"&&!allTilesReviewed(volume);
    if(filter==="preparing")return volume.status!=="ready";
    return true;
  });
  return selected.sort((a,b)=>{
    if(sort==="name")return a.name.localeCompare(b.name,undefined,{numeric:true})||a.id.localeCompare(b.id);
    if(sort==="progress")return (b.confirmed_tiles/Math.max(1,b.total_tiles)-a.confirmed_tiles/Math.max(1,a.total_tiles))||(b.created_at||0)-(a.created_at||0);
    return (b.created_at||0)-(a.created_at||0);
  });
}
