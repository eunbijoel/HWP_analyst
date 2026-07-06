export type FlushScheduler = {
    schedule(): void;
    cancel(): void;
    flushNow(): Promise<void>;
};
export declare function createFlushScheduler(flushFn: () => Promise<void>, debounceMs: number): FlushScheduler;
//# sourceMappingURL=flush.d.ts.map