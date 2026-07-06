import { dispatchViaDaemon } from '../../daemon/dispatch.js';
import { loadHwp } from '../../formats/hwp/reader.js';
import { loadHwpx } from '../../formats/hwpx/loader.js';
import { parseSections } from '../../formats/hwpx/section-parser.js';
import { findInSections } from '../../sdk/document-ops.js';
import { handleError } from '../../cli/error-handler.js';
import { detectFormat } from '../../shared/format-detector.js';
export async function findCommand(file, query, options) {
    try {
        const daemonResult = await dispatchViaDaemon(file, 'find', { query });
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
            const matches = getMatches(daemonResult.data);
            if (options.json) {
                console.log(JSON.stringify({ matches }));
                return;
            }
            for (const match of matches) {
                console.log(`${match.ref}: ${match.text}`);
            }
            return;
        }
        const format = await detectFormat(file);
        const sections = format === 'hwp' ? (await loadHwp(file)).sections : await loadHwpxSections(file);
        const matches = findInSections(sections, query);
        if (options.json) {
            console.log(JSON.stringify({ matches }));
            return;
        }
        for (const match of matches) {
            console.log(`${match.ref}: ${match.text}`);
        }
    }
    catch (e) {
        handleError(e, { context: { file, query } });
    }
}
async function loadHwpxSections(file) {
    const archive = await loadHwpx(file);
    return parseSections(archive);
}
function getMatches(data) {
    if (typeof data !== 'object' || data === null || !('matches' in data)) {
        return [];
    }
    const matches = data.matches;
    if (!Array.isArray(matches)) {
        return [];
    }
    return matches.filter((match) => typeof match === 'object' &&
        match !== null &&
        'ref' in match &&
        typeof match.ref === 'string' &&
        'text' in match &&
        typeof match.text === 'string');
}
//# sourceMappingURL=find.js.map