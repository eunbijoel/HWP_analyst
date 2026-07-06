import type { Paragraph, ParaShape, Style } from '../types.js';
export declare function headingStyleName(level: number): string;
export declare function getHeadingLevel(paragraph: Paragraph, styles: Style[], paraShapes: ParaShape[]): number | null;
export declare function createHeadingInfrastructure(baseCharShapeRef: number, baseParaShapeId: number, baseStyleId: number): {
    paraShapes: ParaShape[];
    styles: Style[];
};
//# sourceMappingURL=heading-styles.d.ts.map