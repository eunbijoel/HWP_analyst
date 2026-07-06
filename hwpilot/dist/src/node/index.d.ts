import { type CreateHwpOptions } from '../sdk/formats/hwp/creator.js';
import type { EditOperation } from '../sdk/edit-types.js';
import type { HwpDocument } from '../sdk/types.js';
export declare function openFile(filePath: string): Promise<HwpDocument>;
export declare function editFile(filePath: string, operations: EditOperation[]): Promise<void>;
export declare function createHwpFile(filePath: string, options?: CreateHwpOptions): Promise<void>;
//# sourceMappingURL=index.d.ts.map