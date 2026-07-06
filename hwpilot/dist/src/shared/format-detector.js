import { readFile } from 'node:fs/promises';
import { detectFormat as sdkDetectFormat } from '../sdk/format-detector.js';
export function detectFormatFromBuffer(buffer) {
    return sdkDetectFormat(buffer);
}
export async function detectFormat(filePath) {
    const buffer = await readFile(filePath);
    return sdkDetectFormat(new Uint8Array(buffer));
}
//# sourceMappingURL=format-detector.js.map