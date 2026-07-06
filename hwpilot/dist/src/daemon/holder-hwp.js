import { createHash } from 'node:crypto';
import { readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import CFB from 'cfb';
import { writeCfb } from '../sdk/formats/hwp/cfb-writer.js';
import { mutateHwpCfb } from '../sdk/formats/hwp/mutator.js';
import { loadHwp, loadHwpSectionTexts } from '../sdk/formats/hwp/reader.js';
import { getCompressionFlag } from '../sdk/formats/hwp/stream-util.js';
import { validateHwpBuffer } from '../sdk/formats/hwp/validator.js';
export class HwpHolder {
    filePath;
    cfb = null;
    compressed = false;
    sectionsCache = null;
    headerCache = null;
    dirty = false;
    fileStats = null;
    contentDigest = null;
    constructor(filePath) {
        this.filePath = filePath;
    }
    async load() {
        const buffer = await readFile(this.filePath);
        this.cfb = CFB.read(buffer, { type: 'buffer' });
        this.compressed = getCompressionFlag(this.getFileHeaderBuffer(this.cfb));
        this.sectionsCache = null;
        this.headerCache = null;
        this.dirty = false;
        const stats = await stat(this.filePath);
        this.fileStats = { ino: stats.ino, mtimeMs: stats.mtimeMs, size: stats.size };
        this.contentDigest = createHash('sha256').update(buffer).digest('hex');
    }
    async getSections() {
        await this.checkFileChanged();
        const cfb = this.requireCfb();
        if (!this.sectionsCache) {
            const buffer = this.serializeCfb(cfb);
            const doc = await loadHwp(new Uint8Array(buffer));
            this.sectionsCache = doc.sections;
            this.headerCache = doc.header;
        }
        return this.sectionsCache;
    }
    async getSectionTexts() {
        await this.checkFileChanged();
        const cfb = this.requireCfb();
        const buffer = this.serializeCfb(cfb);
        return loadHwpSectionTexts(new Uint8Array(buffer));
    }
    async applyOperations(ops) {
        if (ops.length === 0) {
            return;
        }
        const cfb = this.requireCfb();
        mutateHwpCfb(cfb, ops, this.compressed);
        this.sectionsCache = null;
        this.headerCache = null;
        this.dirty = true;
    }
    async flush() {
        if (!this.dirty) {
            return;
        }
        const cfb = this.requireCfb();
        const tmpPath = `${this.filePath}.tmp`;
        const buffer = this.serializeCfb(cfb);
        const result = await validateHwpBuffer(buffer);
        if (!result.valid) {
            const failedChecks = result.checks.filter((c) => c.status === 'fail');
            const failedCheckText = failedChecks.map((c) => c.name + (c.message ? ': ' + c.message : '')).join('; ');
            await this.load();
            throw new Error('HWP validation failed: ' + failedCheckText);
        }
        try {
            await writeFile(tmpPath, buffer);
            await rename(tmpPath, this.filePath);
        }
        catch (error) {
            await rm(tmpPath, { force: true });
            throw error;
        }
        this.dirty = false;
    }
    isDirty() {
        return this.dirty;
    }
    async getHeader() {
        await this.getSections();
        return this.headerCache;
    }
    getFormat() {
        return 'hwp';
    }
    scheduleFlush(scheduler) {
        if (this.dirty) {
            scheduler.schedule();
        }
    }
    async checkFileChanged() {
        if (!this.fileStats)
            return;
        try {
            const stats = await stat(this.filePath);
            let changed = stats.ino !== this.fileStats.ino || stats.mtimeMs > this.fileStats.mtimeMs || stats.size !== this.fileStats.size;
            // When stats look unchanged but we have dirty state, verify content
            // hasn't changed. Stat metadata can match after fast delete+recreate
            // (inode reuse on tmpfs + same-ms mtime + same CFB-padded file size).
            if (!changed && this.dirty && this.contentDigest) {
                const buffer = await readFile(this.filePath);
                const digest = createHash('sha256').update(buffer).digest('hex');
                changed = digest !== this.contentDigest;
            }
            if (changed) {
                if (this.dirty) {
                    console.warn(`File replaced externally while holder had unflushed changes: ${this.filePath}`);
                }
                await this.load();
            }
        }
        catch (err) {
            if (err.code === 'ENOENT') {
                throw new Error(`File no longer exists: ${this.filePath}`);
            }
            throw err;
        }
    }
    requireCfb() {
        if (!this.cfb) {
            throw new Error('HwpHolder is not loaded. Call load() first.');
        }
        return this.cfb;
    }
    getFileHeaderBuffer(cfb) {
        const fileHeaderEntry = CFB.find(cfb, 'FileHeader');
        if (!fileHeaderEntry?.content) {
            throw new Error('Invalid HWP file: FileHeader not found');
        }
        return Buffer.from(fileHeaderEntry.content);
    }
    serializeCfb(cfb) {
        return writeCfb(cfb);
    }
}
//# sourceMappingURL=holder-hwp.js.map