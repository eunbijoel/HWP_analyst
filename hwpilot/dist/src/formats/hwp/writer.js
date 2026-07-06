import { readFile, writeFile } from 'node:fs/promises';
import { editHwp as sdkEditHwp } from '../../sdk/formats/hwp/writer.js';
export async function editHwp(filePath, operations) {
    if (operations.length === 0) {
        return;
    }
    const buffer = await readFile(filePath);
    const result = await sdkEditHwp(new Uint8Array(buffer), operations);
    await writeFile(filePath, Buffer.from(result));
}
//# sourceMappingURL=writer.js.map