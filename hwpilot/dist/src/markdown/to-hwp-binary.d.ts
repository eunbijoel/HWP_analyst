import type { HwpDocument } from '../types.js';
export type MarkdownToHwpBinaryResult = {
    buffer: Buffer;
    doc: HwpDocument;
};
export declare function markdownToHwpBinary(md: string): Promise<MarkdownToHwpBinaryResult>;
//# sourceMappingURL=to-hwp-binary.d.ts.map