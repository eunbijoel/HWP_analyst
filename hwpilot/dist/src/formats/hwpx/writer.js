import { readFile, writeFile } from 'node:fs/promises';
import { editHwpx as sdkEditHwpx } from '../../sdk/formats/hwpx/writer.js';
export async function editHwpx(filePath, operations) {
    if (operations.length === 0) {
        return;
    }
    const buffer = await readFile(filePath);
    const result = await sdkEditHwpx(new Uint8Array(buffer), operations);
    await writeFile(filePath, Buffer.from(result));
}
//# sourceMappingURL=writer.js.map