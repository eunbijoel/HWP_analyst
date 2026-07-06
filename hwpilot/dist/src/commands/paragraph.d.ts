type ParagraphAddCommandOptions = {
    position?: string;
    bold?: boolean;
    italic?: boolean;
    underline?: boolean;
    font?: string;
    size?: number;
    color?: string;
    heading?: number;
    style?: string | number;
    pretty?: boolean;
};
export declare function paragraphAddCommand(file: string, ref: string, text: string, options: ParagraphAddCommandOptions): Promise<void>;
export {};
//# sourceMappingURL=paragraph.d.ts.map