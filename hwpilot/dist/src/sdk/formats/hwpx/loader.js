import JSZip from 'jszip';
import { PATHS, sectionPath } from './paths.js';
export async function loadHwpx(fileBuffer) {
    let zip;
    try {
        zip = await JSZip.loadAsync(Buffer.from(fileBuffer));
    }
    catch (err) {
        throw new Error(`Failed to parse HWPX file as ZIP — ${err.message}`);
    }
    validateHwpx(zip);
    const sectionCount = countSections(zip);
    return {
        async getVersionXml() {
            return getEntry(zip, PATHS.VERSION_XML);
        },
        async getHeaderXml() {
            return getEntry(zip, PATHS.HEADER_XML);
        },
        async getSectionXml(n) {
            return getEntry(zip, sectionPath(n));
        },
        getSectionCount() {
            return sectionCount;
        },
        listBinData() {
            return Object.keys(zip.files).filter((name) => name.startsWith(PATHS.BIN_DATA_DIR) && !zip.files[name].dir);
        },
        async getBinData(path) {
            const entry = zip.file(path);
            if (!entry)
                throw new Error(`BinData entry not found: ${path}`);
            return entry.async('nodebuffer');
        },
        getZip() {
            return zip;
        },
    };
}
function validateHwpx(zip) {
    const required = [PATHS.HEADER_XML, PATHS.CONTENT_HPF];
    for (const path of required) {
        if (!zip.file(path)) {
            throw new Error(`Invalid HWPX file: missing required entry "${path}"`);
        }
    }
    if (!zip.file(sectionPath(0))) {
        throw new Error(`Invalid HWPX file: missing required entry "${sectionPath(0)}"`);
    }
}
function countSections(zip) {
    let count = 0;
    while (zip.file(sectionPath(count))) {
        count++;
    }
    return count;
}
async function getEntry(zip, path) {
    const entry = zip.file(path);
    if (!entry)
        throw new Error(`HWPX entry not found: ${path}`);
    return entry.async('string');
}
//# sourceMappingURL=loader.js.map