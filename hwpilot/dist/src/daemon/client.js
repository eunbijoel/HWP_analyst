import { createConnection } from 'node:net';
import { createMessageReader, encodeMessage } from '../daemon/protocol.js';
const CONNECT_TIMEOUT_MS = 5_000;
const RESPONSE_TIMEOUT_MS = 30_000;
export async function sendRequest(port, token, request) {
    return new Promise((resolve, reject) => {
        const socket = createConnection({ port, host: '127.0.0.1' });
        let resolved = false;
        let connectTimer = null;
        let responseTimer = null;
        function cleanup() {
            if (connectTimer)
                clearTimeout(connectTimer);
            if (responseTimer)
                clearTimeout(responseTimer);
            socket.destroy();
        }
        function done(result) {
            if (resolved)
                return;
            resolved = true;
            cleanup();
            if (result instanceof Error)
                reject(result);
            else
                resolve(result);
        }
        connectTimer = setTimeout(() => {
            done(new Error(`Connection timeout to daemon port ${port}`));
        }, CONNECT_TIMEOUT_MS);
        socket.on('error', (err) => {
            if (err.code === 'ECONNREFUSED') {
                done(new Error(`Daemon not running on port ${port} (ECONNREFUSED)`));
            }
            else if (err.code === 'ETIMEDOUT') {
                done(new Error(`Connection timed out to daemon port ${port} (ETIMEDOUT)`));
            }
            else {
                done(err);
            }
        });
        socket.on('connect', () => {
            if (connectTimer) {
                clearTimeout(connectTimer);
                connectTimer = null;
            }
            responseTimer = setTimeout(() => {
                done(new Error(`Response timeout from daemon port ${port}`));
            }, RESPONSE_TIMEOUT_MS);
            const fullRequest = { token, ...request };
            socket.write(encodeMessage(fullRequest));
            const reader = createMessageReader((msg) => {
                done(msg);
            });
            socket.on('data', reader);
        });
    });
}
//# sourceMappingURL=client.js.map