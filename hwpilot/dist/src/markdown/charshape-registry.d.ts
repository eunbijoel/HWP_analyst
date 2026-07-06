import type { CharShape } from '../types.js';
type CharShapeOptions = {
    bold?: boolean;
    italic?: boolean;
    underline?: boolean;
    fontRef?: number;
};
export declare class CharShapeRegistry {
    private shapes;
    private keyMap;
    private baseFontRef;
    private baseFontSize;
    private baseColor;
    constructor(baseFontRef: number, baseFontSize: number, baseColor?: string);
    getRef(options?: CharShapeOptions): number;
    getCharShapes(): CharShape[];
}
export {};
//# sourceMappingURL=charshape-registry.d.ts.map