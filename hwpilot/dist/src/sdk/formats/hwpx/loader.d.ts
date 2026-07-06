import JSZip from 'jszip';
export type HwpxArchive = {
    getVersionXml(): Promise<string>;
    getHeaderXml(): Promise<string>;
    getSectionXml(n: number): Promise<string>;
    getSectionCount(): number;
    listBinData(): string[];
    getBinData(path: string): Promise<Buffer>;
    getZip(): JSZip;
};
export declare function loadHwpx(fileBuffer: Uint8Array): Promise<HwpxArchive>;
//# sourceMappingURL=loader.d.ts.map