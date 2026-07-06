import { validateHwpBuffer, type ValidateResult, type CheckResult, type CheckStatus, type ValidateHwpOptions } from '../../sdk/formats/hwp/validator.js';
export type { ValidateResult, CheckResult, CheckStatus, ValidateHwpOptions };
export { validateHwpBuffer };
export declare function validateHwp(filePath: string, options?: ValidateHwpOptions): Promise<ValidateResult>;
//# sourceMappingURL=validator.d.ts.map