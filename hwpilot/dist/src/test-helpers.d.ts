import { buildCellListHeaderData } from './formats/hwp/record-serializer.js';
export { buildCellListHeaderData };
export type TestTable = {
    rows: string[][];
};
export type MergedCell = {
    text: string;
    colSpan?: number;
    rowSpan?: number;
    col?: number;
    row?: number;
};
export type MergedTableRow = MergedCell[];
export type TestImage = {
    name: string;
    data: Buffer;
    format: string;
};
export type TestTextBox = {
    text: string;
};
export type TestHwpxOptions = {
    paragraphs?: string[];
    tables?: TestTable[];
    images?: TestImage[];
    textBoxes?: TestTextBox[];
    font?: string;
    fontSize?: number;
};
export type TestHwpOptions = {
    paragraphs?: string[];
    tables?: TestTable[];
    textBoxes?: TestTextBox[];
    compressed?: boolean;
};
export declare function createTestHwpx(opts?: TestHwpxOptions): Promise<Buffer>;
export declare function createTestHwpBinary(opts?: TestHwpOptions): Promise<Buffer>;
export declare function createTestHwpCfb(): Buffer;
export declare function buildMergedTable(rows: MergedTableRow[], colCount: number, rowCount: number): Buffer;
//# sourceMappingURL=test-helpers.d.ts.map