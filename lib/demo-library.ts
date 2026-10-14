import type {DataCategoryId} from "./data-categories";
import type {RoundHistory} from "./round-history";

export type DemoPrompt={tool:string;include:boolean;points:number[][];radius?:number};
export type DemoSample={id:string;title:string;shape:number[];confirmed:boolean;events:number;revision:number;round?:number|null;round_history?:RoundHistory;split?:string;mask_model_id?:string|null;mask_model_label?:string;recorded_mask?:boolean;preferred_z:number;prompt_count:number;positive_clicks:number;negative_clicks:number};
export type LibraryDemo={id:string;category_id:DataCategoryId;title:string;description:string;source_label:string;training_rounds:number;model:{id:string;label:string;phase:string;auto_ready:boolean;confirmed_count:number};samples:DemoSample[];sample_ids:string[];preview_sample_id:string;preview_z:number};
export type DemoIndex={category_id:DataCategoryId;demos:LibraryDemo[]};
export type DemoSlice={category_id:DataCategoryId;demo_id:string;image:string;mask:string;shape:number[];source_shape:number[];stride:number;z:number;revision:number;sample:DemoSample;prompts:DemoPrompt[]};
export type DemoLoad={demo_id:string;category_id:DataCategoryId;model_id:string;sample_id:string;sample_ids:string[];z:number;job?:DemoJob};
export type DemoJob={id:string;status:string;error?:string;message?:string};

export function demoAnnotationHref(category:DataCategoryId,demoId:string,sampleId:string,z:number) {
  const query=new URLSearchParams({category,collection:"experiments",demo:demoId,sample:sampleId});
  if(Number.isInteger(z)&&z>=0)query.set("z",String(z+1));
  return `/?${query}`;
}

/** An explicit library selection always wins over a saved upload selection. */
export function resolveDemoSample<T extends {id:string;volume_id?:string}>(samples:T[],sampleId:string) {
  const sample=samples.find(item=>item.id===sampleId);
  if(!sample||sample.volume_id)throw new Error("This saved demo ROI is unavailable in the selected category. Return to the Data library and reload the demo.");
  return sample;
}

export function demoMaskDescription(sample:DemoSample) {
  if(sample.round_history&&sample.recorded_mask)return `Saved round ${sample.round_history.round} annotation · created with M${sample.round_history.round-1}`;
  return `${sample.confirmed?"Saved, confirmed annotation":"Saved annotation · review in progress"}${sample.mask_model_label?` · ${sample.mask_model_label}`:""}`;
}

export function demoSampleLabel(sample:Pick<DemoSample,"id"|"title"|"round">) {
  return sample.round?`Round ${String(sample.round).padStart(2,"0")} · ${sample.id}`:sample.title;
}
