import { realpathSync } from 'node:fs';
import { open } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { createFlushScheduler } from '../daemon/flush.js';
import { HwpHolder } from '../daemon/holder-hwp.js';
import { HwpxHolder } from '../daemon/holder-hwpx.js';
import { createMessageReader, encodeMessage } from '../daemon/protocol.js';
import { deleteStateFile, generateToken, getVersion, writeStateFileExclusive } from '../daemon/state-file.js';
import { extractAllText, extractPaginatedText, extractRefText, findInSections, getTableData, listImages, listTables, resolveRef, } from '../sdk/document-ops.js';
import { detectFormat } from '../sdk/format-detector.js';
import { buildRef, parseRef } from '../sdk/refs.js';
const DEFAULT_IDLE_MS = 15 * 60 * 1000;
const DEFAULT_FLUSH_MS = 500;
export async function startDaemonServer(filePath) {
    const resolvedPath = resolvePath(filePath);
    const magic = await readMagicBytes(resolvedPath);
    const format = detectFormat(magic);
    const holder = format === 'hwp' ? new HwpHolder(resolvedPath) : new HwpxHolder(resolvedPath);
    await holder.load();
    const flushMs = parseEnvMs('HWPILOT_DAEMON_FLUSH_MS', DEFAULT_FLUSH_MS);
    const scheduler = createFlushScheduler(() => holder.flush(), flushMs);
    const token = generateToken();
    const version = getVersion();
    let requestQueue = Promise.resolve();
    const idleMs = parseEnvMs('HWPILOT_DAEMON_IDLE_MS', DEFAULT_IDLE_MS);
    let idleTimer = null;
    const server = createServer((socket) => {
        const reader = createMessageReader((msg) => {
            requestQueue = requestQueue
                .then(async () => {
                const response = await handleRequest(msg, token, holder, scheduler);
                socket.write(encodeMessage(response));
                resetIdleTimer();
            })
                .catch((err) => {
                const errResponse = {
                    success: false,
                    error: err instanceof Error ? err.message : String(err),
                };
                socket.write(encodeMessage(errResponse));
            });
        });
        socket.on('data', (chunk) => {
            try {
                reader(chunk);
            }
            catch (err) {
                const errResponse = {
                    success: false,
                    error: err instanceof Error ? err.message : String(err),
                };
                socket.write(encodeMessage(errResponse));
            }
        });
        socket.on('error', () => { });
    });
    function resetIdleTimer() {
        if (idleTimer) {
            clearTimeout(idleTimer);
        }
        idleTimer = setTimeout(() => {
            void shutdown('idle timeout');
        }, idleMs);
    }
    async function shutdown(reason) {
        void reason;
        if (idleTimer) {
            clearTimeout(idleTimer);
            idleTimer = null;
        }
        scheduler.cancel();
        try {
            await holder.flush();
        }
        catch { }
        deleteStateFile(resolvedPath);
        await new Promise((resolveClose) => {
            server.close(() => resolveClose());
        });
        process.exit(0);
    }
    await new Promise((resolveListen) => {
        server.listen(0, '127.0.0.1', () => resolveListen());
    });
    const address = server.address();
    if (!address || typeof address === 'string') {
        throw new Error('Failed to bind daemon server');
    }
    try {
        writeStateFileExclusive(resolvedPath, {
            port: address.port,
            token,
            pid: process.pid,
            version,
        });
    }
    catch (err) {
        if (err instanceof Error && 'code' in err && err.code === 'EEXIST') {
            // Another daemon won the race — exit gracefully
            server.close();
            process.exit(0);
        }
        throw err;
    }
    process.on('SIGTERM', () => {
        void shutdown('SIGTERM');
    });
    process.on('SIGINT', () => {
        void shutdown('SIGINT');
    });
    process.on('uncaughtException', (err) => {
        process.stderr.write(`Daemon uncaught exception: ${err.message}\n`);
        void shutdown('uncaughtException');
    });
    process.on('unhandledRejection', (reason) => {
        process.stderr.write(`Daemon unhandled rejection: ${reason}\n`);
        void shutdown('unhandledRejection');
    });
    resetIdleTimer();
}
async function handleRequest(msg, token, holder, scheduler) {
    if (!isValidRequest(msg)) {
        return { success: false, error: 'Invalid request format' };
    }
    if (msg.token !== token) {
        return { success: false, error: 'Unauthorized: invalid token' };
    }
    const sections = await holder.getSections();
    try {
        switch (msg.command) {
            case 'read': {
                const ref = typeof msg.args.ref === 'string' ? msg.args.ref : undefined;
                const header = await holder.getHeader();
                if (ref) {
                    const resolved = resolveRef(ref, sections);
                    return { success: true, data: enrichReadResult(resolved, header) };
                }
                const offset = numberArg(msg.args.offset, 0);
                const limit = numberArg(msg.args.limit, Number.POSITIVE_INFINITY);
                const hasPagination = msg.args.offset !== undefined || msg.args.limit !== undefined;
                return {
                    success: true,
                    data: {
                        format: holder.getFormat(),
                        sections: sections.map((section, index) => {
                            const paragraphs = hasPagination ? section.paragraphs.slice(offset, offset + limit) : section.paragraphs;
                            return {
                                index,
                                ...(hasPagination && {
                                    totalParagraphs: section.paragraphs.length,
                                    totalTables: section.tables.length,
                                    totalImages: section.images.length,
                                    totalTextBoxes: section.textBoxes.length,
                                }),
                                paragraphs: paragraphs.map((paragraph) => enrichParagraph(paragraph, header)),
                                tables: section.tables,
                                images: section.images,
                                textBoxes: section.textBoxes,
                            };
                        }),
                        header,
                    },
                };
            }
            case 'text': {
                const ref = typeof msg.args.ref === 'string' ? msg.args.ref : undefined;
                if (ref) {
                    const text = extractRefText(ref, sections);
                    return { success: true, data: { ref, text } };
                }
                const hasPagination = msg.args.offset !== undefined || msg.args.limit !== undefined;
                if (hasPagination) {
                    const offset = numberArg(msg.args.offset, 0);
                    const limit = numberArg(msg.args.limit, Number.POSITIVE_INFINITY);
                    const result = extractPaginatedText(sections, offset, limit);
                    return { success: true, data: result };
                }
                // HWP full-text must use loadHwpSectionTexts to match direct mode behavior
                if (holder instanceof HwpHolder) {
                    const texts = await holder.getSectionTexts();
                    return { success: true, data: { text: texts.join('\n') } };
                }
                return { success: true, data: { text: extractAllText(sections) } };
            }
            case 'find': {
                const query = typeof msg.args.query === 'string' ? msg.args.query : '';
                const matches = findInSections(sections, query);
                return { success: true, data: { matches } };
            }
            case 'table-read': {
                const ref = stringArg(msg.args.ref, 'ref');
                return { success: true, data: getTableData(sections, ref) };
            }
            case 'table-list': {
                return { success: true, data: listTables(sections) };
            }
            case 'image-list': {
                return { success: true, data: listImages(sections) };
            }
            case 'edit-text': {
                const ref = stringArg(msg.args.ref, 'ref');
                const text = stringArg(msg.args.text, 'text');
                await holder.applyOperations([{ type: 'setText', ref, text }]);
                await scheduler.flushNow();
                return { success: true, data: { ref, text, success: true } };
            }
            case 'edit-format': {
                const ref = stringArg(msg.args.ref, 'ref');
                const format = formatArg(msg.args.format);
                const start = typeof msg.args.start === 'number' ? msg.args.start : undefined;
                const end = typeof msg.args.end === 'number' ? msg.args.end : undefined;
                await holder.applyOperations([{ type: 'setFormat', ref, format, start, end }]);
                await scheduler.flushNow();
                return { success: true, data: { ref, format, success: true } };
            }
            case 'table-edit': {
                const ref = stringArg(msg.args.ref, 'ref');
                const text = stringArg(msg.args.text, 'text');
                await holder.applyOperations([{ type: 'setTableCell', ref, text }]);
                await scheduler.flushNow();
                return { success: true, data: { ref, text, success: true } };
            }
            case 'table-add': {
                const ref = stringArg(msg.args.ref, 'ref');
                const rows = numberArg(msg.args.rows, 0);
                const cols = numberArg(msg.args.cols, 0);
                const data = Array.isArray(msg.args.data) ? msg.args.data : undefined;
                const position = (typeof msg.args.position === 'string' ? msg.args.position : 'end');
                const parsedRef = parseRef(ref);
                const tableCount = sections[parsedRef.section]?.tables.length ?? 0;
                await holder.applyOperations([{ type: 'addTable', ref, rows, cols, data, position }]);
                await scheduler.flushNow();
                const newRef = buildRef({ section: parsedRef.section, table: tableCount });
                return { success: true, data: { ref: newRef, rows, cols, success: true } };
            }
            case 'paragraph-add': {
                const ref = stringArg(msg.args.ref, 'ref');
                const text = stringArg(msg.args.text, 'text');
                const position = stringArg(msg.args.position, 'position');
                const format = msg.args.format;
                const heading = msg.args.heading;
                const style = msg.args.style;
                await holder.applyOperations([
                    { type: 'addParagraph', ref, text, position: position, format, heading, style },
                ]);
                await scheduler.flushNow();
                return { success: true, data: { ref, text, position, success: true } };
            }
            default:
                return { success: false, error: `Unknown command: ${msg.command}` };
        }
    }
    catch (err) {
        return {
            success: false,
            error: err instanceof Error ? err.message : String(err),
        };
    }
}
function resolvePath(filePath) {
    const absolutePath = resolve(filePath);
    try {
        return realpathSync(absolutePath);
    }
    catch {
        return absolutePath;
    }
}
function parseEnvMs(name, fallback) {
    const value = process.env[name];
    if (!value) {
        return fallback;
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) {
        return fallback;
    }
    return parsed;
}
function numberArg(value, fallback) {
    return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}
function stringArg(value, name) {
    if (typeof value !== 'string') {
        throw new Error(`Invalid ${name}`);
    }
    return value;
}
function formatArg(value) {
    if (!value || typeof value !== 'object') {
        throw new Error('Invalid format');
    }
    const format = value;
    const output = {};
    if (typeof format.bold === 'boolean')
        output.bold = format.bold;
    if (typeof format.italic === 'boolean')
        output.italic = format.italic;
    if (typeof format.underline === 'boolean')
        output.underline = format.underline;
    if (typeof format.fontName === 'string')
        output.fontName = format.fontName;
    if (typeof format.fontSize === 'number')
        output.fontSize = format.fontSize;
    if (typeof format.color === 'string')
        output.color = format.color;
    return output;
}
function isValidRequest(msg) {
    return (typeof msg === 'object' &&
        msg !== null &&
        'token' in msg &&
        'command' in msg &&
        'args' in msg &&
        typeof msg.token === 'string' &&
        typeof msg.command === 'string' &&
        typeof msg.args === 'object' &&
        msg.args !== null);
}
function enrichReadResult(resolved, header) {
    if (!resolved || typeof resolved !== 'object') {
        return resolved;
    }
    if ('ref' in resolved && 'runs' in resolved) {
        return enrichParagraph(resolved, header);
    }
    return resolved;
}
function enrichParagraph(para, header) {
    const enriched = { ...para };
    // Resolve style name and heading level from styleRef
    // Style name is the authoritative source for heading level (e.g. '개요 1' = heading 1)
    // because Hancom templates may share a single paraShapeRef across multiple heading styles
    const style = header.styles.find((item) => item.id === para.styleRef);
    if (style) {
        enriched.styleName = style.name;
        const level = headingLevelFromStyleName(style.name);
        if (level !== undefined) {
            enriched.headingLevel = level;
        }
    }
    // Fall back to paraShape heading bits if style didn't provide heading level
    if (enriched.headingLevel === undefined) {
        const paraShape = header.paraShapes.find((shape) => shape.id === para.paraShapeRef);
        if (paraShape?.headingLevel && paraShape.headingLevel > 0) {
            enriched.headingLevel = paraShape.headingLevel;
        }
    }
    return enriched;
}
async function readMagicBytes(filePath) {
    const fh = await open(filePath, 'r');
    try {
        const buf = Buffer.alloc(4);
        await fh.read(buf, 0, 4, 0);
        return new Uint8Array(buf);
    }
    finally {
        await fh.close();
    }
}
const HEADING_STYLE_RE = /^(?:개요|Outline|Heading)\s+(\d+)$/i;
function headingLevelFromStyleName(name) {
    const match = HEADING_STYLE_RE.exec(name);
    if (!match)
        return undefined;
    const level = Number(match[1]);
    return level >= 1 && level <= 7 ? level : undefined;
}
//# sourceMappingURL=server.js.map