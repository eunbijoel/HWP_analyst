#!/usr/bin/env bun
import { Command } from 'commander';
import { convertCommand } from '../cli/commands/convert.js';
import { createCommand } from '../cli/commands/create.js';
import { editFormatCommand } from '../cli/commands/edit-format.js';
import { editTextCommand } from '../cli/commands/edit-text.js';
import { findCommand } from '../cli/commands/find.js';
import { imageExtractCommand, imageInsertCommand, imageListCommand, imageReplaceCommand } from '../cli/commands/image.js';
import { paragraphAddCommand } from '../cli/commands/paragraph.js';
import { readCommand } from '../cli/commands/read.js';
import { tableAddCommand, tableEditCommand, tableListCommand, tableReadCommand } from '../cli/commands/table.js';
import { textCommand } from '../cli/commands/text.js';
import { validateCommand } from '../cli/commands/validate.js';
const program = new Command();
program.name('hwpilot').description('HWP Copilot').version('0.1.0');
// hwpilot read <file> [ref]
program
    .command('read <file> [ref]')
    .description('Read document structure or a specific element')
    .option('--pretty', 'Pretty-print JSON output')
    .option('--offset <number>', 'Skip first N paragraphs (0-indexed)')
    .option('--limit <number>', 'Return at most N paragraphs')
    .action(async (file, ref, options) => {
    await readCommand(file, ref, {
        pretty: options.pretty,
        offset: options.offset ? Number(options.offset) : undefined,
        limit: options.limit ? Number(options.limit) : undefined,
    });
});
// hwpilot text <file> [ref]
program
    .command('text <file> [ref]')
    .description('Extract text from document or a specific element')
    .option('--pretty', 'Pretty-print JSON output')
    .option('--offset <number>', 'Skip first N paragraphs (0-indexed)')
    .option('--limit <number>', 'Return at most N paragraphs')
    .action(async (file, ref, options) => {
    await textCommand(file, ref, {
        pretty: options.pretty,
        offset: options.offset ? Number(options.offset) : undefined,
        limit: options.limit ? Number(options.limit) : undefined,
    });
});
// hwpilot find <file> <query>
program
    .command('find <file> <query>')
    .description('Search text in document and return matching refs')
    .option('--json', 'Output results as JSON')
    .action(async (file, query, options) => {
    await findCommand(file, query, options);
});
// hwpilot edit
const editCmd = program.command('edit').description('Edit document content');
// hwpilot edit text <file> <ref> <text>
editCmd
    .command('text <file> <ref> <text>')
    .description('Edit text at a specific reference')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, ref, text, options) => {
    await editTextCommand(file, ref, text, options);
});
// hwpilot edit format <file> <ref>
editCmd
    .command('format <file> <ref>')
    .description('Edit character formatting at a specific reference')
    .option('--bold', 'Apply bold')
    .option('--no-bold', 'Remove bold')
    .option('--italic', 'Apply italic')
    .option('--no-italic', 'Remove italic')
    .option('--underline', 'Apply underline')
    .option('--no-underline', 'Remove underline')
    .option('--font <name>', 'Set font name')
    .option('--size <pt>', 'Set font size in points')
    .option('--color <hex>', 'Set text color (hex, e.g. #FF0000)')
    .option('--start <n>', 'Start character offset for inline formatting (requires --end)', parseInt)
    .option('--end <n>', 'End character offset for inline formatting (requires --start)', parseInt)
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, ref, options) => {
    await editFormatCommand(file, ref, {
        bold: options.bold,
        italic: options.italic,
        underline: options.underline,
        font: options.font,
        size: options.size ? Number(options.size) : undefined,
        color: options.color,
        start: options.start,
        end: options.end,
        pretty: options.pretty,
    });
});
// hwpilot table
const tableCmd = program.command('table').description('Work with tables');
// hwpilot table read <file> <ref>
tableCmd
    .command('read <file> <ref>')
    .description('Read table structure')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, ref, options) => {
    await tableReadCommand(file, ref, options);
});
// hwpilot table edit <file> <ref> <text>
tableCmd
    .command('edit <file> <ref> <text>')
    .description('Edit text in a table cell')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, ref, text, options) => {
    await tableEditCommand(file, ref, text, options);
});
// hwpilot table list <file>
tableCmd
    .command('list <file>')
    .description('List all tables in the document')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, options) => {
    await tableListCommand(file, options);
});
// hwpilot table add <file> <ref> <rows> <cols>
tableCmd
    .command('add <file> <ref> <rows> <cols>')
    .description('Add a new table to the document')
    .option('--position <pos>', 'Insertion position: before|after|end', 'end')
    .option('--data <json>', 'Cell data as JSON array of arrays')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, ref, rows, cols, options) => {
    await tableAddCommand(file, ref, Number(rows), Number(cols), options);
});
// hwpilot paragraph
const paragraphCmd = program.command('paragraph').description('Paragraph operations');
// hwpilot paragraph add <file> <ref> <text>
paragraphCmd
    .command('add <file> <ref> <text>')
    .description('Add a new paragraph')
    .option('--position <pos>', 'Insertion position: before|after|end', 'end')
    .option('--heading <level>', 'Set heading level (1-7)', (val) => parseInt(val, 10))
    .option('--style <name>', 'Set paragraph style by name or ID')
    .option('--bold', 'Bold text')
    .option('--italic', 'Italic text')
    .option('--underline', 'Underline text')
    .option('--font <name>', 'Font name')
    .option('--size <n>', 'Font size in points', parseFloat)
    .option('--color <hex>', 'Text color (hex)')
    .action(async (file, ref, text, options) => {
    await paragraphAddCommand(file, ref, text, options);
});
// hwpilot image
const imageCmd = program.command('image').description('Work with images');
// hwpilot image list <file>
imageCmd
    .command('list <file>')
    .description('List all images in the document')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, options) => {
    await imageListCommand(file, options);
});
// hwpilot image extract <file> <ref> <output>
imageCmd
    .command('extract <file> <ref> <output>')
    .description('Extract an image to a file')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, ref, output, options) => {
    await imageExtractCommand(file, ref, output, options);
});
// hwpilot image insert <file> <path>
imageCmd
    .command('insert <file> <path>')
    .description('Insert an image into the document')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, path, options) => {
    await imageInsertCommand(file, path, options);
});
// hwpilot image replace <file> <ref> <path>
imageCmd
    .command('replace <file> <ref> <path>')
    .description('Replace an existing image')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, ref, path, options) => {
    await imageReplaceCommand(file, ref, path, options);
});
// hwpilot create <file>
program
    .command('create <file>')
    .description('Create a new blank document')
    .option('--font <name>', 'Set default font name', '맑은 고딕')
    .option('--size <pt>', 'Set default font size', '10')
    .option('--pretty', 'Pretty-print JSON output')
    .action(async (file, options) => {
    await createCommand(file, options);
});
// hwpilot convert <input> <output>
program
    .command('convert <input> <output>')
    .description('Convert between HWP/HWPX and Markdown formats')
    .option('--pretty', 'Pretty-print JSON output')
    .option('--force', 'Overwrite existing output file')
    .option('--images-dir <path>', 'Directory for extracted images (HWP→MD direction)')
    .action(async (input, output, options) => {
    await convertCommand(input, output, {
        pretty: options.pretty,
        force: options.force,
        imagesDir: options.imagesDir,
    });
});
// hwpilot validate <file>
program
    .command('validate <file>')
    .description('Validate file integrity (uses Hancom Viewer for HWP only when HWPILOT_VIEWER=1)')
    .option('--pretty', 'Pretty-print JSON output')
    .option('--viewer', 'Deprecated: set HWPILOT_VIEWER=1 to enable viewer validation')
    .action(async (file, options) => {
    await validateCommand(file, options);
});
program.parse(process.argv);
//# sourceMappingURL=main.js.map