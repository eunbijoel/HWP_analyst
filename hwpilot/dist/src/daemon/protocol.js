const MAX_MESSAGE_SIZE = 64 * 1024 * 1024; // 64 MB
export function encodeMessage(obj) {
    const json = JSON.stringify(obj);
    const body = Buffer.from(json, 'utf8');
    const header = Buffer.alloc(4);
    header.writeUInt32BE(body.length, 0);
    return Buffer.concat([header, body]);
}
export function createMessageReader(callback) {
    let buffer = Buffer.alloc(0);
    return (chunk) => {
        buffer = Buffer.concat([buffer, chunk]);
        while (buffer.length >= 4) {
            const length = buffer.readUInt32BE(0);
            if (length > MAX_MESSAGE_SIZE) {
                buffer = Buffer.alloc(0);
                throw new Error(`Message too large: ${length} bytes`);
            }
            if (buffer.length < 4 + length) {
                break; // wait for more data
            }
            const body = buffer.subarray(4, 4 + length);
            buffer = buffer.subarray(4 + length);
            const parsed = JSON.parse(body.toString('utf8'));
            callback(parsed);
        }
    };
}
//# sourceMappingURL=protocol.js.map