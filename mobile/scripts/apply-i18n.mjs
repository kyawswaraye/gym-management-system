import fs from "node:fs";
import path from "node:path";
import * as parser from "@babel/parser";
import traverseImport from "@babel/traverse";

const traverse = traverseImport.default ?? traverseImport;

const root = path.resolve(import.meta.dirname, "../src");
const ATTRS = new Set([
  "label",
  "title",
  "hint",
  "placeholder",
  "accessibilityLabel",
  "accessibilityHint",
  "message",
]);
const PROP_NAMES = ATTRS;

function isHumanCopy(value) {
  const text = value.replace(/\s+/g, " ").trim();
  if (text.length < 2 || text.length > 500) return false;
  if (/^(https?:|\/)/.test(text)) return false;
  if (/^[A-Z0-9_]+$/.test(text)) return false;
  if (/^[a-z0-9_.:/-]+$/.test(text)) return false;
  if (!/[A-Za-z]/.test(text) || /[{}]/.test(text)) return false;
  if (/[ ]/.test(text) || /[…?!]/.test(text)) return true;
  return /^[A-Z][a-zA-Z0-9'+-]*$/.test(text);
}

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (["i18n", "api", "types"].includes(entry.name)) continue;
      walk(full, files);
    } else if (
      entry.name.endsWith(".tsx") &&
      !entry.name.endsWith(".test.tsx") &&
      !["LanguageSwitch.tsx", "useTx.tsx"].includes(entry.name)
    ) {
      files.push(full);
    }
  }
  return files;
}

function componentPath(nodePath) {
  return nodePath.findParent((parent) => {
    if (
      parent.isFunctionDeclaration() &&
      parent.node.id &&
      /^[A-Z]/.test(parent.node.id.name)
    ) {
      return true;
    }
    if (
      (parent.isArrowFunctionExpression() || parent.isFunctionExpression()) &&
      parent.parentPath?.isVariableDeclarator() &&
      parent.parentPath.node.id.type === "Identifier" &&
      /^[A-Z]/.test(parent.parentPath.node.id.name)
    ) {
      return true;
    }
    return false;
  });
}

function shouldWrap(nodePath) {
  if (nodePath.node.type !== "StringLiteral") return false;
  if (!isHumanCopy(nodePath.node.value) || !componentPath(nodePath))
    return false;
  const parent = nodePath.parent;
  if (
    parent.type === "CallExpression" &&
    parent.callee.type === "Identifier" &&
    parent.callee.name === "tx"
  ) {
    return false;
  }
  if (parent.type === "JSXAttribute") {
    return parent.name.type === "JSXIdentifier" && ATTRS.has(parent.name.name);
  }
  if (parent.type === "JSXExpressionContainer") return true;
  if (
    parent.type === "ConditionalExpression" &&
    (parent.consequent === nodePath.node || parent.alternate === nodePath.node)
  ) {
    return Boolean(
      nodePath.findParent((item) => item.isJSXExpressionContainer()),
    );
  }
  if (
    parent.type === "ObjectProperty" &&
    !parent.computed &&
    parent.key.type === "Identifier" &&
    PROP_NAMES.has(parent.key.name)
  ) {
    return true;
  }
  if (
    parent.type === "CallExpression" &&
    parent.callee.type === "Identifier" &&
    ["setFormError", "setMessage", "setError"].includes(parent.callee.name)
  ) {
    return true;
  }
  return false;
}

function quoteCall(text) {
  const normalized = text.replace(/\s+/g, " ").trim();
  const quote =
    normalized.includes('"') && !normalized.includes("'") ? "'" : '"';
  const escaped = normalized
    .replaceAll("\\", "\\\\")
    .replaceAll(quote, `\\${quote}`);
  return `tx(${quote}${escaped}${quote})`;
}

const keys = new Set();
let changed = 0;
for (const file of walk(root)) {
  const original = fs.readFileSync(file, "utf8");
  const ast = parser.parse(original, {
    sourceType: "module",
    plugins: ["typescript", "jsx"],
  });
  const replacements = [];
  traverse(ast, {
    StringLiteral(nodePath) {
      if (!isHumanCopy(nodePath.node.value)) return;
      keys.add(nodePath.node.value.replace(/\s+/g, " ").trim());
      if (!shouldWrap(nodePath)) return;
      const call = quoteCall(nodePath.node.value);
      replacements.push({
        start: nodePath.node.start,
        end: nodePath.node.end,
        text: nodePath.parent.type === "JSXAttribute" ? `{${call}}` : call,
      });
    },
    JSXText(nodePath) {
      if (!componentPath(nodePath)) return;
      const raw = nodePath.node.value;
      const text = raw.replace(/\s+/g, " ").trim();
      if (!isHumanCopy(text)) return;
      keys.add(text);
      const quote = text.includes('"') && !text.includes("'") ? "'" : '"';
      const escaped = text
        .replaceAll("\\", "\\\\")
        .replaceAll(quote, `\\${quote}`);
      let replacement = `{tx(${quote}${escaped}${quote})}`;
      if (/^\s/.test(raw)) replacement = `{" "}${replacement}`;
      if (/\s$/.test(raw)) replacement = `${replacement}{" "}`;
      replacements.push({
        start: nodePath.node.start,
        end: nodePath.node.end,
        text: replacement,
      });
    },
  });
  if (replacements.length === 0) continue;
  replacements.sort((a, b) => b.start - a.start);
  let next = original;
  for (const replacement of replacements) {
    next =
      next.slice(0, replacement.start) +
      replacement.text +
      next.slice(replacement.end);
  }
  const updated = parser.parse(next, {
    sourceType: "module",
    plugins: ["typescript", "jsx"],
  });
  const inserts = [];
  traverse(updated, {
    "FunctionDeclaration|ArrowFunctionExpression|FunctionExpression"(nodePath) {
      const id =
        nodePath.node.id?.name ||
        (nodePath.parentPath?.isVariableDeclarator() &&
        nodePath.parentPath.node.id.type === "Identifier"
          ? nodePath.parentPath.node.id.name
          : "");
      if (!id || !/^[A-Z]/.test(id)) return;
      const body = nodePath.node.body;
      if (!body) return;
      if (body.type !== "BlockStatement") {
        if (next.slice(body.start, body.end).includes("tx(")) {
          inserts.push({
            start: body.start,
            end: body.end,
            text: `{\n  const tx = useTx();\n  return ${next.slice(body.start, body.end)};\n}`,
          });
        }
        return;
      }
      const block = next.slice(body.start, body.end);
      if (!block.includes("tx(") || block.includes("const tx = useTx(")) return;
      inserts.push({
        start: body.start + 1,
        end: body.start + 1,
        text: "\n  const tx = useTx();",
      });
    },
  });
  inserts.sort((a, b) => b.start - a.start);
  for (const insert of inserts) {
    next = next.slice(0, insert.start) + insert.text + next.slice(insert.end);
  }
  if (!next.includes('from "../i18n/useTx"')) {
    next = `import { useTx } from "../i18n/useTx";\n${next}`;
  }
  fs.writeFileSync(file, next);
  changed += 1;
}
fs.mkdirSync(path.resolve(import.meta.dirname, "i18n-keys"), {
  recursive: true,
});
fs.writeFileSync(
  path.resolve(import.meta.dirname, "i18n-keys/mobile.json"),
  JSON.stringify([...keys].sort(), null, 2),
);
process.stdout.write(`${JSON.stringify({ changed, keys: keys.size })}\n`);
