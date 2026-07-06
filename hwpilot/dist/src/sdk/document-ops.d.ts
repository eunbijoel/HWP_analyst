import type { Image, Section } from '../sdk/types.js';
type Match = {
    ref: string;
    text: string;
    container: 'paragraph' | 'table' | 'textBox';
};
export declare function resolveRef(ref: string, sections: Section[]): unknown;
export declare function findInSections(sections: Section[], query: string): Match[];
export declare function extractRefText(ref: string, sections: Section[]): string;
export declare function extractAllText(sections: Section[]): string;
export declare function extractPaginatedText(sections: Section[], offset: number, limit: number): {
    text: string;
    totalParagraphs: number;
    offset: number;
    count: number;
};
export declare function getTableData(sections: Section[], ref: string): {
    ref: string;
    rows: {
        cells: {
            ref: string;
            text: string;
            paragraphs: unknown[];
        }[];
    }[];
};
export declare function listTables(sections: Section[]): {
    ref: string;
    rows: number;
    cols: number;
}[];
export declare function listImages(sections: Section[]): Image[];
export {};
//# sourceMappingURL=document-ops.d.ts.map