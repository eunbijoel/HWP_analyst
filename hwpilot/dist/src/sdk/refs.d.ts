export type ParsedRef = {
    section: number;
    paragraph?: number;
    run?: number;
    table?: number;
    row?: number;
    cell?: number;
    cellParagraph?: number;
    image?: number;
    textBox?: number;
    textBoxParagraph?: number;
};
export declare function validateRef(ref: string): boolean;
export declare function parseRef(ref: string): ParsedRef;
export declare function buildRef(parts: ParsedRef): string;
//# sourceMappingURL=refs.d.ts.map