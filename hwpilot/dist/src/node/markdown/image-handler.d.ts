import JSZip from 'jszip';
import type { Image } from '../../sdk/types.js';
export declare function extractImages(inputPath: string, images: Image[], outputDir: string): Promise<Map<string, string>>;
export declare function resolveImagePaths(mdImages: Array<{
    url: string;
    alt: string;
}>, baseDir: string): Array<{
    url: string;
    alt: string;
    resolvedPath: string | null;
    warning?: string;
}>;
export declare function embedImage(zip: JSZip, localPath: string, index: number): Promise<Image>;
//# sourceMappingURL=image-handler.d.ts.map