type ReadOptions = {
    pretty?: boolean;
    offset?: number;
    limit?: number;
};
export declare function readCommand(file: string, ref: string | undefined, options: ReadOptions): Promise<void>;
export {};
//# sourceMappingURL=read.d.ts.map