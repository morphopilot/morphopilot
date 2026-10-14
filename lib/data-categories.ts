/** Each category owns its data, active model and model history on the server. */
export const DATA_CATEGORIES = [
  {id:"neurite",label:"Neurite",description:"Neuronal branches and fibers",promptHint:"Place a positive point on a neurite to start 3D segmentation.",negativeHint:"Place N on nearby black background, clear of white neurites."},
  {id:"spot",label:"Spot",description:"Gene and molecular spots",promptHint:"Place a positive point on a bright gene spot to start 3D segmentation.",negativeHint:"Place N on nearby black background, clear of bright spots."},
  {id:"cell",label:"Cell",description:"Cell bodies and cellular structures",promptHint:"Place a positive point inside the cell you want to segment.",negativeHint:"Place N on clear background outside the target cell."},
  {id:"organ",label:"Organ",description:"Organs and larger anatomical structures",promptHint:"Place a positive point inside the organ you want to segment.",negativeHint:"Place N outside the target organ on clear background."},
] as const;

export type BuiltinCategoryId = typeof DATA_CATEGORIES[number]["id"];
export type DataCategoryId = BuiltinCategoryId | `cat_${string}`;
export type CategoryAvailability = {id:DataCategoryId;label:string;available:boolean;status?:"ready"|"starting"|"failed"|"unavailable";builtin?:boolean;error?:string};
export type CategoryIndex = {categories:CategoryAvailability[];current_category?:DataCategoryId;can_create:boolean};
export function isDataCategory(value:unknown):value is DataCategoryId {
  return typeof value==="string"&&(DATA_CATEGORIES.some(category=>category.id===value)||/^cat_[a-f0-9]{16}$/.test(value));
}
/** Explicit categories win over compatible dataset links. */
export function resolveDataCategory(category?:string|null,dataset?:string|null):DataCategoryId {
  if(isDataCategory(category))return category;
  if(isDataCategory(dataset))return dataset;
  return "neurite";
}
export function categoryInfo(id:DataCategoryId,categories?:CategoryAvailability[]){
  const builtin=DATA_CATEGORIES.find(category=>category.id===id),registered=categories?.find(category=>category.id===id);
  return builtin?{...builtin,label:registered?.label||builtin.label}:{id,label:registered?.label||"Custom category",description:"Your data · an independent model",promptHint:"Place a positive point inside the structure you want to segment.",negativeHint:"Place N on clear background outside the target structure."};
}
/** The registry is authoritative once fetched; defaults are only a loading fallback. */
export function categoryOptions(categories?:CategoryAvailability[]){return categories?.map(item=>({...categoryInfo(item.id,categories),...item}))||DATA_CATEGORIES.map(item=>({...item,available:true,builtin:true,status:"ready" as const,error:undefined}));}
export function normalizeCategoryLabel(value:string){return value.trim().replace(/\s+/gu," ");}
export function categoryEndpoint(id:DataCategoryId){return `/api/gpu/categories/${id}`;}
export function categoryLibraryHref(id:DataCategoryId){return `/library?category=${id}`;}
export function categoryAvailable(id:DataCategoryId,availability?:CategoryAvailability[]){
  return !availability||availability.some(category=>category.id===id&&category.available);
}
