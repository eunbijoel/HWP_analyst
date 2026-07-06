import { XMLParser } from 'fast-xml-parser';
const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: '',
    parseAttributeValue: true,
    isArray: (_name) => ['hh:fontface', 'hh:charPr', 'hh:paraPr', 'hh:style'].includes(_name),
});
const ALIGN_MAP = {
    JUSTIFY: 'justify',
    CENTER: 'center',
    LEFT: 'left',
    RIGHT: 'right',
};
function colorToHex(colorInt) {
    const r = (colorInt >> 16) & 0xff;
    const g = (colorInt >> 8) & 0xff;
    const b = colorInt & 0xff;
    return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}
function parseAlign(align) {
    return ALIGN_MAP[align] ?? 'left';
}
function toBool(value) {
    return value === 1 || value === '1';
}
export function parseHeader(xml) {
    const parsed = parser.parse(xml);
    const refList = parsed['hh:head']?.['hh:refList'] ?? {};
    const rawFonts = refList['hh:fontfaces']?.['hh:fontface'] ?? [];
    const fonts = rawFonts.map((f) => ({
        id: f['hh:id'],
        name: f['hh:face'],
    }));
    const rawCharPrs = refList['hh:charProperties']?.['hh:charPr'] ?? [];
    const charShapes = rawCharPrs.map((c) => ({
        id: c['hh:id'],
        fontRef: c['hh:fontRef'],
        fontSize: Math.round(c['hh:height'] / 100),
        bold: toBool(c['hh:fontBold']),
        italic: toBool(c['hh:fontItalic']),
        underline: toBool(c['hh:underline']),
        color: colorToHex(c['hh:color']),
    }));
    const rawParaPrs = refList['hh:paraProperties']?.['hh:paraPr'] ?? [];
    const paraShapes = rawParaPrs.map((p) => {
        const shape = {
            id: p['hh:id'],
            align: parseAlign(p['hh:align']),
        };
        const heading = p['hh:heading'];
        if (heading && heading['hh:level'] !== undefined) {
            shape.headingLevel = heading['hh:level'];
        }
        return shape;
    });
    const rawStyles = refList['hh:styles']?.['hh:style'] ?? [];
    const styles = rawStyles.map((s) => {
        const style = {
            id: s['hh:id'],
            name: s['hh:name'],
            charShapeRef: s['hh:charPrIDRef'],
            paraShapeRef: s['hh:paraPrIDRef'],
        };
        if (s['hh:type'] !== undefined) {
            style.type = s['hh:type'];
        }
        return style;
    });
    return { fonts, charShapes, paraShapes, styles };
}
//# sourceMappingURL=header-parser.js.map