export type RoundHistory = {
  experiment_id: string;
  round: number;
  input_model_id: string;
  output_model_id: string;
  training_steps: number;
  cumulative_confirmed_count: number;
  positive_clicks: number;
  negative_clicks: number;
  slice_indices: number[];
  imported_revision: number;
  loss_first5: number;
  loss_last5: number;
};

export function isRecordedRoundMask(history: RoundHistory, volume: {revision:number;confirmed:boolean;model_id?:string}) {
  return volume.confirmed && volume.revision === history.imported_revision && volume.model_id === history.input_model_id;
}

export function firstRoundSlice(history?: RoundHistory) {
  const slice = history?.slice_indices?.[0];
  return typeof slice === "number" && Number.isInteger(slice) && slice >= 0 ? slice : undefined;
}
