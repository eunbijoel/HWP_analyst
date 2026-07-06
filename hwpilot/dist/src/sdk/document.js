import { detectFormat } from '../sdk/format-detector.js';
import { loadHwp } from '../sdk/formats/hwp/reader.js';
import { editHwp } from '../sdk/formats/hwp/writer.js';
import { parseHeader } from '../sdk/formats/hwpx/header-parser.js';
import { loadHwpx } from '../sdk/formats/hwpx/loader.js';
import { editHwpx } from '../sdk/formats/hwpx/writer.js';
import { parseSections } from '../sdk/formats/hwpx/section-parser.js';
import { extractAllText, extractPaginatedText, extractRefText, findInSections, getTableData, listImages, listTables, resolveRef, } from '../sdk/document-ops.js';
export class Document {
    _doc;
    _bytes;
    _format;
    constructor(doc, bytes) {
        this._doc = doc;
        this._bytes = bytes;
        this._format = doc.format;
    }
    get format() {
        return this._format;
    }
    read(refOrOptions) {
        if (typeof refOrOptions === 'string') {
            return resolveRef(refOrOptions, this._doc.sections);
        }
        const offset = refOrOptions?.offset ?? 0;
        const limit = refOrOptions?.limit ?? Number.POSITIVE_INFINITY;
        const hasPagination = refOrOptions?.offset !== undefined || refOrOptions?.limit !== undefined;
        return {
            format: this._doc.format,
            sections: this._doc.sections.map((section, index) => {
                const paragraphs = hasPagination ? section.paragraphs.slice(offset, offset + limit) : section.paragraphs;
                return {
                    index,
                    ...(hasPagination && {
                        totalParagraphs: section.paragraphs.length,
                        totalTables: section.tables.length,
                        totalImages: section.images.length,
                        totalTextBoxes: section.textBoxes.length,
                    }),
                    paragraphs,
                    tables: section.tables,
                    images: section.images,
                    textBoxes: section.textBoxes,
                };
            }),
            header: this._doc.header,
        };
    }
    text(refOrOptions) {
        if (typeof refOrOptions === 'string') {
            return extractRefText(refOrOptions, this._doc.sections);
        }
        if (refOrOptions?.offset !== undefined || refOrOptions?.limit !== undefined) {
            const offset = refOrOptions?.offset ?? 0;
            const limit = refOrOptions?.limit ?? Number.POSITIVE_INFINITY;
            return extractPaginatedText(this._doc.sections, offset, limit).text;
        }
        return extractAllText(this._doc.sections);
    }
    find(query) {
        return findInSections(this._doc.sections, query);
    }
    tableRead(ref) {
        return getTableData(this._doc.sections, ref);
    }
    tableList() {
        return listTables(this._doc.sections);
    }
    imageList() {
        return listImages(this._doc.sections);
    }
    async editText(ref, text) {
        await this._applyOp({ type: 'setText', ref, text });
    }
    async editFormat(ref, format) {
        const { start, end, ...fmt } = format;
        await this._applyOp({ type: 'setFormat', ref, format: fmt, start, end });
    }
    async tableEdit(ref, text) {
        await this._applyOp({ type: 'setTableCell', ref, text });
    }
    async addParagraph(ref, text, options) {
        const position = options?.position ?? 'end';
        const format = options?.bold !== undefined ||
            options?.italic !== undefined ||
            options?.underline !== undefined ||
            options?.fontName !== undefined ||
            options?.fontSize !== undefined ||
            options?.color !== undefined
            ? {
                bold: options?.bold,
                italic: options?.italic,
                underline: options?.underline,
                fontName: options?.fontName,
                fontSize: options?.fontSize,
                color: options?.color,
            }
            : undefined;
        await this._applyOp({
            type: 'addParagraph',
            ref,
            text,
            position,
            format,
            heading: options?.heading,
            style: options?.style,
        });
    }
    async addTable(ref, rows, cols, options) {
        await this._applyOp({
            type: 'addTable',
            ref,
            rows,
            cols,
            data: options?.data,
            position: options?.position ?? 'end',
        });
    }
    async export() {
        return this._bytes;
    }
    async _applyOp(op) {
        if (this._format === 'hwp') {
            const newBytes = await editHwp(this._bytes, [op]);
            const newDoc = await loadHwp(newBytes);
            this._bytes = newBytes;
            this._doc = newDoc;
        }
        else {
            const newBytes = await editHwpx(this._bytes, [op]);
            const archive = await loadHwpx(newBytes);
            const header = parseHeader(await archive.getHeaderXml());
            const sections = await parseSections(archive);
            this._bytes = newBytes;
            this._doc = { format: 'hwpx', sections, header };
        }
    }
}
export async function documentFromBytes(bytes) {
    const format = detectFormat(bytes);
    if (format === 'hwp') {
        const doc = await loadHwp(bytes);
        return new Document(doc, bytes);
    }
    const archive = await loadHwpx(bytes);
    const header = parseHeader(await archive.getHeaderXml());
    const sections = await parseSections(archive);
    const doc = { format: 'hwpx', sections, header };
    return new Document(doc, bytes);
}
//# sourceMappingURL=document.js.map