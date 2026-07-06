import { createHash, randomBytes } from 'node:crypto';
import { existsSync, readFileSync, realpathSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
export function getStateFilePath(filePath) {
    const resolvedPath = resolve(filePath);
    let realPath;
    if (existsSync(resolvedPath)) {
        realPath = realpathSync(resolvedPath);
    }
    else {
        realPath = resolvedPath;
    }
    const hash = createHash('sha256').update(realPath).digest('hex').slice(0, 16);
    return `${tmpdir()}/hwpilotd-${hash}.json`;
}
export function writeStateFile(filePath, data) {
    const stateFilePath = getStateFilePath(filePath);
    const tmpPath = `${stateFilePath}.tmp`;
    writeFileSync(tmpPath, JSON.stringify(data));
    renameSync(tmpPath, stateFilePath);
}
export function writeStateFileExclusive(filePath, data) {
    const stateFilePath = getStateFilePath(filePath);
    writeFileSync(stateFilePath, JSON.stringify(data), { flag: 'wx' });
}
export function readStateFile(filePath) {
    try {
        const stateFilePath = getStateFilePath(filePath);
        const content = readFileSync(stateFilePath, 'utf8');
        return JSON.parse(content);
    }
    catch {
        return null;
    }
}
export function deleteStateFile(filePath) {
    try {
        unlinkSync(getStateFilePath(filePath));
    }
    catch {
        // ignore ENOENT
    }
}
export function isProcessAlive(pid) {
    try {
        process.kill(pid, 0);
        return true;
    }
    catch {
        return false;
    }
}
export function generateToken() {
    return randomBytes(16).toString('hex');
}
export function getVersion() {
    const require = createRequire(import.meta.url);
    const pkg = require('../../package.json');
    return pkg.version;
}
//# sourceMappingURL=state-file.js.map