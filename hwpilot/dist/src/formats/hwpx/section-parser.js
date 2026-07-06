import { XMLParser } from 'fast-xml-parser';
import { buildRef } from '../../shared/refs.js';
const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: '',
    parseAttributeValue: true,
    trimValues: false,
    isArray: (name) => ['hp:p', 'hp:run', 'hp:tbl', 'hp:tr', 'hp:tc', 'hp:pic', 'hp:rect'].includes(name),
});
export function parseSection(xml, sectionIndex) {
    const parsed = parser.parse(xml);
    const sec = (parsed['hs:sec'] ?? {});
    const rawParagraphs = asArray(sec['hp:p']);
    const rawTables = collectFlowChildren(sec, rawParagraphs, 'hp:tbl');
    const rawPics = collectFlowChildren(sec, rawParagraphs, 'hp:pic');
    const rawRects = collectFlowChildren(sec, rawParagraphs, 'hp:rect');
    const paragraphs = rawParagraphs.map((paragraph, paragraphIndex) => parseParagraph(paragraph, {
        section: sectionIndex,
        paragraph: paragraphIndex,
    }));
    const tables = rawTables.map((table, tableIndex) => parseTable(table, sectionIndex, tableIndex));
    const images = rawPics.map((pic, imageIndex) => parseImage(pic, sectionIndex, imageIndex));
    const textBoxes = rawRects
        .map((rect, textBoxIndex) => parseTextBox(rect, sectionIndex, textBoxIndex))
        .filter((textBox) => textBox !== null);
    return {
        paragraphs,
        tables,
        images,
        textBoxes,
    };
}
/**
 * Collect element instances of `tag` that appear directly in the section flow,
 * in document order. The flow is: section-level direct children, plus any
 * children nested inside `hp:p` (paragraph-direct) or `hp:p > hp:run`
 * (run-direct) wrappers. Real-world HWPX produced by Hancom typically wraps
 * tables/images inside paragraph runs even when conceptually they are
 * top-level objects, so a section-only collector misses them entirely.
 *
 * Traversal is intentionally narrow: we do not recurse into `hp:tc` (table
 * cells), `hp:drawText` (text box bodies), or other subtrees, otherwise nested
 * tables-in-cells would surface as top-level tables and break ref semantics.
 *
 * Ordering limitation: within a single paragraph, paragraph-direct nodes are
 * emitted before run-direct nodes. True interleaved document order between
 * these two would require `preserveOrder: true` on the upstream XML parser,
 * which groups siblings by tag in the current mode. Not observed in
 * real-world HWPX (paragraphs contain either paragraph-direct or run-direct
 * instances of a given tag, not both); revisit if a fixture emerges.
 */
function collectFlowChildren(sec, paragraphs, tag) {
    const sectionDirect = asArray(sec[tag]);
    const fromParagraphs = paragraphs.flatMap((paragraph) => {
        const paragraphDirect = asArray(paragraph[tag]);
        const runDirect = asArray(paragraph['hp:run']).flatMap((run) => asArray(run[tag]));
        return [...paragraphDirect, ...runDirect];
    });
    return [...sectionDirect, ...fromParagraphs];
}
export async function parseSections(archive) {
    const sectionCount = archive.getSectionCount();
    const sections = [];
    for (let i = 0; i < sectionCount; i++) {
        const xml = await archive.getSectionXml(i);
        sections.push(parseSection(xml, i));
    }
    return sections;
}
function parseParagraph(paragraph, refParts) {
    const runs = asArray(paragraph['hp:run']).map(parseRun);
    return {
        ref: buildRef(refParts),
        runs,
        paraShapeRef: asNumber(paragraph['hp:paraPrIDRef'], 0),
        styleRef: asNumber(paragraph['hp:styleIDRef'], 0),
    };
}
function parseTextBox(rect, sectionIndex, textBoxIndex) {
    const drawText = rect['hp:drawText'];
    if (!drawText || typeof drawText !== 'object') {
        return null;
    }
    const subList = drawText['hp:subList'];
    if (!subList || typeof subList !== 'object') {
        return null;
    }
    const paragraphs = asArray(subList['hp:p']).map((paragraph, paragraphIndex) => parseParagraph(paragraph, {
        section: sectionIndex,
        textBox: textBoxIndex,
        textBoxParagraph: paragraphIndex,
    }));
    return {
        ref: buildRef({ section: sectionIndex, textBox: textBoxIndex }),
        paragraphs,
    };
}
function parseRun(run) {
    return {
        text: extractText(run['hp:t']),
        charShapeRef: asNumber(run['hp:charPrIDRef'], 0),
    };
}
function parseTable(table, sectionIndex, tableIndex) {
    const rows = asArray(table['hp:tr']).map((row, rowIndex) => parseTableRow(row, sectionIndex, tableIndex, rowIndex));
    return {
        ref: buildRef({ section: sectionIndex, table: tableIndex }),
        rows,
    };
}
function parseTableRow(row, sectionIndex, tableIndex, rowIndex) {
    const cells = asArray(row['hp:tc']).map((cell, cellIndex) => parseTableCell(cell, sectionIndex, tableIndex, rowIndex, cellIndex));
    return { cells };
}
function parseTableCell(cell, sectionIndex, tableIndex, rowIndex, cellIndex) {
    const span = (cell['hp:cellSpan'] ?? {});
    const rawParagraphs = getCellParagraphs(cell);
    const paragraphs = rawParagraphs.map((paragraph, paragraphIndex) => parseParagraph(paragraph, {
        section: sectionIndex,
        table: tableIndex,
        row: rowIndex,
        cell: cellIndex,
        cellParagraph: paragraphIndex,
    }));
    return {
        ref: buildRef({ section: sectionIndex, table: tableIndex, row: rowIndex, cell: cellIndex }),
        paragraphs,
        colSpan: asNumber(span['hp:colSpan'], 1),
        rowSpan: asNumber(span['hp:rowSpan'], 1),
    };
}
/**
 * Real-world HWPX wraps cell paragraphs in `<hp:subList>` (matching the spec
 * for table-cell text containers), but minimal/synthetic HWPX often nests
 * paragraphs directly under `<hp:tc>`. Accept both shapes.
 */
function getCellParagraphs(cell) {
    const direct = asArray(cell['hp:p']);
    if (direct.length > 0) {
        return direct;
    }
    const subList = cell['hp:subList'];
    if (subList && typeof subList === 'object') {
        return asArray(subList['hp:p']);
    }
    return [];
}
function parseImage(pic, sectionIndex, imageIndex) {
    const width = asNumber(pic['hp:width'], 0);
    const height = asNumber(pic['hp:height'], 0);
    const format = asString(pic['hp:format']);
    const directPath = asString(pic['hp:binDataPath']);
    const binDataPath = directPath || deriveBinDataPath(pic);
    return {
        ref: buildRef({ section: sectionIndex, image: imageIndex }),
        binDataPath,
        width,
        height,
        format,
    };
}
function deriveBinDataPath(pic) {
    const idRef = asString(pic['hp:binDataIDRef']);
    if (idRef) {
        return `BinData/${idRef}`;
    }
    const id = asString(pic['hp:id']);
    if (id) {
        return `BinData/${id}`;
    }
    return '';
}
function extractText(value) {
    if (typeof value === 'string') {
        return value;
    }
    if (typeof value === 'number') {
        return String(value);
    }
    if (value && typeof value === 'object') {
        const text = value['#text'];
        return typeof text === 'string' ? text : typeof text === 'number' ? String(text) : '';
    }
    return '';
}
function asArray(value) {
    if (Array.isArray(value)) {
        return value;
    }
    if (value === undefined || value === null) {
        return [];
    }
    return [value];
}
function asNumber(value, fallback) {
    return typeof value === 'number' ? value : fallback;
}
function asString(value) {
    return typeof value === 'string' ? value : '';
}
//# sourceMappingURL=section-parser.js.map