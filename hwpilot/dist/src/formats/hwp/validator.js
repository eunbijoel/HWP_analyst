import { readFile } from 'node:fs/promises';
import { validateHwp as sdkValidateHwp, validateHwpBuffer, } from '../../sdk/formats/hwp/validator.js';
export { validateHwpBuffer };
export async function validateHwp(filePath, options = {}) {
    const buffer = await readFile(filePath);
    const result = await sdkValidateHwp(buffer, options);
    result.file = filePath;
    return result;
}
//# sourceMappingURL=validator.js.map