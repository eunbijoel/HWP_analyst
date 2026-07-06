import { deflateRaw, inflateRaw } from 'pako';
export function compressStream(buffer) {
    return Buffer.from(deflateRaw(buffer));
}
export function decompressStream(buffer) {
    return Buffer.from(inflateRaw(buffer));
}
export function getCompressionFlag(fileHeaderBuffer) {
    const flags = fileHeaderBuffer.readUInt32LE(36);
    return Boolean(flags & 0x1);
}
export function setCompressionFlag(fileHeaderBuffer, compressed) {
    const flags = fileHeaderBuffer.readUInt32LE(36);
    const updated = compressed ? flags | 0x1 : flags & ~0x1;
    fileHeaderBuffer.writeUInt32LE(updated, 36);
}
//# sourceMappingURL=stream-util.js.map