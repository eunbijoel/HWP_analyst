export class CharShapeRegistry {
    shapes = [];
    keyMap = new Map();
    baseFontRef;
    baseFontSize;
    baseColor;
    constructor(baseFontRef, baseFontSize, baseColor = '#000000') {
        this.baseFontRef = baseFontRef;
        this.baseFontSize = baseFontSize;
        this.baseColor = baseColor;
        const baseShape = {
            id: 0,
            fontRef: baseFontRef,
            fontSize: baseFontSize,
            bold: false,
            italic: false,
            underline: false,
            color: baseColor,
        };
        this.shapes.push(baseShape);
        this.keyMap.set(`${baseFontRef}:false:false:false`, 0);
    }
    getRef(options = {}) {
        const fontRef = options.fontRef ?? this.baseFontRef;
        const bold = options.bold ?? false;
        const italic = options.italic ?? false;
        const underline = options.underline ?? false;
        const key = `${fontRef}:${bold}:${italic}:${underline}`;
        if (this.keyMap.has(key)) {
            return this.keyMap.get(key);
        }
        const newShape = {
            id: this.shapes.length,
            fontRef,
            fontSize: this.baseFontSize,
            bold,
            italic,
            underline,
            color: this.baseColor,
        };
        const index = this.shapes.length;
        this.shapes.push(newShape);
        this.keyMap.set(key, index);
        return index;
    }
    getCharShapes() {
        return this.shapes;
    }
}
//# sourceMappingURL=charshape-registry.js.map