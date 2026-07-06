import type { FormatOptions } from '../sdk/edit-types.js';
import type { HwpDocument } from '../sdk/types.js';
export declare class Document {
    private _doc;
    private _bytes;
    private readonly _format;
    constructor(doc: HwpDocument, bytes: Uint8Array);
    get format(): 'hwp' | 'hwpx';
    read(refOrOptions?: string | {
        offset?: number;
        limit?: number;
    }): unknown;
    text(refOrOptions?: string | {
        offset?: number;
        limit?: number;
    }): string;
    find(query: string): unknown[];
    tableRead(ref: string): unknown;
    tableList(): unknown[];
    imageList(): unknown[];
    editText(ref: string, text: string): Promise<void>;
    editFormat(ref: string, format: FormatOptions & {
        start?: number;
        end?: number;
    }): Promise<void>;
    tableEdit(ref: string, text: string): Promise<void>;
    addParagraph(ref: string, text: string, options?: {
        position?: 'before' | 'after' | 'end';
        bold?: boolean;
        italic?: boolean;
        underline?: boolean;
        fontName?: string;
        fontSize?: number;
        color?: string;
        heading?: number;
        style?: string | number;
    }): Promise<void>;
    addTable(ref: string, rows: number, cols: number, options?: {
        data?: string[][];
        position?: 'before' | 'after' | 'end';
    }): Promise<void>;
    export(): Promise<Uint8Array>;
    private _applyOp;
}
export declare function documentFromBytes(bytes: Uint8Array): Promise<Document>;
//# sourceMappingURL=document.d.ts.map