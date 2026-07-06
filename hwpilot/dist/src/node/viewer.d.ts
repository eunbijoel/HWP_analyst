export type ViewerCheckResult = {
    corrupted: boolean;
    alert?: string;
    skipped: boolean;
};
export declare function isHwpViewerAvailable(): Promise<boolean>;
export declare function checkViewerCorruption(filePath: string): Promise<ViewerCheckResult>;
//# sourceMappingURL=viewer.d.ts.map