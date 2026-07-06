import { type HwpFormat } from '../sdk/format-detector.js';
export type { HwpFormat };
export declare function detectFormatFromBuffer(buffer: Buffer): HwpFormat;
export declare function detectFormat(filePath: string): Promise<HwpFormat>;
//# sourceMappingURL=format-detector.d.ts.map