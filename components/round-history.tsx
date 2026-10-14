import { type RoundHistory } from "@/lib/round-history";

export function RoundHistoryCard({ history, recorded, z, onSlice }: {
  history: RoundHistory; recorded: boolean; z: number; onSlice: (z:number)=>void;
}) {
  return <section className="round-history" aria-label={`Round ${history.round} training record`}>
    <div className="round-history-heading"><strong>Round {String(history.round).padStart(2,"0")}</strong><span>Recorded run</span></div>
    <p>{recorded ? "Recorded prompts and the confirmed 3D mask from this round." : "Saved experiment record. The current ROI has since been reopened or changed."}</p>
    <dl>
      <div><dt>Prompt segmentation</dt><dd>M{history.round - 1}{history.round === 1 ? " · Original" : ""}</dd></div>
      <div><dt>Model after training</dt><dd>M{history.round}</dd></div>
      <div><dt>Cumulative training data</dt><dd>{history.cumulative_confirmed_count} ROIs</dd></div>
      <div><dt>Training in this round</dt><dd>{history.training_steps} steps</dd></div>
      <div><dt>Recorded clicks</dt><dd><span className="positive-count">{history.positive_clicks} P</span> / <span className="negative-count">{history.negative_clicks} N</span></dd></div>
    </dl>
    <p className="round-explanation">M{history.round - 1} generated this round’s annotation. Training on the accumulated masks then produced M{history.round}.</p>
    <div className="round-slice-label">Recorded prompt slices</div>
    <div className="round-slices">{[...history.slice_indices].sort((a,b)=>a-b).map(slice=><button type="button" key={slice} aria-label={`Show recorded prompt slice Z ${slice+1}`} aria-pressed={z===slice} onClick={()=>onSlice(slice)}>Z{slice+1}</button>)}</div>
  </section>;
}
