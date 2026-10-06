// Generate offline icons from the existing MIT-licensed Lucide bundle.
const fs = require("node:fs");
const path = require("node:path");
const lucide = require("../static/vendor/lucide.min.js");
const names = [
  "workflow", "shield-check", "file-check", "chevron-down", "lock-keyhole",
  "bell-ring", "scan-line", "search", "wrench", "check-check",
  "messages-square", "key-round", "fingerprint", "archive", "book-open",
  "network", "terminal", "cpu", "globe",
];
function render([tag, attrs, children = []]) {
  const attributes = Object.entries(attrs).map(([key, value]) => ` ${key}="${value}"`).join("");
  return `<${tag}${attributes}>${children.map(render).join("")}</${tag}>`;
}
const icons = Object.fromEntries(names.map(name => {
  const key = name.split("-").map(part => part[0].toUpperCase() + part.slice(1)).join("");
  if (!lucide[key]) throw new Error(`Missing Lucide icon ${name}`);
  return [name, render(lucide[key])];
}));
fs.writeFileSync(path.join(__dirname, "icons.json"), JSON.stringify(icons, null, 2) + "\n");
