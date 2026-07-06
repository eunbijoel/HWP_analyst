export { Document, documentFromBytes } from './document.js';
export type { HwpDocument, Section, Paragraph, Run, Table, TableRow, TableCell, Image, TextBox, DocumentHeader, FontFace, CharShape, ParaShape, Style } from './types.js';
export type { EditOperation, FormatOptions } from './edit-types.js';
export { detectFormat } from './format-detector.js';
export type { HwpFormat } from './format-detector.js';
export { loadHwp, loadHwpSectionTexts, extractParaText } from './formats/hwp/reader.js';
export { editHwp } from './formats/hwp/writer.js';
export { createHwp } from './formats/hwp/creator.js';
export { validateHwp, validateHwpBuffer } from './formats/hwp/validator.js';
export { loadHwpx } from './formats/hwpx/loader.js';
export { editHwpx } from './formats/hwpx/writer.js';
export { createHwpx } from './formats/hwpx/creator.js';
export { parseSections, parseSection } from './formats/hwpx/section-parser.js';
export { parseHeader } from './formats/hwpx/header-parser.js';
export { markdownToHwp } from './markdown/to-hwp.js';
export { hwpToMarkdown } from './markdown/to-markdown.js';
export declare function loadDocument(buffer: Uint8Array): Promise<import('./document.js').Document>;
//# sourceMappingURL=index.d.ts.map