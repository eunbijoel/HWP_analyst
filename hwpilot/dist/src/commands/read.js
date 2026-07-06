import { dispatchViaDaemon } from '../daemon/dispatch.js';
import { loadHwp } from '../formats/hwp/reader.js';
import { parseHeader } from '../formats/hwpx/header-parser.js';
import { loadHwpx } from '../formats/hwpx/loader.js';
import { parseSections } from '../formats/hwpx/section-parser.js';
import { resolveRef } from '../shared/document-ops.js';
import { handleError } from '../shared/error-handler.js';
import { detectFormat } from '../shared/format-detector.js';
import { formatOutput } from '../shared/output.js';
export async function readCommand(file, ref, options) {
    try {
        const daemonResult = await dispatchViaDaemon(file, 'read', {
            ref,
            offset: options.offset,
            limit: options.limit,
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
        const doc = format === 'hwp' ? await loadHwp(file) : await loadHwpxDocument(file);
        if (ref) {
            const result = resolveRef(ref, doc.sections);
            // Enrich paragraph results with heading level and style name
            if (result && typeof result === 'object' && 'ref' in result && 'runs' in result) {
                const enriched = enrichParagraph(result, doc.header);
                console.log(formatOutput(enriched, options.pretty));
            }
            else {
                console.log(formatOutput(result, options.pretty));
            }
            return;
        }
        const hasPagination = options.offset !== undefined || options.limit !== undefined;
        const offset = options.offset ?? 0;
        const limit = options.limit ?? Number.POSITIVE_INFINITY;
        const output = {
            format: doc.format,
            sections: doc.sections.map((section, index) => {
                const paragraphs = hasPagination ? section.paragraphs.slice(offset, offset + limit) : section.paragraphs;
                return {
                    index,
                    ...(hasPagination && {
                        totalParagraphs: section.paragraphs.length,
                        totalTables: section.tables.length,
                        totalImages: section.images.length,
                        totalTextBoxes: section.textBoxes.length,
                    }),
                    paragraphs: paragraphs.map((p) => enrichParagraph(p, doc.header)),
                    tables: section.tables,
                    images: section.images,
                    textBoxes: section.textBoxes,
                };
            }),
            header: doc.header,
        };
        console.log(formatOutput(output, options.pretty));
    }
    catch (e) {
        handleError(e);
    }
}
function enrichParagraph(para, header) {
    const enriched = { ...para };
    // Resolve style name and heading level from styleRef
    // Style name is the authoritative source for heading level (e.g. '개요 1' = heading 1)
    // because Hancom templates may share a single paraShapeRef across multiple heading styles
    const style = header.styles.find((s) => s.id === para.styleRef);
    if (style) {
        enriched.styleName = style.name;
        const level = headingLevelFromStyleName(style.name);
        if (level !== undefined) {
            enriched.headingLevel = level;
        }
    }
    // Fall back to paraShape heading bits if style didn't provide heading level
    if (enriched.headingLevel === undefined) {
        const paraShape = header.paraShapes.find((ps) => ps.id === para.paraShapeRef);
        if (paraShape?.headingLevel && paraShape.headingLevel > 0) {
            enriched.headingLevel = paraShape.headingLevel;
        }
    }
    return enriched;
}
const HEADING_STYLE_RE = /^(?:개요|Outline|Heading)\s+(\d+)$/i;
function headingLevelFromStyleName(name) {
    const match = HEADING_STYLE_RE.exec(name);
    if (!match)
        return undefined;
    const level = Number(match[1]);
    return level >= 1 && level <= 7 ? level : undefined;
}
async function loadHwpxDocument(file) {
    const archive = await loadHwpx(file);
    const header = parseHeader(await archive.getHeaderXml());
    const sections = await parseSections(archive);
    return {
        format: 'hwpx',
        sections,
        header,
    };
}
//# sourceMappingURL=read.js.map