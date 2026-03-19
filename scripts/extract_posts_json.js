#!/usr/bin/env node
/**
 * One-time bootstrap: extract const posts and const postExtras from index.html into JSON files.
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

// Extract const posts = [...];
const postsMatch = html.match(/const posts = (\[[\s\S]*?\n\]);/);
if (!postsMatch) { console.error('Could not find const posts array'); process.exit(1); }

// Extract const postExtras = {...};
const extrasMatch = html.match(/const postExtras = (\{[\s\S]*?\n\});/);

// Eval in a sandboxed context
const posts = eval('(' + postsMatch[1] + ')');
console.log(`Extracted ${posts.length} posts`);

const dataDir = path.join(ROOT, 'data');
if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

fs.writeFileSync(path.join(dataDir, 'posts.json'), JSON.stringify(posts, null, 2));
console.log(`Saved data/posts.json`);

if (extrasMatch) {
  const extras = eval('(' + extrasMatch[1] + ')');
  fs.writeFileSync(path.join(dataDir, 'post_extras.json'), JSON.stringify(extras, null, 2));
  console.log(`Saved data/post_extras.json (${Object.keys(extras).length} entries)`);
}
