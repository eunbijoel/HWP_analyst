export function createFlushScheduler(flushFn, debounceMs) {
    let timer = null;
    async function flush() {
        if (timer) {
            clearTimeout(timer);
            timer = null;
        }
        await flushFn();
    }
    return {
        schedule() {
            if (timer) {
                clearTimeout(timer);
            }
            timer = setTimeout(() => {
                timer = null;
                flushFn().catch((err) => process.stderr.write(`flush error: ${String(err)}\n`));
            }, debounceMs);
        },
        cancel() {
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
        },
        async flushNow() {
            await flush();
        },
    };
}
//# sourceMappingURL=flush.js.map