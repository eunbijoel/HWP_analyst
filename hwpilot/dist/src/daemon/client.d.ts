import type { DaemonRequest, DaemonResponse } from '../daemon/protocol.js';
export declare function sendRequest(port: number, token: string, request: Omit<DaemonRequest, 'token'>): Promise<DaemonResponse>;
//# sourceMappingURL=client.d.ts.map