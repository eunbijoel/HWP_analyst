import CFB from 'cfb';
import { writeCfb } from './cfb-writer.js';
import { getEntryBuffer, mutateHwpCfb } from './mutator.js';
import { getCompressionFlag } from './stream-util.js';
import { validateHwpBuffer } from './validator.js';
export async function editHwp(fileBuffer, operations) {
    if (operations.length === 0) {
        return fileBuffer;
    }
    const cfb = CFB.read(Buffer.from(fileBuffer), { type: 'buffer' });
    const fileHeader = getEntryBuffer(cfb, '/FileHeader');
    const compressed = getCompressionFlag(fileHeader);
    mutateHwpCfb(cfb, operations, compressed);
    const buffer = writeCfb(cfb);
    const result = await validateHwpBuffer(buffer);
    if (!result.valid) {
        const failedChecks = result.checks.filter((c) => c.status === 'fail');
        const failedCheckText = failedChecks.map((c) => c.name + (c.message ? ': ' + c.message : '')).join('; ');
        throw new Error('HWP validation failed: ' + failedCheckText);
    }
    return new Uint8Array(buffer);
}
//# sourceMappingURL=writer.js.map