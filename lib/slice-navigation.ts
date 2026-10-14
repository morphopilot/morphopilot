export type SliceView = { z: number; depth: number; ready: boolean; drawing: boolean };
/** Native non-passive wheel handler: consume XY scroll, move only the Z index. */
export function createSliceWheel(getView: () => SliceView, setSlice: (z: number) => void) {
  let accumulated = 0, previousTime = -Infinity, previousStep = -Infinity;
  return (event: Pick<WheelEvent, "deltaX" | "deltaY" | "deltaMode" | "ctrlKey" | "timeStamp" | "preventDefault" | "stopPropagation">) => {
    event.preventDefault();
    event.stopPropagation();
    const view = getView();
    if (!view.ready || view.drawing || event.ctrlKey) { accumulated = 0; return; }
    const multiplier = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
    const delta = (Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX) * multiplier;
    if (!delta) return;
    if (event.timeStamp - previousTime > 180 || Math.sign(delta) !== Math.sign(accumulated)) accumulated = 0;
    previousTime = event.timeStamp;
    accumulated += delta;
    if (Math.abs(accumulated) < 35 || event.timeStamp - previousStep < 85) return;
    const next = Math.max(0, Math.min(view.depth - 1, view.z + Math.sign(accumulated)));
    accumulated = 0; previousStep = event.timeStamp;
    if (next !== view.z) setSlice(next);
  };
}
