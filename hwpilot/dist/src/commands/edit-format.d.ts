type FormatCommandOptions = {
    bold?: boolean;
    italic?: boolean;
    underline?: boolean;
    font?: string;
    size?: number;
    color?: string;
    start?: number;
    end?: number;
    pretty?: boolean;
};
export declare function editFormatCommand(file: string, ref: string, options: FormatCommandOptions): Promise<void>;
export {};
//# sourceMappingURL=edit-format.d.ts.map