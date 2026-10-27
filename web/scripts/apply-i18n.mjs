import fs from "node:fs";
import path from "node:path";
import * as parser from "@babel/parser";
import traverse from "@babel/traverse";

const root = path.resolve(import.meta.dirname, "../src");

const ATTRS = new Set(["aria-label", "placeholder", "title", "alt"]);
const PROP_NAMES = new Set([
  "label",
  "title",
  "description",
  "eyebrow",
  "placeholder",
  "hint",
  "confirmLabel",
  "message",
  "empty",
  "pendingLabel",
  "emptyLabel",
]);
const SKIP_ATTRS = new Set([
  "className",
  "href",
  "src",
  "id",
  "name",
  "type",
  "role",
  "method",
  "autoComplete",
  "htmlFor",
  "key",
  "pattern",
  "inputMode",
  "rel",
  "target",
  "accept",
  "value",
  "xmlns",
  "viewBox",
  "d",
  "fill",
  "stroke",
  "width",
  "height",
]);
const CALLS = new Set([
  "setError",
  "setNotice",
  "setMessage",
  "errorText",
  "financeError",
  "phase5Error",
]);
const DISPLAY_IDS = new Set([
  "label",
  "title",
  "description",
  "eyebrow",
  "message",
  "notice",
  "placeholder",
  "hint",
  "confirmLabel",
  "emptyLabel",
]);

function featureFor(file) {
  const rel = file.replaceAll("\\", "/");
  if (
    rel.includes("/features/auth/") ||
    rel.includes("/app/(auth)/") ||
    rel.includes("/app/mfa-setup/")
  ) {
    return "auth";
  }
  if (rel.includes("/features/finance/")) return "finance";
  if (rel.includes("/features/pt/")) return "pt";
  if (rel.includes("/features/reports/")) return "reports";
  if (rel.includes("/features/platform/") || rel.includes("/super-admin/")) {
    return "platform";
  }
  if (
    rel.includes("/components/layout/") ||
    rel.endsWith("/workspace-nav.ts")
  ) {
    return "nav";
  }
  if (rel.includes("/features/operations/") || rel.includes("/app/(tenant)/")) {
    return "operations";
  }
  return "common";
}

function isClassList(text) {
  if (/[.!?]/.test(text) || /[A-Z]/.test(text)) return false;
  const parts = text.trim().split(/\s+/);
  if (parts.length === 0) return false;
  return parts.every(
    (part) =>
      /-|:|\[|&/.test(part) ||
      /^(?:grid|flex|hidden|block|inline|inline-flex|button|truncate|sr-only|contents|relative|absolute|fixed|sticky)$/.test(
        part,
      ),
  );
}

function isHumanCopy(value) {
  const text = value.trim();
  if (text.length < 2 || text.length > 500) return false;
  if (/^(https?:|\/|\.\/|#)/.test(text)) return false;
  if (/^[A-Z0-9_]+$/.test(text)) return false;
  if (/^[a-z0-9_.:/-]+$/.test(text)) return false;
  if (/^[a-z]+(?:[A-Z][a-z0-9]+)+$/.test(text)) return false;
  if (!/[A-Za-z]/.test(text)) return false;
  if (/[{}]/.test(text)) return false;
  if (text.includes("NEXT_REDIRECT")) return false;
  if (/[ ]/.test(text) || /[…?!]/.test(text)) return true;
  return /^[A-Z][a-zA-Z0-9'+-]*$/.test(text);
}

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (
        entry.name === "api" ||
        entry.name === "i18n" ||
        entry.name === "types"
      ) {
        continue;
      }
      walk(full, files);
    } else if (
      /\.(tsx|ts)$/.test(entry.name) &&
      !entry.name.endsWith(".d.ts")
    ) {
      const rel = full.replaceAll("\\", "/");
      if (
        rel.includes("/lib/") &&
        !rel.includes("/lib/ui/") &&
        !rel.endsWith("/errors.ts")
      ) {
        continue;
      }
      files.push(full);
    }
  }
  return files;
}

function parse(file, source) {
  return parser.parse(source, {
    sourceType: "module",
    plugins: ["typescript", "jsx"],
    errorRecovery: true,
  });
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

function skipString(nodePath) {
  const parent = nodePath.parent;
  if (!parent) return true;
  if (
    parent.type === "ImportDeclaration" ||
    parent.type === "ExportNamedDeclaration"
  )
    return true;
  if (parent.type === "TSLiteralType") return true;
  if (parent.type === "JSXAttribute") {
    const name = parent.name.name;
    if (!ATTRS.has(name)) return true;
  }
  if (
    parent.type === "CallExpression" &&
    parent.callee.type === "Identifier" &&
    (parent.callee.name === "tx" ||
      (["actionButtonClass", "actionTone"].includes(parent.callee.name) &&
        parent.arguments[0] === nodePath.node))
  ) {
    return true;
  }
  if (
    parent.type === "BinaryExpression" &&
    ["===", "!==", "==", "!="].includes(parent.operator)
  ) {
    return true;
  }
  if (parent.type === "NewExpression") return true;
  return false;
}

function shouldWrap(nodePath) {
  const node = nodePath.node;
  if (node.type !== "StringLiteral") return false;
  if (!isHumanCopy(node.value) || isClassList(node.value)) return false;
  if (!componentPath(nodePath)) return false;
  if (skipString(nodePath)) return false;
  if (
    nodePath.findParent(
      (parent) =>
        parent.isJSXAttribute() && parent.node.name?.name === "className",
    )
  ) {
    return false;
  }
  const parent = nodePath.parent;
  if (parent.type === "JSXAttribute") return ATTRS.has(parent.name.name);
  if (parent.type === "JSXExpressionContainer") return true;
  if (
    parent.type === "ConditionalExpression" &&
    (parent.consequent === node || parent.alternate === node)
  ) {
    return Boolean(
      nodePath.findParent((item) => item.isJSXExpressionContainer()),
    );
  }
  if (
    parent.type === "LogicalExpression" &&
    parent.operator === "&&" &&
    parent.right === node
  ) {
    return Boolean(
      nodePath.findParent((item) => item.isJSXExpressionContainer()),
    );
  }
  if (
    parent.type === "ObjectProperty" &&
    !parent.computed &&
    ((parent.key.type === "Identifier" && PROP_NAMES.has(parent.key.name)) ||
      (parent.key.type === "StringLiteral" && PROP_NAMES.has(parent.key.value)))
  ) {
    return true;
  }
  if (
    parent.type === "CallExpression" &&
    parent.callee.type === "Identifier" &&
    CALLS.has(parent.callee.name)
  ) {
    return true;
  }
  if (
    parent.type === "ReturnStatement" ||
    parent.type === "ArrowFunctionExpression"
  ) {
    return true;
  }
  return false;
}

function escapeText(text, quote) {
  return text
    .replaceAll("\\", "\\\\")
    .replaceAll(quote, `\\${quote}`)
    .replaceAll("\r", "\\r")
    .replaceAll("\n", "\\n");
}

function quoteCall(text) {
  const normalized = text.replace(/\s+/g, " ").trim();
  const quote =
    normalized.includes('"') && !normalized.includes("'") ? "'" : '"';
  return `tx(${quote}${escapeText(normalized, quote)}${quote})`;
}

function collectKeys(file, source, found) {
  const ast = parse(file, source);
  const keys = [];
  traverse(ast, {
    StringLiteral(nodePath) {
      if (!isHumanCopy(nodePath.node.value) || skipString(nodePath)) return;
      const parent = nodePath.parent;
      const useful =
        parent.type === "JSXAttribute" ||
        parent.type === "JSXExpressionContainer" ||
        parent.type === "JSXText" ||
        (parent.type === "ObjectProperty" &&
          !parent.computed &&
          ((parent.key.type === "Identifier" &&
            PROP_NAMES.has(parent.key.name)) ||
            (parent.key.type === "StringLiteral" &&
              PROP_NAMES.has(parent.key.value)))) ||
        (parent.type === "CallExpression" &&
          parent.callee.type === "Identifier" &&
          CALLS.has(parent.callee.name)) ||
        shouldWrap(nodePath);
      if (useful) keys.push(nodePath.node.value.replace(/\s+/g, " ").trim());
    },
    JSXText(nodePath) {
      const text = nodePath.node.value.trim();
      if (isHumanCopy(text)) keys.push(text.replace(/\s+/g, " ").trim());
    },
  });
  const feature = featureFor(file);
  for (const key of keys) {
    if (!found.has(key)) found.set(key, new Set());
    found.get(key).add(feature);
  }
}

function transform(file) {
  const original = fs.readFileSync(file, "utf8");
  const client =
    original.includes('"use client"') || original.includes("'use client'");
  if (
    !client ||
    file.endsWith(`${path.sep}language-switch.tsx`) ||
    file.endsWith(`${path.sep}use-tx.tsx`)
  ) {
    return false;
  }
  const ast = parse(file, original);
  const replacements = [];
  traverse(ast, {
    StringLiteral(nodePath) {
      if (!shouldWrap(nodePath)) return;
      const text = nodePath.node.value.trim();
      const call = quoteCall(text);
      replacements.push({
        start: nodePath.node.start,
        end: nodePath.node.end,
        text: nodePath.parent.type === "JSXAttribute" ? `{${call}}` : call,
      });
    },
    JSXText(nodePath) {
      if (!componentPath(nodePath)) return;
      const raw = nodePath.node.value;
      const text = raw.trim().replace(/\s+/g, " ");
      if (!isHumanCopy(text) || isClassList(text)) return;
      const quote = text.includes('"') && !text.includes("'") ? "'" : '"';
      let replacement = `{tx(${quote}${escapeText(text, quote)}${quote})}`;
      if (/^\s/.test(raw)) replacement = `{" "}${replacement}`;
      if (/\s$/.test(raw)) replacement = `${replacement}{" "}`;
      replacements.push({
        start: nodePath.node.start,
        end: nodePath.node.end,
        text: replacement,
      });
    },
    JSXExpressionContainer(nodePath) {
      if (!componentPath(nodePath)) return;
      const parent = nodePath.parent;
      if (parent.type !== "JSXElement" && parent.type !== "JSXFragment") return;
      const expr = nodePath.node.expression;
      if (!expr || expr.type === "JSXEmptyExpression") return;
      const displayId =
        expr.type === "Identifier" && DISPLAY_IDS.has(expr.name);
      const displayProp =
        expr.type === "MemberExpression" &&
        !expr.computed &&
        expr.property.type === "Identifier" &&
        DISPLAY_IDS.has(expr.property.name);
      if (!displayId && !displayProp) return;
      const raw = original.slice(expr.start, expr.end);
      if (raw.startsWith("tx(")) return;
      replacements.push({
        start: expr.start,
        end: expr.end,
        text: `tx(${raw})`,
      });
    },
  });
  if (replacements.length === 0) return false;
  replacements.sort((a, b) => b.start - a.start);
  let next = original;
  for (const replacement of replacements) {
    next =
      next.slice(0, replacement.start) +
      replacement.text +
      next.slice(replacement.end);
  }

  const updated = parse(file, next);
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
      if (!body || body.type !== "BlockStatement") {
        if (body && next.slice(body.start, body.end).includes("tx(")) {
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
  if (!next.includes('from "@/i18n/use-tx"')) {
    const directive = next.match(/^\s*["']use client["'];?\n/);
    const at = directive ? directive[0].length : 0;
    next = `${next.slice(0, at)}import { useTx } from "@/i18n/use-tx";\n${next.slice(at)}`;
  }
  fs.writeFileSync(file, next);
  return true;
}

const files = walk(root);
const found = new Map();
let changed = 0;
for (const file of files) {
  const source = fs.readFileSync(file, "utf8");
  collectKeys(file, source, found);
  if (transform(file)) changed += 1;
}
console.log(JSON.stringify({ changed, keys: found.size }));
fs.writeFileSync(
  path.resolve(import.meta.dirname, "i18n-keys.json"),
  JSON.stringify(
    [...found.entries()].map(([key, features]) => ({
      key,
      features: [...features],
    })),
    null,
    2,
  ),
);
