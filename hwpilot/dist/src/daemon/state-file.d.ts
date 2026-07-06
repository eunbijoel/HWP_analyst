export type StateFileData = {
    port: number;
    token: string;
    pid: number;
    version: string;
};
export declare function getStateFilePath(filePath: string): string;
export declare function writeStateFile(filePath: string, data: StateFileData): void;
export declare function writeStateFileExclusive(filePath: string, data: StateFileData): void;
export declare function readStateFile(filePath: string): StateFileData | null;
export declare function deleteStateFile(filePath: string): void;
export declare function isProcessAlive(pid: number): boolean;
export declare function generateToken(): string;
export declare function getVersion(): string;
//# sourceMappingURL=state-file.d.ts.map