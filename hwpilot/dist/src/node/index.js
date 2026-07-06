import { readFile, writeFile } from 'node:fs/promises';
import { detectFormat } from '../sdk/format-detector.js';
import { loadHwp } from '../sdk/formats/hwp/reader.js';
import { editHwp } from '../sdk/formats/hwp/writer.js';
import { createHwp } from '../sdk/formats/hwp/creator.js';
import { parseHeader } from '../sdk/formats/hwpx/header-parser.js';
import { loadHwpx } from '../sdk/formats/hwpx/loader.js';
import { editHwpx } from '../sdk/formats/hwpx/writer.js';
import { parseSections } from '../sdk/formats/hwpx/section-parser.js';
export async function openFile(filePath) {
    const buffer = await readFile(filePath);
    const bytes = new Uint8Array(buffer);
    const format = detectFormat(bytes);
    if (format === 'hwp') {
        return loadHwp(bytes);
    }
    const archive = await loadHwpx(bytes);
    const header = parseHeader(await archive.getHeaderXml());
    const sections = await parseSections(archive);
    return { format: 'hwpx', sections, header };
}
export async function editFile(filePath, operations) {
    const buffer = await readFile(filePath);
    const bytes = new Uint8Array(buffer);
    const format = detectFormat(bytes);
    let result;
    if (format === 'hwp') {
        result = await editHwp(bytes, operations);
    }
    else {
        result = await editHwpx(bytes, operations);
    }
    await writeFile(filePath, Buffer.from(result));
}
export async function createHwpFile(filePath, options) {
    const buffer = await createHwp(options);
    await writeFile(filePath, buffer);
}
//# sourceMappingURL=index.js.map