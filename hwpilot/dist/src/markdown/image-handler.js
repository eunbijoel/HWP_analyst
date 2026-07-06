import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, extname, isAbsolute, join, resolve } from 'node:path';
import JSZip from 'jszip';
export async function extractImages(inputPath, images, outputDir) {
    const data = await readFile(inputPath);
    const zip = await JSZip.loadAsync(data);
    const extractedPaths = new Map();
    await mkdir(outputDir, { recursive: true });
    for (const image of images) {
        const file = zip.file(image.binDataPath);
        if (!file) {
            console.warn(`Image not found in archive: ${image.binDataPath}`);
            continue;
        }
        const filename = basename(image.binDataPath);
        const outputPath = join(outputDir, filename);
        const bytes = await file.async('nodebuffer');
        await writeFile(outputPath, bytes);
        extractedPaths.set(image.binDataPath, filename);
    }
    return extractedPaths;
}
export function resolveImagePaths(mdImages, baseDir) {
    return mdImages.map((image) => {
        if (image.url.startsWith('http://') || image.url.startsWith('https://')) {
            return {
                ...image,
                resolvedPath: null,
                warning: 'Remote URLs not supported',
            };
        }
        const resolvedPath = isAbsolute(image.url)
            ? image.url
            : resolve(baseDir, image.url);
        if (!existsSync(resolvedPath)) {
            return {
                ...image,
                resolvedPath: null,
                warning: `File not found: ${resolvedPath}`,
            };
        }
        return {
            ...image,
            resolvedPath,
        };
    });
}
export async function embedImage(zip, localPath, index) {
    const bytes = await readFile(localPath);
    const format = normalizeImageFormat(extname(localPath));
    const binDataPath = `BinData/image${index}.${format}`;
    zip.file(binDataPath, bytes);
    return {
        ref: `s0.img${index}`,
        binDataPath,
        width: 0,
        height: 0,
        format,
    };
}
function normalizeImageFormat(extension) {
    switch (extension.toLowerCase()) {
        case '.jpeg':
        case '.jpg':
            return 'jpg';
        case '.png':
            return 'png';
        case '.gif':
            return 'gif';
        case '.bmp':
            return 'bmp';
        default:
            throw new Error(`Unsupported image format: ${extension}`);
    }
}
//# sourceMappingURL=image-handler.js.map