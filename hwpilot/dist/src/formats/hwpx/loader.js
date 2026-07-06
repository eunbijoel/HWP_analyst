import { readFile } from 'node:fs/promises';
import { loadHwpx as sdkLoadHwpx } from '../../sdk/formats/hwpx/loader.js';
export async function loadHwpx(filePath) {
    let fileBuffer;
    try {
        fileBuffer = await readFile(filePath);
    }
    catch (err) {
        throw new Error(`Failed to read file: ${filePath} — ${err.message}`);
    }
    try {
        return await sdkLoadHwpx(fileBuffer);
    }
    catch (err) {
        if (err instanceof Error && err.message.includes('not a valid zip file')) {
            throw new Error(`Failed to parse HWPX file as ZIP: ${filePath} — ${err.message}`);
        }
        throw err;
    }
}
//# sourceMappingURL=loader.js.map