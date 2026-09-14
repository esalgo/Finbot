import { dataUriBytes, prepareImage, TARGET_LONG_SIDE, targetSize } from './image-prep';

describe('targetSize', () => {
  it('upscales small images so the longest side reaches the target, keeping the ratio', () => {
    // The measured case: a 455×601 invoice misread "260 €" until upscaled.
    expect(targetSize(455, 601)).toEqual({ width: 1211, height: 1600, scaled: true });
    expect(targetSize(820, 801)).toEqual({ width: 1600, height: 1563, scaled: true });
  });

  it('never downscales large images', () => {
    expect(targetSize(4000, 3000)).toEqual({ width: 4000, height: 3000, scaled: false });
    expect(targetSize(TARGET_LONG_SIDE, 900).scaled).toBe(false);
  });

  it('ignores degenerate sizes', () => {
    expect(targetSize(0, 0).scaled).toBe(false);
  });
});

describe('dataUriBytes', () => {
  it('computes decoded bytes from base64 length and padding', () => {
    expect(dataUriBytes('data:image/png;base64,' + btoa('abc'))).toBe(3);
    expect(dataUriBytes('data:image/png;base64,' + btoa('abcd'))).toBe(4);
    expect(dataUriBytes('data:image/png;base64,' + btoa('abcde'))).toBe(5);
  });
});

describe('prepareImage', () => {
  it('falls back to the original file when the browser cannot decode it', async () => {
    const file = new File([new Uint8Array([1, 2, 3])], 'x.png', { type: 'image/png' });
    const original = vi.fn().mockRejectedValue(new Error('cannot decode'));
    vi.stubGlobal('createImageBitmap', original);
    try {
      const result = await prepareImage(file);
      expect(result.startsWith('data:image/png;base64,')).toBe(true);
      expect(dataUriBytes(result)).toBe(3);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
