import { readFile } from 'node:fs/promises';
import { loadHwp as sdkLoadHwp, loadHwpSectionTexts as sdkLoadHwpSectionTexts, extractParaText } from '../../sdk/formats/hwp/reader.js';
export { extractParaText };
export async function loadHwp(filePath) {
    const buffer = await readFile(filePath);
    return sdkLoadHwp(buffer);
}
export async function loadHwpSectionTexts(filePath) {
    const buffer = await readFile(filePath);
    return sdkLoadHwpSectionTexts(new Uint8Array(buffer));
}
//# sourceMappingURL=reader.js.map