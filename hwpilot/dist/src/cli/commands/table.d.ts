export declare function tableReadCommand(file: string, ref: string, options: {
    pretty?: boolean;
}): Promise<void>;
export declare function tableListCommand(file: string, options: {
    pretty?: boolean;
}): Promise<void>;
export declare function tableEditCommand(file: string, ref: string, text: string, options: {
    pretty?: boolean;
}): Promise<void>;
export declare function tableAddCommand(file: string, ref: string, rows: number, cols: number, options: {
    position?: string;
    data?: string;
    pretty?: boolean;
}): Promise<void>;
//# sourceMappingURL=table.d.ts.map