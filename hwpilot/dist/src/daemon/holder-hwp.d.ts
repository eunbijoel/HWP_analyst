import type { FlushScheduler } from '../daemon/flush.js';
import type { EditOperation } from '../sdk/edit-types.js';
import type { DocumentHeader, Section } from '../sdk/types.js';
export declare class HwpHolder {
    private readonly filePath;
    private cfb;
    private compressed;
    private sectionsCache;
    private headerCache;
    private dirty;
    private fileStats;
    private contentDigest;
    constructor(filePath: string);
    load(): Promise<void>;
    getSections(): Promise<Section[]>;
    getSectionTexts(): Promise<string[]>;
    applyOperations(ops: EditOperation[]): Promise<void>;
    flush(): Promise<void>;
    isDirty(): boolean;
    getHeader(): Promise<DocumentHeader>;
    getFormat(): 'hwp';
    scheduleFlush(scheduler: FlushScheduler): void;
    private checkFileChanged;
    private requireCfb;
    private getFileHeaderBuffer;
    private serializeCfb;
}
//# sourceMappingURL=holder-hwp.d.ts.map