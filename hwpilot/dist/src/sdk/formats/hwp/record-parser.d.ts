export type RecordHeader = {
    tagId: number;
    level: number;
    size: number;
    headerSize: number;
};
export declare function parseRecordHeader(buffer: Buffer, offset: number): RecordHeader | null;
export declare function iterateRecords(buffer: Buffer): Generator<{
    header: RecordHeader;
    data: Buffer;
    offset: number;
}>;
//# sourceMappingURL=record-parser.d.ts.map