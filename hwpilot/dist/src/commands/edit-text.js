import { dispatchViaDaemon } from '../daemon/dispatch.js';
import { editHwp } from '../formats/hwp/writer.js';
import { editHwpx } from '../formats/hwpx/writer.js';
import { handleError } from '../shared/error-handler.js';
import { detectFormat } from '../shared/format-detector.js';
import { formatOutput } from '../shared/output.js';
import { getRefHint } from '../shared/ref-hints.js';
import { parseRef, validateRef } from '../shared/refs.js';
export async function editTextCommand(file, ref, text, options) {
    try {
        const daemonResult = await dispatchViaDaemon(file, 'edit-text', {
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
        const operation = parsed.table !== undefined ? { type: 'setTableCell', ref, text } : { type: 'setText', ref, text };
        if (format === 'hwp') {
            await editHwp(file, [operation]);
        }
        else {
            await editHwpx(file, [operation]);
        }
        console.log(formatOutput({ ref, text, success: true }, options.pretty));
    }
    catch (e) {
        const hint = await getRefHint(file, ref).catch(() => undefined);
        handleError(e, { context: { ref, file }, hint });
    }
}
//# sourceMappingURL=edit-text.js.map