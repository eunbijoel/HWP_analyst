import type { Section } from '../../types.js';
import type { HwpxArchive } from './loader.js';
export declare function parseSection(xml: string, sectionIndex: number): Section;
export declare function parseSections(archive: HwpxArchive): Promise<Section[]>;
//# sourceMappingURL=section-parser.d.ts.map