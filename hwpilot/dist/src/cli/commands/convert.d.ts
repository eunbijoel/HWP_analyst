import type { HwpDocument } from '../../sdk/types.js';
type ConvertOptions = {
    pretty?: boolean;
    force?: boolean;
    imagesDir?: string;
};
export declare function convertCommand(input: string, output: string, options: ConvertOptions): Promise<void>;
export declare function generateHwpx(doc: HwpDocument, imageLocalPaths?: string[]): Promise<Buffer>;
export {};
//# sourceMappingURL=convert.d.ts.map