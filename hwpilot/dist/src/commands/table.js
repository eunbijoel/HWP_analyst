import { dispatchViaDaemon } from '../daemon/dispatch.js';
import { loadHwp } from '../formats/hwp/reader.js';
import { editHwp } from '../formats/hwp/writer.js';
import { loadHwpx } from '../formats/hwpx/loader.js';
import { parseSections } from '../formats/hwpx/section-parser.js';
import { editHwpx } from '../formats/hwpx/writer.js';
import { getTableData, listTables } from '../shared/document-ops.js';
import { handleError } from '../shared/error-handler.js';
import { detectFormat } from '../shared/format-detector.js';
import { formatOutput } from '../shared/output.js';
import { getRefHint } from '../shared/ref-hints.js';
import { buildRef, parseRef, validateRef } from '../shared/refs.js';
export async function tableReadCommand(file, ref, options) {
    try {
        const daemonResult = await dispatchViaDaemon(file, 'table-read', { ref });
        if (daemonResult !== null) {
            if (!daemonResult.success) {
                const errorOptions = daemonResult.context && typeof daemonResult.context === 'object'
                    ? { context: daemonResult.context, hint: daemonResult.hint }
                    : daemonResult.hint
                        ? { hint: daemonResult.hint }
                        : undefined;
                handleError(new Error(daemonResult.error), errorOptions);
                return;
            }
            console.log(formatOutput(daemonResult.data, options.pretty));
            return;
        }
        const format = await detectFormat(file);
        if (!validateRef(ref)) {
            throw new Error(`Invalid reference: ${ref}`);
        }
        const sections = format === 'hwp' ? (await loadHwp(file)).sections : await loadHwpxSections(file);
        const output = getTableData(sections, ref);
        console.log(formatOutput(output, options.pretty));
    }
    catch (e) {
        const hint = await getRefHint(file, ref).catch(() => undefined);
        handleError(e, { context: { ref, file }, hint });
    }
}
export async function tableListCommand(file, options) {
    try {
        const daemonResult = await dispatchViaDaemon(file, 'table-list', {});
        if (daemonResult !== null) {
            if (!daemonResult.success) {
                const errorOptions = daemonResult.context && typeof daemonResult.context === 'object'
                    ? { context: daemonResult.context, hint: daemonResult.hint }
                    : daemonResult.hint
                        ? { hint: daemonResult.hint }
                        : undefined;
                handleError(new Error(daemonResult.error), errorOptions);
                return;
            }
            console.log(formatOutput(daemonResult.data, options.pretty));
            return;
        }
        const format = await detectFormat(file);
        const sections = format === 'hwp' ? (await loadHwp(file)).sections : await loadHwpxSections(file);
        const tables = listTables(sections);
        console.log(formatOutput(tables, options.pretty));
    }
    catch (e) {
        handleError(e, { context: { file } });
    }
}
export async function tableEditCommand(file, ref, text, options) {
    try {
        const daemonResult = await dispatchViaDaemon(file, 'table-edit', {
            ref,
            text,
        });
        if (daemonResult !== null) {
            if (!daemonResult.success) {
                const errorOptions = daemonResult.context && typeof daemonResult.context === 'object'
                    ? { context: daemonResult.context, hint: daemonResult.hint }
                    : daemonResult.hint
                        ? { hint: daemonResult.hint }
                        : undefined;
                handleError(new Error(daemonResult.error), errorOptions);
                return;
            }
            console.log(formatOutput(daemonResult.data, options.pretty));
            return;
        }
        const format = await detectFormat(file);
        if (!validateRef(ref)) {
            throw new Error(`Invalid reference: ${ref}`);
        }
        const parsed = parseRef(ref);
        if (parsed.table === undefined || parsed.row === undefined || parsed.cell === undefined) {
            throw new Error(`Not a cell reference: ${ref}`);
        }
        if (format === 'hwp') {
            await editHwp(file, [{ type: 'setTableCell', ref, text }]);
        }
        else {
            await editHwpx(file, [{ type: 'setTableCell', ref, text }]);
        }
        console.log(formatOutput({ ref, text, success: true }, options.pretty));
    }
    catch (e) {
        const hint = await getRefHint(file, ref).catch(() => undefined);
        handleError(e, { context: { ref, file }, hint });
    }
}
export async function tableAddCommand(file, ref, rows, cols, options) {
    try {
        const position = (options.position ?? 'end');
        if (!['before', 'after', 'end'].includes(position)) {
            throw new Error(`Invalid position: ${position}. Must be 'before', 'after', or 'end'`);
        }
        if (!validateRef(ref)) {
            throw new Error(`Invalid reference: ${ref}`);
        }
        const parsedRef = parseRef(ref);
        if ((position === 'before' || position === 'after') && parsedRef.paragraph === undefined) {
            throw new Error(`table add with position '${position}' requires a paragraph reference (e.g., s0.p0)`);
        }
        const data = options.data ? JSON.parse(options.data) : undefined;
        if (data) {
            if (!Array.isArray(data) || !data.every((r) => Array.isArray(r))) {
                throw new Error('--data must be a JSON array of arrays');
            }
        }
        const daemonResult = await dispatchViaDaemon(file, 'table-add', { ref, rows, cols, data, position });
        if (daemonResult !== null) {
            if (!daemonResult.success) {
                const errorOptions = daemonResult.context && typeof daemonResult.context === 'object'
                    ? { context: daemonResult.context, hint: daemonResult.hint }
                    : daemonResult.hint
                        ? { hint: daemonResult.hint }
                        : undefined;
                handleError(new Error(daemonResult.error), errorOptions);
                return;
            }
            console.log(formatOutput(daemonResult.data, options.pretty));
            return;
        }
        const format = await detectFormat(file);
        const sections = format === 'hwp' ? (await loadHwp(file)).sections : await loadHwpxSections(file);
        const tableCount = sections[parsedRef.section]?.tables.length ?? 0;
        if (format === 'hwp') {
            await editHwp(file, [{ type: 'addTable', ref, rows, cols, data, position }]);
        }
        else {
            await editHwpx(file, [{ type: 'addTable', ref, rows, cols, data, position }]);
        }
        const newRef = position === 'end'
            ? buildRef({ section: parsedRef.section, table: tableCount })
            : ref;
        console.log(formatOutput({ ref: newRef, rows, cols, success: true }, options.pretty));
    }
    catch (e) {
        const hint = await getRefHint(file, ref).catch(() => undefined);
        handleError(e, { context: { ref, file }, hint });
    }
}
async function loadHwpxSections(file) {
    const archive = await loadHwpx(file);
    return parseSections(archive);
}
//# sourceMappingURL=table.js.map