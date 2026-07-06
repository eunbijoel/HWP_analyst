import { XMLBuilder, XMLParser } from 'fast-xml-parser';
import { parseRef } from '../../shared/refs.js';
import { PATHS, sectionPath } from './paths.js';
const parser = new XMLParser({
    preserveOrder: true,
    ignoreAttributes: false,
    attributeNamePrefix: '',
    parseAttributeValue: false,
    trimValues: false,
    processEntities: false,
});
const builder = new XMLBuilder({
    preserveOrder: true,
    ignoreAttributes: false,
    attributeNamePrefix: '',
    format: false,
    suppressEmptyNode: false,
    processEntities: false,
});
/**
 * Escape XML special characters in user-supplied text.
 * Must escape & first, then <, >, ", ' to avoid double-escaping.
 */
export function escapeXml(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&apos;');
}
export async function mutateHwpxZip(zip, archive, operations) {
    if (operations.length === 0)
        return;
    const sectionOps = groupOperationsBySection(operations);
    let headerTree = null;
    let headerChanged = false;
    for (const [sectionIndex, ops] of sectionOps.entries()) {
        const sectionXml = await archive.getSectionXml(sectionIndex);
        const sectionTree = parseXml(sectionXml);
        for (const { op, ref } of ops) {
            if (op.type === 'addTable') {
                addTableToSection(sectionTree, op.rows, op.cols, op.data, op.position, ref);
                continue;
            }
            if (op.type === 'setText') {
                setTextInRef(sectionTree, ref, op.text);
                continue;
            }
            if (op.type === 'setTableCell') {
                setTextInTableCell(sectionTree, ref, op.text);
                continue;
            }
            if (op.type === 'addParagraph') {
                if (!headerTree) {
                    headerTree = parseXml(await archive.getHeaderXml());
                }
                addParagraphToSection(sectionTree, headerTree, op.text, op.position, ref, op.format, op.heading, op.style);
                if (op.format || op.heading !== undefined || op.style !== undefined)
                    headerChanged = true;
                continue;
            }
            if (op.type === 'setFormat') {
                if (!headerTree) {
                    headerTree = parseXml(await archive.getHeaderXml());
                }
                if (op.start !== undefined && op.end !== undefined) {
                    applyInlineFormat(sectionTree, headerTree, ref, op.format, op.start, op.end);
                    headerChanged = true;
                    continue;
                }
                const runNodes = findRunNodesForRef(sectionTree, ref);
                if (runNodes.length === 0) {
                    throw new Error(`Run not found for reference: ${op.ref}`);
                }
                const newCharPrId = appendFormattedCharPr(headerTree, runNodes[0], op.format);
                for (const runNode of runNodes) {
                    setAttr(runNode, 'charPrIDRef', String(newCharPrId), 'hp:charPrIDRef');
                }
                headerChanged = true;
                continue;
            }
            throw new Error(`Unsupported operation: ${op.type}`);
        }
        zip.file(sectionPath(sectionIndex), buildXml(sectionTree));
    }
    if (headerChanged && headerTree) {
        zip.file(PATHS.HEADER_XML, buildXml(headerTree));
    }
}
function groupOperationsBySection(operations) {
    const grouped = new Map();
    for (const op of operations) {
        if (op.type === 'addTable') {
            const ref = parseRef(op.ref);
            const list = grouped.get(ref.section) ?? [];
            list.push({ op, ref });
            grouped.set(ref.section, list);
            continue;
        }
        const ref = parseRef(op.ref);
        const list = grouped.get(ref.section) ?? [];
        list.push({ op, ref });
        grouped.set(ref.section, list);
    }
    return grouped;
}
function setTextInRef(sectionTree, ref, text) {
    if (ref.table !== undefined) {
        setTextInTableCell(sectionTree, ref, text);
        return;
    }
    if (ref.textBox !== undefined) {
        if (ref.textBoxParagraph === undefined) {
            throw new Error(`setText requires a text box paragraph reference: s${ref.section}.tb${ref.textBox}`);
        }
        const paragraphNode = getTextBoxParagraphNode(sectionTree, ref.textBox, ref.textBoxParagraph);
        setParagraphText(paragraphNode, text);
        return;
    }
    if (ref.paragraph === undefined) {
        throw new Error(`setText requires a paragraph or cell reference: s${ref.section}`);
    }
    const paragraphNode = getSectionParagraphNode(sectionTree, ref.paragraph);
    setParagraphText(paragraphNode, text);
}
function setTextInTableCell(sectionTree, ref, text) {
    if (ref.table === undefined || ref.row === undefined || ref.cell === undefined) {
        throw new Error(`setTableCell requires a cell reference: s${ref.section}`);
    }
    const cellNode = getTableCellNode(sectionTree, ref.table, ref.row, ref.cell);
    const paragraphs = getCellParagraphNodes(cellNode);
    if (paragraphs.length === 0) {
        throw new Error(`Cell has no paragraph: s${ref.section}.t${ref.table}.r${ref.row}.c${ref.cell}`);
    }
    paragraphs.forEach((paragraph, index) => {
        setParagraphText(paragraph, index === 0 ? text : '');
    });
}
function addTableToSection(sectionTree, rows, cols, data, position, ref) {
    const sectionRoot = getSectionRootNode(sectionTree);
    const elementName = getElementName(sectionRoot);
    const sectionChildren = getElementChildren(sectionRoot, elementName);
    const tableRows = Array.from({ length: rows }, (_, ri) => {
        const rowCells = Array.from({ length: cols }, (_, ci) => {
            const cellText = data?.[ri]?.[ci] ?? '';
            const cellAddr = {
                'hp:cellAddr': [],
                ':@': {
                    'hp:colAddr': String(ci),
                    'hp:rowAddr': String(ri),
                },
            };
            const cellSpan = {
                'hp:cellSpan': [],
                ':@': {
                    'hp:colSpan': '1',
                    'hp:rowSpan': '1',
                },
            };
            const textNode = {
                'hp:t': [{ '#text': escapeXml(cellText) }],
            };
            const runNode = {
                'hp:run': [textNode],
                ':@': {
                    'hp:charPrIDRef': '0',
                },
            };
            const paragraphNode = {
                'hp:p': [runNode],
                ':@': {
                    'hp:id': '0',
                    'hp:paraPrIDRef': '0',
                    'hp:styleIDRef': '0',
                },
            };
            return {
                'hp:tc': [cellAddr, cellSpan, paragraphNode],
            };
        });
        return {
            'hp:tr': rowCells,
        };
    });
    const tableNode = {
        'hp:tbl': tableRows,
    };
    if (position === 'end') {
        sectionChildren.push(tableNode);
        return;
    }
    if (ref.paragraph === undefined) {
        throw new Error(`addTable with position '${position}' requires a paragraph reference`);
    }
    let paragraphCount = -1;
    let targetIndex = -1;
    for (let i = 0; i < sectionChildren.length; i++) {
        if (hasElement(sectionChildren[i], 'hp:p')) {
            paragraphCount++;
            if (paragraphCount === ref.paragraph) {
                targetIndex = i;
                break;
            }
        }
    }
    if (targetIndex === -1) {
        throw new Error(`Paragraph not found: index ${ref.paragraph}`);
    }
    const insertIndex = position === 'before' ? targetIndex : targetIndex + 1;
    sectionChildren.splice(insertIndex, 0, tableNode);
}
function addParagraphToSection(sectionTree, headerTree, text, position, ref, format, heading, style) {
    if (heading !== undefined && style !== undefined) {
        throw new Error('Cannot specify both heading and style');
    }
    const sectionRoot = getSectionRootNode(sectionTree);
    const elementName = getElementName(sectionRoot);
    const sectionChildren = getElementChildren(sectionRoot, elementName);
    const textNode = { 'hp:t': [{ '#text': escapeXml(text) }] };
    const runNode = {
        'hp:run': [textNode],
        ':@': { 'hp:charPrIDRef': '0' },
    };
    const paraNode = {
        'hp:p': [runNode],
        ':@': { 'hp:id': '0', 'hp:paraPrIDRef': '0', 'hp:styleIDRef': '0' },
    };
    if (heading !== undefined) {
        const styleNode = lookupStyle(headerTree, { heading });
        setAttr(paraNode, 'styleIDRef', getAttr(styleNode, 'id'), 'hp:styleIDRef');
        setAttr(paraNode, 'paraPrIDRef', getAttr(styleNode, 'paraPrIDRef'), 'hp:paraPrIDRef');
        const charPrIDRef = getAttr(styleNode, 'charPrIDRef');
        if (charPrIDRef) {
            setAttr(runNode, 'charPrIDRef', charPrIDRef, 'hp:charPrIDRef');
        }
    }
    else if (style !== undefined) {
        const styleNode = lookupStyle(headerTree, { style });
        setAttr(paraNode, 'styleIDRef', getAttr(styleNode, 'id'), 'hp:styleIDRef');
        setAttr(paraNode, 'paraPrIDRef', getAttr(styleNode, 'paraPrIDRef'), 'hp:paraPrIDRef');
        const charPrIDRef = getAttr(styleNode, 'charPrIDRef');
        if (charPrIDRef) {
            setAttr(runNode, 'charPrIDRef', charPrIDRef, 'hp:charPrIDRef');
        }
    }
    if (format) {
        const newCharPrId = appendFormattedCharPr(headerTree, runNode, format);
        setAttr(runNode, 'charPrIDRef', String(newCharPrId), 'hp:charPrIDRef');
    }
    if (position === 'end') {
        sectionChildren.push(paraNode);
        return;
    }
    if (ref.paragraph === undefined) {
        throw new Error(`addParagraph with position '${position}' requires a paragraph reference`);
    }
    let paragraphCount = -1;
    let targetIndex = -1;
    for (let i = 0; i < sectionChildren.length; i++) {
        if (hasElement(sectionChildren[i], 'hp:p')) {
            paragraphCount++;
            if (paragraphCount === ref.paragraph) {
                targetIndex = i;
                break;
            }
        }
    }
    if (targetIndex === -1) {
        throw new Error(`Paragraph not found: index ${ref.paragraph}`);
    }
    const insertIndex = position === 'before' ? targetIndex : targetIndex + 1;
    sectionChildren.splice(insertIndex, 0, paraNode);
}
function setParagraphText(paragraphNode, text) {
    const runNodes = getRunNodesFromParagraph(paragraphNode);
    if (runNodes.length === 0) {
        throw new Error('Target paragraph has no runs');
    }
    runNodes.forEach((runNode, index) => {
        setRunText(runNode, index === 0 ? text : '');
    });
}
function setRunText(runNode, text) {
    const runChildren = getElementChildren(runNode, 'hp:run');
    const textNode = runChildren.find((child) => hasElement(child, 'hp:t'));
    if (!textNode) {
        runChildren.push({ 'hp:t': [{ '#text': escapeXml(text) }] });
        return;
    }
    textNode['hp:t'] = [{ '#text': escapeXml(text) }];
}
function applyInlineFormat(sectionTree, headerTree, ref, format, start, end) {
    if (ref.paragraph === undefined) {
        throw new Error(`Inline format requires paragraph reference: s${ref.section}`);
    }
    const paragraphNode = getSectionParagraphNode(sectionTree, ref.paragraph);
    const runs = getRunNodesFromParagraph(paragraphNode);
    const totalLength = runs.reduce((sum, run) => sum + getRunText(run).length, 0);
    if (start < 0 || end > totalLength || start >= end) {
        throw new Error(`Offset out of range: start=${start}, end=${end}, length=${totalLength}`);
    }
    const newRuns = [];
    let charOffset = 0;
    for (const run of runs) {
        const text = getRunText(run);
        const runStart = charOffset;
        const runEnd = charOffset + text.length;
        if (runEnd <= start || runStart >= end) {
            newRuns.push(run);
        }
        else if (runStart >= start && runEnd <= end) {
            const formattedRun = deepCloneRun(run);
            const newCharPrId = appendFormattedCharPr(headerTree, formattedRun, format);
            setAttr(formattedRun, 'charPrIDRef', String(newCharPrId), 'hp:charPrIDRef');
            newRuns.push(formattedRun);
        }
        else {
            const overlapStart = Math.max(start, runStart) - runStart;
            const overlapEnd = Math.min(end, runEnd) - runStart;
            if (overlapStart > 0) {
                const beforeRun = deepCloneRun(run);
                setRunText(beforeRun, text.slice(0, overlapStart));
                newRuns.push(beforeRun);
            }
            const middleRun = deepCloneRun(run);
            setRunText(middleRun, text.slice(overlapStart, overlapEnd));
            const newCharPrId = appendFormattedCharPr(headerTree, middleRun, format);
            setAttr(middleRun, 'charPrIDRef', String(newCharPrId), 'hp:charPrIDRef');
            newRuns.push(middleRun);
            if (overlapEnd < text.length) {
                const afterRun = deepCloneRun(run);
                setRunText(afterRun, text.slice(overlapEnd));
                newRuns.push(afterRun);
            }
        }
        charOffset += text.length;
    }
    replaceRunsInParagraph(paragraphNode, newRuns);
}
function getRunText(runNode) {
    const runChildren = getElementChildren(runNode, 'hp:run');
    const textNode = runChildren.find((child) => hasElement(child, 'hp:t'));
    if (!textNode) {
        return '';
    }
    const textChildren = getElementChildren(textNode, 'hp:t');
    const textContent = textChildren.find((child) => Object.hasOwn(child, '#text'));
    return textContent ? String(textContent['#text']) : '';
}
function deepCloneRun(runNode) {
    return deepClone(runNode);
}
function replaceRunsInParagraph(paragraphNode, newRuns) {
    const children = getElementChildren(paragraphNode, 'hp:p');
    const runIndices = [];
    for (let i = 0; i < children.length; i++) {
        if (hasElement(children[i], 'hp:run')) {
            runIndices.push(i);
        }
    }
    for (let i = runIndices.length - 1; i >= 0; i--) {
        children.splice(runIndices[i], 1);
    }
    const insertAt = runIndices[0] ?? children.length;
    children.splice(insertAt, 0, ...newRuns);
}
function findRunNodesForRef(sectionTree, ref) {
    if (ref.table !== undefined) {
        if (ref.row === undefined || ref.cell === undefined) {
            throw new Error(`Table reference must include row/cell: s${ref.section}.t${ref.table}`);
        }
        const cellNode = getTableCellNode(sectionTree, ref.table, ref.row, ref.cell);
        const paragraphs = getCellParagraphNodes(cellNode);
        const targetParagraphs = ref.cellParagraph !== undefined
            ? [paragraphs[ref.cellParagraph]].filter((p) => Boolean(p))
            : paragraphs;
        if (targetParagraphs.length === 0) {
            throw new Error(`Cell paragraph not found: s${ref.section}.t${ref.table}.r${ref.row}.c${ref.cell}`);
        }
        const runs = targetParagraphs.flatMap((paragraph) => getRunNodesFromParagraph(paragraph));
        return selectRunsByIndex(runs, ref);
    }
    if (ref.paragraph === undefined) {
        throw new Error(`setFormat requires paragraph or cell reference: s${ref.section}`);
    }
    const paragraphNode = getSectionParagraphNode(sectionTree, ref.paragraph);
    const runs = getRunNodesFromParagraph(paragraphNode);
    return selectRunsByIndex(runs, ref);
}
function selectRunsByIndex(runs, ref) {
    if (ref.run === undefined) {
        return runs;
    }
    const run = runs[ref.run];
    return run ? [run] : [];
}
function appendFormattedCharPr(headerTree, runNode, format) {
    const charPropertiesNode = getCharPropertiesNode(headerTree);
    const charPrNodes = getChildElements(getElementChildren(charPropertiesNode, 'hh:charProperties'), 'hh:charPr');
    const sourceId = parseIntStrict(getAttr(runNode, 'charPrIDRef') ?? '0', 'charPrIDRef');
    const sourceNode = charPrNodes.find((charPr) => parseIntStrict(getAttr(charPr, 'id') ?? '0', 'charPr id') === sourceId);
    if (!sourceNode) {
        throw new Error(`charPr not found for id: ${sourceId}`);
    }
    const cloned = deepClone(sourceNode);
    const newId = getNextId(charPrNodes, 'id');
    setAttr(cloned, 'id', String(newId), 'hh:id');
    applyFormat(cloned, format, headerTree);
    getElementChildren(charPropertiesNode, 'hh:charProperties').push(cloned);
    return newId;
}
function applyFormat(charPrNode, format, headerTree) {
    if (format.bold !== undefined) {
        setAttr(charPrNode, 'fontBold', format.bold ? '1' : '0', 'hh:fontBold');
    }
    if (format.italic !== undefined) {
        setAttr(charPrNode, 'fontItalic', format.italic ? '1' : '0', 'hh:fontItalic');
    }
    if (format.underline !== undefined) {
        setAttr(charPrNode, 'underline', format.underline ? '1' : '0', 'hh:underline');
    }
    if (format.fontSize !== undefined) {
        setAttr(charPrNode, 'height', String(Math.round(format.fontSize * 100)), 'hh:height');
    }
    if (format.color !== undefined) {
        setAttr(charPrNode, 'color', String(hexToColorInt(format.color)), 'hh:color');
    }
    if (format.fontName !== undefined) {
        const fontId = resolveFontId(headerTree, format.fontName);
        setAttr(charPrNode, 'fontRef', String(fontId), 'hh:fontRef');
    }
}
function resolveFontId(headerTree, fontName) {
    const fontFacesNode = getFontFacesNode(headerTree);
    const fontNodes = getChildElements(getElementChildren(fontFacesNode, 'hh:fontfaces'), 'hh:fontface');
    const existing = fontNodes.find((fontNode) => getAttr(fontNode, 'face') === fontName);
    if (existing) {
        return parseIntStrict(getAttr(existing, 'id') ?? '0', 'font id');
    }
    const newId = getNextId(fontNodes, 'id');
    const newFont = {
        'hh:fontface': [],
        ':@': {
            'hh:id': String(newId),
            'hh:face': fontName,
        },
    };
    getElementChildren(fontFacesNode, 'hh:fontfaces').push(newFont);
    return newId;
}
function getSectionParagraphNode(sectionTree, paragraphIndex) {
    const sectionRoot = getSectionRootNode(sectionTree);
    const sectionChildren = getElementChildren(sectionRoot, getElementName(sectionRoot));
    const paragraphs = getChildElements(sectionChildren, 'hp:p');
    const paragraph = paragraphs[paragraphIndex];
    if (!paragraph) {
        throw new Error(`Paragraph not found: index ${paragraphIndex}`);
    }
    return paragraph;
}
function getTableCellNode(sectionTree, tableIndex, rowIndex, cellIndex) {
    const tables = collectFlowChildElements(sectionTree, 'hp:tbl');
    const table = tables[tableIndex];
    if (!table) {
        throw new Error(`Table not found: index ${tableIndex}`);
    }
    const rows = getChildElements(getElementChildren(table, 'hp:tbl'), 'hp:tr');
    const row = rows[rowIndex];
    if (!row) {
        throw new Error(`Table row not found: index ${rowIndex}`);
    }
    const cells = getChildElements(getElementChildren(row, 'hp:tr'), 'hp:tc');
    const cell = cells[cellIndex];
    if (!cell) {
        throw new Error(`Table cell not found: index ${cellIndex}`);
    }
    return cell;
}
function getTextBoxParagraphNode(sectionTree, textBoxIndex, paragraphIndex) {
    const rects = getTextBoxRectNodes(sectionTree);
    const rect = rects[textBoxIndex];
    if (!rect) {
        throw new Error(`Text box not found: index ${textBoxIndex}`);
    }
    const drawText = getChildElements(getElementChildren(rect, 'hp:rect'), 'hp:drawText')[0];
    if (!drawText) {
        throw new Error(`Text box has no hp:drawText: index ${textBoxIndex}`);
    }
    const subList = getChildElements(getElementChildren(drawText, 'hp:drawText'), 'hp:subList')[0];
    if (!subList) {
        throw new Error(`Text box has no hp:subList: index ${textBoxIndex}`);
    }
    const paragraphs = getChildElements(getElementChildren(subList, 'hp:subList'), 'hp:p');
    const paragraph = paragraphs[paragraphIndex];
    if (!paragraph) {
        throw new Error(`Text box paragraph not found: tb${textBoxIndex}.p${paragraphIndex}`);
    }
    return paragraph;
}
function getTextBoxRectNodes(sectionTree) {
    return collectFlowChildElements(sectionTree, 'hp:rect');
}
/**
 * Mutator-side mirror of `collectFlowChildren` in `section-parser.ts`. Returns
 * `tag` element nodes from section flow in the same order the parser exposes
 * them, so refs like `s0.tN` resolve to the same node on read and on write.
 *
 * Operates on the `preserveOrder: true` tree shape used by the mutator.
 */
function collectFlowChildElements(sectionTree, tag) {
    const sectionRoot = getSectionRootNode(sectionTree);
    const sectionChildren = getElementChildren(sectionRoot, getElementName(sectionRoot));
    const sectionDirect = getChildElements(sectionChildren, tag);
    const sectionParagraphs = getChildElements(sectionChildren, 'hp:p');
    const fromParagraphs = sectionParagraphs.flatMap((paragraph) => {
        const paragraphChildren = getElementChildren(paragraph, 'hp:p');
        const paragraphDirect = getChildElements(paragraphChildren, tag);
        const runs = getChildElements(paragraphChildren, 'hp:run');
        const runDirect = runs.flatMap((run) => getChildElements(getElementChildren(run, 'hp:run'), tag));
        return [...paragraphDirect, ...runDirect];
    });
    return [...sectionDirect, ...fromParagraphs];
}
function getCellParagraphNodes(cellNode) {
    const cellChildren = getElementChildren(cellNode, 'hp:tc');
    const direct = getChildElements(cellChildren, 'hp:p');
    if (direct.length > 0) {
        return direct;
    }
    const subList = getChildElements(cellChildren, 'hp:subList')[0];
    if (subList) {
        return getChildElements(getElementChildren(subList, 'hp:subList'), 'hp:p');
    }
    return [];
}
function getRunNodesFromParagraph(paragraphNode) {
    return getChildElements(getElementChildren(paragraphNode, 'hp:p'), 'hp:run');
}
function getSectionRootNode(tree) {
    const sectionRoot = tree.find((node) => hasElement(node, 'hs:sec') || hasElement(node, 'hs:section'));
    if (!sectionRoot) {
        throw new Error('Section root not found');
    }
    return sectionRoot;
}
function getCharPropertiesNode(headerTree) {
    const refListNode = getRefListNode(headerTree);
    const charPropertiesNode = getChildElements(getElementChildren(refListNode, 'hh:refList'), 'hh:charProperties')[0];
    if (!charPropertiesNode) {
        throw new Error('hh:charProperties not found in header.xml');
    }
    return charPropertiesNode;
}
function getFontFacesNode(headerTree) {
    const refListNode = getRefListNode(headerTree);
    const fontFacesNode = getChildElements(getElementChildren(refListNode, 'hh:refList'), 'hh:fontfaces')[0];
    if (!fontFacesNode) {
        throw new Error('hh:fontfaces not found in header.xml');
    }
    return fontFacesNode;
}
function getRefListNode(headerTree) {
    const headNode = headerTree.find((node) => hasElement(node, 'hh:head'));
    if (!headNode) {
        throw new Error('hh:head not found in header.xml');
    }
    const refListNode = getChildElements(getElementChildren(headNode, 'hh:head'), 'hh:refList')[0];
    if (!refListNode) {
        throw new Error('hh:refList not found in header.xml');
    }
    return refListNode;
}
function getStylesNode(headerTree) {
    const refListNode = getRefListNode(headerTree);
    const stylesNode = getChildElements(getElementChildren(refListNode, 'hh:refList'), 'hh:styles')[0];
    if (!stylesNode) {
        throw new Error('hh:styles not found in header.xml');
    }
    return stylesNode;
}
function lookupStyle(headerTree, query) {
    const stylesNode = getStylesNode(headerTree);
    const styleNodes = getChildElements(getElementChildren(stylesNode, 'hh:styles'), 'hh:style');
    if ('heading' in query) {
        const name = `\uAC1C\uC694 ${query.heading}`;
        const found = styleNodes.find((node) => getAttr(node, 'name') === name);
        if (!found) {
            throw new Error(`Heading style not found: \uAC1C\uC694 ${query.heading}`);
        }
        return found;
    }
    const { style } = query;
    if (typeof style === 'number') {
        const found = styleNodes.find((node) => getAttr(node, 'id') === String(style));
        if (!found) {
            throw new Error(`Style not found with id: ${style}`);
        }
        return found;
    }
    const found = styleNodes.find((node) => getAttr(node, 'name') === style);
    if (!found) {
        throw new Error(`Style not found with name: ${style}`);
    }
    return found;
}
function getElementName(node) {
    for (const key of Object.keys(node)) {
        if (key !== ':@') {
            return key;
        }
    }
    throw new Error('Invalid XML node: missing element name');
}
function getElementChildren(node, elementName) {
    const value = node[elementName];
    if (!Array.isArray(value)) {
        throw new Error(`Invalid XML node shape for element: ${elementName}`);
    }
    return value;
}
function getChildElements(children, elementName) {
    return children.filter((child) => hasElement(child, elementName));
}
function hasElement(node, elementName) {
    return Object.hasOwn(node, elementName);
}
function getAttr(node, baseName) {
    const attrs = node[':@'];
    if (!attrs || typeof attrs !== 'object') {
        return undefined;
    }
    for (const [key, value] of Object.entries(attrs)) {
        if (key === baseName || key.endsWith(`:${baseName}`)) {
            return typeof value === 'string' ? value : undefined;
        }
    }
    return undefined;
}
function setAttr(node, baseName, value, preferredKey) {
    const attrs = getOrCreateAttrs(node);
    const existingKey = Object.keys(attrs).find((key) => key === baseName || key.endsWith(`:${baseName}`));
    attrs[existingKey ?? preferredKey] = value;
}
function getOrCreateAttrs(node) {
    const attrs = node[':@'];
    if (attrs && typeof attrs === 'object') {
        return attrs;
    }
    const nextAttrs = {};
    node[':@'] = nextAttrs;
    return nextAttrs;
}
function getNextId(nodes, attrName) {
    let max = -1;
    for (const node of nodes) {
        const raw = getAttr(node, attrName);
        if (!raw) {
            continue;
        }
        const value = Number.parseInt(raw, 10);
        if (!Number.isNaN(value) && value > max) {
            max = value;
        }
    }
    return max + 1;
}
function parseIntStrict(value, label) {
    const parsed = Number.parseInt(value, 10);
    if (Number.isNaN(parsed)) {
        throw new Error(`Invalid ${label}: ${value}`);
    }
    return parsed;
}
function hexToColorInt(hexColor) {
    const normalized = hexColor.startsWith('#') ? hexColor.slice(1) : hexColor;
    if (!/^[0-9a-fA-F]{6}$/.test(normalized)) {
        throw new Error(`Invalid color value: ${hexColor}`);
    }
    return Number.parseInt(normalized, 16);
}
export function parseXml(xml) {
    return parser.parse(xml);
}
export function buildXml(tree) {
    return builder.build(tree);
}
function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
}
//# sourceMappingURL=mutator.js.map