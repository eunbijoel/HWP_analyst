export type DaemonRequest = {
    token: string;
    command: string;
    args: Record<string, unknown>;
};
export type DaemonResponse = {
    success: true;
    data: unknown;
} | {
    success: false;
    error: string;
    context?: unknown;
    hint?: string;
};
export declare function encodeMessage(obj: unknown): Buffer;
export declare function createMessageReader(callback: (msg: unknown) => void): (chunk: Buffer) => void;
//# sourceMappingURL=protocol.d.ts.map