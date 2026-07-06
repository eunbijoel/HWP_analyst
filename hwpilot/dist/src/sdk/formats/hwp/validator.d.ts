export type CheckStatus = 'pass' | 'fail' | 'warn' | 'skip';
export type CheckResult = {
    name: string;
    status: CheckStatus;
    message?: string;
    details?: Record<string, unknown>;
};
export type ValidateResult = {
    valid: boolean;
    format: 'hwp' | 'hwpx';
    file: string;
    checks: CheckResult[];
};
export type ValidateHwpOptions = {
    contentCoverageThreshold?: number;
};
export declare const HWP_PROP_COMPRESSED = 1;
export declare const HWP_PROP_ENCRYPTED = 2;
export declare const HWP_PROP_DISTRIBUTION = 4;
export declare const HWP_PROP_HAS_SCRIPTS = 8;
export declare const HWP_PROP_DRM = 16;
export declare const HWP_PROP_HAS_XMLTEMPLATE = 32;
export declare const HWP_PROP_HAS_DOCHISTORY = 64;
export declare const HWP_PROP_HAS_SIGNATURE = 128;
export declare const HWP_PROP_CERT_ENCRYPTED = 256;
export declare const HWP_PROP_SIGN_PREVIEW = 512;
export declare const HWP_PROP_CERT_DRM = 1024;
export declare const HWP_PROP_CCL = 2048;
export declare const HWP_PROP_MOBILE = 4096;
export declare const HWP_PROP_PRIVACY = 8192;
export declare const HWP_PROP_TRACK_CHANGES = 16384;
export declare const HWP_PROP_KOGL = 32768;
export declare const HWP_PROP_HAS_VIDEO = 65536;
export declare const HWP_PROP_HAS_TOC_FIELD = 131072;
export declare function validateHwp(fileBuffer: Uint8Array, options?: ValidateHwpOptions): Promise<ValidateResult>;
export declare function validateHwpBuffer(buffer: Buffer, options?: ValidateHwpOptions): Promise<ValidateResult>;
//# sourceMappingURL=validator.d.ts.map