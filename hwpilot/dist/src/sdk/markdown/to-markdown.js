import { getHeadingLevel } from './heading-styles.js';
export function hwpToMarkdown(doc) {
    const sections = doc.sections.map((section) => convertSection(section, doc.header));
    return sections.join('\n\n---\n\n');
}
function convertSection(section, header) {
    const blocks = [];
    for (const paragraph of section.paragraphs) {
        blocks.push(convertParagraph(paragraph, header));
    }
    for (const image of section.images) {
        blocks.push(`![](${image.binDataPath})`);
    }
    for (const textBox of section.textBoxes) {
        for (const paragraph of textBox.paragraphs) {
            blocks.push(convertParagraph(paragraph, header));
        }
    }
    for (const table of section.tables) {
        blocks.push(convertTable(table, header));
    }
    return blocks.join('\n\n');
}
function convertParagraph(paragraph, header) {
    const text = convertParagraphText(paragraph, header);
    if (text.length === 0) {
        return '';
    }
    const headingLevel = getHeadingLevel(paragraph, header.styles, header.paraShapes);
    if (headingLevel === null) {
        return text;
    }
    const normalizedHeadingLevel = Math.max(1, Math.min(headingLevel, 6));
    return `${'#'.repeat(normalizedHeadingLevel)} ${text}`;
}
function convertParagraphText(paragraph, header) {
    return paragraph.runs
        .map((run) => formatRunText(run.text, resolveCharShape(run.charShapeRef, header)))
        .filter((text) => text.length > 0)
        .join('');
}
function resolveCharShape(charShapeRef, header) {
    return (header.charShapes.find((charShape) => charShape.id === charShapeRef) ??
        header.charShapes[charShapeRef]);
}
function formatRunText(text, charShape) {
    if (text.length === 0) {
        return '';
    }
    if (!charShape) {
        return text;
    }
    if (charShape.bold && charShape.italic) {
        return `***${text}***`;
    }
    if (charShape.bold) {
        return `**${text}**`;
    }
    if (charShape.italic) {
        return `*${text}*`;
    }
    return text;
}
function convertTable(table, header) {
    if (table.rows.length === 0) {
        return '';
    }
    const headerRow = table.rows[0];
    const lines = [rowToMarkdown(headerRow, header)];
    lines.push(separatorRow(headerRow, header));
    for (const row of table.rows.slice(1)) {
        lines.push(rowToMarkdown(row, header));
    }
    return lines.join('\n');
}
function rowToMarkdown(row, header) {
    const content = row.cells.map((cell) => convertCell(cell, header)).join(' | ');
    return `| ${content} |`;
}
function separatorRow(headerRow, header) {
    const separators = headerRow.cells
        .map((cell) => {
        const paraShapeRef = cell.paragraphs[0]?.paraShapeRef ?? 0;
        const paraShape = header.paraShapes.find((ps) => ps.id === paraShapeRef) ?? header.paraShapes[paraShapeRef];
        const align = paraShape?.align;
        if (align === 'center')
            return ':---:';
        if (align === 'right')
            return '---:';
        if (align === 'left')
            return ':---';
        return '---';
    })
        .join('|');
    return `|${separators}|`;
}
function convertCell(cell, header) {
    return cell.paragraphs
        .map((paragraph) => convertParagraphText(paragraph, header))
        .filter((text) => text.length > 0)
        .join(' ')
        .replaceAll('|', '\\|');
}
//# sourceMappingURL=to-markdown.js.map