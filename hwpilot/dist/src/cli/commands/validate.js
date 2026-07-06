import { validateHwp } from '../../formats/hwp/validator.js';
import { handleError } from '../../cli/error-handler.js';
import { formatOutput } from '../../cli/output.js';
import { checkViewerCorruption } from '../../shared/viewer.js';
const VIEWER_ENV_FLAG = 'HWPILOT_VIEWER';
export async function validateCommand(file, options) {
    try {
        const result = await validateHwp(file);
        if (shouldRunViewerCheck(result)) {
            const viewerCheck = await runViewerCheck(file);
            result.checks.push(viewerCheck);
            result.valid = result.checks.every((c) => c.status !== 'fail');
        }
        process.stdout.write(formatOutput(result, options.pretty) + '\n');
        if (!result.valid) {
            process.exit(1);
        }
    }
    catch (e) {
        handleError(e);
    }
}
async function runViewerCheck(filePath) {
    const result = await checkViewerCorruption(filePath);
    if (result.skipped) {
        return { name: 'viewer', status: 'skip', message: 'Hancom Office HWP Viewer not found' };
    }
    if (result.corrupted) {
        return {
            name: 'viewer',
            status: 'fail',
            message: 'Hancom Office HWP Viewer detected corruption',
            details: result.alert ? { alert: result.alert } : undefined,
        };
    }
    return { name: 'viewer', status: 'pass' };
}
function shouldRunViewerCheck(result) {
    return isViewerCheckEnabled() && result.format === 'hwp' && !result.checks.some((check) => check.status === 'fail');
}
function isViewerCheckEnabled() {
    return process.env[VIEWER_ENV_FLAG] === '1';
}
//# sourceMappingURL=validate.js.map