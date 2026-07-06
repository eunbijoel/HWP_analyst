import { readFileSync, writeFileSync } from 'node:fs';
const pkgPath = 'package.json';
const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
function rewritePath(p) {
    if (p.startsWith('./dist/'))
        return p;
    return p.replace(/^\.\/src\//, './dist/src/').replace(/\.ts$/, '.js');
}
function rewriteTypesPath(p) {
    if (p.startsWith('./dist/'))
        return p;
    return p.replace(/^\.\/src\//, './dist/src/').replace(/\.ts$/, '.d.ts');
}
for (const [name, path] of Object.entries(pkg.bin)) {
    pkg.bin[name] = rewritePath(path);
}
if (pkg.exports) {
    for (const [_key, value] of Object.entries(pkg.exports)) {
        if (typeof value === 'object' && value !== null) {
            if (value.types)
                value.types = rewriteTypesPath(value.types);
            if (value.default)
                value.default = rewritePath(value.default);
        }
    }
}
if (pkg.typesVersions) {
    for (const [_ver, mappings] of Object.entries(pkg.typesVersions)) {
        for (const [key, paths] of Object.entries(mappings)) {
            mappings[key] = paths.map((p) => rewriteTypesPath(p));
        }
    }
}
writeFileSync(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`);
console.log('Rewrote bin, exports, and typesVersions paths for publish');
//# sourceMappingURL=prepublish.js.map