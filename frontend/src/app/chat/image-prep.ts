/**
 * Upscales small images before sending them to the vision model.
 *
 * Measured with DeepSeek: the model assigns image tokens by input resolution (up to
 * ~1,000), and a small document arrives with too little detail — a 455×601 invoice
 * read "260 €" as "280 €". Upscaled to 1,600 px on the longest side with smooth
 * interpolation it read every total, date and name correctly. The `detail`
 * parameter is accepted by the API but has no effect, so resolution is the lever.
 *
 * Only upscales: larger images are sent untouched.
 */

/** Longest side the model gets. Measured sweet spot: reaches the token ceiling. */
export const TARGET_LONG_SIDE = 1600;
/** Mirrors MAX_IMAGE_MB on the backend. */
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export interface TargetSize {
  width: number;
  height: number;
  scaled: boolean;
}

export function targetSize(width: number, height: number, longSide = TARGET_LONG_SIDE): TargetSize {
  const longest = Math.max(width, height);
  if (!width || !height || longest >= longSide) return { width, height, scaled: false };
  const factor = longSide / longest;
  return { width: Math.round(width * factor), height: Math.round(height * factor), scaled: true };
}

/** Bytes represented by a base64 data URI, without decoding it. */
export function dataUriBytes(dataUri: string): number {
  const base64 = dataUri.slice(dataUri.indexOf(',') + 1);
  const padding = base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}

function readAsDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/**
 * Returns the data URI to send. Falls back to the original file whenever the
 * browser cannot decode or draw it, or when the upscaled version would exceed the
 * size limit: the backend still validates the bytes either way.
 */
export async function prepareImage(file: File): Promise<string> {
  const original = await readAsDataUrl(file);
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return original;
  }
  try {
    const size = targetSize(bitmap.width, bitmap.height);
    if (!size.scaled) return original;

    const canvas = document.createElement('canvas');
    canvas.width = size.width;
    canvas.height = size.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return original;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bitmap, 0, 0, size.width, size.height);

    // PNG keeps text edges crisp; JPEG only if PNG would not fit the limit.
    const png = canvas.toDataURL('image/png');
    if (dataUriBytes(png) <= MAX_IMAGE_BYTES) return png;
    const jpeg = canvas.toDataURL('image/jpeg', 0.92);
    return dataUriBytes(jpeg) <= MAX_IMAGE_BYTES ? jpeg : original;
  } finally {
    bitmap.close();
  }
}
