/** Display-only ZYX indexing. Never mutates the image or the segmentation. */
export function renderSlice(
  image: Uint8Array,
  mask: Uint8Array,
  shape: readonly number[],
  z: number,
  opacity: number,
  brightness = 100,
  contrast = 100,
): Uint8ClampedArray {
  if (shape.length !== 3 || shape.some(n => !Number.isInteger(n) || n < 1)) throw new Error("Invalid 3D image dimensions");
  const [depth, height, width] = shape;
  if (image.length !== depth * height * width || mask.length !== image.length) throw new Error("Incomplete 3D data. Please reload.");
  if (!Number.isInteger(z) || z < 0 || z >= depth) throw new Error("Z slice is out of range");
  const pixels = new Uint8ClampedArray(width * height * 4);
  const offset = z * width * height;
  const alpha = Math.max(0, Math.min(1, opacity / 100));
  for (let i = 0; i < width * height; i++) {
    const gray = Math.max(0, Math.min(255, ((image[offset + i] - 128) * contrast / 100 + 128) * brightness / 100));
    const a = mask[offset + i] ? alpha : 0;
    pixels[4 * i] = gray * (1 - a) + 40 * a;
    pixels[4 * i + 1] = gray * (1 - a) + 225 * a;
    pixels[4 * i + 2] = gray * (1 - a) + 171 * a;
    pixels[4 * i + 3] = 255;
  }
  return pixels;
}
