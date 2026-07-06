import type { FlushScheduler } from '../daemon/flush.js';
import type { EditOperation } from '../sdk/edit-types.js';
import type { DocumentHeader, Section } from '../sdk/types.js';
export declare class HwpxHolder {
    private readonly filePath;
    private archive;
    private zip;
    private sectionsCache;
    private headerCache;
    private dirty;
    private fileStats;
    private contentDigest;
    constructor(filePath: string);
    load(): Promise<void>;
    getSections(): Promise<Section[]>;
    applyOperations(ops: EditOperation[]): Promise<void>;
    flush(): Promise<void>;
    isDirty(): boolean;
    getHeader(): Promise<DocumentHeader>;
    getFormat(): 'hwpx';
    scheduleFlush(scheduler: FlushScheduler): void;
    private checkFileChanged;
    private requireArchive;
    private requireZip;
}
//# sourceMappingURL=holder-hwpx.d.ts.map