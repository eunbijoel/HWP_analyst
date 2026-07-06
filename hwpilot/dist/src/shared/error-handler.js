export function handleError(error, options) {
    const message = error instanceof Error ? error.message : String(error);
    const output = { error: message };
    if (options?.context)
        output.context = options.context;
    if (options?.hint)
        output.hint = options.hint;
    console.error(JSON.stringify(output));
    process.exit(1);
}
//# sourceMappingURL=error-handler.js.map