import { createHash } from 'node:crypto';
import { readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { parseHeader } from '../sdk/formats/hwpx/header-parser.js';
import { loadHwpx } from '../sdk/formats/hwpx/loader.js';
import { mutateHwpxZip } from '../sdk/formats/hwpx/mutator.js';
import { parseSections } from '../sdk/formats/hwpx/section-parser.js';
export class HwpxHolder {
    filePath;
    archive = null;
    zip = null;
    sectionsCache = null;
    headerCache = null;
    dirty = false;
    fileStats = null;
    contentDigest = null;
    constructor(filePath) {
        this.filePath = filePath;
    }
    async load() {
        const rawBuffer = await readFile(this.filePath);
        this.archive = await loadHwpx(new Uint8Array(rawBuffer));
        this.zip = this.archive.getZip();
        this.sectionsCache = null;
        this.headerCache = null;
        this.dirty = false;
        const stats = await stat(this.filePath);
        this.fileStats = { ino: stats.ino, mtimeMs: stats.mtimeMs, size: stats.size };
        this.contentDigest = createHash('sha256').update(rawBuffer).digest('hex');
    }
    async getSections() {
        await this.checkFileChanged();
        const archive = this.requireArchive();
        if (!this.sectionsCache) {
            this.sectionsCache = await parseSections(archive);
        }
        return this.sectionsCache;
    }
    async applyOperations(ops) {
        if (ops.length === 0) {
            return;
        }
        const archive = this.requireArchive();
        const zip = this.requireZip();
        await mutateHwpxZip(zip, archive, ops);
        this.sectionsCache = null;
        this.headerCache = null;
        this.dirty = true;
    }
    async flush() {
        if (!this.dirty) {
            return;
        }
        const zip = this.requireZip();
        const buffer = await zip.generateAsync({ type: 'nodebuffer' });
        const tmpPath = `${this.filePath}.tmp`;
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
        await this.checkFileChanged();
        if (!this.headerCache) {
            const archive = this.requireArchive();
            this.headerCache = parseHeader(await archive.getHeaderXml());
        }
        return this.headerCache;
    }
    getFormat() {
        return 'hwpx';
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
            // (inode reuse on tmpfs + same-ms mtime + same file size).
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
    requireArchive() {
        if (!this.archive) {
            throw new Error('HwpxHolder is not loaded. Call load() first.');
        }
        return this.archive;
    }
    requireZip() {
        if (!this.zip) {
            throw new Error('HwpxHolder is not loaded. Call load() first.');
        }
        return this.zip;
    }
}
//# sourceMappingURL=holder-hwpx.js.map