import { sendRequest } from '../daemon/client.js';
import { ensureDaemon } from '../daemon/launcher.js';
import { deleteStateFile } from '../daemon/state-file.js';
export async function dispatchViaDaemon(filePath, command, args) {
    if (process.env.HWPILOT_NO_DAEMON === '1') {
        return null;
    }
    const { port, token } = await ensureDaemon(filePath);
    try {
        return await sendRequest(port, token, { command, args });
    }
    catch (error) {
        if (!isConnectionRefused(error)) {
            throw error;
        }
        deleteStateFile(filePath);
        const { port: retryPort, token: retryToken } = await ensureDaemon(filePath);
        return sendRequest(retryPort, retryToken, { command, args });
    }
}
function isConnectionRefused(error) {
    if (!(error instanceof Error)) {
        return false;
    }
    return error.message.includes('ECONNREFUSED');
}
//# sourceMappingURL=dispatch.js.map