import type { VolumeDetail } from "./large-volume";

/** Resolve a library link without ever selecting an ROI from another parent. */
export function resolveLibrarySelection(detail: VolumeDetail, savedSample: string | null, requestedTile?: string, requestedZ?: string) {
  if (detail.volume.status !== "ready") throw new Error("This volume is not ready. Open the Data Library to check its import status.");
  const tiles = detail.tiles.filter(tile => tile.volume_id === detail.volume.id);
  const explicit = requestedTile ? tiles.find(tile => tile.id === requestedTile) : undefined;
  if (requestedTile && !explicit) throw new Error("The requested tile does not belong to this volume. Choose a tile from the Data Library.");
  const tile = explicit || tiles.find(tile => tile.id === savedSample) || tiles.find(tile => !tile.confirmed) || tiles[0];
  if (!tile) throw new Error("This volume has no annotation tiles yet. Open the Data Library to check its status.");
  const slice = Number(requestedZ);
  // URLs use 1-based local Z; only an explicitly selected tile can carry a slice.
  return {tile, z: explicit && Number.isInteger(slice) && slice >= 1 ? Math.min(slice, tile.shape[0]) - 1 : 0};
}
