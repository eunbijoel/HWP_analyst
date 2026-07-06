export declare function encodeRecordHeader(tagId: number, level: number, dataSize: number): Buffer;
export declare function buildRecord(tagId: number, level: number, data: Buffer): Buffer;
export declare function replaceRecordData(stream: Buffer, recordOffset: number, newData: Buffer): Buffer;
export declare function buildTableData(rowCount: number, colCount: number, cellsPerRow?: number[]): Buffer;
export declare function buildCellListHeaderData(col: number, row: number, colSpan: number, rowSpan: number): Buffer;
export declare function buildTableCtrlHeaderData(instanceId?: number): Buffer;
export declare function buildParaLineSegBuffer(segmentWidth?: number): Buffer;
//# sourceMappingURL=record-serializer.d.ts.map