import type JSZip from 'jszip';
import { type EditOperation, type XmlNode } from '../../shared/edit-types.js';
import type { HwpxArchive } from './loader.js';
/**
 * Escape XML special characters in user-supplied text.
 * Must escape & first, then <, >, ", ' to avoid double-escaping.
 */
export declare function escapeXml(text: string): string;
export declare function mutateHwpxZip(zip: JSZip, archive: HwpxArchive, operations: EditOperation[]): Promise<void>;
export declare function parseXml(xml: string): XmlNode[];
export declare function buildXml(tree: XmlNode[]): string;
//# sourceMappingURL=mutator.d.ts.map